"""Local-only diagnostics for Qwen responses that fail schema validation.

These artifacts are deliberately separate from the successful-response cache.
They contain response evidence and safe request identity only; request payloads,
image base64, authorization headers, and API keys are never accepted here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


DIAGNOSTIC_SCHEMA_VERSION = "betguard.vision.qwen-failure-diagnostic.v2"
QWEN_OUTPUT_TRUNCATED = "QWEN_OUTPUT_TRUNCATED"
NESTED_JSON_SALVAGE_MISLEADING_ERROR = (
    "NESTED_JSON_SALVAGE_MISLEADING_ERROR"
)


class QwenFailureDiagnosticStore:
    """Atomically preserve safe schema-failure evidence in user data."""

    def __init__(self, diagnostics_dir: Path | None = None) -> None:
        self.diagnostics_dir = diagnostics_dir or default_qwen_diagnostics_dir()

    def preserve(
        self,
        *,
        request_metadata: Mapping[str, Any],
        cache_identity: Mapping[str, Any],
        response_content: str | None,
        response_metadata: Mapping[str, Any],
        schema_error: Exception,
    ) -> dict[str, Any]:
        """Write one metadata JSON and, when available, the raw model content."""
        created_at = datetime.now(timezone.utc)
        request_id = str(request_metadata.get("request_id") or "unknown-request")
        diagnostic_id = _diagnostic_id(request_id, created_at)
        content = response_content if isinstance(response_content, str) else None
        observation = inspect_json_extraction(content or "")
        usage = _safe_usage(response_metadata.get("usage"))
        classification = _optional_string(
            getattr(schema_error, "classification", None)
        )
        secondary_classifications = _string_list(
            getattr(schema_error, "secondary_classifications", ())
        )

        response_filename: str | None = None
        response_length = len(content) if content is not None else None
        response_sha256 = (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content is not None
            else None
        )

        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        if content is not None:
            response_filename = f"{diagnostic_id}.response.txt"
            _atomic_write(
                self.diagnostics_dir / response_filename,
                content.encode("utf-8"),
            )

        endpoint_url = str(cache_identity.get("endpoint_url") or "")
        record: dict[str, Any] = {
            "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "diagnostic_id": diagnostic_id,
            "created_at": created_at.isoformat(),
            "request_id": request_id,
            "provider_id": "qwen-dashscope",
            "model": str(request_metadata.get("model") or ""),
            "prompt_version": str(request_metadata.get("prompt_version") or ""),
            "prompt_sha256": str(request_metadata.get("prompt_sha256") or ""),
            "task_type": str(request_metadata.get("task_type") or ""),
            "request_schema_version": str(
                request_metadata.get("request_schema_version") or ""
            ),
            "image_sha256": str(cache_identity.get("image_sha256") or ""),
            "effective_crop_sha256": str(
                cache_identity.get("effective_crop_sha256") or ""
            ),
            "crop_box": _json_safe(cache_identity.get("crop_box")),
            "image_variant": str(cache_identity.get("variant") or "none"),
            "mime_type": str(cache_identity.get("mime_type") or ""),
            "max_tokens": _optional_int(cache_identity.get("max_tokens")),
            "endpoint_host": urlsplit(endpoint_url).hostname or "",
            "latency_s": _optional_number(response_metadata.get("latency_s")),
            "attempt_count": _optional_int(response_metadata.get("attempt_count")),
            "retry_count": _optional_int(response_metadata.get("retry_count")),
            "http_status": _optional_int(response_metadata.get("http_status")),
            "finish_reason": _optional_string(response_metadata.get("finish_reason")),
            "usage": usage,
            "response_content_length": response_length,
            "response_content_sha256": response_sha256,
            **observation,
            "schema_error_type": type(schema_error).__name__,
            "schema_error_message": str(schema_error),
            "classification": classification,
            "secondary_classifications": secondary_classifications,
            "response_artifact_filename": response_filename,
        }
        metadata_filename = f"{diagnostic_id}.json"
        _atomic_write(
            self.diagnostics_dir / metadata_filename,
            json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        reference = {
            "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "diagnostic_id": diagnostic_id,
            "request_id": request_id,
            "metadata_filename": metadata_filename,
            "response_artifact_filename": response_filename,
            "finish_reason": record["finish_reason"],
        }
        if classification is not None:
            reference["classification"] = classification
        if secondary_classifications:
            reference["secondary_classifications"] = secondary_classifications
        return reference


def default_qwen_diagnostics_dir() -> Path:
    """Return a user-data path that is always outside the repository."""
    if os.name == "nt":
        base_value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(base_value) if base_value else Path.home() / "AppData" / "Local"
        return base / "Betguard Assistant" / "vision" / "qwen-diagnostics"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "betguard-assistant" / "vision" / "qwen-diagnostics"


def inspect_json_extraction(content: str) -> dict[str, Any]:
    """Observe strict-root and existing first-dict salvage behavior without changing it."""
    strict_obj: Any = None
    strict_ok = False
    strict_error: str | None = None
    strict_error_position: int | None = None
    try:
        strict_obj = json.loads(content)
        strict_ok = True
    except JSONDecodeError as exc:
        strict_error = f"{type(exc).__name__}: {exc}"
        strict_error_position = exc.pos

    salvage_attempted = not strict_ok
    salvage_obj: dict[str, Any] | None = None
    salvage_offset: int | None = None
    if salvage_attempted:
        decoder = JSONDecoder()
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(content[index:])
            except JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                salvage_obj = candidate
                salvage_offset = index
                break

    selected_obj = strict_obj if strict_ok and isinstance(strict_obj, dict) else salvage_obj
    return {
        "strict_json_parse_ok": strict_ok,
        "strict_json_error": strict_error,
        "strict_json_error_position": strict_error_position,
        "strict_json_error_at_end": (
            strict_error_position == len(content)
            if strict_error_position is not None
            else False
        ),
        "strict_root_type": _json_type_name(strict_obj) if strict_ok else "unknown",
        "strict_root_keys": list(strict_obj) if isinstance(strict_obj, dict) else [],
        "salvage_attempted": salvage_attempted,
        "salvage_success": salvage_obj is not None,
        "salvage_offset": salvage_offset,
        "salvaged_root_keys": list(salvage_obj) if salvage_obj is not None else [],
        "sections_presence": _sections_presence(selected_obj),
        "braces_balance": _delimiter_balance(content, "{", "}"),
        "brackets_balance": _delimiter_balance(content, "[", "]"),
    }


def _sections_presence(obj: dict[str, Any] | None) -> str:
    if not isinstance(obj, dict):
        return "unknown"
    if "sections" not in obj:
        return "missing"
    sections = obj["sections"]
    if sections is None:
        return "null"
    if not isinstance(sections, list):
        return "wrong_type"
    return "non_empty" if sections else "empty"


def _safe_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    usage = {str(key): _json_safe(item) for key, item in value.items()}
    image_tokens = value.get("image_tokens")
    if image_tokens is None:
        details = value.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            image_tokens = details.get("image_tokens")
    usage["prompt_tokens"] = _optional_int(value.get("prompt_tokens"))
    usage["completion_tokens"] = _optional_int(value.get("completion_tokens"))
    usage["total_tokens"] = _optional_int(value.get("total_tokens"))
    usage["image_tokens"] = _optional_int(image_tokens)
    return usage


def _delimiter_balance(content: str, opening: str, closing: str) -> int:
    balance = 0
    in_string = False
    escaped = False
    for char in content:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            balance += 1
        elif char == closing:
            balance -= 1
    return balance


def _json_type_name(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item)]


def _diagnostic_id(request_id: str, created_at: datetime) -> str:
    safe_request_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", request_id).strip("._")
    safe_request_id = safe_request_id[:120] or "unknown-request"
    timestamp = created_at.strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{safe_request_id}-{timestamp}"


def _atomic_write(path: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
