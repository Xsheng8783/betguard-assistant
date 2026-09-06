"""Vision API service layer — ties image_intake to providers.

All functions return {"ok": bool, ...} dicts suitable for JSON responses.
Parser use is limited to an explicit read-only image-text preflight.  This
module never authorizes candidates, queues, webfill, or Playwright actions.
"""

from __future__ import annotations

import re
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from betguard.vision.image_text_literals import (
    FORMATTER_VERSION as IMAGE_TEXT_FORMATTER_VERSION,
    normalize_parser_safe_literals,
)
from betguard.vision.openai_luna_transcription import (
    PROVIDER_ID as OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
    LunaTranscriptionError,
    get_config_from_env as get_luna_transcription_config,
    has_api_key as has_luna_api_key,
    transcribe_with_luna,
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


# ── Plain-text image transcription ─────────────────────────────────────────


_CANCELLED_PROSE_RE = re.compile(
    r"\(\s*(?:crossed[\s-]*out(?:\s+with[^)]*)?|cancelled(?:\s+bet)?|canceled(?:\s+bet)?)\s*\)",
    re.IGNORECASE,
)
_DECIMAL_LITERAL_RE = re.compile(r"0\s*[.．]\s*5")
_MULTIPLIER_05_AT_END_RE = re.compile(r"(?P<operator>[xX×]\s*)0?5(?P<suffix>\s*(?:支|元)?)$")
_SPECIAL_LITERAL_RE = re.compile(r"(?P<literal>[0-9?]+(?P<kind>半車|尾|車|各))")


def _protect_literal_decimal(raw_text: str, multiplier_text: str) -> tuple[str, bool]:
    """Compatibility helper: another model field cannot authorize value repair."""
    return raw_text, False


def _restore_literal_special(raw_text: str, special_text: str) -> tuple[str, bool]:
    """Compatibility helper: preserve missing/unknown digits for human editing."""
    return raw_text, False


def _gemma_source_records(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Read native records before item validation, when the archived JSON exists."""
    try:
        decoded = json.loads(evidence.get("raw_response_text") or "null")
        if isinstance(decoded, dict) and isinstance(decoded.get("items"), list):
            return [item if isinstance(item, dict) else {"raw_text": json.dumps(item, ensure_ascii=False), "uncertain": True}
                    for item in decoded["items"]]
    except (TypeError, ValueError):
        pass
    return [item for item in evidence.get("items", []) if isinstance(item, dict)]


def gemma_evidence_to_betguard_text_with_notices(
    evidence: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Apply the safe editable-text output contract to whole-image records."""
    if evidence.get("status") != "completed":
        return "", []
    items = _gemma_source_records(evidence)
    if not isinstance(items, list):
        return "", []
    records = []
    notices: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("raw_text") or "")
        original = raw_text
        notices_before_record = len(notices)
        source_id = str(item.get("evidence_id") or f"record-{index + 1}")
        rejected = any(r.get("item_index") == index + 1 for r in evidence.get("rejected_items", []))
        if rejected:
            # Unvalidated content remains visible but cannot silently join active text.
            raw_text = "? " + (raw_text or json.dumps(item, ensure_ascii=False))
            notices.append({"code": "PROVIDER_RECORD_UNSUPPORTED", "source_record_id": source_id,
                            "message": "辨識區塊格式不完整，原文已保留。", "before": item, "after": raw_text})
        has_prohibited_cancelled_prose = bool(_CANCELLED_PROSE_RE.search(raw_text))
        if item.get("cancelled") in {"yes", "unclear"} or has_prohibited_cancelled_prose:
            # Existing '?' is deliberately unresolved. Preserve *all* source text,
            # including neighboring lines; no new executable cancellation syntax.
            raw_text = "? " + raw_text
            notices.append({
                "code": "CANCELLATION_UNCONFIRMED", "source_record_id": source_id,
                "message": "此區塊可能有取消記號，原文已保留，請直接檢查文字。",
                "before": original, "after": raw_text, "human_confirmed": False,
            })
        for field in ("multiplier_text", "special_text"):
            literal = str(item.get(field) or "")
            # This is a disagreement flag, never evidence that one field is right.
            parts = [part.strip() for part in literal.split(";") if part.strip()]
            compact = re.sub(r"\s+", "", original).replace("X", "×").replace("x", "×")
            if literal.lower() not in {"", "none", "unclear"} and any(
                re.sub(r"\s+", "", part).replace("X", "×").replace("x", "×") not in compact
                for part in parts
            ):
                notices.append({
                    "code": "MODEL_FIELD_CONFLICT", "source_record_id": source_id,
                    "message": "辨識欄位不一致；保留原文，未自動改值。",
                    "field": field, "field_text": literal,
                    "before": original, "after": original, "human_confirmed": False,
                })
        if not item.get("uncertain") and len(notices) == notices_before_record:
            # Syntax-only spelling, never repair a conflicting model field or
            # make an unvalidated/possibly-cancelled record executable.
            raw_text, count = normalize_parser_safe_literals(raw_text)
            if count:
                notices.append({
                    "code": "CAR_LITERAL_REFORMATTED", "source_record_id": source_id,
                    "message": "車數已改排為明確單位格式；號碼與車數未變更，仍請核對原圖。",
                    "formatter_version": IMAGE_TEXT_FORMATTER_VERSION,
                    "before": original, "after": raw_text, "human_confirmed": False,
                })
        if raw_text:
            records.append(raw_text)
    return "\n\n".join(records), notices


def gemma_evidence_to_betguard_text(evidence: dict[str, Any]) -> str:
    """Return only editable text for compatibility with existing callers."""
    text, _notices = gemma_evidence_to_betguard_text_with_notices(evidence)
    return text


def preflight_image_text(text: str, *, game: str = "六合") -> dict[str, Any]:
    """Run the existing parser as a read-only preview for editable image text."""
    from betguard.vision.image_text_preflight import preview_image_text_with_existing_parser

    return preview_image_text_with_existing_parser(text, game=game)


def _capture_failed_transcription(image_id, meta, reader, game, raw_response, code):
    """Preserve received failed output without turning it into a prediction/truth."""
    if raw_response is None:
        return False
    try:
        from betguard.vision.image_text_acceptance import record_machine_transcription
        record_machine_transcription(
            image_id, source_image_sha256=meta.sha256, reader=reader, game=game,
            ai_original_text="", provider_raw_response=raw_response, adapter_text="",
            parser_result={"all_parseable": False, "prediction_validated": False, "error_code": code},
            transformations=[{"code": code, "action": "preserved_unvalidated_response", "human_confirmed": False}],
        )
        return True
    except Exception:
        return False


def transcribe_image_to_text(image_id: str, *, reader: str = "gemma", game: str = "六合") -> dict[str, Any]:
    """Use one explicitly selected whole-image reader as a typing aid.

    The returned text has no value authority.  It becomes actionable only
    after the user sends the editable text through the existing text parser.
    """
    if game not in {"539", "六合"}:
        return _safe_error("INVALID_GAME", "請明確選擇 539 或六合。")
    meta = get_metadata(image_id)
    if meta is None or meta.is_expired():
        return _error("IMAGE_NOT_FOUND", "圖片不存在或已過期")
    normalized_reader = str(reader or "gemma").strip().lower()
    if normalized_reader in {"luna", OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID.lower()}:
        return _transcribe_image_with_luna(image_id, meta, game=game)
    if normalized_reader not in {"gemma", GEMMA_SHADOW_PROVIDER_ID.lower()}:
        return _safe_error("IMAGE_TRANSCRIPTION_READER_INVALID", "不支援的圖片辨識方式。")
    request = RecognitionRequest(
        request_id=f"transcription-{image_id}",
        image_id=image_id,
        image_path=str(meta.storage_path),
        mime_type=meta.mime_type,
        metadata={
            "sha256": meta.sha256,
            "width": meta.width,
            "height": meta.height,
            "size_bytes": meta.byte_size,
        },
    )
    try:
        # The old feature flag controlled shadow routing.  This explicit
        # transcription endpoint is enabled by the user's click whenever the
        # existing Gemma credential is available.
        config = replace(get_gemma_shadow_config(), enabled=True)
        evidence = run_gemma_shadow(request, config=config)
    except Exception:
        return _safe_error(
            "IMAGE_TRANSCRIPTION_FAILED",
            "AI 圖片辨識目前無法使用，仍可直接輸入文字。",
        )
    text, transcription_notices = gemma_evidence_to_betguard_text_with_notices(evidence)
    if not text:
        status = str(evidence.get("status") or "failed")
        raw_captured = _capture_failed_transcription(image_id, meta, GEMMA_SHADOW_PROVIDER_ID, game,
                                                    evidence.get("provider_raw_response"), status)
        return {
            **_safe_error(
                "IMAGE_TRANSCRIPTION_EMPTY",
                "AI 沒有讀到可用文字，請直接輸入或重新辨識。",
            ),
            "reader_status": status,
            "failed_provider_response_captured": raw_captured,
            "transcription_notices": transcription_notices,
            "external_call_count": int(evidence.get("external_call_count") or 0),
            "retry_count": int(evidence.get("retry_count") or 0),
        }
    capture_available = False
    try:
        from betguard.vision.image_text_acceptance import record_machine_transcription

        record_machine_transcription(
            image_id,
            source_image_sha256=meta.sha256,
            ai_original_text="\n\n".join(str(item.get("raw_text") or "") for item in _gemma_source_records(evidence)),
            game=game,
            provider_raw_response=evidence.get("provider_raw_response", evidence.get("raw_response_text")),
            native_ocr_text="\n\n".join(str(item.get("raw_text") or "") for item in _gemma_source_records(evidence)),
            adapter_text=text,
            parser_result=preflight_image_text(text, game=game),
            transformations=transcription_notices,
            source_records=_gemma_source_records(evidence),
            reader=GEMMA_SHADOW_PROVIDER_ID,
            model_cache_identity={
                "model": config.model,
                "model_version": config.model_version,
                "adapter_version": config.adapter_version,
                "prompt_sha256": config.prompt_sha256,
                "request_schema_version": config.request_schema_version,
                "text_formatter_version": IMAGE_TEXT_FORMATTER_VERSION,
                "cache_hit": bool(evidence.get("cache_hit", False)),
            },
        )
        capture_available = True
    except Exception:
        # Acceptance capture is optional bookkeeping.  A local disk failure
        # must never block the normal editable transcription workflow.
        capture_available = False
    return _ok({
        "text": text,
        "transcription_notices": transcription_notices,
        "game": game,
        "parser_preflight": preflight_image_text(text, game=game),
        "reader": GEMMA_SHADOW_PROVIDER_ID,
        "machine_transcription_only": True,
        "user_editable": True,
        "value_authority": "existing_text_parser_after_explicit_user_action",
        "cache_hit": bool(evidence.get("cache_hit", False)),
        "external_call_count": int(evidence.get("external_call_count") or 0),
        "retry_count": int(evidence.get("retry_count") or 0),
        "verified_sample_capture_available": capture_available,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    })


def _transcribe_image_with_luna(image_id: str, meta: ImageMetadata, *, game: str = "六合") -> dict[str, Any]:
    """Run one cached Luna request without entering legacy vision review."""
    config = get_luna_transcription_config()
    try:
        prediction = transcribe_with_luna(
            image_path=meta.storage_path,
            mime_type=meta.mime_type,
            image_sha256=meta.sha256,
            config=config,
        )
    except LunaTranscriptionError as exc:
        result = _safe_error(exc.code, str(exc))
        result["failed_provider_response_captured"] = _capture_failed_transcription(
            image_id, meta, OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID, game, getattr(exc, "provider_raw_response", None), exc.code)
        result.update(
            {
                "reader": OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
                "external_call_count": 1 if has_luna_api_key() else 0,
                "retry_count": 0,
            }
        )
        return result
    except Exception:
        return {
            **_safe_error(
                "IMAGE_TRANSCRIPTION_FAILED",
                "Luna 圖片辨識目前無法使用，仍可改用 Gemma 或直接輸入文字。",
            ),
            "reader": OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
            "external_call_count": 1 if has_luna_api_key() else 0,
            "retry_count": 0,
        }

    native_text = str(prediction.get("native_ocr_text", prediction.get("text")) or "")
    text = str(prediction.get("text") or "")
    adapter_input = text
    text, car_literal_reformatted = normalize_parser_safe_literals(text)
    if not text:
        return {
            **_safe_error(
                "IMAGE_TRANSCRIPTION_EMPTY",
                "Luna 沒有讀到可用文字，請改用 Gemma 或直接輸入。",
            ),
            "reader": OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
            "external_call_count": int(prediction.get("external_call_count") or 0),
            "retry_count": 0,
        }

    notices: list[dict[str, str]] = []
    if car_literal_reformatted:
        notices.append(
            {
                "code": "CAR_LITERAL_REFORMATTED",
                "message": (
                    f"已將 {car_literal_reformatted} 個可見車玩法文字改排為 existing parser 格式；"
                    "號碼與車數未變更，仍請核對原圖。"
                ),
                "formatter_version": IMAGE_TEXT_FORMATTER_VERSION,
            }
        )
    cancelled_count = int(prediction.get("cancelled_count") or 0)
    if cancelled_count:
        notices.append(
            {
                "code": "CANCELLED_RECORD_OMITTED",
                "message": (
                    f"Luna 辨識到 {cancelled_count} 個可能已劃掉的區塊，未放入投注文字；"
                    "請對照圖片確認。"
                ),
            }
        )
    uncertain_count = int(prediction.get("uncertain_count") or 0)
    if uncertain_count:
        notices.append(
            {
                "code": "LUNA_UNCERTAIN_TEXT_PRESENT",
                "message": f"Luna 標記 {uncertain_count} 個看不清楚的位置，請直接檢查文字中的 ?。",
            }
        )

    capture_available = False
    try:
        from betguard.vision.image_text_acceptance import record_machine_transcription

        record_machine_transcription(
            image_id,
            source_image_sha256=meta.sha256,
            ai_original_text=native_text,
            game=game,
            provider_raw_response=prediction.get("provider_raw_response"),
            native_ocr_text=prediction.get("native_ocr_text"),
            adapter_text=text,
            parser_result=preflight_image_text(text, game=game),
            transformations=notices
                + ([{"code": "PROVIDER_RECORD_FORMATTING", "before": native_text, "after": adapter_input}]
                   if prediction.get("native_ocr_text") is not None and native_text != adapter_input else [])
                + ([{"code": "CAR_LITERAL_REFORMATTED", "before": adapter_input, "after": text}]
                   if adapter_input != text else []),
            source_records=prediction.get("source_records", []),
            reader=OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
            model_cache_identity={
                "model": prediction.get("model"),
                "adapter_version": prediction.get("adapter_version"),
                "prompt_sha256": prediction.get("prompt_sha256"),
                "response_schema_version": prediction.get("response_schema_version"),
                "image_detail": prediction.get("image_detail"),
                "cache_identity": prediction.get("cache_identity"),
                "text_formatter_version": IMAGE_TEXT_FORMATTER_VERSION,
                "cache_hit": bool(prediction.get("cache_hit", False)),
            },
        )
        capture_available = True
    except Exception:
        capture_available = False

    return _ok(
        {
            "text": text,
            "transcription_notices": notices,
            "game": game,
            "parser_preflight": preflight_image_text(text, game=game),
            "reader": OPENAI_LUNA_TRANSCRIPTION_PROVIDER_ID,
            "model": prediction.get("model"),
            "machine_transcription_only": True,
            "user_editable": True,
            "value_authority": "existing_text_parser_after_explicit_user_action",
            "cache_hit": bool(prediction.get("cache_hit", False)),
            "external_call_count": int(prediction.get("external_call_count") or 0),
            "retry_count": 0,
            "latency_ms": int(prediction.get("latency_ms") or 0),
            "usage": dict(prediction.get("usage") or {}),
            "verified_sample_capture_available": capture_available,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
        }
    )


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
