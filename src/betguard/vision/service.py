"""Vision API service layer — ties image_intake to providers.

All functions return {"ok": bool, ...} dicts suitable for JSON responses.
Does NOT import parser, validator, webfill, or Playwright.
"""

from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from betguard.vision.contracts import (
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
)
from betguard.vision.errors import ErrorCode
from betguard.vision.evidence_comparison import compare_ppocr_to_qwen
from betguard.vision.gemma_shadow import (
    ENABLED_ENV as GEMMA_SHADOW_ENABLED_ENV,
    PROVIDER_ID as GEMMA_SHADOW_PROVIDER_ID,
    compare_multi_model_evidence,
    get_gemma_shadow_config,
    has_api_key as has_gemma_api_key,
    run_gemma_shadow,
)
from betguard.vision.image_intake import (
    ImageMetadata,
    delete_image,
    get_metadata,
    read_image_data,
    save_metadata,
    validate_and_store,
)
from betguard.vision.paid_fallback import run_paid_vision_fallback_job
from betguard.vision.providers.fake import FakeProvider
from betguard.vision.providers.openai_paid import (
    DOCUMENT_MODES,
    MODEL_ENV,
    PROVIDER_ID as OPENAI_PAID_PROVIDER_ID,
    get_config_from_env,
    has_api_key,
)
from betguard.vision.providers.qwen_dashscope import (
    API_KEY_ENV as QWEN_API_KEY_ENV,
    PROVIDER_ID as QWEN_PROVIDER_ID,
    QwenDashScopeProvider,
    has_api_key as has_qwen_api_key,
)
from betguard.vision.ppocr_shadow import (
    ENABLED_ENV as PPOCR_SHADOW_ENABLED_ENV,
    PROVIDER_ID as PPOCR_SHADOW_PROVIDER_ID,
    get_ppocr_shadow_config,
    run_ppocr_shadow,
)
from betguard.vision.runtime_reader_router import (
    PROVIDER_ID as RUNTIME_READER_PROVIDER_ID,
    route_runtime_readers,
)
from betguard.vision.structure_reconstruction import reconstruct_structure


# ── Error helpers ────────────────────────────────────────────────────────────


def _ok(data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, **(data or {})}


def _error(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def _safe_error(code: str, message: str) -> dict[str, Any]:
    return {
        **_error(code, message, retryable=False),
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


# ── Providers ────────────────────────────────────────────────────────────────


def list_providers() -> dict[str, Any]:
    """Return available vision providers."""
    ppocr_shadow_config = get_ppocr_shadow_config()
    gemma_shadow_config = get_gemma_shadow_config()
    return _ok({
        "providers": [
            {
                "id": "fake",
                "mode": "fake",
                "real_ocr": False,
                "external_network": False,
                "configured": True,
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
                "fixtures": ["bet_slip", "no_confidence", "multi_line"],
            },
            {
                "id": RUNTIME_READER_PROVIDER_ID,
                "mode": "machine_reader_router",
                "real_ocr": True,
                "external_network": "conditional",
                "configured": True,
                "selectable": True,
                "primary_machine_source": GEMMA_SHADOW_PROVIDER_ID,
                "local_evidence_source": PPOCR_SHADOW_PROVIDER_ID,
                "conditional_fallback_source": QWEN_PROVIDER_ID,
                "gemma_configured": has_gemma_api_key(),
                "ppocr_configured": ppocr_shadow_config.configured,
                "qwen_configured": has_qwen_api_key(),
                "runtime_retries": 0,
                "codex_vision_runtime_calls": 0,
                "value_authority": "human_confirmed_answer",
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
            },
            {
                "id": QWEN_PROVIDER_ID,
                "mode": "paid_api",
                "real_ocr": True,
                "external_network": True,
                "requires_env": [QWEN_API_KEY_ENV],
                "configured": has_qwen_api_key(),
                "routing_role": "conditional_fallback_or_explicit_second_opinion",
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
            },
            {
                "id": PPOCR_SHADOW_PROVIDER_ID,
                "mode": "local_subprocess_shadow",
                "real_ocr": True,
                "external_network": False,
                "requires_env": [PPOCR_SHADOW_ENABLED_ENV],
                "configured": (
                    ppocr_shadow_config.enabled
                    and ppocr_shadow_config.configured
                ),
                "enabled": ppocr_shadow_config.enabled,
                "selectable": False,
                "evidence_only": True,
                "routing_provider": RUNTIME_READER_PROVIDER_ID,
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
            },
            {
                "id": GEMMA_SHADOW_PROVIDER_ID,
                "mode": "external_api_shadow",
                "real_ocr": True,
                "external_network": True,
                "requires_env": [GEMMA_SHADOW_ENABLED_ENV, "GEMINI_API_KEY"],
                "configured": gemma_shadow_config.configured,
                "enabled": gemma_shadow_config.enabled,
                "selectable": False,
                "evidence_only": True,
                "routing_provider": RUNTIME_READER_PROVIDER_ID,
                "primary_machine_source": True,
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
            },
            {
                "id": OPENAI_PAID_PROVIDER_ID,
                "mode": "paid_api",
                "real_ocr": True,
                "external_network": True,
                "requires_env": ["OPENAI_API_KEY", MODEL_ENV],
                "configured": has_api_key() and get_config_from_env() is not None,
                "human_confirmation_required": True,
                "auto_submit": False,
                "auto_confirm": False,
            },
        ],
    })


# ── Image upload ─────────────────────────────────────────────────────────────


def upload_image(
    data: bytes,
    content_type: str = "",
    original_filename: str = "",
) -> dict[str, Any]:
    """Validate and store an image. Returns ImageMetadata dict on success."""
    try:
        meta = validate_and_store(data, content_type, original_filename)
        save_metadata(meta)
        return _ok({"image": meta.to_dict()})
    except ValueError as e:
        code = str(e)
        # Map ValueError codes to stable error responses
        return _error(code, _error_message(code), retryable=False)


# ── Image preview ────────────────────────────────────────────────────────────


def get_image_preview(image_id: str) -> tuple[bytes | None, str | None, dict[str, Any] | None]:
    """Return (image_bytes, mime_type, error_dict)."""
    meta = get_metadata(image_id)
    if meta is None:
        return None, None, _error("IMAGE_NOT_FOUND", "圖片不存在")
    if meta.is_expired():
        return None, None, _error("IMAGE_EXPIRED", "圖片已過期")
    data = read_image_data(image_id)
    if data is None:
        return None, None, _error("IMAGE_NOT_FOUND", "圖片檔案不存在")
    return data, meta.mime_type, None


# ── Image delete ─────────────────────────────────────────────────────────────


def delete_image_api(image_id: str) -> dict[str, Any]:
    """Delete an image. Returns ok/error dict."""
    if not image_id or len(image_id) != 32:
        return _error("INVALID_IMAGE_ID", "不正確的圖片 ID")
    try:
        found = delete_image(image_id)
        if not found:
            return _error("IMAGE_NOT_FOUND", "圖片不存在")
        return _ok()
    except Exception:
        return _error("IMAGE_STORAGE_FAILED", "圖片刪除失敗", retryable=True)


# ── Job execution ────────────────────────────────────────────────────────────


def run_job(
    image_id: str,
    provider_id: str,
    fixture: str = "bet_slip",
    aided_image_id: str = "",
    document_mode: str = "auto",
    game: str | None = None,
    second_opinion_requested: bool = False,
) -> dict[str, Any]:
    """Run a recognition job. Returns RecognitionResult dict on success."""
    # Validate image_id exists and is not expired
    meta = get_metadata(image_id)
    if meta is None:
        return _error("IMAGE_NOT_FOUND", "圖片不存在或已過期")

    if provider_id in {OPENAI_PAID_PROVIDER_ID, "openai-paid", "paid-openai"}:
        normalized_document_mode = str(document_mode or "auto").strip().lower()
        if normalized_document_mode not in DOCUMENT_MODES:
            return _error("INVALID_DOCUMENT_MODE", "unsupported vision document mode")
        try:
            aided_meta = get_metadata(aided_image_id) if aided_image_id else None
        except (OSError, ValueError):
            aided_meta = None
        return run_paid_vision_fallback_job(
            meta,
            request_id=f"job-{image_id}:openai-paid",
            paid_image_metadata=aided_meta,
            document_mode=normalized_document_mode,
        )

    if provider_id not in {"fake", QWEN_PROVIDER_ID, RUNTIME_READER_PROVIDER_ID}:
        return _error("PROVIDER_NOT_SUPPORTED", f"不支援的 provider: {provider_id}")

    if provider_id == QWEN_PROVIDER_ID:
        if not isinstance(game, str) or game not in {"539", "六合"}:
            return _safe_error("INVALID_GAME", "Qwen 辨識必須明確指定 539 或六合彩")

    # Build request
    request = RecognitionRequest(
        request_id=f"job-{image_id}",
        image_id=image_id,
        image_path=str(meta.storage_path),  # internal, not exposed
        mime_type=meta.mime_type,
        metadata={
            "sha256": meta.sha256,
            "width": meta.width,
            "height": meta.height,
            "size_bytes": meta.byte_size,
        },
    )

    if provider_id == RUNTIME_READER_PROVIDER_ID:
        try:
            return _ok(
                {
                    "routing_result": route_runtime_readers(
                        request,
                        second_opinion_requested=bool(second_opinion_requested),
                    )
                }
            )
        except Exception:
            return _safe_error(
                "RUNTIME_READER_ROUTING_FAILED",
                "Machine reader routing failed; manual review remains available",
            )

    if provider_id == QWEN_PROVIDER_ID:
        try:
            vision_started = time.perf_counter()
            shadow_evidence: dict[str, Any] | None = None
            gemma_shadow_evidence: dict[str, Any] | None = None
            try:
                shadow_config = get_ppocr_shadow_config()
            except Exception as exc:
                shadow_config = None
                shadow_evidence = _unexpected_shadow_failure(exc)
            try:
                gemma_shadow_config = get_gemma_shadow_config()
            except Exception as exc:
                gemma_shadow_config = None
                gemma_shadow_evidence = _unexpected_gemma_shadow_failure(exc)
            ppocr_enabled = bool(shadow_config and shadow_config.enabled)
            gemma_enabled = bool(gemma_shadow_config and gemma_shadow_config.enabled)
            parallel_execution = ppocr_enabled or gemma_enabled
            if parallel_execution:
                with ThreadPoolExecutor(
                    max_workers=1 + int(ppocr_enabled) + int(gemma_enabled),
                    thread_name_prefix="vision-shadow",
                ) as executor:
                    qwen_future = executor.submit(_run_qwen_primary, request)
                    ppocr_future = (
                        executor.submit(run_ppocr_shadow, request, config=shadow_config)
                        if ppocr_enabled else None
                    )
                    gemma_future = (
                        executor.submit(
                            run_gemma_shadow,
                            request,
                            config=gemma_shadow_config,
                        )
                        if gemma_enabled else None
                    )
                    result, qwen_latency_ms = qwen_future.result()
                    if ppocr_future is not None:
                        try:
                            shadow_evidence = ppocr_future.result()
                        except Exception as exc:
                            shadow_evidence = _unexpected_shadow_failure(exc)
                    if gemma_future is not None:
                        try:
                            gemma_shadow_evidence = gemma_future.result()
                        except Exception as exc:
                            gemma_shadow_evidence = _unexpected_gemma_shadow_failure(exc)
            else:
                result, qwen_latency_ms = _run_qwen_primary(request)
            if shadow_evidence is not None and not isinstance(shadow_evidence, dict):
                shadow_evidence = _unexpected_shadow_failure(
                    TypeError("PP-OCR shadow result must be an object")
                )
            if gemma_shadow_evidence is not None and not isinstance(
                gemma_shadow_evidence, dict
            ):
                gemma_shadow_evidence = _unexpected_gemma_shadow_failure(
                    TypeError("Gemma shadow result must be an object")
                )
            payload: dict[str, Any] = {"result": result.to_dict()}
            comparison_latency_ms = 0.0
            if result.status == RecognitionStatus.COMPLETED:
                try:
                    payload["structure_evidence"] = reconstruct_structure(
                        result,
                        game=game,
                    )
                except Exception:
                    payload["structure_evidence"] = _failed_structure_evidence(
                        result,
                        game=game,
                    )
            if shadow_evidence is not None:
                if (
                    result.status == RecognitionStatus.COMPLETED
                    and shadow_evidence.get("status") == "completed"
                ):
                    try:
                        source_image = payload["result"].get("source_image", {})
                        comparison = compare_ppocr_to_qwen(
                            payload["result"],
                            shadow_evidence.get("regions", []),
                            image_width=int(source_image.get("width") or meta.width),
                            image_height=int(source_image.get("height") or meta.height),
                        )
                        comparison_latency_ms = float(comparison.get("latency_ms") or 0.0)
                        shadow_evidence = {**shadow_evidence, "comparison": comparison}
                    except Exception as exc:
                        shadow_evidence = {
                            **shadow_evidence,
                            "comparison": _unexpected_comparison_failure(exc),
                        }
                payload["shadow_evidence"] = shadow_evidence
                payload["vision_latency"] = {
                    "pp_latency_ms": float(shadow_evidence.get("latency_ms") or 0.0),
                    "qwen_latency_ms": qwen_latency_ms,
                    "comparison_latency_ms": comparison_latency_ms,
                    "total_vision_latency_ms": round(
                        (time.perf_counter() - vision_started) * 1000.0,
                        3,
                    ),
                    "parallel_execution": parallel_execution,
                }
            if gemma_shadow_evidence is not None:
                payload["gemma_shadow_evidence"] = gemma_shadow_evidence
                try:
                    multi_comparison_started = time.perf_counter()
                    payload["multi_model_comparison"] = compare_multi_model_evidence(
                        payload["result"],
                        shadow_evidence,
                        gemma_shadow_evidence,
                    )
                    comparison_latency_ms += round(
                        (time.perf_counter() - multi_comparison_started) * 1000.0,
                        3,
                    )
                except Exception as exc:
                    comparison_latency_ms += round(
                        (time.perf_counter() - multi_comparison_started) * 1000.0,
                        3,
                    )
                    payload["multi_model_comparison"] = {
                        "schema_version": "betguard.vision.multi-model-evidence-comparison.v1",
                        "status": "failed",
                        "classification": "MULTI_MODEL_COMPARISON_UNAVAILABLE",
                        "error": {
                            "code": "MULTI_MODEL_COMPARISON_FAILED",
                            "detail": type(exc).__name__,
                        },
                        "authority": QWEN_PROVIDER_ID,
                        "needs_review": True,
                        "evidence_only": True,
                        "human_confirmation_required": True,
                        "auto_apply": False,
                        "auto_confirm": False,
                        "auto_submit": False,
                    }
                latency = payload.setdefault("vision_latency", {
                    "pp_latency_ms": 0.0,
                    "qwen_latency_ms": qwen_latency_ms,
                    "comparison_latency_ms": 0.0,
                    "parallel_execution": parallel_execution,
                })
                latency["gemma_latency_ms"] = float(
                    gemma_shadow_evidence.get("latency_ms") or 0.0
                )
                latency["comparison_latency_ms"] = comparison_latency_ms
                latency["total_vision_latency_ms"] = round(
                    (time.perf_counter() - vision_started) * 1000.0,
                    3,
                )
            return _ok(payload)
        except Exception:
            return _error(
                "VISION_JOB_FAILED",
                "辨識工作發生錯誤",
                retryable=False,
            )

    # Run fake provider
    mode_map = {
        "bet_slip": FakeProvider.MODE_BET_SLIP,
        "no_confidence": FakeProvider.MODE_NO_CONFIDENCE,
        "multi_line": FakeProvider.MODE_MULTI_LINE,
    }
    mode = mode_map.get(fixture, FakeProvider.MODE_BET_SLIP)

    try:
        provider = FakeProvider(mode=mode)
        result: RecognitionResult = provider.recognize(request)
        return _ok({"result": result.to_dict()})
    except Exception:
        return _error(
            "VISION_JOB_FAILED",
            "辨識工作執行失敗",
            retryable=False,
        )


def _run_qwen_primary(
    request: RecognitionRequest,
) -> tuple[RecognitionResult, float]:
    started = time.perf_counter()
    result = QwenDashScopeProvider().recognize(request)
    return result, round((time.perf_counter() - started) * 1000.0, 3)


def _unexpected_shadow_failure(exc: Exception) -> dict[str, Any]:
    """Keep an unexpected optional worker error outside the primary flow."""
    return {
        "schema_version": "betguard.vision.ppocr-shadow-evidence.v1",
        "status": "failed",
        "provider": {"id": PPOCR_SHADOW_PROVIDER_ID},
        "error": {
            "code": "PPOCR_SHADOW_INTERNAL_FAILURE",
            "detail": type(exc).__name__,
        },
        "evidence_only": True,
        "authority": QWEN_PROVIDER_ID,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "latency_ms": 0.0,
    }


def _unexpected_gemma_shadow_failure(exc: Exception) -> dict[str, Any]:
    """Keep unexpected Gemma evidence errors outside the primary flow."""
    return {
        "schema_version": "betguard.vision.gemma-shadow-evidence.v2",
        "status": "failed",
        "provider": {"id": GEMMA_SHADOW_PROVIDER_ID},
        "model": "gemma-4-26b-a4b-it",
        "error": {
            "code": "GEMMA_SHADOW_INTERNAL_FAILURE",
            "detail": type(exc).__name__,
        },
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "authority": QWEN_PROVIDER_ID,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "latency_ms": 0.0,
    }


def _unexpected_comparison_failure(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": "betguard.vision.ocr-shadow-comparison.v1",
        "status": "failed",
        "error": {
            "code": "PPOCR_COMPARISON_FAILED",
            "detail": type(exc).__name__,
        },
        "authority": QWEN_PROVIDER_ID,
        "records": [],
        "needs_review": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _failed_structure_evidence(
    result: RecognitionResult,
    *,
    game: str,
) -> list[dict[str, Any]]:
    """Preserve a successful provider result when the evidence layer fails."""
    line_ids = sorted(line.line_id for line in result.lines)
    groups: dict[str, list[str]] = {}
    for line_id in line_ids:
        structure_id = line_id.split("-L", 1)[0] if "-L" in line_id else line_id
        groups.setdefault(structure_id, []).append(line_id)

    evidence: list[dict[str, Any]] = []
    for structure_id in sorted(groups):
        member_line_ids = groups[structure_id]
        primary_line_id = member_line_ids[0]
        for line_id in member_line_ids:
            evidence.append({
                "line_id": line_id,
                "structure_id": structure_id,
                "primary_line_id": primary_line_id,
                "member_line_ids": member_line_ids,
                "line_role": "primary" if line_id == primary_line_id else "continuation",
                "source": "deterministic_geometry_v1",
                "game": game,
                "status": "incomplete",
                "warnings": ["structure_reconstruction_failed"],
                "evidence": {
                    "tokens": [],
                    "bbox_debug": {},
                    "rules_used": [],
                },
                "human_confirmation_required": True,
                "auto_apply": False,
                "auto_confirm": False,
                "auto_submit": False,
            })
    return evidence


# ── Error message mapping ────────────────────────────────────────────────────


def _error_message(code: str) -> str:
    messages = {
        "IMAGE_EMPTY": "圖片為空",
        "IMAGE_TOO_LARGE": "圖片過大（上限 10 MiB）",
        "IMAGE_TYPE_UNSUPPORTED": "不支援的圖片格式（僅接受 PNG、JPEG、WebP）",
        "IMAGE_MAGIC_MISMATCH": "圖片格式與 Content-Type 不符",
        "IMAGE_DIMENSIONS_INVALID": "圖片尺寸無效或超過上限（12000×12000 px）",
        "IMAGE_PIXEL_LIMIT_EXCEEDED": "圖片總像素超過上限（4000 萬像素）",
        "IMAGE_STORAGE_FAILED": "圖片儲存失敗",
        "IMAGE_NOT_FOUND": "圖片不存在",
        "IMAGE_EXPIRED": "圖片已過期",
        "INVALID_IMAGE_ID": "不正確的圖片 ID",
        "PROVIDER_NOT_SUPPORTED": "不支援的辨識 provider",
        "VISION_JOB_FAILED": "辨識工作執行失敗",
        "INVALID_JSON": "JSON 格式無效",
    }
    return messages.get(code, code)
