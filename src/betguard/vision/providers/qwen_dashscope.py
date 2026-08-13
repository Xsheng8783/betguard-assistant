"""First-class DashScope Qwen vision provider and shared HTTP client.

The client intentionally preserves the request shape used by the original
sample-034 debug tooling.  Provider conversion is structural only: this module
does not parse betting semantics, merge candidates, or confirm workflow data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from io import BytesIO
from json.decoder import JSONDecoder
from pathlib import Path
from typing import Any, Callable, Mapping

from betguard.vision.contracts import (
    BoundingBox,
    Confidence,
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
    Token,
)
from betguard.vision.errors import ErrorCode, ProviderError
from betguard.vision.qwen_cache import QwenCacheIdentity, QwenResponseCache
from betguard.vision.qwen_diagnostics import (
    NESTED_JSON_SALVAGE_MISLEADING_ERROR,
    QWEN_OUTPUT_TRUNCATED,
    QwenFailureDiagnosticStore,
    inspect_json_extraction,
)
from betguard.vision.qwen_prompts import (
    PROMPT,
    PROMPT_VERSION,
    TASK_TYPE_COLUMN_COMBO,
    TASK_TYPE_FOCUSED_CROP,
    TASK_TYPE_FULL_PAGE,
    TASK_TYPE_PLAY_MARK,
    prompt_sha256,
)


PROVIDER_ID = "qwen-dashscope"
API_KEY_ENV = "DASHSCOPE_API_KEY"
MODEL_ENV = "BETGUARD_QWEN_MODEL"
URL_ENV = "BETGUARD_QWEN_URL"
TIMEOUT_ENV = "BETGUARD_QWEN_TIMEOUT_SECONDS"
RETRY_ENV = "BETGUARD_QWEN_MAX_RETRIES"

DEFAULT_MODEL = "qwen3-vl-plus"
DEFAULT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 240.0
DEFAULT_MAX_RETRIES = 2
REQUEST_SCHEMA_VERSION = "dashscope-compatible-chat-completions-v1"
ADAPTER_VERSION = "betguard-qwen-dashscope-v1"

_SAFETY_METADATA: dict[str, bool] = {
    "human_confirmation_required": True,
    "auto_confirm": False,
    "auto_submit": False,
}


@dataclass(frozen=True)
class QwenDashScopeConfig:
    """Typed transport configuration; secrets are deliberately excluded."""

    model: str = DEFAULT_MODEL
    url: str = DEFAULT_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Qwen model must not be blank")
        if not self.url.strip():
            raise ValueError("Qwen URL must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("Qwen timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("Qwen max_retries must not be negative")

    @classmethod
    def from_env(cls) -> QwenDashScopeConfig:
        return cls(
            model=os.environ.get(MODEL_ENV, DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            url=os.environ.get(URL_ENV, DEFAULT_URL).strip() or DEFAULT_URL,
            timeout_seconds=_positive_float_from_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS),
            max_retries=_nonnegative_int_from_env(RETRY_ENV, DEFAULT_MAX_RETRIES),
        )


class QwenClientError(RuntimeError):
    """Base error for a DashScope request."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retryable = retryable


class QwenAPIKeyMissing(QwenClientError):
    pass


class QwenSchemaError(QwenClientError):
    pass


class QwenOutputTruncatedError(QwenSchemaError):
    """A full-page response ended before its top-level JSON was complete."""

    classification = QWEN_OUTPUT_TRUNCATED

    def __init__(
        self,
        message: str = QWEN_OUTPUT_TRUNCATED,
        *,
        secondary_classifications: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message, retryable=False)
        self.secondary_classifications = secondary_classifications


class QwenTimeoutError(QwenClientError):
    pass


Transport = Callable[[dict[str, Any], str, QwenDashScopeConfig], dict[str, Any]]


class _QwenCallMetadata(dict[str, Any]):
    """Public metadata keys plus non-serialized failure diagnostic context."""

    diagnostic_identity: dict[str, Any]
    response_metadata: dict[str, Any]


class _HTTPResponseEnvelope(dict[str, Any]):
    """Parsed response body carrying HTTP status outside its JSON keys."""

    http_status: int | None

    def __init__(self, value: dict[str, Any], *, http_status: int | None) -> None:
        super().__init__(value)
        self.http_status = http_status


class QwenDashScopeClient:
    """Shared DashScope client with task-aware validated-response caching."""

    def __init__(
        self,
        *,
        config: QwenDashScopeConfig | None = None,
        cache: QwenResponseCache | None = None,
        diagnostic_store: QwenFailureDiagnosticStore | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or QwenDashScopeConfig.from_env()
        self.cache = cache or QwenResponseCache()
        self.diagnostic_store = diagnostic_store or QwenFailureDiagnosticStore()
        self._transport = transport or _post_chat_completions
        self._sleep = sleep

    def chat(
        self,
        b64: str,
        mime: str,
        prompt: str,
        *,
        max_tokens: int,
        image_sha256: str | None = None,
        crop_box: list[int] | tuple[int, int, int, int] | None = None,
        scale: int | None = None,
        image_variant: str | None = None,
        prompt_version: str = PROMPT_VERSION,
        task_type: str = TASK_TYPE_FULL_PAGE,
        effective_crop_sha256: str | None = None,
        request_id: str | None = None,
        retries: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Send one unchanged compatible-mode request or return a cache hit."""
        # Read the key for every request.  It is never retained on the client,
        # placed in metadata, or sent to the persistent cache.
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            raise QwenAPIKeyMissing(
                "DASHSCOPE_API_KEY 未設定；拒絕以空 Bearer token 呼叫。"
            )

        rid = request_id or uuid.uuid4().hex[:12]
        effective_sha = effective_crop_sha256 or _sha256_base64(b64)
        source_sha = image_sha256 or effective_sha
        normalized_box = _normalize_crop_box(crop_box)
        identity = QwenCacheIdentity(
            image_sha256=source_sha,
            crop_box=normalized_box,
            scale=scale,
            variant=image_variant or "none",
            model=self.config.model,
            prompt_version=prompt_version,
            task_type=task_type,
            prompt_sha256=prompt_sha256(prompt),
            request_schema_version=REQUEST_SCHEMA_VERSION,
            effective_crop_sha256=effective_sha,
            mime_type=mime,
            max_tokens=max_tokens,
            endpoint_url=self.config.url,
        )
        retry_limit = self.config.max_retries if retries is None else max(0, int(retries))
        meta = _QwenCallMetadata({
            "request_id": rid,
            "model": self.config.model,
            "prompt_version": prompt_version,
            "prompt_sha256": identity.prompt_sha256,
            "task_type": task_type,
            "request_schema_version": REQUEST_SCHEMA_VERSION,
            "image_sha256": source_sha,
            "effective_crop_sha256": effective_sha,
            "crop_box": list(normalized_box) if normalized_box is not None else None,
            "scale": scale,
            "image_variant": image_variant,
            "retries": 0,
            "latency_s": None,
            "cache_hit": False,
            "response_schema_valid": False,
        })
        meta.diagnostic_identity = identity.to_dict()
        meta.response_metadata = {}

        cached = self.cache.get(identity)
        if cached is not None:
            try:
                validate_task_response(cached, task_type)
            except QwenSchemaError:
                cached = None
            else:
                meta["cache_hit"] = True
                meta["response_schema_valid"] = True
                meta["latency_s"] = 0.0
                return cached, meta

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for attempt in range(retry_limit + 1):
            started = time.time()
            try:
                data = self._transport(payload, api_key, self.config)
                meta["latency_s"] = round(time.time() - started, 2)
                meta["retries"] = attempt
                meta.response_metadata = _response_metadata(
                    data,
                    latency_s=meta["latency_s"],
                    attempt=attempt,
                )
                content = _response_content_candidate(data)
                try:
                    content = _response_content(data, request_id=rid)
                    validated = validate_task_response(
                        content,
                        task_type,
                        response_metadata=meta.response_metadata,
                    )
                except QwenSchemaError as exc:
                    if not isinstance(exc, QwenOutputTruncatedError):
                        truncation_error = _full_page_truncation_error(
                            content or "",
                            task_type=task_type,
                            response_metadata=meta.response_metadata,
                        )
                        if truncation_error is not None:
                            exc = truncation_error
                    diagnostic = self._preserve_schema_failure(
                        meta=meta,
                        content=content,
                        error=exc,
                    )
                    if diagnostic is not None:
                        meta["failure_diagnostic"] = diagnostic
                    # Debug callers historically receive model content even
                    # when its inner JSON is invalid.  Do not cache it; the
                    # first-class provider validates again and fails closed.
                    if content is not None and content.strip():
                        return content, meta
                    raise exc
                meta["response_schema_valid"] = True
                try:
                    self.cache.put_validated(
                        identity,
                        content=content,
                        validated_response=validated,
                    )
                except OSError as exc:
                    meta["cache_write_error"] = type(exc).__name__
                return content, meta
            except QwenTimeoutError:
                raise
            except QwenClientError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            if attempt < retry_limit:
                self._sleep(1.0 * (attempt + 1))

        raise QwenClientError(
            f"Qwen 重試耗盡（request_id={rid}）：{last_error}",
            http_status=getattr(last_error, "http_status", None),
            retryable=True,
        )

    def _preserve_schema_failure(
        self,
        *,
        meta: _QwenCallMetadata,
        content: str | None,
        error: QwenSchemaError,
    ) -> dict[str, Any] | None:
        try:
            return self.diagnostic_store.preserve(
                request_metadata=meta,
                cache_identity=meta.diagnostic_identity,
                response_content=content,
                response_metadata=meta.response_metadata,
                schema_error=error,
            )
        except Exception as exc:
            meta["failure_diagnostic_write_error"] = type(exc).__name__
            return None


class QwenDashScopeProvider:
    """Image request -> validated response -> RecognitionResult only."""

    provider_id = PROVIDER_ID

    def __init__(self, *, client: QwenDashScopeClient | None = None) -> None:
        self._client = client or get_default_client()

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        started = time.time()
        source = _source_image_from_request(request)
        content: str | None = None
        meta: dict[str, Any] = {}
        try:
            image, png_bytes = load_normalized_image(Path(request.image_path))
            sent_sha = hashlib.sha256(png_bytes).hexdigest()
            source = SourceImage(
                image_id=request.image_id,
                sha256=sent_sha,
                mime_type="image/png",
                original_filename=Path(request.image_path).name,
                width=image.width,
                height=image.height,
                byte_size=len(png_bytes),
                retention="transient",
                cloud_uploaded=False,
            )
            content, meta = self._client.chat(
                base64.b64encode(png_bytes).decode(),
                "image/png",
                PROMPT,
                max_tokens=8000,
                image_sha256=sent_sha,
                scale=None,
                image_variant=request.preprocessing_variant or "none",
                prompt_version=PROMPT_VERSION,
                task_type=TASK_TYPE_FULL_PAGE,
                effective_crop_sha256=sent_sha,
                request_id=request.request_id,
            )
            source.cloud_uploaded = not bool(meta.get("cache_hit"))
            parsed = validate_task_response(
                content,
                TASK_TYPE_FULL_PAGE,
                response_metadata=(
                    meta.response_metadata
                    if isinstance(meta, _QwenCallMetadata)
                    else None
                ),
            )
            try:
                lines = _lines_from_full_page_response(parsed)
            except ValueError as exc:
                raise QwenSchemaError(f"Qwen response geometry is invalid: {exc}") from exc
            if not lines:
                raise QwenSchemaError("Qwen response contains no recognized rows")
        except Exception as exc:
            diagnostic = meta.get("failure_diagnostic")
            if (
                diagnostic is None
                and isinstance(exc, QwenSchemaError)
                and isinstance(content, str)
                and isinstance(meta, _QwenCallMetadata)
            ):
                diagnostic = self._client._preserve_schema_failure(
                    meta=meta,
                    content=content,
                    error=exc,
                )
            return _failed_result(
                request,
                source=source,
                error=exc,
                model=self._client.config.model,
                latency_ms=(time.time() - started) * 1000,
                failure_diagnostic=(
                    diagnostic if isinstance(diagnostic, dict) else None
                ),
            )

        preprocessing: dict[str, Any] = {
            **_SAFETY_METADATA,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_sha256(PROMPT),
            "task_type": TASK_TYPE_FULL_PAGE,
            "request_schema_version": REQUEST_SCHEMA_VERSION,
            "response_schema_valid": True,
            "qwen_response": parsed,
            "qwen_request": meta,
        }
        return RecognitionResult(
            recognition_id=f"{request.request_id}:qwen-dashscope",
            request_id=request.request_id,
            status=RecognitionStatus.COMPLETED,
            provider=ProviderMetadata(
                id=PROVIDER_ID,
                mode="paid_api",
                model_name=self._client.config.model,
                adapter_version=ADAPTER_VERSION,
            ),
            source_image=source,
            preprocessing=preprocessing,
            raw_text="\n".join(line.text for line in lines),
            lines=lines,
            warnings=["human confirmation required"],
            latency_ms=(time.time() - started) * 1000,
        )


def validate_task_response(
    content: str,
    task_type: str,
    *,
    response_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate only the JSON structure required by a Qwen task."""
    if task_type == TASK_TYPE_FULL_PAGE:
        obj = _strict_full_page_root(content, response_metadata=response_metadata)
    else:
        obj = extract_json(content)
    if obj is None:
        raise QwenSchemaError("Qwen content is not a JSON object")
    if task_type == TASK_TYPE_FULL_PAGE:
        _validate_full_page(obj)
    elif task_type == TASK_TYPE_FOCUSED_CROP:
        _validate_focused_crop(obj)
    elif task_type == TASK_TYPE_PLAY_MARK:
        _validate_play_mark(obj)
    elif task_type == TASK_TYPE_COLUMN_COMBO:
        _validate_column_combo(obj)
    else:
        raise QwenSchemaError(f"unsupported Qwen task_type: {task_type}")
    return obj


def _strict_full_page_root(
    content: str,
    *,
    response_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Require a complete top-level object; nested salvage is never a root."""
    truncation_error = _full_page_truncation_error(
        content,
        task_type=TASK_TYPE_FULL_PAGE,
        response_metadata=response_metadata,
    )
    if truncation_error is not None:
        raise truncation_error

    try:
        obj = json.loads(content)
    except json.JSONDecodeError as exc:
        raise QwenSchemaError(
            "full-page response must be a complete top-level JSON object"
        ) from exc
    if not isinstance(obj, dict):
        raise QwenSchemaError("full-page response root must be a JSON object")
    return obj


def _full_page_truncation_error(
    content: str,
    *,
    task_type: str,
    response_metadata: Mapping[str, Any] | None,
) -> QwenOutputTruncatedError | None:
    if task_type != TASK_TYPE_FULL_PAGE:
        return None
    observation = inspect_json_extraction(content)
    finish_reason = (
        response_metadata.get("finish_reason")
        if isinstance(response_metadata, Mapping)
        else None
    )
    incomplete_root = (
        not observation["strict_json_parse_ok"]
        and (
            observation["braces_balance"] > 0
            or observation["brackets_balance"] > 0
        )
    )
    if finish_reason == "length" or incomplete_root:
        secondary = (
            (NESTED_JSON_SALVAGE_MISLEADING_ERROR,)
            if observation["salvage_success"]
            else ()
        )
        return QwenOutputTruncatedError(
            secondary_classifications=secondary,
        )
    return None


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract one JSON object without a greedy regular expression."""
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def load_normalized_image(image_path: Path):
    """Return EXIF-transposed RGB image and the exact PNG bytes sent."""
    from PIL import Image, ImageOps

    with Image.open(image_path) as image:
        normalized = ImageOps.exif_transpose(image.copy()).convert("RGB")
    buffer = BytesIO()
    normalized.save(buffer, format="PNG")
    return normalized, buffer.getvalue()


_default_client: QwenDashScopeClient | None = None


def get_default_client() -> QwenDashScopeClient:
    """Return the one lazy process-wide Qwen client used by all call paths."""
    global _default_client
    if _default_client is None:
        _default_client = QwenDashScopeClient()
    return _default_client


def has_api_key() -> bool:
    """Report configuration without returning or retaining the secret."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def _post_chat_completions(
    payload: dict[str, Any],
    api_key: str,
    config: QwenDashScopeConfig,
) -> dict[str, Any]:
    """The sole DashScope urllib implementation in the repository."""
    request = urllib.request.Request(
        config.url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    http_status: int | None = None
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            http_status = getattr(response, "status", None)
            if http_status is None:
                try:
                    http_status = int(response.getcode())
                except (AttributeError, TypeError, ValueError):
                    http_status = None
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code == 429:
            raise QwenClientError(
                f"Qwen 429：{body}", http_status=429, retryable=True
            ) from exc
        if 400 <= exc.code < 500:
            raise QwenClientError(
                f"Qwen 4xx（code={exc.code}）不重試：{body}",
                http_status=exc.code,
                retryable=False,
            ) from exc
        raise QwenClientError(
            f"Qwen 5xx（code={exc.code}）：{body}",
            http_status=exc.code,
            retryable=True,
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise QwenTimeoutError("Qwen timeout", retryable=False) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise QwenTimeoutError("Qwen timeout", retryable=False) from exc
        raise QwenClientError(f"Qwen network error: {exc}", retryable=True) from exc

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise QwenSchemaError("Qwen HTTP response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise QwenSchemaError("Qwen HTTP response is not a JSON object")
    return _HTTPResponseEnvelope(data, http_status=http_status)


def _response_metadata(
    data: dict[str, Any],
    *,
    latency_s: int | float | None,
    attempt: int,
) -> dict[str, Any]:
    choice: dict[str, Any] = {}
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
    usage = data.get("usage")
    return {
        "latency_s": latency_s,
        "attempt_count": attempt + 1,
        "retry_count": attempt,
        "http_status": getattr(data, "http_status", None) or 200,
        "finish_reason": choice.get("finish_reason"),
        "usage": usage if isinstance(usage, dict) else None,
    }


def _response_content(data: dict[str, Any], *, request_id: str) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenSchemaError(
            f"Qwen 回應缺少 choices/message/content（request_id={request_id}）"
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise QwenSchemaError(
            f"Qwen 回傳空 content（request_id={request_id}）；raw={str(data)[:300]}"
        )
    return content


def _response_content_candidate(data: dict[str, Any]) -> str | None:
    """Return string content, including empty content, for failure observation."""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _validate_full_page(obj: dict[str, Any]) -> None:
    sections = obj.get("sections")
    if not isinstance(sections, list) or not sections:
        raise QwenSchemaError("full-page response.sections must be a non-empty list")
    for section in sections:
        if (
            not isinstance(section, dict)
            or not isinstance(section.get("rows"), list)
            or not section["rows"]
        ):
            raise QwenSchemaError("full-page section.rows must be a non-empty list")
        if "shared_multiplier" not in section:
            raise QwenSchemaError("full-page section.shared_multiplier is required")
        for row in section["rows"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("tokens"), list)
                or not row["tokens"]
            ):
                raise QwenSchemaError("full-page row.tokens must be a non-empty list")
            numbers = row.get("numbers")
            if (
                not isinstance(numbers, list)
                or any(
                    not isinstance(group, list)
                    or any(not isinstance(value, str) for value in group)
                    for group in numbers
                )
            ):
                raise QwenSchemaError("full-page row.numbers must be a list of string lists")
            if "multiplier" not in row or not isinstance(row.get("layout_hint"), str):
                raise QwenSchemaError("full-page row multiplier/layout_hint schema is invalid")
            for token in row["tokens"]:
                if not isinstance(token, dict) or not isinstance(token.get("text"), str):
                    raise QwenSchemaError("full-page token.text must be a string")
                bbox = token.get("bbox")
                if (
                    not isinstance(bbox, list)
                    or len(bbox) != 4
                    or any(not _is_number(value) for value in bbox)
                ):
                    raise QwenSchemaError("full-page token.bbox must contain four numbers")


def _validate_focused_crop(obj: dict[str, Any]) -> None:
    for key in ("full_text", "category_digits", "multiplier"):
        if key not in obj or not isinstance(obj[key], (str, type(None))):
            raise QwenSchemaError(f"focused-crop response.{key} must be string or null")


def _validate_play_mark(obj: dict[str, Any]) -> None:
    if (
        not isinstance(obj.get("raw_text"), str)
        or not _is_string_list(obj.get("main_numbers"))
    ):
        raise QwenSchemaError("play-mark response raw_text/main_numbers schema is invalid")
    play = obj.get("play_mark")
    if not isinstance(play, dict):
        raise QwenSchemaError("play-mark response.play_mark must be an object")
    for key in ("upper_digits", "lower_digits", "other_visible_digits", "uncertain_candidates"):
        if not _is_string_list(play.get(key)):
            raise QwenSchemaError(f"play-mark response.play_mark.{key} must be a string list")
    if not isinstance(play.get("uncertain"), bool):
        raise QwenSchemaError("play-mark response.play_mark.uncertain must be boolean")
    if not isinstance(play.get("layout"), str) or not isinstance(play.get("raw_play_text"), str):
        raise QwenSchemaError("play-mark layout/raw_play_text schema is invalid")
    for key in ("multiplier", "uncertain_reason"):
        if not isinstance(play.get(key), (str, type(None))):
            raise QwenSchemaError(f"play-mark response.play_mark.{key} must be string or null")
    if not isinstance(obj.get("overall_uncertain"), bool):
        raise QwenSchemaError("play-mark response.overall_uncertain must be boolean")
    if not isinstance(obj.get("overall_uncertain_reason"), (str, type(None))):
        raise QwenSchemaError("play-mark overall_uncertain_reason must be string or null")


def _validate_column_combo(obj: dict[str, Any]) -> None:
    columns = obj.get("columns")
    if (
        not isinstance(columns, list)
        or any(
            not isinstance(column, list)
            or any(not isinstance(value, str) for value in column)
            for column in columns
        )
    ):
        raise QwenSchemaError("column-combo response.columns must be a list of string lists")
    if not isinstance(obj.get("uncertain"), bool):
        raise QwenSchemaError("column-combo response.uncertain must be boolean")
    if "uncertain_columns" in obj and not isinstance(obj["uncertain_columns"], list):
        raise QwenSchemaError("column-combo response.uncertain_columns must be a list")
    for key in ("collision", "multiplier", "uncertain_reason"):
        if not isinstance(obj.get(key), (str, type(None))):
            raise QwenSchemaError(f"column-combo response.{key} must be string or null")


def _lines_from_full_page_response(obj: dict[str, Any]) -> list[Line]:
    lines: list[Line] = []
    for section_index, section in enumerate(obj["sections"], start=1):
        for row_index, row in enumerate(section["rows"], start=1):
            token_models: list[Token] = []
            pieces: list[str] = []
            cursor = 0
            for token_index, token in enumerate(row["tokens"], start=1):
                token_text = token["text"]
                if pieces:
                    cursor += 1
                start = cursor
                cursor += len(token_text)
                pieces.append(token_text)
                token_models.append(
                    Token(
                        token_id=f"S{section_index:02d}-L{row_index:02d}-T{token_index:02d}",
                        text=token_text,
                        start=start,
                        end=cursor,
                        confidence=Confidence(value=None, source="qwen-dashscope"),
                        bounding_box=_bbox_from_xyxy(token["bbox"]),
                    )
                )
            line_text = " ".join(pieces)
            lines.append(
                Line(
                    line_id=f"S{section_index:02d}-L{row_index:02d}",
                    order=len(lines) + 1,
                    text=line_text,
                    confidence=Confidence(value=None, source="qwen-dashscope"),
                    bounding_box=_union_bbox([token["bbox"] for token in row["tokens"]]),
                    tokens=token_models,
                )
            )
    return lines


def _bbox_from_xyxy(values: list[Any]) -> BoundingBox:
    x1, y1, x2, y2 = [float(value) for value in values]
    return BoundingBox(
        coordinate_space="pixel",
        polygon=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    )


def _union_bbox(boxes: list[list[Any]]) -> BoundingBox | None:
    if not boxes:
        return None
    return _bbox_from_xyxy([
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ])


def _failed_result(
    request: RecognitionRequest,
    *,
    source: SourceImage,
    error: Exception,
    model: str,
    latency_ms: float,
    failure_diagnostic: dict[str, Any] | None = None,
) -> RecognitionResult:
    code, retryable, status = _provider_error_details(error)
    preprocessing: dict[str, Any] = {
        **_SAFETY_METADATA,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha256(PROMPT),
        "task_type": TASK_TYPE_FULL_PAGE,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "response_schema_valid": False,
    }
    if failure_diagnostic is not None:
        preprocessing["failure_diagnostic"] = dict(failure_diagnostic)
    return RecognitionResult(
        recognition_id=f"{request.request_id}:qwen-dashscope:failed",
        request_id=request.request_id,
        status=RecognitionStatus.FAILED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            mode="paid_api",
            model_name=model,
            adapter_version=ADAPTER_VERSION,
        ),
        source_image=source,
        preprocessing=preprocessing,
        raw_text="",
        lines=[],
        warnings=[],
        latency_ms=max(0.0, latency_ms),
        provider_error=ProviderError(
            code=code,
            message=str(error),
            retryable=retryable,
            http_status=status,
        ),
    )


def _provider_error_details(error: Exception) -> tuple[str, bool, int | None]:
    if isinstance(error, QwenAPIKeyMissing):
        return ErrorCode.AUTHENTICATION_FAILED.value, False, None
    if isinstance(error, QwenTimeoutError):
        return ErrorCode.TIMEOUT.value, True, None
    if isinstance(error, QwenOutputTruncatedError):
        return QWEN_OUTPUT_TRUNCATED, False, error.http_status
    if isinstance(error, QwenSchemaError):
        return ErrorCode.INTERNAL_ERROR.value, False, error.http_status
    if isinstance(error, QwenClientError):
        if error.http_status == 429:
            return ErrorCode.RATE_LIMITED.value, True, 429
        return ErrorCode.PROVIDER_UNAVAILABLE.value, error.retryable, error.http_status
    if isinstance(error, (OSError, ValueError)):
        return ErrorCode.INVALID_IMAGE.value, False, None
    return ErrorCode.INTERNAL_ERROR.value, False, None


def _source_image_from_request(request: RecognitionRequest) -> SourceImage:
    return SourceImage(
        image_id=request.image_id,
        sha256=str(request.metadata.get("sha256") or ""),
        mime_type=request.mime_type,
        original_filename=Path(request.image_path).name if request.image_path else "",
        width=int(request.metadata.get("width", 0) or 0),
        height=int(request.metadata.get("height", 0) or 0),
        byte_size=int(request.metadata.get("size_bytes", 0) or 0),
        retention="transient",
        cloud_uploaded=False,
    )


def _normalize_crop_box(
    crop_box: list[int] | tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    if crop_box is None:
        return None
    if len(crop_box) != 4:
        raise ValueError("crop_box must contain four integers")
    return tuple(int(value) for value in crop_box)  # type: ignore[return-value]


def _sha256_base64(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid request image base64") from exc
    return hashlib.sha256(raw).hexdigest()


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _positive_float_from_env(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int_from_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default
