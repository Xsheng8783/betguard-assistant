"""Gemini (Google) paid vision provider — raw transcription first.

Deliberately reuses the SAME closed-alphabet prompt, JSON schema, validator
and result builder as the OpenAI provider (providers/openai_paid.py) so that
both paid providers speak one language: raw transcription → deterministic
Closed Set V2 parser → human confirmation. Only the HTTP layer differs
(Gemini generateContent with inline base64 image).

Safety: never auto-fills, never auto-confirms; every result stays
PENDING_HUMAN_CONFIRMATION; paid calls require GEMINI_API_KEY.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from ..contracts import RecognitionRequest, RecognitionResult
from ..errors import ErrorCode, ProviderError
from .openai_paid import (
    _result_from_model_output,
    build_output_schema,
    build_prompt,
    validate_model_output,
)

PROVIDER_ID = "gemini-paid"


def _failed_result(request: RecognitionRequest, *, code: str, message: str) -> RecognitionResult:
    """Gemini-specific failed result (recognition_id carries gemini-paid)."""
    from ..contracts import ProviderMetadata, RecognitionStatus, SourceImage
    from .openai_paid import PROMPT_VERSION, SCHEMA_VERSION, _source_image_from_request
    return RecognitionResult(
        recognition_id=f"{request.request_id}:{PROVIDER_ID}:failed",
        request_id=request.request_id,
        status=RecognitionStatus.FAILED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            model_name=os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
            mode="paid_api",
            adapter_version=PROMPT_VERSION,
        ),
        source_image=_source_image_from_request(request),
        preprocessing={
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "human_confirmation_required": True,
            "auto_submit": False,
            "auto_confirm": False,
        },
        raw_text="",
        lines=[],
        warnings=[],
        latency_ms=0.0,
        provider_error=ProviderError(code=code, message=message, retryable=False),
    )
MODEL_ENV = "BETGUARD_GEMINI_MODEL"
API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-3.6-flash"
GENERATE_CONTENT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)

# Same cache namespace as OpenAI so identical images do not get billed twice.
CACHE_NAMESPACE = "gemini-paid"


class GeminiPaidVisionError(RuntimeError):
    pass


GeminiClient = Callable[[dict[str, Any], str, int], dict[str, Any]]


def has_api_key() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def cache_key(image_sha: str, model: str, *, document_mode: str = "auto") -> str:
    return f"{CACHE_NAMESPACE}:{image_sha}:{model}:{document_mode}"


def build_generate_payload(
    prompt: str,
    image_bytes: bytes,
    mime_type: str = "image/png",
) -> dict[str, Any]:
    """Gemini generateContent request body with inline image."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 16384,
            "responseMimeType": "application/json",
        },
    }


def extract_output_text(response: dict[str, Any]) -> str:
    """Extract the model text from a generateContent response.

    Strips a Markdown ```json code fence if present (Gemini often wraps
    the JSON payload in one).
    """
    try:
        candidates = response["candidates"]
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiPaidVisionError(f"unexpected Gemini response shape: {exc}") from exc
    text = text.strip()
    if text.startswith("```"):
        # strip ```json ... ```
        fence = text.splitlines()
        if fence and fence[0].startswith("```"):
            fence = fence[1:]
        if fence and fence[-1].strip() == "```":
            fence = fence[:-1]
        text = "\n".join(fence).strip()
    return text


def normalize_gemini_output(data: Any) -> dict[str, Any]:
    """Normalize Gemini's JSON into the shared bet-slip schema shape.

    Gemini (without a responseSchema) returns one of:
      - {"lines": [...]}            (matches our schema)
      - {"entries": [{"lines": [...]}, ...]}
      - [...]                        (flat array of line objects)
    Accept all three, fill defaults for optional fields, and return a
    {"schema_version": "1.1", "lines": [...]} payload for
    validate_model_output.
    """
    if not isinstance(data, dict) and not isinstance(data, list):
        raise ValueError("model output must be a JSON object or array")

    if isinstance(data, list):
        lines = data
    elif "lines" in data:
        lines = data["lines"]
    elif "entries" in data:
        lines = []
        for entry in data["entries"]:
            if isinstance(entry, dict) and isinstance(entry.get("lines"), list):
                lines.extend(entry["lines"])
            elif isinstance(entry, dict):
                lines.append(entry)
    else:
        raise ValueError("model output has no lines/entries")

    if not isinstance(lines, list):
        raise ValueError("lines must be an array")

    normalized: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError(f"line {i} is not an object")
        out = {
            "line_id": str(line.get("line_id") or f"L{i + 1:02d}"),
            "entry_id": str(line.get("entry_id") or f"E{i + 1:02d}"),
            "region": str(line.get("region") or "whole_page"),
            "layout_hint": str(line.get("layout_hint") or "unknown"),
            "number_groups": line.get("number_groups") or [],
            "multiplier_text": line.get("multiplier_text"),
            "raw_text": str(line.get("raw_text") or ""),
            "alternatives": line.get("alternatives") or [],
            "uncertain": bool(line.get("uncertain", False)),
            "uncertain_reason": line.get("uncertain_reason"),
        }
        normalized.append(out)

    return {"schema_version": "1.1", "lines": normalized}


def _post_generate(payload: dict[str, Any], api_key: str, timeout_seconds: int) -> dict[str, Any]:
    """Call generateContent. Returns the parsed JSON body or raises."""
    model = payload.pop("_model", DEFAULT_MODEL)
    url = GENERATE_CONTENT_URL.format(model=model, api_key=api_key)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GeminiPaidVisionError(f"Gemini HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GeminiPaidVisionError(f"Gemini network error: {exc}") from exc


def _error_code_for_gemini(err: GeminiPaidVisionError) -> str:
    msg = str(err)
    if "HTTP 401" in msg or "API key not valid" in msg or "API_KEY_INVALID" in msg:
        return ErrorCode.AUTHENTICATION_FAILED.value
    if "HTTP 429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
        return ErrorCode.RATE_LIMITED.value
    if "HTTP 404" in msg:
        return ErrorCode.MODEL_NOT_FOUND.value
    return ErrorCode.PROVIDER_ERROR.value


class GeminiPaidVisionProvider:
    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        client: GeminiClient | None = None,
        cache_dir: Path | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client or _post_generate
        self._cache_dir = cache_dir
        self._model = model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        if not has_api_key():
            return _failed_result(
                request,
                code=ErrorCode.AUTHENTICATION_FAILED.value,
                message=f"{API_KEY_ENV} is not configured",
            )
        api_key = os.environ.get(API_KEY_ENV, "").strip()

        try:
            image_bytes = Path(request.image_path).read_bytes()
        except OSError as exc:
            return _failed_result(
                request,
                code=ErrorCode.INVALID_IMAGE.value,
                message=f"unable to read image: {exc}",
            )

        image_sha = request.metadata.get("sha256") or hashlib.sha256(image_bytes).hexdigest()
        document_mode = request.metadata.get("document_mode", "auto")
        key = cache_key(str(image_sha), self._model, document_mode=document_mode)
        # cache filenames must be filesystem-safe on Windows (no ':')
        safe_key = key.replace(":", "_")

        # cache
        if self._cache_dir is not None:
            cache_path = self._cache_dir / f"{safe_key}.json"
            if cache_path.is_file():
                try:
                    cached_raw = json.loads(cache_path.read_text("utf-8"))
                    data = validate_model_output(normalize_gemini_output(cached_raw))
                    return _result_from_model_output(
                        request, model=self._model, model_output=data,
                        latency_ms=0, warnings=["gemini paid vision cache hit"],
                    )
                except (ValueError, OSError):
                    pass

        prompt = build_prompt(document_mode=document_mode)
        mime_type = _mime_for_path(request.image_path)
        payload = build_generate_payload(prompt, image_bytes, mime_type=mime_type)
        payload["_model"] = self._model

        import time
        started = time.monotonic()
        try:
            response = self._client(payload, api_key, timeout_seconds=60)
        except GeminiPaidVisionError as exc:
            return _failed_result(
                request,
                code=_error_code_for_gemini(exc),
                message=str(exc),
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        try:
            text = extract_output_text(response)
        except GeminiPaidVisionError as exc:
            return _failed_result(
                request,
                code=ErrorCode.PROVIDER_ERROR.value,
                message=str(exc),
            )

        try:
            data = validate_model_output(normalize_gemini_output(json.loads(text)))
        except (ValueError, json.JSONDecodeError) as exc:
            return _failed_result(
                request,
                code="GEMINI_RESPONSE_SCHEMA_INVALID",
                message=f"model output did not match bet slip schema: {exc}",
            )

        if self._cache_dir is not None:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                (self._cache_dir / f"{safe_key}.json").write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass

        return _result_from_model_output(
            request, model=self._model, model_output=data,
            latency_ms=latency_ms, warnings=[],
        )


def _mime_for_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/png"


# ─────────────────────────────────────────────────────────────────────────
# Assistive whole-page plaintext recognition (MVP v1)
#
# NEW mode, additive only: the JSON-schema recognize() above is untouched.
# Full image -> one call -> plain text (no JSON schema, no region crops,
# no single-line crops, no parser pre-fix). Status classification:
#   SUCCESS / BLOCKED_EMPTY_RESPONSE / BLOCKED_TRUNCATED_RESPONSE /
#   BLOCKED_MODEL_RESPONSE / RESOURCE_EXHAUSTED / AUTH_OR_CONFIG_ERROR
# ─────────────────────────────────────────────────────────────────────────

ASSISTIVE_PROMPT_VERSION = "assistive-whole-page-v1"
ASSISTIVE_DEFAULT_MODEL = "gemini-2.5-flash"
ASSISTIVE_MAX_OUTPUT_TOKENS = 8192
ASSISTIVE_TEMPERATURE = 0.1

ASSISTIVE_WHOLE_PAGE_PROMPT_V1 = (
    "請辨識這張手寫下注牌單，逐行輸出每一注的號碼與倍率。\n"
    "格式要求：號碼用空格分隔；倍率寫在該行末尾，用「二X1」「三X0.5」"
    "「四X1」等格式（二=二星/2個號碼、三=三星/3個號碼、四=四星/4個號碼）；"
    "同一注有多組號碼（斜線分隔）時用 / 分隔；"
    "看不清楚的數字用 ? 代替。\n"
    "只輸出牌單內容，不要解釋，不要加註。"
)

PLAINTEXT_STATUS_SUCCESS = "SUCCESS"
PLAINTEXT_STATUS_EMPTY = "BLOCKED_EMPTY_RESPONSE"
PLAINTEXT_STATUS_TRUNCATED = "BLOCKED_TRUNCATED_RESPONSE"
PLAINTEXT_STATUS_MODEL_ERROR = "BLOCKED_MODEL_RESPONSE"
PLAINTEXT_STATUS_RATE_LIMITED = "RESOURCE_EXHAUSTED"
PLAINTEXT_STATUS_AUTH_ERROR = "AUTH_OR_CONFIG_ERROR"


@dataclasses.dataclass
class PlaintextRecognitionResult:
    text: str
    provider: str = PROVIDER_ID
    requested_model: str = ""
    response_model: str = ""
    prompt_version: str = ASSISTIVE_PROMPT_VERSION
    latency_ms: float = 0.0
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    finish_reason: str = ""
    request_id: Optional[str] = None
    generation_config: dict[str, Any] = dataclasses.field(
        default_factory=dict)
    status: str = PLAINTEXT_STATUS_SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _build_plaintext_payload(image_bytes: bytes,
                             max_output_tokens: int) -> dict[str, Any]:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "contents": [{
            "parts": [
                {"text": ASSISTIVE_WHOLE_PAGE_PROMPT_V1},
                {"inline_data": {"mime_type": "image/png", "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": ASSISTIVE_TEMPERATURE,
            "maxOutputTokens": max_output_tokens,
        },
    }


def _classify_plaintext_status(text: str, finish_reason: str) -> str:
    if finish_reason == "MAX_TOKENS":
        return PLAINTEXT_STATUS_TRUNCATED
    if not text.strip():
        return PLAINTEXT_STATUS_EMPTY
    if finish_reason not in ("STOP", ""):
        return PLAINTEXT_STATUS_MODEL_ERROR
    return PLAINTEXT_STATUS_SUCCESS


def recognize_whole_page_plaintext(
    image_bytes: bytes,
    *,
    model: str = ASSISTIVE_DEFAULT_MODEL,
    max_output_tokens: int = ASSISTIVE_MAX_OUTPUT_TOKENS,
    api_key: str | None = None,
    client: Callable[[dict[str, Any], str, int], dict[str, Any]] | None = None,
) -> PlaintextRecognitionResult:
    """Full-page plain-text recognition (assistive flow). No JSON schema,
    no region crops, no parser pre-fix. Bounded retry only (2x on 429)."""
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        return PlaintextRecognitionResult(
            text="", requested_model=model, prompt_version=ASSISTIVE_PROMPT_VERSION,
            status=PLAINTEXT_STATUS_AUTH_ERROR,
            generation_config={"error": f"{API_KEY_ENV} is not configured"},
        )
    payload = _build_plaintext_payload(image_bytes, max_output_tokens)
    payload["_model"] = model
    poster = client or _post_generate

    import time
    started = time.monotonic()
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = poster(payload, key, timeout_seconds=120)
            break
        except GeminiPaidVisionError as exc:
            last_err = exc
            if "HTTP 429" in str(exc) and attempt < 2:
                time.sleep(15 * (attempt + 1))  # bounded backoff, no long waits
                continue
            raise
    else:  # pragma: no cover - defensive
        assert last_err is not None
        raise last_err
    latency_ms = round((time.monotonic() - started) * 1000, 1)

    err = _classify_http_error(last_err) if last_err else None
    if err is not None:
        return err

    try:
        text = extract_output_text(response)
        finish_reason = str(response.get("candidates", [{}])[0].get(
            "finishReason", ""))
        usage = response.get("usageMetadata", {}) or {}
        resp_model = response.get("modelVersion", "") or model
        request_id = (response.get("response", {}) or {}).get("requestId")
    except (KeyError, IndexError, TypeError, GeminiPaidVisionError) as exc:
        return PlaintextRecognitionResult(
            text="", requested_model=model, response_model=model,
            prompt_version=ASSISTIVE_PROMPT_VERSION, latency_ms=latency_ms,
            status=PLAINTEXT_STATUS_MODEL_ERROR,
            generation_config={"error": f"unexpected response shape: {exc}"},
        )

    status = _classify_plaintext_status(text, finish_reason)
    return PlaintextRecognitionResult(
        text=text,
        provider=PROVIDER_ID,
        requested_model=model,
        response_model=resp_model,
        prompt_version=ASSISTIVE_PROMPT_VERSION,
        latency_ms=latency_ms,
        prompt_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        total_tokens=usage.get("totalTokenCount"),
        finish_reason=finish_reason,
        request_id=request_id,
        generation_config={
            "temperature": ASSISTIVE_TEMPERATURE,
            "max_output_tokens": max_output_tokens,
        },
        status=status,
    )


def _classify_http_error(err: GeminiPaidVisionError) -> Optional[PlaintextRecognitionResult]:
    """Map a transport/auth/rate-limit error to a status result."""
    msg = str(err)
    if "HTTP 429" in msg:
        return PlaintextRecognitionResult(
            text="", status=PLAINTEXT_STATUS_RATE_LIMITED,
            generation_config={"error": msg})
    if "HTTP 401" in msg or "HTTP 403" in msg or "API key not valid" in msg:
        return PlaintextRecognitionResult(
            text="", status=PLAINTEXT_STATUS_AUTH_ERROR,
            generation_config={"error": msg})
    return None  # not classified — caller sees the exception
