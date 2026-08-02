"""Safe paid-vision fallback orchestration.

Paid and local OCR outputs are never treated as confirmed bets. This module
returns a pending-human-confirmation package that must be reviewed line by line
before existing parser/review flows may be used.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .contracts import RecognitionRequest, RecognitionResult, RecognitionStatus
from .image_intake import ImageMetadata
from .providers.openai_paid import (
    API_KEY_ENV,
    MODEL_ENV,
    OpenAIPaidVisionProvider,
    get_config_from_env,
    has_api_key,
)
from .providers.paddleocr_subprocess import recognize_with_metadata
from .quality import assess_image_quality, quality_gate


PENDING_HUMAN_CONFIRMATION = "PENDING_HUMAN_CONFIRMATION"
PROMPT_STRATEGY_VERSION = "paid-whole-image-first-v1"


class ProviderLike(Protocol):
    def recognize(self, request: RecognitionRequest) -> RecognitionResult: ...


@dataclass(frozen=True)
class PaidFallbackProviders:
    paid_provider: ProviderLike | None = None
    local_provider: ProviderLike | None = None


def run_paid_vision_fallback_job(
    metadata: ImageMetadata,
    *,
    request_id: str = "",
    providers: PaidFallbackProviders | None = None,
    field_crops: Sequence[ImageMetadata] | None = None,
    paid_image_metadata: ImageMetadata | None = None,
    document_mode: str = "auto",
) -> dict[str, Any]:
    """Run quality gate, local OCR, and optional paid whole-image OCR.

    This is still OCR only. It does not call parser, review, webfill, click,
    fill, press, submit, or any live-site automation.
    """
    request_id = request_id or f"{metadata.image_id}:paid-fallback"
    providers = providers or PaidFallbackProviders()

    quality_assessment = assess_image_quality(
        image_width=metadata.width,
        image_height=metadata.height,
        blur_score=None,
        estimated_text_height=None,
    )
    quality = quality_gate(quality_assessment)

    local_result = _run_local_ocr(metadata, request_id, providers.local_provider)

    paid_result: RecognitionResult | None = None
    paid_skipped_reason: str | None = None
    paid_config = get_config_from_env()
    if not has_api_key():
        paid_skipped_reason = f"{API_KEY_ENV} is not configured; using local OCR fallback"
    elif paid_config is None:
        paid_skipped_reason = f"{MODEL_ENV} is not configured; using local OCR fallback"
    else:
        paid_provider = providers.paid_provider or OpenAIPaidVisionProvider(config=paid_config)
        paid_input = paid_image_metadata or metadata
        paid_result = paid_provider.recognize(
            _request_from_metadata(paid_input, request_id, document_mode=document_mode)
        )

    crop_results: list[dict[str, Any]] = []
    crop_fallback_reason: str | None = None
    if (
        paid_result is not None
        and paid_config is not None
        and has_api_key()
        and _needs_field_crop_fallback(paid_result, local_result)
    ):
        if field_crops:
            paid_provider = providers.paid_provider or OpenAIPaidVisionProvider(config=paid_config)
            for crop_index, crop_meta in enumerate(field_crops, start=1):
                crop_request_id = f"{request_id}:crop-{crop_index}"
                crop_result = paid_provider.recognize(
                    _request_from_metadata(crop_meta, crop_request_id, document_mode=document_mode)
                )
                crop_results.append(crop_result.to_dict())
            crop_fallback_reason = "whole-image result was uncertain or conflicted; field crops were read"
        else:
            crop_fallback_reason = (
                "whole-image result was uncertain or conflicted; no field crops were available"
            )

    selected_result = paid_result if _usable_result(paid_result) else local_result
    selected_source = "paid_vision" if _usable_result(paid_result) else "local_ocr"
    warnings: list[str] = []
    if paid_skipped_reason:
        warnings.append(paid_skipped_reason)
    if paid_result is not None and not _usable_result(paid_result):
        message = paid_result.provider_error.message if paid_result.provider_error else paid_result.status.value
        warnings.append(f"paid vision unavailable; using local OCR fallback: {message}")
    if quality["blocked"]:
        warnings.append("image quality gate did not pass; human confirmation is still required")
    if crop_fallback_reason:
        warnings.append(crop_fallback_reason)

    return {
        "ok": True,
        "mode": "paid_vision_fallback",
        "prompt_strategy_version": PROMPT_STRATEGY_VERSION,
        "document_mode": document_mode,
        "quality": quality,
        "selected_source": selected_source,
        "paid_input_aided": paid_image_metadata is not None,
        "paid_skipped_reason": paid_skipped_reason,
        "local_result": local_result.to_dict(),
        "paid_result": paid_result.to_dict() if paid_result is not None else None,
        "crop_fallback_reason": crop_fallback_reason,
        "crop_results": crop_results,
        "pending_confirmation": build_pending_human_confirmation(
            selected_result,
            quality_passed=bool(quality["pass"]),
            selected_source=selected_source,
        ),
        "warnings": warnings,
        "safety": _safety_flags(),
    }


def build_pending_human_confirmation(
    result: RecognitionResult,
    *,
    quality_passed: bool = True,
    selected_source: str | None = None,
) -> dict[str, Any]:
    structured_by_id = {
        line.get("line_id"): line
        for line in result.preprocessing.get("openai_lines", [])
        if isinstance(line, dict)
    }
    lines = [
        {
            "line_id": line.line_id,
            "entry_id": structured_by_id.get(line.line_id, {}).get("entry_id", line.line_id),
            "raw_text": line.text,
            "layout_hint": structured_by_id.get(line.line_id, {}).get("layout_hint", "unknown"),
            "number_groups": structured_by_id.get(line.line_id, {}).get("number_groups", []),
            "multiplier_text": structured_by_id.get(line.line_id, {}).get("multiplier_text"),
            "alternatives": [alt.text for alt in line.alternatives],
            "uncertain": bool(
                line.warnings or structured_by_id.get(line.line_id, {}).get("uncertain")
            ),
            "uncertain_reason": structured_by_id.get(line.line_id, {}).get("uncertain_reason"),
            "needs_human_confirmation": bool(
                structured_by_id.get(line.line_id, {}).get("needs_human_confirmation", False)
            ),
            "validation_issues": structured_by_id.get(line.line_id, {}).get("validation_issues", []),
            "token_validations": structured_by_id.get(line.line_id, {}).get("token_validations", []),
            "semantics": structured_by_id.get(line.line_id, {}).get("semantics"),
            "human_confirmed": False,
        }
        for line in result.lines
    ]
    return {
        "status": PENDING_HUMAN_CONFIRMATION,
        "provider_status": result.status.value,
        "selected_source": selected_source,
        "quality_passed": quality_passed,
        "lines": lines,
        # can_enter_review = may proceed into the parser/Review pipeline.
        # OCR output never auto-enters Review — every line needs human
        # confirmation first. The human correction screen itself is always
        # available (can_human_correct=True) and edits never assisted-fill.
        "can_enter_review": False,
        "can_human_correct": True,
        "can_assisted_fill": False,
        "requires_human_confirmation": True,
        "safety": _safety_flags(),
    }


def can_send_ocr_to_review(pending_confirmation: dict[str, Any]) -> bool:
    if pending_confirmation.get("status") != PENDING_HUMAN_CONFIRMATION:
        return False
    if pending_confirmation.get("quality_passed") is not True:
        return False
    lines = pending_confirmation.get("lines")
    if not isinstance(lines, list) or not lines:
        return False
    return all(line.get("human_confirmed") is True for line in lines)


def _run_local_ocr(
    metadata: ImageMetadata,
    request_id: str,
    local_provider: ProviderLike | None,
) -> RecognitionResult:
    request = _request_from_metadata(metadata, f"{request_id}:local")
    if local_provider is not None:
        return local_provider.recognize(request)
    return recognize_with_metadata(metadata, request_id=f"{request_id}:local")


def _request_from_metadata(
    metadata: ImageMetadata,
    request_id: str,
    *,
    document_mode: str = "auto",
) -> RecognitionRequest:
    return RecognitionRequest(
        request_id=request_id,
        image_id=metadata.image_id,
        image_path=Path(metadata.storage_path),
        mime_type=metadata.mime_type,
        metadata={
            "sha256": metadata.sha256,
            "width": metadata.width,
            "height": metadata.height,
            "size_bytes": metadata.byte_size,
            "document_mode": document_mode,
        },
    )


def _usable_result(result: RecognitionResult | None) -> bool:
    return result is not None and result.status in {
        RecognitionStatus.COMPLETED,
        RecognitionStatus.PARTIAL,
    }


def _needs_field_crop_fallback(
    paid_result: RecognitionResult,
    local_result: RecognitionResult,
) -> bool:
    if paid_result.status not in {RecognitionStatus.COMPLETED, RecognitionStatus.PARTIAL}:
        return False
    if any(line.warnings for line in paid_result.lines):
        return True
    openai_lines = paid_result.preprocessing.get("openai_lines", [])
    if any(isinstance(line, dict) and line.get("uncertain") for line in openai_lines):
        return True
    if _result_text(local_result) and _result_text(paid_result) != _result_text(local_result):
        return True
    return False


def _result_text(result: RecognitionResult) -> str:
    return "\n".join(line.text.strip() for line in result.lines if line.text.strip())


def _safety_flags() -> dict[str, Any]:
    return {
        "real_site_operation": False,
        "auto_submit": False,
        "auto_confirm": False,
        "danger_buttons_clicked": [],
        "webfill_allowed": False,
    }
