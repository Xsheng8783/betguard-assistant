"""Vision API service layer — ties image_intake to providers.

All functions return {"ok": bool, ...} dicts suitable for JSON responses.
Does NOT import parser, validator, webfill, or Playwright.
"""

from __future__ import annotations

import traceback
from typing import Any

from betguard.vision.contracts import RecognitionRequest, RecognitionResult
from betguard.vision.errors import ErrorCode
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


# ── Providers ────────────────────────────────────────────────────────────────


def list_providers() -> dict[str, Any]:
    """Return available vision providers."""
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
                "id": QWEN_PROVIDER_ID,
                "mode": "paid_api",
                "real_ocr": True,
                "external_network": True,
                "requires_env": [QWEN_API_KEY_ENV],
                "configured": has_qwen_api_key(),
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

    if provider_id not in {"fake", QWEN_PROVIDER_ID}:
        return _error("PROVIDER_NOT_SUPPORTED", f"不支援的 provider: {provider_id}")

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

    if provider_id == QWEN_PROVIDER_ID:
        try:
            result = QwenDashScopeProvider().recognize(request)
            return _ok({"result": result.to_dict()})
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
