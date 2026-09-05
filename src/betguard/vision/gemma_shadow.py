"""Gemma 4 raw-reader machine evidence adapter.

The adapter deliberately does not implement ``ImageRecognitionProvider`` and
never returns ``RecognitionResult``.  The runtime router may select its raw
reading as the primary prefill suggestion, but only a Human Confirmed Answer
can become value authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from betguard.vision.contracts import RecognitionRequest


PROVIDER_ID = "gemma4-26b-shadow"
MODEL_NAME = "gemma-4-26b-a4b-it"
MODEL_VERSION = MODEL_NAME
ADAPTER_VERSION = "betguard.gemma-shadow.v3"
EVIDENCE_SCHEMA_VERSION = "betguard.vision.gemma-shadow-evidence.v2"
CACHE_SCHEMA_VERSION = "betguard.vision.gemma-shadow-cache.v2"
REQUEST_SCHEMA_VERSION = "betguard.vision.gemma-raw-reader-request.v4"
ENABLED_ENV = "BETGUARD_GEMMA_SHADOW_ENABLED"
API_KEY_ENV = "GEMINI_API_KEY"
TIMEOUT_ENV = "BETGUARD_GEMMA_SHADOW_TIMEOUT_SECONDS"
ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

RAW_READER_PROMPT = """Directly inspect only the attached original handwritten betting-slip image.
Faithfully transcribe visible writing from top-left to bottom-right into plain text that can be
pasted into Betguard's existing text input. This is a typing aid, not betting value authority.

PHYSICAL RECORD BOUNDARIES ARE HARD TRANSCRIPTION BOUNDARIES. Red horizontal or vertical grid
lines, boxed cells, and other clear physical separators divide records. Characters on opposite
sides of a clear separator must never appear in the same item. Return a separate item for every
clearly separate physical record, in page order. Keep the number line, multiplier/category line,
special-play text, and continuation that are inside one record together. If a boundary is unclear,
preserve the visible line break instead of joining text across it. Never merge nearby left/right
records merely because their writing is close. Each item must contain one physical betting record.
Do not split those components into independent records, and never combine unrelated records.
Treat a visible vertical red border as a hard left/right cut even when writing on both sides shares
the same height. Trace every item only inside its own visible red box; do not append a number row
from the neighboring box as a continuation line.

Preserve every actually visible x / X / × operator, original line order, 01-39 leading zeroes,
and multiplier text. DECIMAL DOTS ARE CRITICAL: visible 0.5, x0.5, ×0.5, x 0.5, or × 0.5 must
retain the decimal dot and must never become 05, x05, or ×05. A decimal multiplier is not a
lottery number. When the decimal or a number is unreadable, emit ? in that exact position instead
of guessing a digit. Do not use betting knowledge or likely patterns to fill unreadable marks.

Do not infer final betting semantics. One narrow Betguard-compatible visual formatting operation
is allowed: when ONE physical record has exactly two clearly aligned number rows and the image
also clearly shows the ×/column relation between aligned positions, transpose the visible pairs
into columns. For visible rows ``36 07 08 06`` and ``38 17 18 13``, output
``36 38 × 07 17 × 08 18 × 06 13``. For visible rows ``12 24 36`` and ``08 14 38``, output
``12 08 × 24 14 × 36 38``. If either alignment or the operator relation is unclear, preserve the
two original rows and do not invent × from spacing or line breaks.
Do not invent multiplication operators outside that clearly visible two-row case.
After this two-row transposition, raw_text must contain exactly ONE transposed number line, followed
only by that record's multiplier/category or special-play line when visible. Do not repeat either
original number row below the transposed line, and do not append any neighboring record's row.

Preserve special-play literals completely, including their adjacent digit or uncertainty marker.
For visible ``03 × 16 × 7尾``, output exactly ``03 × 16 × 7尾``; never drop 7 and output only 尾.
If the digit is unreadable, output ``03 × 16 × ?尾``. Apply the same literal rule to 尾, 車,
半車, and 各.

For a definitely crossed-out/cancelled physical record, set ``cancelled`` to ``yes`` and use ``?``
as raw_text if no uncancelled literal remains. Never write English explanations such as
``(crossed out)``, ``(cancelled)``, or ``(cancelled bet)`` in raw_text. For an unclear mark, set
cancelled and uncertain to ``unclear``/true rather than guessing.

In each ``raw_text``, use only literal Betguard-compatible transcription. Do not add explanations,
JSON-like labels, confidence prose, or reconstructed betting schemas. Examples of complete record
text are ``05 × 08 09 23 × 10 20 29\n2,3 × 2`` and
``36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5``.

Return ONLY one JSON object with this exact shape:
{"version":"gemma-raw-reader-v2","items":[{"raw_text":"visible text","numbers":"literal visible numbers/columns or unclear","multiplier_text":"all literal visible rules or none","layout_guess":"normal|column|unclear","continuation":"yes|no|unclear","special_text":"raw text or none","cancelled":"yes|no|unclear","uncertain":true,"uncertain_reason":"reason or none"}]}
This is evidence only. Never claim that an item is confirmed, executable, exportable, or safe to submit."""
PROMPT_SHA256 = hashlib.sha256(RAW_READER_PROMPT.encode("utf-8")).hexdigest()

_RAW_ITEM_KEYS = frozenset(
    {
        "raw_text",
        "numbers",
        "multiplier_text",
        "layout_guess",
        "continuation",
        "special_text",
        "cancelled",
        "uncertain",
        "uncertain_reason",
    }
)
_ITEM_REJECTION_CODES = frozenset(
    {
        "GEMMA_ITEM_NOT_OBJECT",
        "GEMMA_ITEM_SCHEMA_KEYS_INVALID",
        "GEMMA_ITEM_RAW_TEXT_INVALID",
        "GEMMA_ITEM_LITERAL_FIELDS_INVALID",
        "GEMMA_ITEM_LAYOUT_INVALID",
        "GEMMA_ITEM_CONTINUATION_INVALID",
        "GEMMA_ITEM_SPECIAL_TEXT_INVALID",
        "GEMMA_ITEM_CANCELLED_INVALID",
        "GEMMA_ITEM_UNCERTAIN_INVALID",
        "GEMMA_ITEM_UNCERTAIN_REASON_INVALID",
    }
)

RAW_READER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "string", "enum": ["gemma-raw-reader-v2"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string"},
                    "numbers": {"type": "string"},
                    "multiplier_text": {"type": "string"},
                    "layout_guess": {
                        "type": "string",
                        "enum": ["normal", "column", "unclear"],
                    },
                    "continuation": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                    },
                    "special_text": {"type": "string"},
                    "cancelled": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                    },
                    "uncertain": {"type": "boolean"},
                    "uncertain_reason": {"type": "string"},
                },
                "required": sorted(_RAW_ITEM_KEYS),
                "additionalProperties": False,
            },
        },
    },
    "required": ["version", "items"],
    "additionalProperties": False,
}


class GemmaFinishError(ValueError):
    """The provider returned a candidate that did not finish completely."""


class GemmaItemValidationError(ValueError):
    """One raw-reader item failed the strict suggestion-only contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GemmaShadowConfig:
    enabled: bool
    endpoint: str
    timeout_seconds: float
    cache_dir: Path
    model: str = MODEL_NAME
    model_version: str = MODEL_VERSION
    adapter_version: str = ADAPTER_VERSION
    prompt: str = RAW_READER_PROMPT
    prompt_sha256: str = PROMPT_SHA256
    request_schema_version: str = REQUEST_SCHEMA_VERSION
    temperature: float = 0.0
    max_output_tokens: int = 8192

    @property
    def configured(self) -> bool:
        return self.enabled and has_api_key()


@dataclass(frozen=True)
class GemmaShadowCacheIdentity:
    image_sha256: str
    provider: str
    model: str
    model_version: str
    adapter_version: str
    prompt_sha256: str
    request_schema_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "image_sha256": self.image_sha256,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
            "prompt_sha256": self.prompt_sha256,
            "request_schema_version": self.request_schema_version,
        }

    def key(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()


class GemmaShadowCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or default_gemma_shadow_cache_dir()

    def get(self, identity: GemmaShadowCacheIdentity) -> dict[str, Any] | None:
        path = self.cache_dir / f"{identity.key()}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            return None
        if record.get("identity") != identity.to_dict():
            return None
        evidence = record.get("evidence")
        if not isinstance(evidence, dict):
            return None
        if hashlib.sha256(_canonical_json(evidence)).hexdigest() != record.get("evidence_sha256"):
            return None
        try:
            return validate_gemma_evidence(evidence)
        except ValueError:
            return None

    def put_validated(
        self, identity: GemmaShadowCacheIdentity, evidence: dict[str, Any]
    ) -> Path:
        validated = validate_gemma_evidence(evidence)
        if validated["status"] != "completed":
            raise ValueError("only completed Gemma evidence may be cached")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{identity.key()}.json"
        record = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity.to_dict(),
            "evidence": validated,
            "evidence_sha256": hashlib.sha256(_canonical_json(validated)).hexdigest(),
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{identity.key()}.", suffix=".tmp", dir=self.cache_dir
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return target


def has_api_key() -> bool:
    """Read the API key at the moment it is needed; never retain it in config."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def get_gemma_shadow_config() -> GemmaShadowConfig:
    timeout_value = os.environ.get(TIMEOUT_ENV, "120")
    try:
        timeout = float(timeout_value)
    except ValueError:
        timeout = 120.0
    timeout = min(max(timeout, 1.0), 300.0)
    return GemmaShadowConfig(
        enabled=os.environ.get(ENABLED_ENV, "").strip() == "1",
        endpoint=ENDPOINT_TEMPLATE.format(model=MODEL_NAME),
        timeout_seconds=timeout,
        cache_dir=default_gemma_shadow_cache_dir(),
    )


def run_gemma_shadow(
    request: RecognitionRequest,
    *,
    config: GemmaShadowConfig | None = None,
    cache: GemmaShadowCache | None = None,
    transport: Callable[[dict[str, Any], str, GemmaShadowConfig], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return fail-closed optional evidence; never raise into the Qwen flow."""
    config = config or get_gemma_shadow_config()
    started = time.perf_counter()
    base = _base_evidence(request, config)
    if not config.enabled:
        return _failure(base, "GEMMA_SHADOW_DISABLED", started, status="disabled")
    identity = GemmaShadowCacheIdentity(
        image_sha256=str(request.metadata.get("sha256") or ""),
        provider=PROVIDER_ID,
        model=config.model,
        model_version=config.model_version,
        adapter_version=config.adapter_version,
        prompt_sha256=config.prompt_sha256,
        request_schema_version=config.request_schema_version,
    )
    cache = cache or GemmaShadowCache(config.cache_dir)
    cached = cache.get(identity)
    if cached is not None:
        return {
            **cached,
            "cache_hit": True,
            "cache_identity": identity.to_dict(),
            "external_call_count": 0,
            "retry_count": 0,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        return {
            **_failure(base, "GEMMA_SHADOW_NOT_CONFIGURED", started, status="unavailable"),
            "cache_identity": identity.to_dict(),
        }
    external_call_count = 0
    try:
        image_bytes = Path(request.image_path).read_bytes()
        payload = _request_payload(request, image_bytes, config)
        external_call_count = 1
        envelope = (transport or _post_generate_content)(payload, api_key, config)
        base["provider_raw_response"] = envelope
        evidence = _validated_response(envelope, base)
    except TimeoutError:
        return _failure(
            base,
            "GEMMA_SHADOW_TIMEOUT",
            started,
            status="timeout",
            external_call_count=external_call_count,
        )
    except urllib.error.HTTPError as exc:
        return _failure(
            base,
            "GEMMA_SHADOW_HTTP_ERROR",
            started,
            detail=str(exc.code),
            external_call_count=external_call_count,
        )
    except urllib.error.URLError as exc:
        return _failure(
            base,
            "GEMMA_SHADOW_NETWORK_ERROR",
            started,
            detail=type(exc.reason).__name__,
            external_call_count=external_call_count,
        )
    except GemmaFinishError as exc:
        return _failure(
            base,
            "GEMMA_SHADOW_FINISH_FAILURE",
            started,
            detail=type(exc).__name__,
            external_call_count=external_call_count,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _failure(
            base,
            "GEMMA_SHADOW_INVALID_RESPONSE",
            started,
            detail=type(exc).__name__,
            external_call_count=external_call_count,
        )
    evidence["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    evidence["external_call_count"] = external_call_count
    evidence["retry_count"] = 0
    try:
        cache.put_validated(identity, evidence)
    except OSError:
        pass
    return {
        **evidence,
        "cache_hit": False,
        "cache_identity": identity.to_dict(),
    }


def validate_gemma_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("invalid Gemma evidence schema")
    if value.get("status") != "completed" or not isinstance(value.get("items"), list):
        raise ValueError("Gemma evidence must be completed items")
    required_safety = {
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "authority": "qwen-dashscope",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    if any(value.get(key) != expected for key, expected in required_safety.items()):
        raise ValueError("Gemma evidence safety flags are invalid")
    items: list[dict[str, Any]] = []
    accepted_indexes: set[int] = set()
    for position, item in enumerate(value["items"], 1):
        try:
            normalized = _validated_item(item, position)
        except GemmaItemValidationError as exc:
            raise ValueError(exc.code) from exc
        evidence_index = int(normalized["evidence_id"].split("-")[1])
        if evidence_index in accepted_indexes:
            raise ValueError("Gemma evidence IDs must be unique")
        accepted_indexes.add(evidence_index)
        items.append(normalized)

    rejected_items_value = value.get("rejected_items", [])
    if not isinstance(rejected_items_value, list):
        raise ValueError("Gemma rejected_items must be a list")
    rejected_items: list[dict[str, Any]] = []
    rejected_indexes: set[int] = set()
    for rejected in rejected_items_value:
        if not isinstance(rejected, dict) or set(rejected) != {
            "item_index",
            "reason_code",
        }:
            raise ValueError("Gemma rejected item diagnostic is invalid")
        item_index = rejected.get("item_index")
        reason_code = rejected.get("reason_code")
        if (
            isinstance(item_index, bool)
            or not isinstance(item_index, int)
            or item_index < 1
            or reason_code not in _ITEM_REJECTION_CODES
            or item_index in rejected_indexes
            or item_index in accepted_indexes
        ):
            raise ValueError("Gemma rejected item diagnostic is invalid")
        rejected_indexes.add(item_index)
        rejected_items.append(
            {"item_index": item_index, "reason_code": str(reason_code)}
        )

    raw_item_count = value.get("raw_item_count", len(items) + len(rejected_items))
    accepted_item_count = value.get("accepted_item_count", len(items))
    rejected_item_count = value.get("rejected_item_count", len(rejected_items))
    counts = (raw_item_count, accepted_item_count, rejected_item_count)
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts):
        raise ValueError("Gemma item diagnostics counts are invalid")
    if (
        accepted_item_count != len(items)
        or rejected_item_count != len(rejected_items)
        or raw_item_count != accepted_item_count + rejected_item_count
        or any(index > raw_item_count for index in accepted_indexes | rejected_indexes)
    ):
        raise ValueError("Gemma item diagnostics counts are inconsistent")
    reason_codes = list(dict.fromkeys(item["reason_code"] for item in rejected_items))
    supplied_reason_codes = value.get("rejected_reason_codes", reason_codes)
    if supplied_reason_codes != reason_codes:
        raise ValueError("Gemma rejected reason codes are inconsistent")
    partial_machine_read = accepted_item_count > 0 and rejected_item_count > 0
    if value.get("partial_machine_read", partial_machine_read) is not partial_machine_read:
        raise ValueError("Gemma partial read flag is inconsistent")
    needs_review = rejected_item_count > 0
    if value.get("needs_review", needs_review) is not needs_review:
        raise ValueError("Gemma needs_review flag is inconsistent")
    provider = value.get("provider")
    if not isinstance(provider, dict) or provider.get("id") != PROVIDER_ID:
        raise ValueError("Gemma provider provenance is invalid")
    if value.get("model") != MODEL_NAME:
        raise ValueError("Gemma model provenance is invalid")
    return {
        **value,
        "provider": dict(provider),
        "items": items,
        "raw_item_count": raw_item_count,
        "accepted_item_count": accepted_item_count,
        "rejected_item_count": rejected_item_count,
        "rejected_items": rejected_items,
        "rejected_reason_codes": reason_codes,
        "partial_machine_read": partial_machine_read,
        "needs_review": needs_review,
        **required_safety,
    }


def _validated_item(item: Any, item_index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GemmaItemValidationError("GEMMA_ITEM_NOT_OBJECT")
    item_keys = set(item)
    allowed_keys = set(_RAW_ITEM_KEYS) | {"evidence_id"}
    if not _RAW_ITEM_KEYS.issubset(item_keys) or not item_keys.issubset(allowed_keys):
        raise GemmaItemValidationError("GEMMA_ITEM_SCHEMA_KEYS_INVALID")
    raw_text = item.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise GemmaItemValidationError("GEMMA_ITEM_RAW_TEXT_INVALID")
    numbers = item.get("numbers")
    multiplier_text = item.get("multiplier_text")
    if not isinstance(numbers, str) or not isinstance(multiplier_text, str):
        raise GemmaItemValidationError("GEMMA_ITEM_LITERAL_FIELDS_INVALID")
    layout = item.get("layout_guess")
    if layout not in {"normal", "column", "unclear"}:
        raise GemmaItemValidationError("GEMMA_ITEM_LAYOUT_INVALID")
    continuation = item.get("continuation")
    if continuation not in {"yes", "no", "unclear"}:
        raise GemmaItemValidationError("GEMMA_ITEM_CONTINUATION_INVALID")
    special_text = item.get("special_text")
    if not isinstance(special_text, str):
        raise GemmaItemValidationError("GEMMA_ITEM_SPECIAL_TEXT_INVALID")
    cancelled = item.get("cancelled")
    if cancelled not in {"yes", "no", "unclear"}:
        raise GemmaItemValidationError("GEMMA_ITEM_CANCELLED_INVALID")
    if not isinstance(item.get("uncertain"), bool):
        raise GemmaItemValidationError("GEMMA_ITEM_UNCERTAIN_INVALID")
    uncertain_reason = item.get("uncertain_reason")
    if not isinstance(uncertain_reason, str):
        raise GemmaItemValidationError("GEMMA_ITEM_UNCERTAIN_REASON_INVALID")
    evidence_id = item.get("evidence_id", f"GEMMA-{item_index:04d}")
    if not isinstance(evidence_id, str) or re.fullmatch(r"GEMMA-\d{4}", evidence_id) is None:
        raise GemmaItemValidationError("GEMMA_ITEM_SCHEMA_KEYS_INVALID")
    return {
        "evidence_id": evidence_id,
        "raw_text": raw_text,
        "numbers": numbers,
        "multiplier_text": multiplier_text,
        "layout_guess": layout,
        "continuation": continuation,
        "special_text": special_text,
        "cancelled": cancelled,
        "uncertain": item["uncertain"],
        "uncertain_reason": uncertain_reason,
    }


def default_gemma_shadow_cache_dir() -> Path:
    if os.name == "nt":
        base_value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(base_value) if base_value else Path.home() / "AppData" / "Local"
        return base / "Betguard Assistant" / "vision" / "gemma4-shadow-cache"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "betguard-assistant" / "vision" / "gemma4-shadow-cache"


def compare_multi_model_evidence(
    qwen_result: dict[str, Any],
    ppocr_evidence: dict[str, Any] | None,
    gemma_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare page-level literal multisets without inventing item linkage.

    Gemma has no trustworthy token geometry, so this intentionally performs no
    row, index, or physical-bet matching.  It is a review warning, never a vote.
    """
    claims: dict[str, list[str]] = {}
    qwen_claims = _qwen_literal_number_claims(qwen_result)
    if qwen_claims:
        claims["qwen-dashscope"] = qwen_claims
    if isinstance(ppocr_evidence, dict) and ppocr_evidence.get("status") == "completed":
        pp_claims = sorted(
            str(region.get("text"))
            for region in ppocr_evidence.get("regions", [])
            if isinstance(region, dict) and _is_standalone_number(region.get("text"))
        )
        if pp_claims:
            claims["ppocrv6-shadow"] = pp_claims
    if isinstance(gemma_evidence, dict) and gemma_evidence.get("status") == "completed":
        gemma_claims: list[str] = []
        for item in gemma_evidence.get("items", []):
            if isinstance(item, dict):
                gemma_claims.extend(_literal_numbers(str(item.get("numbers") or "")))
        if gemma_claims:
            claims[PROVIDER_ID] = sorted(gemma_claims)

    if len(claims) < 2:
        classification = "MULTI_MODEL_COMPARISON_UNAVAILABLE"
    else:
        unique = {tuple(values) for values in claims.values()}
        classification = (
            "MULTI_MODEL_AGREEMENT" if len(unique) == 1
            else "MULTI_MODEL_DISAGREEMENT"
        )
    return {
        "schema_version": "betguard.vision.multi-model-evidence-comparison.v1",
        "status": "completed" if len(claims) >= 2 else "unavailable",
        "classification": classification,
        "scope": "page_literal_number_multiset",
        "mapping_policy": "no_row_index_text_or_physical_bet_mapping",
        "claims": claims,
        "authority": "qwen-dashscope",
        "agreement_only": classification == "MULTI_MODEL_AGREEMENT",
        "needs_review": True,
        "evidence_only": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _qwen_literal_number_claims(result: dict[str, Any]) -> list[str]:
    response = ((result.get("preprocessing") or {}).get("qwen_response") or {})
    claims: list[str] = []
    for section in response.get("sections", []):
        if not isinstance(section, dict):
            continue
        for row in section.get("rows", []):
            if not isinstance(row, dict):
                continue
            for group in row.get("numbers", []):
                if isinstance(group, list):
                    claims.extend(
                        str(value) for value in group if _is_standalone_number(value)
                    )
    return sorted(claims)


def _literal_numbers(text: str) -> list[str]:
    return re.findall(r"(?<!\d)(?:0[1-9]|[12]\d|3[0-9])(?!\d)", text)


def _is_standalone_number(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:0[1-9]|[12]\d|3[0-9])", value) is not None


def _request_payload(
    request: RecognitionRequest, image_bytes: bytes, config: GemmaShadowConfig
) -> dict[str, Any]:
    return {
        "contents": [{"parts": [
            {"text": config.prompt},
            {"inline_data": {
                "mime_type": request.mime_type,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }},
        ]}],
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": RAW_READER_RESPONSE_SCHEMA,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }


def _post_generate_content(
    payload: dict[str, Any], api_key: str, config: GemmaShadowConfig
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        config.endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError:
        raise


def _validated_response(envelope: Any, base: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ValueError("Gemma envelope must be an object")
    if envelope.get("modelVersion") not in {None, MODEL_NAME}:
        raise ValueError("Gemma response model is unexpected")
    candidates = envelope.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError("Gemma response must have one candidate")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ValueError("Gemma response candidate is invalid")
    if candidate.get("finishReason") != "STOP":
        raise GemmaFinishError("Gemma response did not finish completely")
    parts = ((candidate.get("content") or {}).get("parts") or [])
    texts = [part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")]
    if len(texts) != 1:
        raise ValueError("Gemma response content is missing or ambiguous")
    decoded = json.loads(texts[0])
    if not isinstance(decoded, dict) or decoded.get("version") != "gemma-raw-reader-v2":
        raise ValueError("Gemma raw-reader root is invalid")
    raw_items = decoded.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Gemma raw-reader items are invalid")
    items: list[dict[str, Any]] = []
    rejected_items: list[dict[str, Any]] = []
    for item_index, item in enumerate(raw_items, 1):
        try:
            items.append(_validated_item(item, item_index))
        except GemmaItemValidationError as exc:
            rejected_items.append(
                {"item_index": item_index, "reason_code": exc.code}
            )
    rejected_reason_codes = list(
        dict.fromkeys(item["reason_code"] for item in rejected_items)
    )
    evidence = {
        **base,
        "status": "completed",
        "items": items,
        "raw_item_count": len(raw_items),
        "accepted_item_count": len(items),
        "rejected_item_count": len(rejected_items),
        "rejected_items": rejected_items,
        "rejected_reason_codes": rejected_reason_codes,
        "partial_machine_read": bool(items and rejected_items),
        "needs_review": bool(rejected_items),
        "raw_response_text": texts[0],
        "provider_raw_response": envelope,
        "provider_request_id": str(envelope.get("responseId") or ""),
        "finish_reason": "STOP",
        "usage": envelope.get("usageMetadata") if isinstance(envelope.get("usageMetadata"), dict) else {},
    }
    return validate_gemma_evidence(evidence)


def _base_evidence(request: RecognitionRequest, config: GemmaShadowConfig) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "provider": {
            "id": PROVIDER_ID,
            "mode": "external_api_shadow",
            "model_name": config.model,
            "model_version": config.model_version,
            "adapter_version": config.adapter_version,
        },
        "request_id": f"{request.request_id}:gemma-shadow",
        "model": config.model,
        "image_sha256": str(request.metadata.get("sha256") or ""),
        "prompt_sha256": config.prompt_sha256,
        "request_schema_version": config.request_schema_version,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "authority": "qwen-dashscope",
        "value_authority": "human_confirmed_answer",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _failure(
    base: dict[str, Any],
    code: str,
    started: float,
    *,
    status: str = "failed",
    detail: str | None = None,
    external_call_count: int = 0,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code}
    if detail:
        error["detail"] = detail[:120]
    return {
        **base,
        "status": status,
        "error": error,
        "cache_hit": False,
        "external_call_count": max(0, min(int(external_call_count), 1)),
        "retry_count": 0,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
