"""Paid OpenAI vision provider with strict JSON-schema output.

This module intentionally keeps the OpenAI API key outside code and logs. The
key is read only from OPENAI_API_KEY at request time.
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

from ..contracts import (
    Alternative,
    Confidence,
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
)
from ..closed_set import SCOPES
from ..errors import ErrorCode, ProviderError

PROVIDER_ID = "openai-vision-paid"
API_KEY_ENV = "OPENAI_API_KEY"
MODEL_ENV = "BETGUARD_VISION_MODEL"
PROMPT_VERSION = "betguard-vision-handwritten-bet-grammar-v5"
SCHEMA_VERSION = "1.1"
CACHE_SCHEMA_VERSION = "1.0"
RESPONSES_URL = "https://api.openai.com/v1/responses"
IMAGE_DETAIL_ENV = "BETGUARD_VISION_IMAGE_DETAIL"
ALLOWED_IMAGE_DETAILS = frozenset({"low", "high", "auto"})

# Deliberately narrow alphabet for this OCR profile (Closed Set V2). The slip
# contains only digits, Chinese 二/三/四/各, the fixed × token, the number-set
# separators . ( ), the shared-multiplier marker =, and whitespace. English
# letters and other punctuation never appear.
BET_SLIP_TEXT_PATTERN = r"^[0-9?二三四各尾×.=() 　]*$"
_BET_SLIP_TEXT_RE = re.compile(BET_SLIP_TEXT_PATTERN)
_NUMBER_GROUP_TOKEN_RE = re.compile(r"^[0-9?]{1,2}$")

LAYOUT_HINTS = frozenset({"normal_like", "column_like", "car_like", "unknown"})
DOCUMENT_MODES = frozenset({"auto", "normal", "column", "mixed"})

# uncertain_reason is NOT free text — closed enum only
UNCERTAIN_REASONS = frozenset({
    "unreadable_digit",
    "unclear_multiplier",
    "unclear_grouping",
    "unclear_boundary",
    "unclear_reading_order",
    "unknown",
})


REQUIRED_TOP_KEYS = {"schema_version", "lines"}
REQUIRED_LINE_KEYS = {
    "line_id",
    "entry_id",
    "region",
    "layout_hint",
    "number_groups",
    "multiplier_text",
    "raw_text",
    "alternatives",
    "uncertain",
    "uncertain_reason",
}
OPTIONAL_LINE_KEYS = {"scope"}


OpenAIResponseClient = Callable[[dict[str, Any], str, int], dict[str, Any]]


class OpenAIPaidVisionError(RuntimeError):
    """Internal marker for retry and provider error handling."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class PaidVisionConfig:
    model: str
    timeout_seconds: int = 120
    max_retries: int = 0


def get_config_from_env() -> PaidVisionConfig | None:
    """Return paid vision config, or None when the model is not configured."""
    model = os.environ.get(MODEL_ENV, "").strip()
    if not model:
        return None
    timeout = _int_env("BETGUARD_VISION_TIMEOUT_SECONDS", 120)
    retries = _int_env("BETGUARD_VISION_RETRIES", 0)
    return PaidVisionConfig(model=model, timeout_seconds=timeout, max_retries=retries)


def has_api_key() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def _normalize_document_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in DOCUMENT_MODES else "auto"


def cache_key(
    image_sha256: str,
    model: str,
    prompt_version: str = PROMPT_VERSION,
    schema_version: str = SCHEMA_VERSION,
    document_mode: str = "auto",
) -> str:
    material = "\n".join(
        [image_sha256, model, prompt_version, schema_version, _normalize_document_mode(document_mode)]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_output_schema() -> dict[str, Any]:
    """Strict schema for model output.

    The model is not allowed to set human confirmation or validation status.
    Those are backend-owned workflow states.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "lines"],
        "properties": {
            "schema_version": {"type": "string", "enum": [SCHEMA_VERSION]},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "line_id",
                        "entry_id",
                        "region",
                        "layout_hint",
                        "number_groups",
                        "multiplier_text",
                        "raw_text",
                        "alternatives",
                        "uncertain",
                        "uncertain_reason",
                    ],
                    "properties": {
                        "line_id": {"type": "string", "minLength": 1},
                        "entry_id": {"type": "string", "minLength": 1},
                        "region": {
                            "type": "string",
                            "enum": [
                                "left_numbers",
                                "right_numbers",
                                "amount",
                                "multiplier",
                                "line",
                                "unknown",
                            ],
                        },
                        "layout_hint": {
                            "type": "string",
                            "enum": sorted(LAYOUT_HINTS),
                        },
                        "scope": {
                            "type": "string",
                            "enum": sorted(SCOPES),
                        },
                        "number_groups": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "string", "pattern": r"^[0-9?]{1,2}$"},
                            },
                        },
                        "multiplier_text": {
                            "type": ["string", "null"],
                            "pattern": BET_SLIP_TEXT_PATTERN,
                        },
                        "raw_text": {"type": "string", "pattern": BET_SLIP_TEXT_PATTERN},
                        "alternatives": {
                            "type": "array",
                            "items": {"type": "string", "pattern": BET_SLIP_TEXT_PATTERN},
                        },
                        "uncertain": {"type": "boolean"},
                        "uncertain_reason": {
                            "type": ["string", "null"],
                            "enum": sorted(UNCERTAIN_REASONS) + [None],
                        },
                    },
                },
            },
        },
    }


def build_prompt(document_mode: str = "auto") -> str:
    mode = _normalize_document_mode(document_mode)
    mode_instruction = {
        "auto": "No document-wide bet layout was supplied; infer visual grouping conservatively.",
        "normal": (
            "The human reviewer says this sheet primarily contains normal bets. Preserve each "
            "complete number row and its star/multiplier as one entry."
        ),
        "column": (
            "The human reviewer says this sheet contains column bets. Treat x marks or spacing "
            "between number clusters as column separators, preserve every visible column in "
            "number_groups, and do not flatten a column entry into normal rows."
        ),
        "mixed": (
            "The human reviewer says this sheet mixes normal and column-style entries. Respect "
            "cell borders and determine grouping independently inside each visible cell."
        ),
    }[mode]
    return (
        f"Human-supplied document mode: {mode}. {mode_instruction} "
        "Transcribe the handwritten betting entries and preserve their visual grouping. "
        "This is a CLOSED-ALPHABET transcription task. The only characters that can "
        "appear are: digits 0-9, Chinese 二/三/四/各, the fixed multiplier/separator "
        "symbol ×, the number-set separators . ( ), the shared-multiplier marker =, "
        "spaces, and the question mark ? for unreadable content. "
        "There are NO English letters, NO English words, NO other Chinese characters, "
        "and NO other punctuation on these slips. Never write x, X, or * — use × "
        "only. Never invent an English letter to describe a glyph. "
        "Ignore printed logos, hotel text, paper headings, red or blue grid lines, "
        "background objects, and entries that are clearly crossed out. "
        "Use red or hand-drawn grid borders when present to locate complete betting entries. "
        "When there are no cell borders, a complete number row with its own trailing "
        "multiplier is one entry. Treat each visual betting cell or coherent handwritten "
        "betting block as exactly one output item with "
        "one unique entry_id E01, E02, ... and one unique line_id L01, L02, ... . Writing "
        "wrapped onto two or more physical rows inside the same cell is still one entry. "
        "Never split the numbers, star text, or multiplier belonging to one entry into "
        "separate output items. Never merge content across a visible cell border. Read "
        "complete entries in visual order: top to bottom, then left to right when unambiguous. "
        "For every complete entry, report a non-authoritative layout_hint: normal_like when the "
        "numbers form one group, column_like when visible slashes, spacing, or vertical "
        "alignment divide numbers into multiple groups, car_like only when the visible "
        "writing explicitly indicates a car entry, otherwise unknown. layout_hint is an "
        "observation for human review, never a final bet type. Put faithfully visible "
        "number groupings in number_groups. For a possible column entry, preserve each "
        "visually separated column as its own inner list; do not flatten the columns. Keep "
        "each written number as a string and preserve leading zeroes. Put only the visible "
        "star or multiplier expression in multiplier_text, or null when absent. raw_text "
        "must be a single-line closed-alphabet transcription of the complete cell; separate "
        "visible number groups with spaces and append the visible star or multiplier once. "
        "Multipliers may be integers or decimals (×1, ×2, ×10, ×0.2, ×0.3, ×0.5); keep the "
        "decimal point inside the multiplier. A parenthesized dot-separated number list is "
        "one number set, e.g. (12.18.20.23) means the four numbers 12, 18, 20, 23 with the "
        "following star/multiplier text applying to the whole set. "
        "A shared multiplier marked with 各, such as 各=三×0.3, applies to each clearly "
        "associated number row in that same visual section; emit it as its own line with "
        "scope=all_groups_in_region. Do not apply a shared annotation across "
        "a border, heading, blank gap, or unrelated section. When the region a shared "
        "multiplier controls cannot be determined, set scope=unresolved_region and "
        "uncertain=true. "
        "A bare multi-category multiplier such as 二三×0.3 (the user writes 二三x0.3; "
        "transcribe it as 二三×0.3) means BOTH 二 and 三 share multiplier 0.3 — emit "
        "it verbatim as 二三×0.3, never rewrite it as 各=三×0.3, never drop either "
        "category, and never split it into separate lines. "
        "A multi-column arrangement "
        "connected visually by × marks and followed by one shared star/multiplier is one "
        "column_like entry with one inner list per visible column. "
        "Formatting examples describe notation only, not guaranteed image content: visible "
        "'05 × 18 ×1' with 05 and 18 as one number pair should be transcribed as raw_text "
        "'05 18 ×1', number_groups [['05', '18']], multiplier_text '×1'. A visible four-number "
        "row '14 16 23 28' with trailing '二三×1' should be emitted as "
        "raw_text '14 16 23 28 二三×1', number_groups [['14', '16', '23', '28']], and "
        "multiplier_text '二三×1'. A parenthesized set with two multipliers such as "
        "'(12.18.20.23) 三×0.5 四×3' should be emitted as raw_text '(12.18.20.23) 三×0.5 四×3', "
        "number_groups [['12', '18', '20', '23']], multiplier_text '三×0.5 四×3'. "
        "Never treat the × between two number clusters as the "
        "final multiplier when another trailing × expression is visible. "
        "Preserve visible leading zeroes and separators. "
        "Do not calculate, validate, reinterpret, or complete a bet. "
        "You are REQUIRED to refuse instead of guessing. Never infer a number that is not "
        "clearly visible; never pick a plausible 01-39 value for an unclear glyph; never "
        "treat a stain, grid line, or ink smudge as a digit; never flatten a column bet "
        "(柱碰) into a normal bet; never skip the top half of the image or any unclear "
        "group. When any digit, multiplier, cell boundary, grouping, or reading order is "
        "unclear, set uncertain=true, give a short uncertain_reason, and list only "
        "plausible visible alternatives. When a glyph cannot be read at all, transcribe "
        "it as ? inside the token (for example '1?' or '?') instead of guessing. "
        "Return only the required JSON schema."
    )


class OpenAIPaidVisionProvider:
    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        client: OpenAIResponseClient | None = None,
        cache_dir: Path | None = None,
        config: PaidVisionConfig | None = None,
    ) -> None:
        self._client = client or _post_responses
        self._cache_dir = cache_dir or _default_cache_dir()
        self._config = config

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        config = self._config or get_config_from_env()
        if config is None:
            return _failed_result(
                request,
                code="BETGUARD_VISION_MODEL_NOT_CONFIGURED",
                message=f"{MODEL_ENV} is not configured",
            )

        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            return _failed_result(
                request,
                code=ErrorCode.AUTHENTICATION_FAILED.value,
                message=f"{API_KEY_ENV} is not configured",
            )

        try:
            image_bytes = Path(request.image_path).read_bytes()
        except OSError as exc:
            return _failed_result(
                request,
                code=ErrorCode.INVALID_IMAGE.value,
                message=f"unable to read image: {exc}",
            )

        image_sha = request.metadata.get("sha256") or hashlib.sha256(image_bytes).hexdigest()
        document_mode = _normalize_document_mode(request.metadata.get("document_mode"))
        key = cache_key(str(image_sha), config.model, document_mode=document_mode)

        cached = _read_cache(self._cache_dir, key)
        if cached is not None:
            try:
                data = validate_model_output(cached["model_output"])
            except ValueError as exc:
                return _failed_result(
                    request,
                    code="OPENAI_CACHE_INVALID",
                    message=str(exc),
                )
            result = _result_from_model_output(
                request,
                model=config.model,
                model_output=data,
                latency_ms=0,
                warnings=["paid vision cache hit"],
            )
            return result

        payload = build_responses_payload(
            model=config.model,
            image_bytes=image_bytes,
            mime_type=request.mime_type,
            document_mode=document_mode,
        )

        started = time.perf_counter()
        last_error: OpenAIPaidVisionError | None = None
        for attempt in range(config.max_retries + 1):
            try:
                response = self._client(payload, api_key, config.timeout_seconds)
                output_text = extract_output_text(response)
                model_output = validate_model_output(json.loads(output_text))
                _write_cache(
                    self._cache_dir,
                    key,
                    {
                        "cache_schema_version": CACHE_SCHEMA_VERSION,
                        "image_sha256": image_sha,
                        "model": config.model,
                        "prompt_version": PROMPT_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "document_mode": document_mode,
                        "model_output": model_output,
                    },
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                return _result_from_model_output(
                    request,
                    model=config.model,
                    model_output=model_output,
                    latency_ms=latency_ms,
                    warnings=[],
                )
            except json.JSONDecodeError as exc:
                return _failed_result(
                    request,
                    code="OPENAI_RESPONSE_NOT_JSON",
                    message=f"OpenAI response was not valid JSON: {exc}",
                )
            except ValueError as exc:
                return _failed_result(
                    request,
                    code="OPENAI_RESPONSE_SCHEMA_INVALID",
                    message=str(exc),
                )
            except OpenAIPaidVisionError as exc:
                last_error = exc
                if not exc.retryable or attempt >= config.max_retries:
                    break
                time.sleep(min(0.25 * (attempt + 1), 1.0))

        assert last_error is not None
        return _failed_result(request, code=last_error.code, message=str(last_error))


def build_responses_payload(
    *,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    document_mode: str = "auto",
) -> dict[str, Any]:
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    image_detail = os.environ.get(IMAGE_DETAIL_ENV, "high").strip().lower()
    if image_detail not in ALLOWED_IMAGE_DETAILS:
        image_detail = "high"
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": build_prompt(document_mode)},
                    {"type": "input_image", "image_url": data_url, "detail": image_detail},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "betguard_vision_lines",
                "strict": True,
                "schema": build_output_schema(),
            }
        },
    }


def extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for output in response.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                return text
    raise ValueError("OpenAI response did not contain output text")


def validate_model_output(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("model output must be an object")
    top_keys = set(data)
    extra_top = top_keys - REQUIRED_TOP_KEYS
    missing_top = REQUIRED_TOP_KEYS - top_keys
    if extra_top:
        raise ValueError(f"model output has unsupported fields: {sorted(extra_top)}")
    if missing_top:
        raise ValueError(f"model output missing fields: {sorted(missing_top)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data['schema_version']}")
    lines = data["lines"]
    if not isinstance(lines, list):
        raise ValueError("lines must be a list")
    seen_line_ids: set[str] = set()
    seen_entry_ids: set[str] = set()
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            raise ValueError(f"line {index} must be an object")
        keys = set(line)
        allowed = REQUIRED_LINE_KEYS | OPTIONAL_LINE_KEYS
        extra = keys - allowed
        missing = REQUIRED_LINE_KEYS - keys
        if extra:
            raise ValueError(f"line {index} has unsupported fields: {sorted(extra)}")
        if missing:
            raise ValueError(f"line {index} missing fields: {sorted(missing)}")
        if not isinstance(line["line_id"], str) or not line["line_id"]:
            raise ValueError(f"line {index} line_id must be a non-empty string")
        if line["line_id"] in seen_line_ids:
            raise ValueError(f"line {index} has duplicate line_id: {line['line_id']}")
        seen_line_ids.add(line["line_id"])
        if not isinstance(line["region"], str):
            raise ValueError(f"line {index} region must be a string")
        if not isinstance(line["entry_id"], str) or not line["entry_id"]:
            raise ValueError(f"line {index} entry_id must be a non-empty string")
        if line["entry_id"] in seen_entry_ids:
            raise ValueError(f"line {index} has duplicate entry_id: {line['entry_id']}")
        seen_entry_ids.add(line["entry_id"])
        if line["layout_hint"] not in LAYOUT_HINTS:
            raise ValueError(f"line {index} layout_hint is unsupported")
        if "scope" in line and line["scope"] not in SCOPES:
            raise ValueError(f"line {index} scope is unsupported")
        groups = line["number_groups"]
        if not isinstance(groups, list) or not all(isinstance(group, list) for group in groups):
            raise ValueError(f"line {index} number_groups must be a list of lists")
        if any(
            not isinstance(token, str) or not _NUMBER_GROUP_TOKEN_RE.fullmatch(token)
            for group in groups
            for token in group
        ):
            raise ValueError(f"line {index} number_groups contain invalid number tokens")
        multiplier_text = line["multiplier_text"]
        if multiplier_text is not None and (
            not isinstance(multiplier_text, str)
            or not _BET_SLIP_TEXT_RE.fullmatch(multiplier_text)
        ):
            raise ValueError(f"line {index} multiplier_text contains unsupported OCR characters")
        if not isinstance(line["raw_text"], str):
            raise ValueError(f"line {index} raw_text must be a string")
        if not _BET_SLIP_TEXT_RE.fullmatch(line["raw_text"]):
            raise ValueError(f"line {index} raw_text contains unsupported OCR characters")
        if not isinstance(line["alternatives"], list) or not all(
            isinstance(alt, str) for alt in line["alternatives"]
        ):
            raise ValueError(f"line {index} alternatives must be strings")
        if any(not _BET_SLIP_TEXT_RE.fullmatch(alt) for alt in line["alternatives"]):
            raise ValueError(f"line {index} alternatives contain unsupported OCR characters")
        if not isinstance(line["uncertain"], bool):
            raise ValueError(f"line {index} uncertain must be a boolean")
        reason = line["uncertain_reason"]
        if reason is not None and reason not in UNCERTAIN_REASONS:
            raise ValueError(f"line {index} uncertain_reason must be a closed-set enum value")
        if line["uncertain"] and not reason:
            raise ValueError(f"line {index} uncertain_reason is required when uncertain")
    return data


def _post_responses(payload: dict[str, Any], api_key: str, timeout_seconds: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        RESPONSES_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise OpenAIPaidVisionError(ErrorCode.TIMEOUT.value, "OpenAI request timed out", True) from exc
    except urllib.error.HTTPError as exc:
        code = _error_code_for_status(exc.code)
        retryable = exc.code in {408, 409, 429, 500, 502, 503, 504}
        raise OpenAIPaidVisionError(code, f"OpenAI request failed with HTTP {exc.code}", retryable) from exc
    except urllib.error.URLError as exc:
        raise OpenAIPaidVisionError(
            ErrorCode.PROVIDER_UNAVAILABLE.value,
            f"OpenAI request failed: {exc.reason}",
            True,
        ) from exc
    except json.JSONDecodeError as exc:
        raise OpenAIPaidVisionError(
            "OPENAI_HTTP_RESPONSE_NOT_JSON",
            f"OpenAI HTTP response was not JSON: {exc}",
            False,
        ) from exc


def _error_code_for_status(status: int) -> str:
    if status in {401, 403}:
        return ErrorCode.AUTHENTICATION_FAILED.value
    if status == 429:
        return ErrorCode.RATE_LIMITED.value
    if status in {402, 451}:
        return ErrorCode.BILLING_REQUIRED.value
    return ErrorCode.PROVIDER_UNAVAILABLE.value


def _result_from_model_output(
    request: RecognitionRequest,
    *,
    model: str,
    model_output: dict[str, Any],
    latency_ms: int,
    warnings: list[str],
) -> RecognitionResult:
    from ..closed_set import validate_line

    lines: list[Line] = []
    all_warnings = list(warnings)
    openai_lines: list[dict[str, Any]] = []
    for order, line in enumerate(model_output["lines"], start=1):
        alternatives = [
            Alternative(text=alt, source="openai_paid") for alt in line.get("alternatives", [])
        ]
        if line["uncertain"]:
            all_warnings.append(
                f"{line['line_id']} uncertain: {line.get('uncertain_reason') or 'unknown'}"
            )

        # Closed character-set validation (never auto-corrects; flags for human)
        validation = validate_line(
            number_groups=line.get("number_groups", []),
            multiplier_text=line.get("multiplier_text"),
            raw_text=line.get("raw_text", ""),
            layout_hint=line.get("layout_hint", "unknown"),
            uncertain=line["uncertain"],
            scope=line.get("scope"),
            region_bound=False,  # no ROI binding in whole-image mode
        )
        if validation["needs_human_confirmation"]:
            all_warnings.append(
                f"{line['line_id']} needs human confirmation: "
                f"{','.join(validation['issues']) or 'uncertain'}"
            )

        structured = dict(line)
        structured["needs_human_confirmation"] = validation["needs_human_confirmation"]
        structured["validation_issues"] = validation["issues"]
        structured["token_validations"] = validation["token_validations"]
        structured["semantics"] = validation["semantics"]
        openai_lines.append(structured)

        lines.append(
            Line(
                line_id=line["line_id"],
                order=order,
                text=line["raw_text"],
                tokens=[],
                confidence=Confidence(value=None, source="openai_paid"),
                alternatives=alternatives,
                # warnings carries ONLY the model's uncertain signal — never
                # the policy-level needs_human_confirmation (that lives in
                # openai_lines[].needs_human_confirmation and semantics).
                warnings=["uncertain"] if line["uncertain"] else [],
            )
        )

    if not lines:
        return _failed_result(
            request,
            code=ErrorCode.NO_TEXT_DETECTED.value,
            message="OpenAI vision returned no lines",
        )

    return RecognitionResult(
        recognition_id=f"{request.request_id}:openai-paid",
        request_id=request.request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            model_name=model,
            mode="paid_api",
            adapter_version=PROMPT_VERSION,
        ),
        source_image=_source_image_from_request(request, cloud_uploaded=True),
        preprocessing={
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "document_mode": _normalize_document_mode(request.metadata.get("document_mode")),
            "human_confirmation_required": True,
            "auto_submit": False,
            "auto_confirm": False,
            "openai_lines": openai_lines,
        },
        raw_text="\n".join(line.text for line in lines),
        lines=lines,
        warnings=all_warnings,
        latency_ms=latency_ms,
        provider_error=None,
    )


def _failed_result(request: RecognitionRequest, *, code: str, message: str) -> RecognitionResult:
    return RecognitionResult(
        recognition_id=f"{request.request_id}:openai-paid:failed",
        request_id=request.request_id,
        status=RecognitionStatus.FAILED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            model_name=os.environ.get(MODEL_ENV, "").strip(),
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


def _source_image_from_request(
    request: RecognitionRequest,
    *,
    cloud_uploaded: bool = False,
) -> SourceImage:
    width = request.metadata.get("width")
    height = request.metadata.get("height")
    sha256 = request.metadata.get("sha256")
    return SourceImage(
        image_id=request.image_id,
        sha256=str(sha256) if sha256 else "",
        mime_type=request.mime_type,
        width=int(width) if isinstance(width, int) else 0,
        height=int(height) if isinstance(height, int) else 0,
        byte_size=int(request.metadata.get("size_bytes", 0) or 0),
        cloud_uploaded=cloud_uploaded,
    )


def _read_cache(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return None
    return data


def _write_cache(cache_dir: Path, key: str, data: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_cache_dir() -> Path:
    return Path(get_data_dir()) / "vision_paid_cache"


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(parsed, 0)
