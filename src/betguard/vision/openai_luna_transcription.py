"""Bounded GPT-5.6 Luna image-to-editable-text transcription.

This reader is deliberately separate from the legacy structured vision review
provider.  It returns only a Betguard-compatible typing aid and never creates
or confirms actionable bets.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from betguard.user_data import get_data_dir


PROVIDER_ID = "openai-gpt-5.6-luna-transcription"
MODEL_NAME = "gpt-5.6-luna"
API_KEY_ENV = "OPENAI_API_KEY"
RESPONSES_URL = "https://api.openai.com/v1/responses"
ADAPTER_VERSION = "betguard.openai-luna-transcription.v2"
PROMPT_VERSION = "betguard-image-to-text-records-v2"
RESPONSE_SCHEMA_VERSION = "betguard.openai-luna-text.v2"
CACHE_SCHEMA_VERSION = "betguard.openai-luna-transcription-cache.v2"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_MAX_OUTPUT_TOKENS = 4_000
DEFAULT_IMAGE_DETAIL = "original"
DEFAULT_REASONING_EFFORT = "medium"

_ALLOWED_IMAGE_DETAILS = frozenset({"low", "high", "auto", "original"})
_ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium"})
_CANCELLED_PROSE = ("(crossed out)", "crossed out")
_CAR_LITERAL_LINE_RE = re.compile(
    r"^(?P<number>\d{1,2})\s*[xX×]\s*(?P<amount>\d+(?:\.\d+)?)\s*車$"
)

PROMPT = """You are a literal visual transcription reader for handwritten lottery bet slips.
Return only the strict JSON requested by the response schema. This is a typing aid, not betting
authority. Read only visible marks; never infer a likely lottery number.

A PHYSICAL RECORD is one number block together with every nearby category, multiplier, special-play,
or continuation annotation that belongs to that number block. Produce exactly one `records` element
per physical record, not one element per handwritten row. Preserve top-left to bottom-right order.
Red horizontal/vertical lines and boxed cells are hard boundaries: never join text across them.

Each `betguard_lines` value is one complete Betguard parser line. It must include its own number
expression plus its category/multiplier or special-play amount. Never emit orphan lines such as
`4×1`, `13 33`, or `×0.5` when those marks belong to the number block above or beside them.

Formatting rules:
1. Preserve every actually visible x / X / ×. Never invent × from spacing alone.
2. Preserve 01-39 leading zeroes and every visible decimal point. `0.5`, `x0.5`, and `×0.5`
   must never become `05`. If one digit or decimal is unreadable, put `?` in that exact position.
3. Stacked 2/3/4 digits next to one ×amount are multiplier categories, not lottery numbers.
   Combine categories sharing one amount: a visible stacked 3 and 4 beside ×1 becomes `3,4 ×1`.
4. If one physical record has different amounts for different categories, repeat the complete
   number expression in separate `betguard_lines` inside the SAME record. For example, visible
   numbers `06 18 29` with `2×1` and `3×5` become lines `06 18 29 2×1` and
   `06 18 29 3×5`. Never return `3×5` alone.
5. For a clear column record, collect vertically aligned numbers into their columns and place ×
   only between visible columns. Unequal column heights are allowed. Example visible layout:
      04 × 15 × 26
      14   25   36
           35
   becomes one line: `04 14 × 15 25 35 × 26 36 2,3 ×0.5` when that multiplier is visible.
6. For exactly two clearly aligned number rows with a visible column relation, transpose rows.
   Rows `36 07 08 06` and `38 17 18 13` become
   `36 38 × 07 17 × 08 18 × 06 13`. If alignment or operators are unclear, preserve visible
   text with `?`; do not invent a column structure.
7. Keep special literals complete, including adjacent digits: `7尾`, `各1車`, `半車`.
   An unreadable tail digit is `?尾`, never bare `尾`, and special digits are not ordinary numbers.
8. Omit definitely crossed-out/cancelled records and increment `cancelled_count`. Never output
   English prose such as `(crossed out)` or `(cancelled)`.
9. Mark a record uncertain when any `?` is used and report the total in `uncertain_count`.
10. Do not include Markdown, explanations, labels, raw JSON text, or line breaks inside a single
    `betguard_lines` string.
"""
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


class LunaTranscriptionError(RuntimeError):
    """Safe, classified error from the optional Luna reader."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LunaTranscriptionConfig:
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    image_detail: str = DEFAULT_IMAGE_DETAIL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    cache_dir: Path | None = None


LunaTransport = Callable[[dict[str, Any], str, LunaTranscriptionConfig], dict[str, Any]]


def default_cache_dir() -> Path:
    return Path(get_data_dir()) / "vision" / "openai-luna-transcription-cache"


def get_config_from_env() -> LunaTranscriptionConfig:
    detail = os.environ.get("BETGUARD_LUNA_IMAGE_DETAIL", DEFAULT_IMAGE_DETAIL).strip().lower()
    if detail not in _ALLOWED_IMAGE_DETAILS:
        detail = DEFAULT_IMAGE_DETAIL
    effort = os.environ.get(
        "BETGUARD_LUNA_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
    ).strip().lower()
    if effort not in _ALLOWED_REASONING_EFFORTS:
        effort = DEFAULT_REASONING_EFFORT
    return LunaTranscriptionConfig(
        timeout_seconds=_bounded_int_env(
            "BETGUARD_LUNA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=10, maximum=180
        ),
        max_output_tokens=_bounded_int_env(
            "BETGUARD_LUNA_MAX_OUTPUT_TOKENS",
            DEFAULT_MAX_OUTPUT_TOKENS,
            minimum=512,
            maximum=8_000,
        ),
        image_detail=detail,
        reasoning_effort=effort,
        cache_dir=default_cache_dir(),
    )


def has_api_key() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def normalize_parser_safe_literals(text: str) -> tuple[str, int]:
    """Render an exact visible ``number × amount 車`` literal in parser syntax.

    This only reorders an unambiguous full-line literal.  It never changes a
    digit, fills uncertainty, or turns a partial line into a bet.
    """
    normalized: list[str] = []
    reformatted = 0
    for line in str(text or "").splitlines():
        stripped = line.strip()
        match = _CAR_LITERAL_LINE_RE.fullmatch(stripped)
        if match is None:
            normalized.append(line)
            continue
        normalized.append(f"{match.group('number')}車{match.group('amount')}")
        reformatted += 1
    return "\n".join(normalized), reformatted


def build_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "records",
            "cancelled_count",
            "uncertain_count",
        ],
        "properties": {
            "schema_version": {"type": "string", "enum": [RESPONSE_SCHEMA_VERSION]},
            "records": {
                "type": "array",
                "minItems": 1,
                "maxItems": 500,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["betguard_lines", "uncertain"],
                    "properties": {
                        "betguard_lines": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 2_000,
                                "pattern": "^[^\\r\\n]+$",
                            },
                        },
                        "uncertain": {"type": "boolean"},
                    },
                },
            },
            "cancelled_count": {"type": "integer", "minimum": 0},
            "uncertain_count": {"type": "integer", "minimum": 0},
        },
    }


def build_responses_payload(
    *, image_bytes: bytes, mime_type: str, config: LunaTranscriptionConfig
) -> dict[str, Any]:
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    return {
        "model": MODEL_NAME,
        "store": False,
        "instructions": PROMPT,
        "max_output_tokens": config.max_output_tokens,
        "reasoning": {"effort": config.reasoning_effort},
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": config.image_detail,
                    },
                ],
            }
        ],
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "betguard_image_text_transcription",
                "strict": True,
                "schema": build_output_schema(),
            },
        },
    }


def transcribe_with_luna(
    *,
    image_path: str | Path,
    mime_type: str,
    image_sha256: str,
    config: LunaTranscriptionConfig | None = None,
    transport: LunaTransport | None = None,
) -> dict[str, Any]:
    """Return one strictly validated Luna prediction, with retry fixed at zero."""
    config = config or get_config_from_env()
    cache_dir = config.cache_dir or default_cache_dir()
    identity = _cache_identity(image_sha256, config)
    cached = _read_cache(cache_dir, identity)
    if cached is not None:
        return {
            **cached,
            "cache_hit": True,
            "external_call_count": 0,
            "retry_count": 0,
            "latency_ms": 0,
        }

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise LunaTranscriptionError(
            "OPENAI_API_KEY_MISSING",
            "尚未設定 OpenAI API key；您仍可使用目前的 Gemma 或直接輸入文字。",
        )

    try:
        image_bytes = Path(image_path).read_bytes()
    except OSError as exc:
        raise LunaTranscriptionError("IMAGE_READ_FAILED", "無法讀取已上傳圖片。") from exc

    payload = build_responses_payload(
        image_bytes=image_bytes, mime_type=mime_type, config=config
    )
    started = time.perf_counter()
    try:
        response = (transport or _post_responses)(payload, api_key, config)
    except LunaTranscriptionError:
        raise
    except Exception as exc:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_FAILED", "Luna 圖片辨識目前無法使用。"
        ) from exc
    latency_ms = round((time.perf_counter() - started) * 1000)
    try:
        decoded = _validate_response(response)
    except LunaTranscriptionError as exc:
        exc.provider_raw_response = response
        raise
    result = {
        "status": "completed",
        "text": decoded["betguard_text"],
        "provider_raw_response": response,
        "native_ocr_text": "\n\n".join("\n".join(record["betguard_lines"]) for record in decoded["records"]),
        "source_records": decoded["records"],
        "record_count": len(decoded["records"]),
        "cancelled_count": decoded["cancelled_count"],
        "uncertain_count": decoded["uncertain_count"],
        "reader": PROVIDER_ID,
        "model": MODEL_NAME,
        "adapter_version": ADAPTER_VERSION,
        "prompt_sha256": PROMPT_SHA256,
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "image_detail": config.image_detail,
        "usage": _safe_usage(response.get("usage")),
        "cache_identity": identity,
        "cache_hit": False,
        "external_call_count": 1,
        "retry_count": 0,
        "latency_ms": latency_ms,
    }
    _write_cache(cache_dir, identity, result)
    return result


def _validate_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_INVALID", "Luna 回傳格式無效。"
        )
    if response.get("status") != "completed":
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_INCOMPLETE", "Luna 未完成圖片辨識。"
        )
    output_text = _extract_output_text(response)
    try:
        decoded = json.loads(output_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_NOT_JSON", "Luna 回傳內容不是完整 JSON。"
        ) from exc
    if not isinstance(decoded, dict) or set(decoded) != {
        "schema_version",
        "records",
        "cancelled_count",
        "uncertain_count",
    }:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳欄位不完整。"
        )
    if decoded.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳版本不符。"
        )
    records = decoded.get("records")
    text = _render_records(records)
    if not text or len(text) > 100_000:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_TEXT_INVALID", "Luna 沒有讀到可編輯文字。"
        )
    if "```" in text or any(marker in text.lower() for marker in _CANCELLED_PROSE):
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_TEXT_INVALID", "Luna 回傳了非 Betguard 文字。"
        )
    for key in ("cancelled_count", "uncertain_count"):
        value = decoded.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise LunaTranscriptionError(
                "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳計數無效。"
            )
    uncertain_records = sum(1 for record in records if record["uncertain"])
    if decoded["uncertain_count"] != uncertain_records:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳的不確定筆數不一致。"
        )
    return {**decoded, "betguard_text": text}


def _render_records(value: Any) -> str:
    if not isinstance(value, list) or not value or len(value) > 500:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳的投注區塊無效。"
        )
    rendered: list[str] = []
    for record in value:
        if not isinstance(record, dict) or set(record) != {"betguard_lines", "uncertain"}:
            raise LunaTranscriptionError(
                "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳的投注區塊無效。"
            )
        if not isinstance(record["uncertain"], bool):
            raise LunaTranscriptionError(
                "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳的不確定標記無效。"
            )
        lines = record["betguard_lines"]
        if not isinstance(lines, list) or not 1 <= len(lines) <= 8:
            raise LunaTranscriptionError(
                "OPENAI_RESPONSE_SCHEMA_INVALID", "Luna 回傳的投注文字無效。"
            )
        clean_lines: list[str] = []
        for line in lines:
            if (
                not isinstance(line, str)
                or not line.strip()
                or len(line) > 2_000
                or "\n" in line
                or "\r" in line
            ):
                raise LunaTranscriptionError(
                    "OPENAI_RESPONSE_TEXT_INVALID", "Luna 回傳的投注文字無效。"
                )
            clean_lines.append(line.strip())
        rendered.append("\n".join(clean_lines))
    return "\n\n".join(rendered)


def _extract_output_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str):
        return output_text
    texts: list[str] = []
    for output in response.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if len(texts) != 1:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_TEXT_MISSING", "Luna 回傳缺少唯一文字結果。"
        )
    return texts[0]


def _post_responses(
    payload: dict[str, Any], api_key: str, config: LunaTranscriptionConfig
) -> dict[str, Any]:
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise LunaTranscriptionError("OPENAI_TIMEOUT", "Luna 圖片辨識逾時。") from exc
    except urllib.error.HTTPError as exc:
        code = {
            401: "OPENAI_AUTH_FAILED",
            403: "OPENAI_ACCESS_DENIED",
            404: "OPENAI_MODEL_UNAVAILABLE",
            429: "OPENAI_RATE_LIMITED",
        }.get(exc.code, "OPENAI_HTTP_ERROR")
        raise LunaTranscriptionError(code, f"Luna 圖片辨識失敗（HTTP {exc.code}）。") from exc
    except urllib.error.URLError as exc:
        raise LunaTranscriptionError(
            "OPENAI_UNAVAILABLE", "目前無法連線至 Luna 圖片辨識。"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LunaTranscriptionError(
            "OPENAI_RESPONSE_INVALID", "Luna 回傳格式無效。"
        ) from exc


def _cache_identity(image_sha256: str, config: LunaTranscriptionConfig) -> str:
    material = "\n".join(
        [
            CACHE_SCHEMA_VERSION,
            image_sha256,
            MODEL_NAME,
            ADAPTER_VERSION,
            PROMPT_SHA256,
            RESPONSE_SCHEMA_VERSION,
            config.image_detail,
            config.reasoning_effort,
            str(config.max_output_tokens),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, identity: str) -> dict[str, Any] | None:
    path = cache_dir / f"{identity}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("cache_identity") != identity:
        return None
    if value.get("status") != "completed" or value.get("reader") != PROVIDER_ID:
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return value


def _write_cache(cache_dir: Path, identity: str, result: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{identity}.json"
    temporary = cache_dir / f".{identity}.{os.getpid()}.tmp"
    safe_result = {
        key: value
        for key, value in result.items()
        if key not in {"cache_hit", "external_call_count", "retry_count", "latency_ms"}
    }
    temporary.write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _safe_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        token_count = value.get(key)
        if isinstance(token_count, int) and not isinstance(token_count, bool) and token_count >= 0:
            usage[key] = token_count
    output_details = value.get("output_tokens_details")
    if isinstance(output_details, dict):
        reasoning_tokens = output_details.get("reasoning_tokens")
        if (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= 0
        ):
            usage["reasoning_tokens"] = reasoning_tokens
    return usage


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))
