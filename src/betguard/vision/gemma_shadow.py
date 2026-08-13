"""Optional Gemma 4 raw-reader evidence shadow.

The adapter deliberately does not implement ``ImageRecognitionProvider`` and
never returns ``RecognitionResult``.  Qwen remains the only primary vision
authority; this module supplies review-only evidence when explicitly enabled.
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
ADAPTER_VERSION = "betguard.gemma-shadow.v2"
EVIDENCE_SCHEMA_VERSION = "betguard.vision.gemma-shadow-evidence.v2"
CACHE_SCHEMA_VERSION = "betguard.vision.gemma-shadow-cache.v2"
REQUEST_SCHEMA_VERSION = "betguard.vision.gemma-raw-reader-request.v2"
ENABLED_ENV = "BETGUARD_GEMMA_SHADOW_ENABLED"
API_KEY_ENV = "GEMINI_API_KEY"
TIMEOUT_ENV = "BETGUARD_GEMMA_SHADOW_TIMEOUT_SECONDS"
ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

RAW_READER_PROMPT = """Directly inspect only the attached original handwritten betting-slip image.
Faithfully transcribe visible writing from top-left to bottom-right. Preserve line breaks,
leading zeroes, separators, stacked writing, corrections, cancellation marks, and uncertainty.
Do not use betting knowledge or likely patterns to invent missing content.

Return ONLY one JSON object with this exact shape:
{"version":"gemma-raw-reader-v2","items":[{"raw_text":"visible text","numbers":"literal visible numbers/columns or unclear","multiplier_text":"all literal visible rules or none","layout_guess":"normal|column|unclear","continuation":"yes|no|unclear","special_text":"raw text or none","cancelled":"yes|no|unclear","uncertain":true,"uncertain_reason":"reason or none"}]}
This is evidence only. Never claim that an item is confirmed, executable, exportable, or safe to submit."""
PROMPT_SHA256 = hashlib.sha256(RAW_READER_PROMPT.encode("utf-8")).hexdigest()


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
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        return {
            **_failure(base, "GEMMA_SHADOW_NOT_CONFIGURED", started, status="unavailable"),
            "cache_identity": identity.to_dict(),
        }
    try:
        image_bytes = Path(request.image_path).read_bytes()
        payload = _request_payload(request, image_bytes, config)
        envelope = (transport or _post_generate_content)(payload, api_key, config)
        evidence = _validated_response(envelope, base)
    except TimeoutError:
        return _failure(base, "GEMMA_SHADOW_TIMEOUT", started, status="timeout")
    except urllib.error.HTTPError as exc:
        return _failure(base, "GEMMA_SHADOW_HTTP_ERROR", started, detail=str(exc.code))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _failure(base, "GEMMA_SHADOW_INVALID_RESPONSE", started, detail=type(exc).__name__)
    evidence["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
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
    for index, item in enumerate(value["items"], 1):
        if not isinstance(item, dict):
            raise ValueError("Gemma evidence item must be an object")
        raw_text = item.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("Gemma raw_text must be non-empty")
        numbers = item.get("numbers")
        multiplier_text = item.get("multiplier_text")
        if not isinstance(numbers, str) or not isinstance(multiplier_text, str):
            raise ValueError("Gemma literal evidence fields must be strings")
        layout = item.get("layout_guess")
        if layout not in {"normal", "column", "unclear"}:
            raise ValueError("Gemma layout must fail closed")
        continuation = item.get("continuation")
        cancelled = item.get("cancelled")
        if continuation not in {"yes", "no", "unclear"} or cancelled not in {"yes", "no", "unclear"}:
            raise ValueError("Gemma evidence enum is invalid")
        if not isinstance(item.get("uncertain"), bool):
            raise ValueError("Gemma uncertainty must be explicit")
        items.append({
            "evidence_id": f"GEMMA-{index:04d}",
            "raw_text": raw_text,
            "numbers": numbers,
            "multiplier_text": multiplier_text,
            "layout_guess": layout,
            "continuation": continuation,
            "special_text": str(item.get("special_text") or "none"),
            "cancelled": cancelled,
            "uncertain": item["uncertain"],
            "uncertain_reason": str(item.get("uncertain_reason") or "none"),
        })
    provider = value.get("provider")
    if not isinstance(provider, dict) or provider.get("id") != PROVIDER_ID:
        raise ValueError("Gemma provider provenance is invalid")
    if value.get("model") != MODEL_NAME:
        raise ValueError("Gemma model provenance is invalid")
    return {
        **value,
        "provider": dict(provider),
        "items": items,
        **required_safety,
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
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise ValueError("Gemma response did not finish completely")
    parts = ((candidate.get("content") or {}).get("parts") or [])
    texts = [part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")]
    if len(texts) != 1:
        raise ValueError("Gemma response content is missing or ambiguous")
    decoded = json.loads(texts[0])
    if not isinstance(decoded, dict) or decoded.get("version") != "gemma-raw-reader-v2":
        raise ValueError("Gemma raw-reader root is invalid")
    items = decoded.get("items")
    if not isinstance(items, list):
        raise ValueError("Gemma raw-reader items are invalid")
    evidence = {
        **base,
        "status": "completed",
        "items": items,
        "raw_response_text": texts[0],
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
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _failure(
    base: dict[str, Any], code: str, started: float, *, status: str = "failed", detail: str | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code}
    if detail:
        error["detail"] = detail[:120]
    return {
        **base,
        "status": status,
        "error": error,
        "cache_hit": False,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
