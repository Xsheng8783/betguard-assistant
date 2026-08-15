"""Read-only DOM grounding and immutable mapping previews for Gate 3C-2.

This module deliberately stops before browser execution.  It persists only
sanitized DOM structure, trusted adapter profiles, and pointer/hash-only
mapping previews.  Candidate values are loaded from a freshly validated
Gate 3C-0 Prepare artifact and are never copied into a preview record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from betguard.vision.candidate_authority import canonical_json_bytes, canonical_sha256
from betguard.vision.webfill_prepare import WebfillPrepareError, WebfillPrepareStore


OBSERVATION_SCHEMA_VERSION = "vision-webfill-dom-observation-v1"
ADAPTER_PROFILE_SCHEMA_VERSION = "vision-webfill-adapter-target-profile-v1"
REQUEST_SCHEMA_VERSION = "vision-webfill-mapping-preview-request-v1"
PREVIEW_SCHEMA_VERSION = "vision-webfill-mapping-preview-artifact-v1"
EVENT_SCHEMA_VERSION = "vision-webfill-mapping-preview-lifecycle-event-v1"

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_OBSERVATION_ID_RE = re.compile(r"^vwdo-[a-f0-9]{32}$")
_PAGE_INSTANCE_ID_RE = re.compile(r"^vwpi-[a-f0-9]{32}$")
_FORM_ID_RE = re.compile(r"^vwform-[a-f0-9]{32}$")
_FIELD_ID_RE = re.compile(r"^vwf-[a-f0-9]{32}$")
_PROFILE_ID_RE = re.compile(r"^watp-[a-z0-9][a-z0-9._-]{2,63}$")
_PREPARE_ID_RE = re.compile(r"^vwp-[a-f0-9]{32}$")
_CLAIM_ID_RE = re.compile(r"^vqc-[a-f0-9]{32}$")
_CONSUMER_ID_RE = re.compile(r"^vqcns-[a-f0-9]{32}$")
_FENCE_RE = re.compile(r"^vqf-[a-f0-9]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^vwmi-[a-f0-9]{32,128}$")
_PREVIEW_ID_RE = re.compile(r"^vwmp-[a-f0-9]{32}$")
_EVENT_ID_RE = re.compile(r"^vwmpe-[a-f0-9]{32}$")
_HUMAN_BET_ID_RE = re.compile(r"^H-[0-9]{3,}$")
_RULE_ID_RE = re.compile(r"^rule-[a-z0-9][a-z0-9._-]{2,63}$")
_SITE_ID_RE = re.compile(r"^site-[a-z0-9][a-z0-9._-]{2,63}$")

_ALLOWED_SELECTOR_KINDS = {"ID", "NAME", "DATA_TESTID", "CSS_STRUCTURAL"}
_ALLOWED_ACTIONS = {
    "SET_BET_TYPE",
    "SET_NUMBER",
    "SET_MULTIPLIER_RULE",
    "SET_SPECIAL_PLAY",
    "SET_CONTINUATION",
}
_ALLOWED_PLACEHOLDERS = {
    "operation_index",
    "group_index",
    "number_index",
    "multiplier_rule_index",
    "component_index",
}
_MAPPING_STATES = {"MAPPED_UNIQUE", "AMBIGUOUS", "MISSING", "UNSUPPORTED", "BLOCKED"}
_TERMINAL_EVENTS = {
    "AUTHORITY_BLOCKED",
    "EXPIRED",
    "OBSERVATION_STALE",
    "SUPERSEDED",
    "INVALIDATED",
    "REVOKED",
}
_FORBIDDEN_VALUE_KEYS = {
    "bets",
    "bet",
    "bet_type",
    "numbers",
    "number_groups",
    "columns",
    "multiplier",
    "special_play",
    "continuation",
    "logical_plan",
    "logical_operations",
    "dom_values",
    "field_values",
    "input_value",
    "raw_value",
    "replacement_value",
    "candidate_snapshot",
    "preview_state",
    "executable_state",
    "confidence_override",
    "selector_override",
    "cookie",
    "cookies",
    "access_token",
    "session_secret",
    "raw_html",
    "html",
    "script",
    "screenshot",
}
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class WebfillMappingError(Exception):
    """Stable, value-free failure for Gate 3C-2 stores and compiler."""

    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _fail(code: str, message: str, status: int = 422) -> WebfillMappingError:
    return WebfillMappingError(code, message, status)


def _assert_json_safe(value: Any, *, persisted: bool = False, label: str = "value", forbid_values: bool = False) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} contains float", 500 if persisted else 422)
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} is not NFC", 500 if persisted else 422)
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_safe(item, persisted=persisted, label=label, forbid_values=forbid_values)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or unicodedata.normalize("NFC", key) != key:
                raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} key invalid", 500 if persisted else 422)
            if forbid_values and key.lower() in _FORBIDDEN_VALUE_KEYS:
                raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_REQUEST_INVALID", f"{label} contains prohibited semantic field", 500 if persisted else 400)
            _assert_json_safe(item, persisted=persisted, label=label, forbid_values=forbid_values)
        return
    raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} contains non-JSON value", 500 if persisted else 422)


def _require_exact(value: Any, keys: set[str], label: str, *, persisted: bool = False) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} shape invalid", 500 if persisted else 422)
    return value


def _require_hash(value: Any, label: str, *, persisted: bool = False) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} hash invalid", 500 if persisted else 422)
    return value


def _require_timestamp(value: Any, label: str, *, persisted: bool = False) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} timestamp invalid", 500 if persisted else 422) from exc
    if not isinstance(value, str) or "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail("MAPPING_STORE_CORRUPT" if persisted else "MAPPING_SCHEMA_INVALID", f"{label} must be timezone-aware RFC3339", 500 if persisted else 422)
    return value


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail("MAPPING_STORE_CORRUPT", "trusted clock is invalid", 500)
    return value.astimezone(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("MAPPING_STORE_CORRUPT", f"cannot read {path.name}", 500) from exc
    if not isinstance(value, dict):
        raise _fail("MAPPING_STORE_CORRUPT", f"{path.name} root invalid", 500)
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(dict(value))
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise _fail("MAPPING_STORE_CORRUPT", f"immutable record already exists: {path.name}", 500) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _store_lock(root: Path, name: str) -> Iterator[None]:
    key = f"{root.resolve()}::{name}"
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
    if not process_lock.acquire(timeout=5.0):
        raise _fail("MAPPING_STORE_BUSY", f"{name} store is busy", 409)
    stream = None
    try:
        lock_path = root / "locks" / f"{name}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(lock_path, "a+b")
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 5.0
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise _fail("MAPPING_STORE_BUSY", f"{name} store is busy", 409) from exc
                time.sleep(0.01)
        yield
    finally:
        if stream is not None:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            stream.close()
        process_lock.release()


def _selector_safe(kind: Any, text: Any, *, template: bool = False) -> bool:
    if kind not in _ALLOWED_SELECTOR_KINDS or not isinstance(text, str) or not 1 <= len(text) <= 512:
        return False
    lowered = text.lower()
    forbidden = ("xpath", "javascript:", "<script", "onerror", "onclick", "onchange", "http://", "https://")
    if any(token in lowered for token in forbidden):
        return False
    if template:
        fields = re.findall(r"\{([^{}]+)\}", text)
        if "{" in re.sub(r"\{[^{}]+\}", "", text) or "}" in re.sub(r"\{[^{}]+\}", "", text):
            return False
        return set(fields) <= _ALLOWED_PLACEHOLDERS
    return "{" not in text and "}" not in text


def _field_identity_projection(form: Mapping[str, Any], field: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "vision-webfill-dom-field-identity-v1",
        "form_document_order": form["document_order"],
        "form_method_category": form["method_category"],
        "form_structural_marker_hashes": form["structural_marker_hashes"],
        "field_document_order": field["document_order"],
        "tag_name": field["tag_name"],
        "input_type": field["input_type"],
        "label_text": field["label_text"],
        "accessible_name": field["accessible_name"],
        "selector_candidates": field["selector_candidates"],
        "structural_marker_hashes": field["structural_marker_hashes"],
    }


def _form_identity_projection(form: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "vision-webfill-dom-form-identity-v1",
        "document_order": form["document_order"],
        "method_category": form["method_category"],
        "structural_marker_hashes": form["structural_marker_hashes"],
        "ordered_field_identity_hashes": [field["field_identity_hash"] for field in form["fields"]],
    }


def _form_fingerprint_projection(form: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "vision-webfill-dom-form-fingerprint-v1",
        "form_id": form["form_id"],
        "document_order": form["document_order"],
        "method_category": form["method_category"],
        "structural_marker_hashes": form["structural_marker_hashes"],
        "fields": [
            {
                "field_id": field["field_id"],
                "document_order": field["document_order"],
                "field_identity_hash": field["field_identity_hash"],
                "tag_name": field["tag_name"],
                "input_type": field["input_type"],
                "label_text": field["label_text"],
                "accessible_name": field["accessible_name"],
                "selector_candidates": field["selector_candidates"],
                "structural_marker_hashes": field["structural_marker_hashes"],
                "visible": field["visible"],
                "disabled": field["disabled"],
                "readonly": field["readonly"],
                "occupancy": field["occupancy"],
            }
            for field in form["fields"]
        ],
    }


def _page_fingerprint_projection(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "vision-webfill-dom-page-fingerprint-v1",
        "page_fingerprint_algorithm": observation["page_fingerprint_algorithm"],
        "page_identity": observation["page_identity"],
        "ordered_forms": [
            {"form_id": form["form_id"], "document_order": form["document_order"], "form_fingerprint": form["form_fingerprint"]}
            for form in observation["forms"]
        ],
    }


class WebfillDomObservationStore:
    """Immutable sanitized DOM observations with a current-page pointer."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self._records = self.base_dir / "dom-observations"
        self._commits = self.base_dir / "dom-observation-commits"
        self._active = self.base_dir / "dom-observation-active"
        for directory in (self._records, self._commits, self._active, self.base_dir / "locks"):
            directory.mkdir(parents=True, exist_ok=True)

    def register_observation(self, observation: Mapping[str, Any], *, activate: bool = True) -> dict[str, Any]:
        value = deepcopy(dict(observation)) if isinstance(observation, Mapping) else None
        self._validate(value)
        with _store_lock(self.base_dir, "dom-observations"):
            path = self._records / f"{value['dom_observation_id']}.json"
            commit_path = self._commits / f"{value['dom_observation_id']}.json"
            commit = {"dom_observation_id": value["dom_observation_id"], "observation_hash": value["observation_hash"]}
            if path.exists():
                if _read(path) != value:
                    raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation immutable identity conflict", 409)
                if commit_path.exists() and _read(commit_path) != commit:
                    raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation commit conflict", 409)
                if not commit_path.exists():
                    _write_immutable(commit_path, commit)
            elif commit_path.exists():
                raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation commit lacks immutable record", 500)
            else:
                _write_immutable(path, value)
                _write_immutable(commit_path, commit)
            if activate:
                active_path = self._active / f"{value['page_identity']['site_id']}.json"
                if active_path.exists():
                    active_pointer = _read(active_path)
                    current = self._get_locked(active_pointer.get("dom_observation_id"), require_current=False)
                    if current["dom_observation_id"] != value["dom_observation_id"]:
                        current_time = datetime.fromisoformat(current["observed_at"])
                        new_time = datetime.fromisoformat(value["observed_at"])
                        same_instance = current["page_instance_id"] == value["page_instance_id"]
                        if new_time <= current_time or (same_instance and value["navigation_epoch"] <= current["navigation_epoch"]):
                            raise _fail("MAPPING_OBSERVATION_STALE", "older observation cannot replace current page authority", 409)
                pointer = {
                    "schema_version": "vision-webfill-dom-observation-active-v1",
                    "site_id": value["page_identity"]["site_id"],
                    "dom_observation_id": value["dom_observation_id"],
                    "observation_hash": value["observation_hash"],
                    "page_instance_id": value["page_instance_id"],
                    "navigation_epoch": value["navigation_epoch"],
                    "page_fingerprint": value["page_fingerprint"],
                }
                _write_atomic(self._active / f"{value['page_identity']['site_id']}.json", pointer)
        return deepcopy(value)

    def get_observation(self, observation_id: str, *, require_current: bool = True) -> dict[str, Any]:
        with _store_lock(self.base_dir, "dom-observations"):
            return self._get_locked(observation_id, require_current=require_current)

    def get_current_observation(self, site_id: str) -> dict[str, Any]:
        with _store_lock(self.base_dir, "dom-observations"):
            if not isinstance(site_id, str) or not _SITE_ID_RE.fullmatch(site_id):
                raise _fail("MAPPING_OBSERVATION_NOT_FOUND", "site identity invalid", 404)
            path = self._active / f"{site_id}.json"
            if not path.exists():
                raise _fail("MAPPING_OBSERVATION_NOT_FOUND", "current observation not found", 404)
            pointer = _read(path)
            expected = {"schema_version", "site_id", "dom_observation_id", "observation_hash", "page_instance_id", "navigation_epoch", "page_fingerprint"}
            if set(pointer) != expected or pointer.get("schema_version") != "vision-webfill-dom-observation-active-v1" or pointer.get("site_id") != site_id:
                raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "current observation pointer invalid", 500)
            observation = self._get_locked(pointer["dom_observation_id"], require_current=False)
            expected_pointer = {
                "schema_version": "vision-webfill-dom-observation-active-v1",
                "site_id": site_id,
                "dom_observation_id": observation["dom_observation_id"],
                "observation_hash": observation["observation_hash"],
                "page_instance_id": observation["page_instance_id"],
                "navigation_epoch": observation["navigation_epoch"],
                "page_fingerprint": observation["page_fingerprint"],
            }
            if pointer != expected_pointer:
                raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "current observation pointer mismatch", 500)
            return observation

    @contextmanager
    def coordination(self) -> Iterator["_ObservationCoordinator"]:
        with _store_lock(self.base_dir, "dom-observations"):
            yield _ObservationCoordinator(self)

    def _get_locked(self, observation_id: str, *, require_current: bool) -> dict[str, Any]:
        if not isinstance(observation_id, str) or not _OBSERVATION_ID_RE.fullmatch(observation_id):
            raise _fail("MAPPING_OBSERVATION_NOT_FOUND", "observation identity invalid", 404)
        path = self._records / f"{observation_id}.json"
        commit_path = self._commits / f"{observation_id}.json"
        if not path.exists() or not commit_path.exists():
            raise _fail("MAPPING_OBSERVATION_NOT_FOUND", "committed observation not found", 404)
        value = _read(path)
        self._validate(value, persisted=True)
        if _read(commit_path) != {"dom_observation_id": observation_id, "observation_hash": value["observation_hash"]}:
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation commit mismatch", 500)
        if require_current:
            pointer_path = self._active / f"{value['page_identity']['site_id']}.json"
            if not pointer_path.exists():
                raise _fail("MAPPING_OBSERVATION_STALE", "observation is not current", 409)
            pointer = _read(pointer_path)
            if pointer.get("dom_observation_id") != observation_id or pointer.get("observation_hash") != value["observation_hash"] or pointer.get("page_instance_id") != value["page_instance_id"] or pointer.get("navigation_epoch") != value["navigation_epoch"] or pointer.get("page_fingerprint") != value["page_fingerprint"]:
                raise _fail("MAPPING_OBSERVATION_STALE", "observation is not current", 409)
        return deepcopy(value)

    @staticmethod
    def _validate(observation: Any, *, persisted: bool = False) -> None:
        root = _require_exact(
            observation,
            {"schema_version", "dom_observation_id", "observed_at", "page_instance_id", "navigation_epoch", "page_identity", "forms", "page_fingerprint_algorithm", "page_fingerprint", "observation_hash", "safety"},
            "DOM observation",
            persisted=persisted,
        )
        _assert_json_safe(root, persisted=persisted, label="DOM observation", forbid_values=True)
        if root.get("schema_version") != OBSERVATION_SCHEMA_VERSION or not isinstance(root.get("dom_observation_id"), str) or not _OBSERVATION_ID_RE.fullmatch(root["dom_observation_id"]) or not isinstance(root.get("page_instance_id"), str) or not _PAGE_INSTANCE_ID_RE.fullmatch(root["page_instance_id"]):
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation identity invalid", 500 if persisted else 422)
        _require_timestamp(root["observed_at"], "observation", persisted=persisted)
        if isinstance(root["navigation_epoch"], bool) or not isinstance(root["navigation_epoch"], int) or root["navigation_epoch"] < 1 or root["page_fingerprint_algorithm"] != "sha256-canonical-dom-structure-v1":
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation navigation/fingerprint contract invalid", 500 if persisted else 422)
        page = _require_exact(root["page_identity"], {"site_id", "game", "origin_identity_hash", "normalized_path_identity_hash", "structural_marker_hashes"}, "page identity", persisted=persisted)
        if not isinstance(page["site_id"], str) or not _SITE_ID_RE.fullmatch(page["site_id"]) or not isinstance(page["game"], str) or not page["game"]:
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "page identity invalid", 500 if persisted else 422)
        _require_hash(page["origin_identity_hash"], "origin", persisted=persisted)
        _require_hash(page["normalized_path_identity_hash"], "path", persisted=persisted)
        if not isinstance(page["structural_marker_hashes"], list) or not page["structural_marker_hashes"] or len(page["structural_marker_hashes"]) != len(set(page["structural_marker_hashes"])):
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "page markers invalid", 500 if persisted else 422)
        for marker in page["structural_marker_hashes"]:
            _require_hash(marker, "page marker", persisted=persisted)
        if not isinstance(root["forms"], list) or not root["forms"]:
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "forms invalid", 500 if persisted else 422)
        seen_forms: set[str] = set()
        seen_fields: set[str] = set()
        for form_position, form in enumerate(root["forms"], 1):
            form = _require_exact(form, {"form_id", "document_order", "method_category", "structural_marker_hashes", "fields", "form_fingerprint"}, "form", persisted=persisted)
            if form["document_order"] != form_position or form["method_category"] not in {"GET", "POST", "DIALOG", "UNKNOWN"} or not isinstance(form["fields"], list) or not form["fields"]:
                raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "form ordering/shape invalid", 500 if persisted else 422)
            if not isinstance(form["structural_marker_hashes"], list) or not form["structural_marker_hashes"] or len(form["structural_marker_hashes"]) != len(set(form["structural_marker_hashes"])):
                raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "form markers invalid", 500 if persisted else 422)
            for marker in form["structural_marker_hashes"]:
                _require_hash(marker, "form marker", persisted=persisted)
            for field_position, field in enumerate(form["fields"], 1):
                field = _require_exact(field, {"field_id", "document_order", "field_identity_hash", "tag_name", "input_type", "label_text", "accessible_name", "selector_candidates", "structural_marker_hashes", "visible", "disabled", "readonly", "occupancy"}, "field", persisted=persisted)
                if field["document_order"] != field_position or field["tag_name"] not in {"input", "select", "textarea", "button", "fieldset", "div"} or field["input_type"] not in {"text", "number", "radio", "checkbox", "select-one", "select-multiple", "button", "other"}:
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field ordering/type invalid", 500 if persisted else 422)
                if field["label_text"] is not None and (not isinstance(field["label_text"], str) or len(field["label_text"]) > 256):
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field label invalid", 500 if persisted else 422)
                if field["accessible_name"] is not None and (not isinstance(field["accessible_name"], str) or len(field["accessible_name"]) > 256):
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field accessible name invalid", 500 if persisted else 422)
                if not isinstance(field["selector_candidates"], list) or not field["selector_candidates"]:
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "selector candidates invalid", 500 if persisted else 422)
                selectors: list[tuple[str, str]] = []
                for selector in field["selector_candidates"]:
                    selector = _require_exact(selector, {"selector_kind", "selector_text", "structural_only"}, "selector", persisted=persisted)
                    if selector["structural_only"] is not True or not _selector_safe(selector["selector_kind"], selector["selector_text"]):
                        raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "selector is not structural-only", 500 if persisted else 422)
                    selectors.append((selector["selector_kind"], selector["selector_text"]))
                if len(selectors) != len(set(selectors)) or not isinstance(field["structural_marker_hashes"], list) or len(field["structural_marker_hashes"]) != len(set(field["structural_marker_hashes"])):
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field structural evidence invalid", 500 if persisted else 422)
                for marker in field["structural_marker_hashes"]:
                    _require_hash(marker, "field marker", persisted=persisted)
                if any(not isinstance(field[key], bool) for key in ("visible", "disabled", "readonly")) or field["occupancy"] not in {"EMPTY", "NONEMPTY", "UNKNOWN"}:
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field state invalid", 500 if persisted else 422)
                expected_field_hash = canonical_sha256(_field_identity_projection(form, field))
                if field["field_identity_hash"] != expected_field_hash or field["field_id"] != f"vwf-{expected_field_hash[:32]}" or field["field_id"] in seen_fields:
                    raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "field identity hash mismatch or duplicate", 500 if persisted else 422)
                seen_fields.add(field["field_id"])
            expected_form_hash = canonical_sha256(_form_identity_projection(form))
            if form["form_id"] != f"vwform-{expected_form_hash[:32]}" or form["form_id"] in seen_forms or form["form_fingerprint"] != canonical_sha256(_form_fingerprint_projection(form)):
                raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "form identity/fingerprint mismatch", 500 if persisted else 422)
            seen_forms.add(form["form_id"])
        expected_page_hash = canonical_sha256(_page_fingerprint_projection(root))
        if root["page_fingerprint"] != expected_page_hash:
            raise _fail("MAPPING_PAGE_IDENTITY_MISMATCH", "page fingerprint mismatch", 500 if persisted else 422)
        safety = root["safety"]
        required_safety = {
            "read_only_observation": True,
            "occupancy_class_derived": True,
            "raw_input_value_content_exposed": False,
            "raw_input_value_content_persisted": False,
            "query_or_fragment_persisted": False,
            "cookies_or_storage_read": False,
            "html_or_script_persisted": False,
            "dom_mutation_performed": False,
            "click_performed": False,
            "typing_performed": False,
            "events_dispatched": False,
            "navigation_performed": False,
            "submit_performed": False,
        }
        if safety != required_safety:
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation safety invalid", 500 if persisted else 422)
        expected_observation_hash = canonical_sha256({key: value for key, value in root.items() if key != "observation_hash"})
        if root["observation_hash"] != expected_observation_hash:
            raise _fail("MAPPING_OBSERVATION_INTEGRITY_INVALID", "observation hash mismatch", 500 if persisted else 422)


class _ObservationCoordinator:
    def __init__(self, store: WebfillDomObservationStore) -> None:
        self._store = store

    def get_locked(self, observation_id: str, *, require_current: bool = True) -> dict[str, Any]:
        return self._store._get_locked(observation_id, require_current=require_current)


class WebfillAdapterTargetProfileStore:
    """Trusted immutable adapter profiles with append-only activation evidence."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self._profiles = self.base_dir / "adapter-target-profiles"
        self._commits = self.base_dir / "adapter-target-profile-commits"
        self._active = self.base_dir / "adapter-target-profile-active-version"
        self._events = self.base_dir / "adapter-target-profile-lifecycle-events"
        for directory in (self._profiles, self._commits, self._active, self._events, self.base_dir / "locks"):
            directory.mkdir(parents=True, exist_ok=True)

    def register_profile(self, profile: Mapping[str, Any], *, activate: bool = False) -> dict[str, Any]:
        value = deepcopy(dict(profile)) if isinstance(profile, Mapping) else None
        self._validate(value)
        with _store_lock(self.base_dir, "adapter-profiles"):
            path = self._profile_path(value["adapter_target_profile_id"], value["adapter_target_profile_version"])
            commit_path = self._commits / value["adapter_target_profile_id"] / f"{value['adapter_target_profile_version']:08d}.json"
            commit = {"adapter_target_profile_id": value["adapter_target_profile_id"], "adapter_target_profile_version": value["adapter_target_profile_version"], "profile_integrity_hash": value["profile_integrity_hash"]}
            if path.exists():
                existing = _read(path)
                self._validate(existing, persisted=True)
                if existing != value:
                    raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile immutable version conflict", 409)
                if commit_path.exists() and _read(commit_path) != commit:
                    raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile commit conflict", 409)
                if not commit_path.exists():
                    _write_immutable(commit_path, commit)
            elif commit_path.exists():
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile commit lacks immutable record", 500)
            else:
                _write_immutable(path, value)
                _write_immutable(commit_path, commit)
            if not self._profile_event_exists_locked(value["adapter_target_profile_id"], "REGISTERED", value["adapter_target_profile_version"]):
                self._append_profile_event_locked(value, "REGISTERED", None)
            if activate:
                self._activate_locked(value["adapter_target_profile_id"], value["adapter_target_profile_version"])
        return deepcopy(value)

    def activate_version(self, profile_id: str, version: int) -> dict[str, Any]:
        with _store_lock(self.base_dir, "adapter-profiles"):
            return self._activate_locked(profile_id, version)

    def get_profile(self, profile_id: str, version: int, *, require_active: bool = True) -> dict[str, Any]:
        with _store_lock(self.base_dir, "adapter-profiles"):
            return self._get_locked(profile_id, version, require_active=require_active)

    def get_active_profile(self, profile_id: str) -> dict[str, Any]:
        with _store_lock(self.base_dir, "adapter-profiles"):
            return self._get_active_locked(profile_id)

    def get_lifecycle_events(self, profile_id: str) -> list[dict[str, Any]]:
        with _store_lock(self.base_dir, "adapter-profiles"):
            self._require_profile_id(profile_id)
            directory = self._events / profile_id
            events = [_read(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []
            for index, event in enumerate(events, 1):
                expected = {"schema_version", "event_sequence", "event_type", "adapter_target_profile_id", "adapter_target_profile_version", "profile_integrity_hash", "occurred_at", "superseded_version", "event_integrity_hash"}
                if set(event) != expected or event.get("schema_version") != "vision-webfill-adapter-target-profile-lifecycle-v1" or event.get("event_sequence") != index or event.get("adapter_target_profile_id") != profile_id:
                    raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile lifecycle invalid", 500)
                integrity = event["event_integrity_hash"]
                _require_hash(integrity, "adapter profile event", persisted=True)
                if canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"}) != integrity:
                    raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile event hash mismatch", 500)
            return deepcopy(events)

    @contextmanager
    def coordination(self) -> Iterator["_AdapterProfileCoordinator"]:
        with _store_lock(self.base_dir, "adapter-profiles"):
            yield _AdapterProfileCoordinator(self)

    def _profile_path(self, profile_id: str, version: int) -> Path:
        return self._profiles / profile_id / f"{version:08d}.json"

    @staticmethod
    def _require_profile_id(profile_id: Any) -> None:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise _fail("MAPPING_TARGET_PROFILE_NOT_FOUND", "adapter profile identity invalid", 404)

    def _get_locked(self, profile_id: str, version: int, *, require_active: bool) -> dict[str, Any]:
        self._require_profile_id(profile_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise _fail("MAPPING_TARGET_PROFILE_NOT_FOUND", "adapter profile version invalid", 404)
        path = self._profile_path(profile_id, version)
        commit_path = self._commits / profile_id / f"{version:08d}.json"
        if not path.exists() or not commit_path.exists():
            raise _fail("MAPPING_TARGET_PROFILE_NOT_FOUND", "adapter profile not found", 404)
        profile = _read(path)
        self._validate(profile, persisted=True)
        commit = {"adapter_target_profile_id": profile_id, "adapter_target_profile_version": version, "profile_integrity_hash": profile["profile_integrity_hash"]}
        if _read(commit_path) != commit:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile commit mismatch", 500)
        if require_active:
            active = self._get_active_locked(profile_id)
            if active["adapter_target_profile_version"] != version or active["profile_integrity_hash"] != profile["profile_integrity_hash"]:
                raise _fail("MAPPING_TARGET_PROFILE_SUPERSEDED", "adapter profile is not active", 409)
        return deepcopy(profile)

    def _get_active_locked(self, profile_id: str) -> dict[str, Any]:
        self._require_profile_id(profile_id)
        path = self._active / f"{profile_id}.json"
        if not path.exists():
            raise _fail("MAPPING_TARGET_PROFILE_NOT_FOUND", "active adapter profile not found", 404)
        pointer = _read(path)
        expected = {"schema_version", "adapter_target_profile_id", "adapter_target_profile_version", "profile_integrity_hash"}
        if set(pointer) != expected or pointer.get("schema_version") != "vision-webfill-adapter-target-profile-active-v1" or pointer.get("adapter_target_profile_id") != profile_id:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "active adapter profile pointer invalid", 500)
        profile = self._get_locked(profile_id, pointer.get("adapter_target_profile_version"), require_active=False)
        if pointer["profile_integrity_hash"] != profile["profile_integrity_hash"]:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "active adapter profile pointer hash mismatch", 500)
        return profile

    def _activate_locked(self, profile_id: str, version: int) -> dict[str, Any]:
        profile = self._get_locked(profile_id, version, require_active=False)
        pointer_path = self._active / f"{profile_id}.json"
        previous = None
        if pointer_path.exists():
            pointer = _read(pointer_path)
            if pointer.get("adapter_target_profile_version") == version and pointer.get("profile_integrity_hash") == profile["profile_integrity_hash"]:
                if not self._profile_event_exists_locked(profile_id, "ACTIVATED", version):
                    self._append_profile_event_locked(profile, "ACTIVATED", None)
                return deepcopy(pointer)
            previous = pointer.get("adapter_target_profile_version")
            if isinstance(previous, bool) or not isinstance(previous, int) or version <= previous:
                raise _fail("MAPPING_TARGET_PROFILE_SUPERSEDED", "adapter profile activation must advance monotonically", 409)
        pointer = {
            "schema_version": "vision-webfill-adapter-target-profile-active-v1",
            "adapter_target_profile_id": profile_id,
            "adapter_target_profile_version": version,
            "profile_integrity_hash": profile["profile_integrity_hash"],
        }
        _write_atomic(pointer_path, pointer)
        self._append_profile_event_locked(profile, "ACTIVATED", previous)
        return deepcopy(pointer)

    def _profile_event_exists_locked(self, profile_id: str, event_type: str, version: int) -> bool:
        directory = self._events / profile_id
        if not directory.exists():
            return False
        for path in sorted(directory.glob("*.json")):
            event = _read(path)
            if event.get("event_type") == event_type and event.get("adapter_target_profile_version") == version:
                return True
        return False

    def _append_profile_event_locked(self, profile: Mapping[str, Any], event_type: str, previous: int | None) -> None:
        directory = self._events / profile["adapter_target_profile_id"]
        sequence = len(list(directory.glob("*.json"))) + 1 if directory.exists() else 1
        event = {
            "schema_version": "vision-webfill-adapter-target-profile-lifecycle-v1",
            "event_sequence": sequence,
            "event_type": event_type,
            "adapter_target_profile_id": profile["adapter_target_profile_id"],
            "adapter_target_profile_version": profile["adapter_target_profile_version"],
            "profile_integrity_hash": profile["profile_integrity_hash"],
            "occurred_at": profile["created_at"],
            "superseded_version": previous,
            "event_integrity_hash": "",
        }
        event["event_integrity_hash"] = canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"})
        _write_immutable(directory / f"{sequence:06d}.json", event)

    @staticmethod
    def _validate(profile: Any, *, persisted: bool = False) -> None:
        root = _require_exact(profile, {"schema_version", "adapter_target_profile_id", "adapter_target_profile_version", "logical_target_profile_identity", "site_contract", "form_contract", "logical_field_rules", "created_at", "profile_integrity_hash", "safety"}, "adapter target profile", persisted=persisted)
        _assert_json_safe(root, persisted=persisted, label="adapter target profile", forbid_values=True)
        if root.get("schema_version") != ADAPTER_PROFILE_SCHEMA_VERSION or not isinstance(root.get("adapter_target_profile_id"), str) or not _PROFILE_ID_RE.fullmatch(root["adapter_target_profile_id"]) or isinstance(root.get("adapter_target_profile_version"), bool) or not isinstance(root.get("adapter_target_profile_version"), int) or root["adapter_target_profile_version"] < 1:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile identity invalid", 500 if persisted else 422)
        _require_timestamp(root["created_at"], "adapter profile", persisted=persisted)
        logical = _require_exact(root["logical_target_profile_identity"], {"target_profile_id", "target_profile_version", "profile_integrity_hash"}, "logical profile identity", persisted=persisted)
        if not isinstance(logical["target_profile_id"], str) or not re.fullmatch(r"^wtp-[a-z0-9][a-z0-9._-]{2,63}$", logical["target_profile_id"]) or isinstance(logical["target_profile_version"], bool) or not isinstance(logical["target_profile_version"], int) or logical["target_profile_version"] < 1:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "logical profile identity invalid", 500 if persisted else 422)
        _require_hash(logical["profile_integrity_hash"], "logical profile", persisted=persisted)
        site = _require_exact(root["site_contract"], {"site_id", "game", "origin_identity_hash", "normalized_path_identity_hash", "page_fingerprint_algorithm", "expected_page_fingerprint", "required_page_marker_hashes"}, "site contract", persisted=persisted)
        if not isinstance(site["site_id"], str) or not _SITE_ID_RE.fullmatch(site["site_id"]) or not isinstance(site["game"], str) or not site["game"] or site["page_fingerprint_algorithm"] != "sha256-canonical-dom-structure-v1":
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "site contract invalid", 500 if persisted else 422)
        for key in ("origin_identity_hash", "normalized_path_identity_hash", "expected_page_fingerprint"):
            _require_hash(site[key], key, persisted=persisted)
        if not isinstance(site["required_page_marker_hashes"], list) or not site["required_page_marker_hashes"] or len(site["required_page_marker_hashes"]) != len(set(site["required_page_marker_hashes"])):
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "page marker contract invalid", 500 if persisted else 422)
        for marker in site["required_page_marker_hashes"]:
            _require_hash(marker, "required page marker", persisted=persisted)
        form = _require_exact(root["form_contract"], {"expected_form_id", "expected_form_fingerprint", "required_form_marker_hashes", "exact_form_match"}, "form contract", persisted=persisted)
        if not isinstance(form["expected_form_id"], str) or not _FORM_ID_RE.fullmatch(form["expected_form_id"]) or form["exact_form_match"] is not True:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "form contract invalid", 500 if persisted else 422)
        _require_hash(form["expected_form_fingerprint"], "expected form", persisted=persisted)
        if not isinstance(form["required_form_marker_hashes"], list) or not form["required_form_marker_hashes"] or len(form["required_form_marker_hashes"]) != len(set(form["required_form_marker_hashes"])):
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "form markers invalid", 500 if persisted else 422)
        for marker in form["required_form_marker_hashes"]:
            _require_hash(marker, "required form marker", persisted=persisted)
        rules = root["logical_field_rules"]
        if not isinstance(rules, list) or len(rules) < 4:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter mapping rules incomplete", 500 if persisted else 422)
        seen_rule_ids: set[str] = set()
        for rule in rules:
            rule = _require_exact(rule, {"rule_id", "logical_action", "applicable_bet_types", "source_scope", "selector_kind", "selector_template", "allowed_placeholders", "component_policy", "required_component_count", "cardinality", "expected_field", "lossless_required"}, "adapter rule", persisted=persisted)
            if not isinstance(rule["rule_id"], str) or not _RULE_ID_RE.fullmatch(rule["rule_id"]) or rule["rule_id"] in seen_rule_ids or rule["logical_action"] not in _ALLOWED_ACTIONS or rule["source_scope"] not in {"BET", "GROUP", "NUMBER", "MULTIPLIER_RULE", "SPECIAL_PLAY", "CONTINUATION"}:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter rule identity/scope invalid", 500 if persisted else 422)
            seen_rule_ids.add(rule["rule_id"])
            if not isinstance(rule["applicable_bet_types"], list) or not rule["applicable_bet_types"] or len(rule["applicable_bet_types"]) != len(set(rule["applicable_bet_types"])) or not set(rule["applicable_bet_types"]) <= {"normal", "column"}:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter rule bet types invalid", 500 if persisted else 422)
            if not _selector_safe(rule["selector_kind"], rule["selector_template"], template=True) or not isinstance(rule["allowed_placeholders"], list) or len(rule["allowed_placeholders"]) != len(set(rule["allowed_placeholders"])) or not set(rule["allowed_placeholders"]) <= _ALLOWED_PLACEHOLDERS:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter selector template invalid", 500 if persisted else 422)
            placeholders = set(re.findall(r"\{([^{}]+)\}", rule["selector_template"]))
            if placeholders != set(rule["allowed_placeholders"]):
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter placeholder declaration mismatch", 500 if persisted else 422)
            if rule["component_policy"] not in {"SINGLE_FIELD", "ORDERED_LOSSLESS_COMPONENTS"} or isinstance(rule["required_component_count"], bool) or not isinstance(rule["required_component_count"], int) or rule["required_component_count"] < 1 or rule["cardinality"] != "EXACTLY_ONE_PER_COMPONENT" or rule["lossless_required"] is not True:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter component policy invalid", 500 if persisted else 422)
            if rule["component_policy"] == "SINGLE_FIELD" and rule["required_component_count"] != 1:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "single-field rule cardinality invalid", 500 if persisted else 422)
            expected_field = _require_exact(rule["expected_field"], {"tag_name", "input_types", "label_text", "required_structural_marker_hashes", "must_be_visible", "must_be_enabled", "must_be_writable", "must_be_empty"}, "expected field", persisted=persisted)
            if expected_field["tag_name"] not in {"input", "select", "textarea", "button", "fieldset", "div"} or not isinstance(expected_field["input_types"], list) or not expected_field["input_types"] or len(expected_field["input_types"]) != len(set(expected_field["input_types"])) or not set(expected_field["input_types"]) <= {"text", "number", "radio", "checkbox", "select-one", "select-multiple", "button", "other"}:
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "expected field type invalid", 500 if persisted else 422)
            if expected_field["label_text"] is not None and (not isinstance(expected_field["label_text"], str) or len(expected_field["label_text"]) > 256):
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "expected field label invalid", 500 if persisted else 422)
            if not isinstance(expected_field["required_structural_marker_hashes"], list) or len(expected_field["required_structural_marker_hashes"]) != len(set(expected_field["required_structural_marker_hashes"])):
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "expected field markers invalid", 500 if persisted else 422)
            for marker in expected_field["required_structural_marker_hashes"]:
                _require_hash(marker, "expected field marker", persisted=persisted)
            if any(expected_field[key] is not True for key in ("must_be_visible", "must_be_enabled", "must_be_writable", "must_be_empty")):
                raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "expected field safety invalid", 500 if persisted else 422)
        required_actions = {"SET_BET_TYPE", "SET_NUMBER", "SET_MULTIPLIER_RULE", "SET_SPECIAL_PLAY"}
        if not required_actions <= {rule["logical_action"] for rule in rules}:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "required mapping action rules missing", 500 if persisted else 422)
        safety = {
            "contains_bet_values": False,
            "logical_profile_extension_only": True,
            "read_only_mapping_only": True,
            "selector_values_are_structural_only": True,
            "browser_execution_authorized": False,
            "dom_mutation_authorized": False,
            "fill_authorized": False,
            "submit_authorized": False,
        }
        if root["safety"] != safety:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile safety invalid", 500 if persisted else 422)
        _require_hash(root["profile_integrity_hash"], "adapter profile", persisted=persisted)
        if canonical_sha256({key: value for key, value in root.items() if key != "profile_integrity_hash"}) != root["profile_integrity_hash"]:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile hash mismatch", 500 if persisted else 422)


class _AdapterProfileCoordinator:
    def __init__(self, store: WebfillAdapterTargetProfileStore) -> None:
        self._store = store

    def get_locked(self, profile_id: str, version: int, *, require_active: bool = True) -> dict[str, Any]:
        return self._store._get_locked(profile_id, version, require_active=require_active)

    def get_active_locked(self, profile_id: str) -> dict[str, Any]:
        return self._store._get_active_locked(profile_id)


class WebfillMappingPreviewStore:
    """Immutable deterministic mapping previews and read-only Human DTOs."""

    def __init__(
        self,
        base_dir: Path | str,
        prepare_store: WebfillPrepareStore,
        observation_store: WebfillDomObservationStore,
        adapter_profile_store: WebfillAdapterTargetProfileStore,
        *,
        clock: Callable[[], datetime] | None = None,
        administrative_principal_validator: Callable[[str], bool] | None = None,
    ) -> None:
        if not isinstance(prepare_store, WebfillPrepareStore):
            raise TypeError("prepare_store must be WebfillPrepareStore")
        if not isinstance(observation_store, WebfillDomObservationStore):
            raise TypeError("observation_store must be WebfillDomObservationStore")
        if not isinstance(adapter_profile_store, WebfillAdapterTargetProfileStore):
            raise TypeError("adapter_profile_store must be WebfillAdapterTargetProfileStore")
        self.base_dir = Path(base_dir)
        self._prepare = prepare_store
        self._observations = observation_store
        self._profiles = adapter_profile_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._admin_validator = administrative_principal_validator
        self._previews = self.base_dir / "mapping-previews"
        self._commits = self.base_dir / "mapping-preview-commits"
        self._events = self.base_dir / "lifecycle-events"
        self._idempotency = self.base_dir / "idempotency"
        self._tuples = self.base_dir / "authority-tuple-index"
        self._transactions = self.base_dir / "preview-transactions"
        self._lifecycle_transactions = self.base_dir / "lifecycle-transactions"
        for directory in (self._previews, self._commits, self._events, self._idempotency, self._tuples, self._transactions, self._lifecycle_transactions, self.base_dir / "locks"):
            directory.mkdir(parents=True, exist_ok=True)

    def create_mapping_preview(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        request = self._decode_request(payload)
        self._require_owner(request, authenticated_principal, consumer_id, server_session_id)
        with _store_lock(self.base_dir, "mapping-previews"):
            self._preflight_all_transactions()
            try:
                with self._prepare.mapping_coordination(preflight_callback=self._preflight_all_transactions) as prepare:
                    with self._profiles.coordination() as profiles:
                        with self._observations.coordination() as observations:
                            prepared = self._get_prepared_locked(prepare, request, authenticated_principal, consumer_id, server_session_id)
                            if prepared["status"] != "VALID_PREPARED" or prepared["state"] != "PREPARED":
                                raise _fail("MAPPING_PREPARE_INVALID", "Prepare authority is not PREPARED", 409)
                            profile = profiles.get_locked(request["adapter_target_profile_id"], request["adapter_target_profile_version"], require_active=True)
                            observation = observations.get_locked(request["dom_observation_id"], require_current=True)
                            self._assert_request_authority(request, prepared["artifact"], profile, observation, authenticated_principal, consumer_id, server_session_id)
                            compiled = self._compile(prepared["artifact"], profile, observation)
                            artifact = self._build_artifact(request, prepared["artifact"], profile, observation, compiled, authenticated_principal, consumer_id)
                            pending_path = self._transaction_path(request["idempotency_key"])
                            if pending_path.exists():
                                transaction = _read(pending_path)
                                self._validate_transaction(transaction)
                                if transaction["request_hash"] != canonical_sha256(request):
                                    raise _fail("MAPPING_IDEMPOTENCY_CONFLICT", "pending mapping request differs", 409)
                                self._assert_recompile(transaction["artifact"], artifact)
                                self._resume_transaction(transaction)
                                return self._result(transaction["artifact"], "PREVIEWED", transaction["previewed_event"], replayed=True)
                            replay = self._lookup_replay(request)
                            if replay is not None:
                                self._assert_recompile(replay, artifact)
                                state = self._derive_state(replay)
                                return self._result(replay, state["state"], state["event"], replayed=True)
                            tuple_replay = self._lookup_tuple_replay(artifact, request)
                            if tuple_replay is not None:
                                self._assert_recompile(tuple_replay, artifact)
                                state = self._derive_state(tuple_replay)
                                return self._result(tuple_replay, state["state"], state["event"], replayed=True)
                            transaction = self._build_transaction(request, artifact)
                            _write_immutable(pending_path, transaction)
                            self._resume_transaction(transaction)
                            return self._result(artifact, "PREVIEWED", transaction["previewed_event"], replayed=False)
            except WebfillPrepareError as exc:
                raise self._map_prepare_error(exc) from exc

    def get_mapping_preview_state(
        self,
        mapping_preview_id: str,
        *,
        claim_generation: int,
        fencing_token: str,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        self._require_preview_id(mapping_preview_id)
        with _store_lock(self.base_dir, "mapping-previews"):
            self._preflight_all_transactions()
            artifact = self._load_committed(mapping_preview_id)
            self._recover_lifecycle_for(mapping_preview_id)
            current = self._derive_state(artifact)
            if current["state"] != "PREVIEWED":
                return self._result(artifact, current["state"], current["event"], replayed=False)
            request = self._request_from_artifact(artifact, claim_generation, fencing_token, server_session_id)
            try:
                with self._prepare.mapping_coordination(preflight_callback=self._preflight_all_transactions) as prepare:
                    with self._profiles.coordination() as profiles:
                        with self._observations.coordination() as observations:
                            try:
                                prepared = self._get_prepared_locked(prepare, request, authenticated_principal, consumer_id, server_session_id)
                            except WebfillPrepareError as exc:
                                return self._terminalize_from_prepare(artifact, exc, prepare.observed_at)
                            if prepared["status"] != "VALID_PREPARED":
                                return self._terminalize(
                                    artifact,
                                    self._prepare_terminal_event(prepared["state"]),
                                    "prepare_authority_changed",
                                    prepared["state"],
                                    prepare.observed_at,
                                )
                            try:
                                profile = profiles.get_locked(request["adapter_target_profile_id"], request["adapter_target_profile_version"], require_active=True)
                            except WebfillMappingError as exc:
                                if exc.code == "MAPPING_TARGET_PROFILE_SUPERSEDED":
                                    return self._terminalize(artifact, "SUPERSEDED", "adapter_profile_advanced", exc.code, prepare.observed_at)
                                raise
                            try:
                                observation = observations.get_locked(request["dom_observation_id"], require_current=True)
                            except WebfillMappingError as exc:
                                if exc.code in {"MAPPING_OBSERVATION_STALE", "MAPPING_OBSERVATION_NOT_FOUND"}:
                                    return self._terminalize(artifact, "OBSERVATION_STALE", "dom_observation_changed", exc.code, prepare.observed_at)
                                raise
                            self._assert_request_authority(request, prepared["artifact"], profile, observation, authenticated_principal, consumer_id, server_session_id)
                            rebuilt = self._build_artifact(
                                request,
                                prepared["artifact"],
                                profile,
                                observation,
                                self._compile(prepared["artifact"], profile, observation),
                                authenticated_principal,
                                consumer_id,
                                preview_id=artifact["mapping_preview_id"],
                                created_at=artifact["created_at"],
                            )
                            self._assert_recompile(artifact, rebuilt)
            except WebfillPrepareError as exc:
                return self._terminalize_from_prepare(artifact, exc, _utc_now(self._clock))
            return self._result(artifact, "PREVIEWED", current["event"], replayed=False)

    def get_human_preview(
        self,
        mapping_preview_id: str,
        *,
        claim_generation: int,
        fencing_token: str,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        self._require_preview_id(mapping_preview_id)
        with _store_lock(self.base_dir, "mapping-previews"):
            self._preflight_all_transactions()
            artifact = self._load_committed(mapping_preview_id)
            self._recover_lifecycle_for(mapping_preview_id)
            if self._derive_state(artifact)["state"] != "PREVIEWED":
                raise _fail("MAPPING_PREPARE_INVALID", "mapping preview authority is terminal", 409)
            request = self._request_from_artifact(artifact, claim_generation, fencing_token, server_session_id)
            try:
                with self._prepare.mapping_coordination(preflight_callback=self._preflight_all_transactions) as prepare:
                    with self._profiles.coordination() as profiles:
                        with self._observations.coordination() as observations:
                            prepared = self._get_prepared_locked(prepare, request, authenticated_principal, consumer_id, server_session_id)
                            if prepared["status"] != "VALID_PREPARED":
                                self._terminalize(artifact, self._prepare_terminal_event(prepared["state"]), "prepare_authority_changed", prepared["state"], prepare.observed_at)
                                raise _fail("MAPPING_PREPARE_INVALID", "Prepare is no longer valid", 409)
                            try:
                                profile = profiles.get_locked(request["adapter_target_profile_id"], request["adapter_target_profile_version"], require_active=True)
                            except WebfillMappingError as exc:
                                if exc.code == "MAPPING_TARGET_PROFILE_SUPERSEDED":
                                    self._terminalize(artifact, "SUPERSEDED", "adapter_profile_advanced", exc.code, prepare.observed_at)
                                raise
                            try:
                                observation = observations.get_locked(request["dom_observation_id"], require_current=True)
                            except WebfillMappingError as exc:
                                if exc.code in {"MAPPING_OBSERVATION_STALE", "MAPPING_OBSERVATION_NOT_FOUND"}:
                                    self._terminalize(artifact, "OBSERVATION_STALE", "dom_observation_changed", exc.code, prepare.observed_at)
                                raise
                            self._assert_request_authority(request, prepared["artifact"], profile, observation, authenticated_principal, consumer_id, server_session_id)
                            rebuilt = self._build_artifact(request, prepared["artifact"], profile, observation, self._compile(prepared["artifact"], profile, observation), authenticated_principal, consumer_id, preview_id=artifact["mapping_preview_id"], created_at=artifact["created_at"])
                            self._assert_recompile(artifact, rebuilt)
                            field_labels = {
                                field["field_id"]: {"field_id": field["field_id"], "label_text": field["label_text"], "accessible_name": field["accessible_name"]}
                                for form in observation["forms"]
                                for field in form["fields"]
                            }
                            operation_by_id = {operation["human_bet_id"]: operation for operation in prepared["artifact"]["logical_plan"]["operations"]}
                            rows: list[dict[str, Any]] = []
                            for summary in artifact["bet_summaries"]:
                                operation = operation_by_id[summary["human_bet_id"]]
                                items = [item for item in artifact["mapping_items"] if item["source"]["human_bet_id"] == summary["human_bet_id"]]
                                rows.append({
                                    "human_bet_id": summary["human_bet_id"],
                                    "operation_index": summary["operation_index"],
                                    "human_confirmed_value": deepcopy(operation),
                                    "mapping_state": summary["state"],
                                    "targets": [{"logical_pointer": item["source"]["logical_pointer"], "mapping_state": item["mapping_state"], "blocking_reason": item["blocking_reason"], "fields": [field_labels[field_id] for field_id in item["selected_target_field_ids"]]} for item in items],
                                })
            except WebfillPrepareError as exc:
                raise self._map_prepare_error(exc) from exc
            return {
                "schema_version": "vision-webfill-human-mapping-preview-v1",
                "mapping_preview_id": artifact["mapping_preview_id"],
                "mapping_preview_status": artifact["mapping_preview_status"],
                "display_status": "READY" if artifact["mapping_preview_status"] == "VALID_MAPPING_PREVIEW" else "BLOCKED",
                "bets": rows,
                "cancelled_exclusions": deepcopy(artifact["cancelled_exclusions"]),
                "safety": {"read_only": True, "persisted": False, "browser_commands_present": False, "fill_authority": False, "submit_authority": False, "queue_or_claim_mutated": False},
            }

    def get_lifecycle_events(self, mapping_preview_id: str) -> list[dict[str, Any]]:
        self._require_preview_id(mapping_preview_id)
        with _store_lock(self.base_dir, "mapping-previews"):
            self._preflight_all_transactions()
            if not (self._commits / f"{mapping_preview_id}.json").exists():
                return []
            return self._events_for(mapping_preview_id)

    def revoke_preview(self, mapping_preview_id: str, *, authenticated_principal: str, reason_code: str) -> dict[str, Any]:
        if not isinstance(authenticated_principal, str) or not authenticated_principal or not callable(self._admin_validator) or not self._admin_validator(authenticated_principal):
            raise _fail("MAPPING_CLAIM_INVALID", "trusted administrative principal required", 403)
        if not isinstance(reason_code, str) or not reason_code:
            raise _fail("MAPPING_REQUEST_INVALID", "revocation reason invalid", 400)
        with _store_lock(self.base_dir, "mapping-previews"):
            self._preflight_all_transactions()
            artifact = self._load_committed(mapping_preview_id)
            current = self._derive_state(artifact)
            if current["state"] != "PREVIEWED":
                if current["state"] == "REVOKED" and current["event"]["reason_code"] == reason_code:
                    return self._result(artifact, "REVOKED", current["event"], replayed=True)
                raise _fail("MAPPING_IDEMPOTENCY_CONFLICT", "preview is already terminal", 409)
            return self._terminalize(artifact, "REVOKED", reason_code, "TRUSTED_ADMINISTRATIVE_REVOCATION", _utc_now(self._clock), actor=authenticated_principal)

    # ------------------------------ exact authority boundary

    @staticmethod
    def _get_prepared_locked(prepare: Any, request: Mapping[str, Any], principal: str, consumer: str, session: str) -> dict[str, Any]:
        return prepare.get_valid_prepared_locked(
            prepare_id=request["prepare_id"],
            expected_record_integrity_hash=request["expected_prepare_record_integrity_hash"],
            expected_deterministic_plan_hash=request["expected_deterministic_plan_hash"],
            expected_authority_binding_hash=request["expected_authority_binding_hash"],
            claim_id=request["claim_id"],
            claim_generation=request["claim_generation"],
            fencing_token=request["fencing_token"],
            authenticated_principal=principal,
            consumer_id=consumer,
            server_session_id=session,
        )

    @staticmethod
    def _assert_request_authority(request: Mapping[str, Any], prepare: Mapping[str, Any], profile: Mapping[str, Any], observation: Mapping[str, Any], principal: str, consumer: str, session: str) -> None:
        if prepare["prepare_id"] != request["prepare_id"] or prepare["record_integrity_hash"] != request["expected_prepare_record_integrity_hash"] or prepare["deterministic_plan_hash"] != request["expected_deterministic_plan_hash"] or prepare["authority_binding_hash"] != request["expected_authority_binding_hash"]:
            raise _fail("MAPPING_PREPARE_INVALID", "Prepare identity/hash mismatch", 409)
        claim = prepare["authority"]["claim"]
        if claim["claim_id"] != request["claim_id"] or claim["claim_generation"] != request["claim_generation"] or claim["fencing_token_hash"] != hashlib.sha256(request["fencing_token"].encode()).hexdigest() or claim["claim_session_id_hash"] != hashlib.sha256(session.encode()).hexdigest() or claim["authenticated_principal"] != principal or claim["consumer_id"] != consumer:
            raise _fail("MAPPING_CLAIM_INVALID", "Claim identity/fence/owner mismatch", 409)
        logical_identity = profile["logical_target_profile_identity"]
        if logical_identity != prepare["authority"]["target_profile"]:
            raise _fail("MAPPING_TARGET_PROFILE_SUPERSEDED", "adapter logical profile binding mismatch", 409)
        if profile["adapter_target_profile_id"] != request["adapter_target_profile_id"] or profile["adapter_target_profile_version"] != request["adapter_target_profile_version"] or profile["profile_integrity_hash"] != request["expected_adapter_target_profile_integrity_hash"]:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "adapter profile identity/hash mismatch", 409)
        if observation["dom_observation_id"] != request["dom_observation_id"] or observation["observation_hash"] != request["expected_observation_hash"] or observation["page_fingerprint"] != request["expected_page_fingerprint"] or observation["page_instance_id"] != request["expected_page_instance_id"] or observation["navigation_epoch"] != request["expected_navigation_epoch"]:
            raise _fail("MAPPING_OBSERVATION_STALE", "observation identity/freshness mismatch", 409)
        site = profile["site_contract"]
        page = observation["page_identity"]
        if site["site_id"] != page["site_id"] or site["game"] != page["game"] or site["game"] != prepare["logical_plan"]["game"] or site["origin_identity_hash"] != page["origin_identity_hash"] or site["normalized_path_identity_hash"] != page["normalized_path_identity_hash"] or site["page_fingerprint_algorithm"] != observation["page_fingerprint_algorithm"] or site["expected_page_fingerprint"] != observation["page_fingerprint"] or not set(site["required_page_marker_hashes"]) <= set(page["structural_marker_hashes"]):
            raise _fail("MAPPING_PAGE_IDENTITY_MISMATCH", "page/site/game identity mismatch", 409)
        forms = [form for form in observation["forms"] if form["form_id"] == profile["form_contract"]["expected_form_id"]]
        if len(forms) != 1:
            raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "exact target form not found", 409)
        form = forms[0]
        contract = profile["form_contract"]
        if form["form_fingerprint"] != contract["expected_form_fingerprint"] or not set(contract["required_form_marker_hashes"]) <= set(form["structural_marker_hashes"]):
            raise _fail("MAPPING_FORM_IDENTITY_MISMATCH", "target form fingerprint mismatch", 409)

    # ------------------------------ deterministic mapping compiler

    def _compile(self, prepare: Mapping[str, Any], profile: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
        plan = prepare["logical_plan"]
        form = next(form for form in observation["forms"] if form["form_id"] == profile["form_contract"]["expected_form_id"])
        fields = form["fields"]
        rules = profile["logical_field_rules"]
        mapping_items: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        mapping_index = 1
        for operation in plan["operations"]:
            operation_items: list[dict[str, Any]] = []
            descriptors: list[dict[str, Any]] = [
                self._descriptor(operation, "SET_BET_TYPE", "BET", f"/logical_plan/operations/{operation['operation_index'] - 1}/bet_type", operation["bet_type"])
            ]
            for group_index, group in enumerate(operation["number_groups"], 1):
                for number_index, number in enumerate(group, 1):
                    descriptors.append(self._descriptor(operation, "SET_NUMBER", "NUMBER", f"/logical_plan/operations/{operation['operation_index'] - 1}/number_groups/{group_index - 1}/{number_index - 1}", number, group_index=group_index, item_index=number_index, number_index=number_index))
            for rule_index, multiplier in enumerate(operation["multiplier"]["ordered_rules"], 1):
                descriptors.append(self._descriptor(operation, "SET_MULTIPLIER_RULE", "MULTIPLIER_RULE", f"/logical_plan/operations/{operation['operation_index'] - 1}/multiplier/ordered_rules/{rule_index - 1}", multiplier, item_index=rule_index, multiplier_rule_index=rule_index))
            special = operation["special_play"]
            if special["kind"] != "none":
                descriptors.append(self._descriptor(operation, "SET_SPECIAL_PLAY", "SPECIAL_PLAY", f"/logical_plan/operations/{operation['operation_index'] - 1}/special_play", special))
            if operation["continuation"]["present"]:
                descriptors.append(self._descriptor(operation, "SET_CONTINUATION", "CONTINUATION", f"/logical_plan/operations/{operation['operation_index'] - 1}/continuation", operation["continuation"]))
            for descriptor in descriptors:
                rule = self._select_rule(rules, operation["bet_type"], descriptor["logical_action"], descriptor["source_scope"])
                component_count = rule["required_component_count"] if rule is not None else 1
                for component_index in range(1, component_count + 1):
                    item = self._map_component(mapping_index, descriptor, component_index, rule, fields)
                    mapping_items.append(item)
                    operation_items.append(item)
                    mapping_index += 1
            group_mapped: set[int] = set()
            for group_index, group in enumerate(operation["number_groups"], 1):
                number_items = [item for item in operation_items if item["source"]["logical_action"] == "SET_NUMBER" and item["source"]["group_index"] == group_index]
                if len(number_items) == len(group) and all(item["mapping_state"] == "MAPPED_UNIQUE" for item in number_items):
                    group_mapped.add(group_index)
            all_unique = all(item["mapping_state"] == "MAPPED_UNIQUE" for item in operation_items)
            column_complete = operation["bet_type"] != "column" or (all_unique and len(group_mapped) == len(operation["number_groups"]))
            if all_unique and column_complete:
                state = "READY"
            elif any(item["mapping_state"] == "UNSUPPORTED" for item in operation_items):
                state = "UNSUPPORTED"
            else:
                state = "BLOCKED"
            summaries.append({"human_bet_id": operation["human_bet_id"], "operation_index": operation["operation_index"], "bet_type": operation["bet_type"], "required_group_count": len(operation["number_groups"]), "mapped_group_count": len(group_mapped), "all_required_items_mapped_unique": all_unique, "column_mapping_complete": column_complete, "state": state})
        # No target field may be selected by two logical components.
        claims: dict[str, list[int]] = {}
        for position, item in enumerate(mapping_items):
            for field_id in item["selected_target_field_ids"]:
                claims.setdefault(field_id, []).append(position)
        duplicated_fields = {field_id for field_id, positions in claims.items() if len(positions) > 1}
        if duplicated_fields:
            affected = {position for field_id in duplicated_fields for position in claims[field_id]}
            for position in affected:
                item = mapping_items[position]
                item["candidate_target_field_ids"] = sorted(set(item["candidate_target_field_ids"] + item["selected_target_field_ids"]))
                item["selected_target_field_ids"] = []
                item["mapping_state"] = "AMBIGUOUS"
                item["mapping_confidence"] = "NONE"
                item["blocking_reason"] = "target_field_multi_claim"
            affected_bets = {mapping_items[position]["source"]["human_bet_id"] for position in affected}
            for summary in summaries:
                if summary["human_bet_id"] in affected_bets:
                    summary["all_required_items_mapped_unique"] = False
                    summary["column_mapping_complete"] = summary["bet_type"] != "column" and summary["column_mapping_complete"]
                    summary["state"] = "BLOCKED"
        blocking_reasons = sorted({item["blocking_reason"] for item in mapping_items if item["blocking_reason"] is not None})
        all_unique = all(item["mapping_state"] == "MAPPED_UNIQUE" for item in mapping_items)
        all_columns = all(summary["column_mapping_complete"] for summary in summaries)
        validation = {
            "required_item_count": len(mapping_items),
            "mapped_unique_count": sum(item["mapping_state"] == "MAPPED_UNIQUE" for item in mapping_items),
            "blocked_item_count": sum(item["mapping_state"] != "MAPPED_UNIQUE" for item in mapping_items),
            "active_bet_count": len(plan["operations"]),
            "cancelled_exclusion_count": len(plan["cancelled_audit_refs"]),
            "multi_claim_count": len(duplicated_fields),
            "all_required_items_mapped_unique": all_unique,
            "all_column_mappings_complete": all_columns,
            "human_preview_available": True,
            "client_override_applied": False,
        }
        exclusions = [{"human_bet_id": ref["human_bet_id"], "source_section": "cancelled_audit_refs", "excluded_reason": "cancelled_non_executable", "mapping_item_count": 0} for ref in plan["cancelled_audit_refs"]]
        return {"mapping_items": mapping_items, "bet_summaries": summaries, "cancelled_exclusions": exclusions, "validation": validation, "blocking_reasons": blocking_reasons}

    @staticmethod
    def _descriptor(operation: Mapping[str, Any], action: str, scope: str, pointer: str, value: Any, *, group_index: int | None = None, item_index: int | None = None, number_index: int = 0, multiplier_rule_index: int = 0) -> dict[str, Any]:
        return {"human_bet_id": operation["human_bet_id"], "operation_index": operation["operation_index"], "bet_type": operation["bet_type"], "logical_action": action, "source_scope": scope, "logical_pointer": pointer, "value": deepcopy(value), "group_index": group_index, "item_index": item_index, "number_index": number_index, "multiplier_rule_index": multiplier_rule_index}

    @staticmethod
    def _select_rule(rules: list[Mapping[str, Any]], bet_type: str, action: str, scope: str) -> Mapping[str, Any] | None:
        matches = [rule for rule in rules if rule["logical_action"] == action and scope == rule["source_scope"] and bet_type in rule["applicable_bet_types"]]
        return matches[0] if len(matches) == 1 else None

    def _map_component(self, mapping_index: int, descriptor: Mapping[str, Any], component_index: int, rule: Mapping[str, Any] | None, fields: list[Mapping[str, Any]]) -> dict[str, Any]:
        source = {
            "human_bet_id": descriptor["human_bet_id"],
            "operation_index": descriptor["operation_index"],
            "logical_pointer": descriptor["logical_pointer"],
            "logical_action": descriptor["logical_action"],
            "group_index": descriptor["group_index"],
            "item_index": descriptor["item_index"],
            "component_index": component_index,
            "intended_value_hash": canonical_sha256({"schema_version": "vision-webfill-intended-value-binding-v1", "logical_pointer": descriptor["logical_pointer"], "value": descriptor["value"]}),
        }
        if rule is None:
            return self._mapping_item(mapping_index, source, "rule-unsupported-mapping", [], [], "UNSUPPORTED", "profile_rule_missing_or_ambiguous")
        values = {
            "operation_index": descriptor["operation_index"],
            "group_index": descriptor["group_index"] or 0,
            "number_index": descriptor["number_index"],
            "multiplier_rule_index": descriptor["multiplier_rule_index"],
            "component_index": component_index,
        }
        try:
            selector_text = rule["selector_template"].format(**values)
        except (KeyError, ValueError) as exc:
            raise _fail("MAPPING_TARGET_PROFILE_INTEGRITY_INVALID", "selector template cannot be instantiated deterministically", 500) from exc
        candidates = [field for field in fields if any(selector["selector_kind"] == rule["selector_kind"] and selector["selector_text"] == selector_text for selector in field["selector_candidates"])]
        candidates = [field for field in candidates if self._field_structural_match(field, rule["expected_field"])]
        candidate_ids = [field["field_id"] for field in candidates]
        if not candidates:
            return self._mapping_item(mapping_index, source, rule["rule_id"], [], [], "MISSING", "target_field_missing")
        if len(candidates) > 1:
            return self._mapping_item(mapping_index, source, rule["rule_id"], candidate_ids, [], "AMBIGUOUS", "multiple_exact_target_fields")
        field = candidates[0]
        if not field["visible"] or field["disabled"] or field["readonly"] or field["occupancy"] != "EMPTY":
            return self._mapping_item(mapping_index, source, rule["rule_id"], candidate_ids, [], "BLOCKED", f"target_field_{field['occupancy'].lower() if field['occupancy'] != 'EMPTY' else 'unavailable'}")
        return self._mapping_item(mapping_index, source, rule["rule_id"], candidate_ids, candidate_ids, "MAPPED_UNIQUE", None)

    @staticmethod
    def _field_structural_match(field: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        return field["tag_name"] == expected["tag_name"] and field["input_type"] in expected["input_types"] and field["label_text"] == expected["label_text"] and set(expected["required_structural_marker_hashes"]) <= set(field["structural_marker_hashes"])

    @staticmethod
    def _mapping_item(index: int, source: Mapping[str, Any], rule_id: str, candidates: list[str], selected: list[str], state: str, reason: str | None) -> dict[str, Any]:
        return {"mapping_index": index, "source": deepcopy(dict(source)), "rule_id": rule_id, "candidate_target_field_ids": candidates, "selected_target_field_ids": selected, "mapping_state": state, "mapping_confidence": "DETERMINISTIC_EXACT" if state == "MAPPED_UNIQUE" else "NONE", "blocking_reason": reason}

    # ------------------------------ immutable records and replay

    def _build_artifact(
        self,
        request: Mapping[str, Any],
        prepare: Mapping[str, Any],
        profile: Mapping[str, Any],
        observation: Mapping[str, Any],
        compiled: Mapping[str, Any],
        authenticated_principal: str,
        consumer_id: str,
        *,
        preview_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        claim = prepare["authority"]["claim"]
        authority = {
            "prepare": {
                "prepare_id": prepare["prepare_id"],
                "prepare_record_integrity_hash": prepare["record_integrity_hash"],
                "deterministic_plan_hash": prepare["deterministic_plan_hash"],
                "authority_binding_hash": prepare["authority_binding_hash"],
            },
            "claim": {
                "claim_id": claim["claim_id"],
                "claim_generation": claim["claim_generation"],
                "fencing_token_hash": claim["fencing_token_hash"],
                "consumer_id": consumer_id,
                "authenticated_principal": authenticated_principal,
                "claim_session_id_hash": claim["claim_session_id_hash"],
            },
            "adapter_target_profile": {
                "adapter_target_profile_id": profile["adapter_target_profile_id"],
                "adapter_target_profile_version": profile["adapter_target_profile_version"],
                "profile_integrity_hash": profile["profile_integrity_hash"],
            },
            "dom_observation": {
                "dom_observation_id": observation["dom_observation_id"],
                "observation_hash": observation["observation_hash"],
                "page_fingerprint": observation["page_fingerprint"],
                "page_instance_id": observation["page_instance_id"],
                "navigation_epoch": observation["navigation_epoch"],
            },
            "idempotency_key_hash": hashlib.sha256(request["idempotency_key"].encode()).hexdigest(),
        }
        status = "VALID_MAPPING_PREVIEW" if compiled["validation"]["all_required_items_mapped_unique"] and compiled["validation"]["all_column_mappings_complete"] and compiled["validation"]["multi_claim_count"] == 0 else "BLOCKED_MAPPING_PREVIEW"
        blocking = list(compiled["blocking_reasons"])
        if status == "BLOCKED_MAPPING_PREVIEW" and not blocking:
            blocking = ["mapping_incomplete"]
        artifact = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "mapping_preview_id": preview_id or f"vwmp-{uuid.uuid4().hex}",
            "created_at": created_at or _utc_now(self._clock).isoformat(),
            "state_at_creation": "PREVIEWED",
            "mapping_preview_status": status,
            "authority": authority,
            "mapping_items": deepcopy(compiled["mapping_items"]),
            "bet_summaries": deepcopy(compiled["bet_summaries"]),
            "cancelled_exclusions": deepcopy(compiled["cancelled_exclusions"]),
            "mapping_content_hash": "",
            "validation": deepcopy(compiled["validation"]),
            "blocking_reasons": blocking,
            "record_integrity_hash": "",
            "safety": {
                "mapping_preview_only": True,
                "read_only_dom_observation": True,
                "contains_raw_bet_values": False,
                "contains_current_dom_values": False,
                "dom_mutation_authorized": False,
                "click_authorized": False,
                "typing_authorized": False,
                "navigation_authorized": False,
                "events_authorized": False,
                "approved_for_fill": False,
                "approved_for_submit": False,
                "webfill_authorized": False,
                "submitted": False,
                "auto_submit": False,
                "queue_completed": False,
                "claim_completed": False,
            },
        }
        artifact["mapping_content_hash"] = canonical_sha256(self._mapping_content_projection(artifact))
        artifact["record_integrity_hash"] = canonical_sha256({key: value for key, value in artifact.items() if key != "record_integrity_hash"})
        self._validate_artifact(artifact)
        return artifact

    @staticmethod
    def _mapping_content_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
        authority = deepcopy(dict(artifact["authority"]))
        authority.pop("idempotency_key_hash")
        return {
            "schema_version": "vision-webfill-mapping-content-v1",
            "authority": authority,
            "mapping_preview_status": artifact["mapping_preview_status"],
            "mapping_items": artifact["mapping_items"],
            "bet_summaries": artifact["bet_summaries"],
            "cancelled_exclusions": artifact["cancelled_exclusions"],
            "validation": artifact["validation"],
            "blocking_reasons": artifact["blocking_reasons"],
        }

    @staticmethod
    def _tuple_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
        authority = deepcopy(dict(artifact["authority"]))
        authority.pop("idempotency_key_hash")
        return authority

    def _build_transaction(self, request: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
        event = self._event(artifact, 1, "PREVIEWED", artifact["created_at"], "gate3c2_mapping_preview", None, None)
        return {
            "schema_version": "vision-webfill-mapping-preview-transaction-v1",
            "request_hash": canonical_sha256(request),
            "idempotency_key_hash": artifact["authority"]["idempotency_key_hash"],
            "authority_tuple_hash": canonical_sha256(self._tuple_projection(artifact)),
            "mapping_preview_id": artifact["mapping_preview_id"],
            "artifact": deepcopy(dict(artifact)),
            "previewed_event": event,
        }

    def _resume_transaction(self, transaction: Mapping[str, Any]) -> None:
        self._validate_transaction(transaction)
        artifact = transaction["artifact"]
        preview_id = artifact["mapping_preview_id"]
        outputs = [
            (self._previews / f"{preview_id}.json", artifact),
            (self._events / preview_id / "000001.json", transaction["previewed_event"]),
            (
                self._idempotency / f"{transaction['idempotency_key_hash']}.json",
                {"mapping_preview_id": preview_id, "request_hash": transaction["request_hash"], "authority_tuple_hash": transaction["authority_tuple_hash"], "record_integrity_hash": artifact["record_integrity_hash"]},
            ),
            (
                self._tuples / f"{transaction['authority_tuple_hash']}.json",
                {"mapping_preview_id": preview_id, "record_integrity_hash": artifact["record_integrity_hash"], "mapping_content_hash": artifact["mapping_content_hash"]},
            ),
        ]
        for path, value in outputs:
            if path.exists():
                if _read(path) != value:
                    raise _fail("MAPPING_STORE_CORRUPT", f"transaction output conflict: {path.name}", 500)
            else:
                _write_immutable(path, value)
        commit = {"mapping_preview_id": preview_id, "mapping_content_hash": artifact["mapping_content_hash"], "record_integrity_hash": artifact["record_integrity_hash"]}
        commit_path = self._commits / f"{preview_id}.json"
        if commit_path.exists():
            if _read(commit_path) != commit:
                raise _fail("MAPPING_STORE_CORRUPT", "preview commit mismatch", 500)
        else:
            _write_immutable(commit_path, commit)

    def _lookup_replay(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self._idempotency / f"{hashlib.sha256(request['idempotency_key'].encode()).hexdigest()}.json"
        if not path.exists():
            return None
        record = _read(path)
        expected = {"mapping_preview_id", "request_hash", "authority_tuple_hash", "record_integrity_hash"}
        if set(record) != expected or record["request_hash"] != canonical_sha256(request):
            raise _fail("MAPPING_IDEMPOTENCY_CONFLICT", "mapping idempotency key reused", 409)
        artifact = self._load_committed(record["mapping_preview_id"])
        if artifact["record_integrity_hash"] != record["record_integrity_hash"] or canonical_sha256(self._tuple_projection(artifact)) != record["authority_tuple_hash"]:
            raise _fail("MAPPING_STORE_CORRUPT", "mapping idempotency relation invalid", 500)
        return artifact

    def _lookup_tuple_replay(self, artifact: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any] | None:
        tuple_hash = canonical_sha256(self._tuple_projection(artifact))
        path = self._tuples / f"{tuple_hash}.json"
        if not path.exists():
            return None
        record = _read(path)
        expected = {"mapping_preview_id", "record_integrity_hash", "mapping_content_hash"}
        if set(record) != expected:
            raise _fail("MAPPING_STORE_CORRUPT", "mapping tuple index invalid", 500)
        existing = self._load_committed(record["mapping_preview_id"])
        if existing["record_integrity_hash"] != record["record_integrity_hash"] or existing["mapping_content_hash"] != record["mapping_content_hash"] or existing["mapping_content_hash"] != artifact["mapping_content_hash"]:
            raise _fail("MAPPING_NONDETERMINISTIC", "same authority tuple compiled differently", 409)
        idem = {"mapping_preview_id": existing["mapping_preview_id"], "request_hash": canonical_sha256(request), "authority_tuple_hash": tuple_hash, "record_integrity_hash": existing["record_integrity_hash"]}
        idem_path = self._idempotency / f"{hashlib.sha256(request['idempotency_key'].encode()).hexdigest()}.json"
        if idem_path.exists() and _read(idem_path) != idem:
            raise _fail("MAPPING_IDEMPOTENCY_CONFLICT", "mapping idempotency relation conflict", 409)
        if not idem_path.exists():
            _write_immutable(idem_path, idem)
        return existing

    @staticmethod
    def _assert_recompile(existing: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> None:
        if existing["mapping_content_hash"] != rebuilt["mapping_content_hash"] or WebfillMappingPreviewStore._mapping_content_projection(existing) != WebfillMappingPreviewStore._mapping_content_projection(rebuilt):
            raise _fail("MAPPING_NONDETERMINISTIC", "mapping preview differs from deterministic live recompile", 409)

    def _load_committed(self, preview_id: str) -> dict[str, Any]:
        self._require_preview_id(preview_id)
        path = self._previews / f"{preview_id}.json"
        commit_path = self._commits / f"{preview_id}.json"
        if not path.exists() or not commit_path.exists():
            raise _fail("MAPPING_OBSERVATION_NOT_FOUND", "committed mapping preview not found", 404)
        artifact = _read(path)
        self._validate_artifact(artifact, persisted=True)
        commit = {"mapping_preview_id": preview_id, "mapping_content_hash": artifact["mapping_content_hash"], "record_integrity_hash": artifact["record_integrity_hash"]}
        if _read(commit_path) != commit:
            raise _fail("MAPPING_HASH_MISMATCH", "mapping preview commit mismatch", 409)
        return artifact

    def _preflight_all_transactions(self) -> None:
        for path in sorted(self._transactions.glob("*.json")):
            transaction = _read(path)
            self._validate_transaction(transaction)
            if path.stem != transaction["idempotency_key_hash"]:
                raise _fail("MAPPING_STORE_CORRUPT", "transaction filename binding invalid", 500)
            self._preflight_transaction_outputs(transaction)
        lifecycle_by_preview: dict[str, dict[str, Any]] = {}
        for path in sorted(self._lifecycle_transactions.glob("*.json")):
            transaction = _read(path)
            self._validate_lifecycle_transaction(transaction)
            event = transaction["event"]
            preview_id = event["mapping_preview_id"]
            if path.stem != f"{preview_id}-{event['event_type']}" or preview_id in lifecycle_by_preview:
                raise _fail("MAPPING_STORE_CORRUPT", "lifecycle transaction binding/conflict invalid", 500)
            lifecycle_by_preview[preview_id] = transaction
            output = self._events / preview_id / "000002.json"
            if output.exists() and _read(output) != event:
                raise _fail("MAPPING_STORE_CORRUPT", "lifecycle transaction output conflict", 500)
        for path in sorted(self._transactions.glob("*.json")):
            self._resume_transaction(_read(path))
        for transaction in lifecycle_by_preview.values():
            self._resume_lifecycle_transaction(transaction)

    def _preflight_transaction_outputs(self, transaction: Mapping[str, Any]) -> None:
        artifact = transaction["artifact"]
        preview_id = artifact["mapping_preview_id"]
        expected = [
            (self._previews / f"{preview_id}.json", artifact),
            (self._events / preview_id / "000001.json", transaction["previewed_event"]),
            (self._idempotency / f"{transaction['idempotency_key_hash']}.json", {"mapping_preview_id": preview_id, "request_hash": transaction["request_hash"], "authority_tuple_hash": transaction["authority_tuple_hash"], "record_integrity_hash": artifact["record_integrity_hash"]}),
            (self._tuples / f"{transaction['authority_tuple_hash']}.json", {"mapping_preview_id": preview_id, "record_integrity_hash": artifact["record_integrity_hash"], "mapping_content_hash": artifact["mapping_content_hash"]}),
            (self._commits / f"{preview_id}.json", {"mapping_preview_id": preview_id, "mapping_content_hash": artifact["mapping_content_hash"], "record_integrity_hash": artifact["record_integrity_hash"]}),
        ]
        for path, value in expected:
            if path.exists() and _read(path) != value:
                raise _fail("MAPPING_STORE_CORRUPT", f"pending transaction conflicts with {path.name}", 500)

    # ------------------------------ lifecycle

    def _events_for(self, preview_id: str) -> list[dict[str, Any]]:
        directory = self._events / preview_id
        events = [_read(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []
        for index, event in enumerate(events, 1):
            self._validate_event(event)
            if event["event_sequence"] != index or event["mapping_preview_id"] != preview_id:
                raise _fail("MAPPING_STORE_CORRUPT", "preview lifecycle sequence invalid", 500)
        if not events or events[0]["event_type"] != "PREVIEWED" or len(events) > 2 or (len(events) == 2 and events[1]["event_type"] not in _TERMINAL_EVENTS):
            raise _fail("MAPPING_STORE_CORRUPT", "preview lifecycle transition invalid", 500)
        artifact = _read(self._previews / f"{preview_id}.json")
        self._validate_artifact(artifact, persisted=True)
        for event in events:
            if event["mapping_content_hash"] != artifact["mapping_content_hash"] or event["preview_record_integrity_hash"] != artifact["record_integrity_hash"]:
                raise _fail("MAPPING_STORE_CORRUPT", "preview lifecycle relation invalid", 500)
        return deepcopy(events)

    def _derive_state(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        events = self._events_for(artifact["mapping_preview_id"])
        return {"state": events[-1]["event_type"], "event": events[-1]}

    def _terminalize(self, artifact: Mapping[str, Any], event_type: str, reason: str, upstream: str, occurred_at: datetime, *, actor: str = "gate3c2_authority") -> dict[str, Any]:
        if event_type not in _TERMINAL_EVENTS:
            raise _fail("MAPPING_STORE_CORRUPT", "terminal event type invalid", 500)
        current = self._derive_state(artifact)
        if current["state"] != "PREVIEWED":
            return self._result(artifact, current["state"], current["event"], replayed=True)
        path = self._lifecycle_transactions / f"{artifact['mapping_preview_id']}-{event_type}.json"
        if path.exists():
            transaction = _read(path)
            self._validate_lifecycle_transaction(transaction)
            event = transaction["event"]
            if event["reason_code"] != reason or event["upstream_state"] != upstream or event["actor"] != actor:
                raise _fail("MAPPING_IDEMPOTENCY_CONFLICT", "terminal lifecycle request differs", 409)
        else:
            # A different terminal intent is a fail-closed conflict.
            other = list(self._lifecycle_transactions.glob(f"{artifact['mapping_preview_id']}-*.json"))
            if other:
                raise _fail("MAPPING_STORE_CORRUPT", "multiple terminal lifecycle intents", 500)
            event = self._event(artifact, 2, event_type, occurred_at.astimezone(timezone.utc).isoformat(), actor, reason, upstream)
            transaction = {"schema_version": "vision-webfill-mapping-preview-lifecycle-transaction-v1", "event": event}
            _write_immutable(path, transaction)
        self._resume_lifecycle_transaction(transaction)
        return self._result(artifact, event_type, event, replayed=False)

    def _resume_lifecycle_transaction(self, transaction: Mapping[str, Any]) -> None:
        self._validate_lifecycle_transaction(transaction)
        event = transaction["event"]
        path = self._events / event["mapping_preview_id"] / "000002.json"
        if path.exists():
            if _read(path) != event:
                raise _fail("MAPPING_STORE_CORRUPT", "terminal event conflict", 500)
        else:
            _write_immutable(path, event)

    def _recover_lifecycle_for(self, preview_id: str) -> None:
        for path in sorted(self._lifecycle_transactions.glob(f"{preview_id}-*.json")):
            self._resume_lifecycle_transaction(_read(path))

    def _event(self, artifact: Mapping[str, Any], sequence: int, event_type: str, occurred_at: str, actor: str, reason: str | None, upstream: str | None) -> dict[str, Any]:
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": f"vwmpe-{uuid.uuid4().hex}",
            "mapping_preview_id": artifact["mapping_preview_id"],
            "event_sequence": sequence,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "actor": actor,
            "reason_code": reason,
            "upstream_state": upstream,
            "mapping_content_hash": artifact["mapping_content_hash"],
            "preview_record_integrity_hash": artifact["record_integrity_hash"],
            "event_integrity_hash": "",
        }
        event["event_integrity_hash"] = canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"})
        self._validate_event(event)
        return event

    @staticmethod
    def _prepare_terminal_event(state: str) -> str:
        if state == "EXPIRED":
            return "EXPIRED"
        if state == "SUPERSEDED":
            return "SUPERSEDED"
        if state == "INVALIDATED":
            return "AUTHORITY_BLOCKED"
        return "AUTHORITY_BLOCKED"

    def _terminalize_from_prepare(self, artifact: Mapping[str, Any], exc: WebfillPrepareError, occurred_at: datetime) -> dict[str, Any]:
        if exc.code in {"PREPARE_CLAIM_INVALID", "PREPARE_HASH_MISMATCH"}:
            raise self._map_prepare_error(exc)
        event_type = "EXPIRED" if exc.code == "PREPARE_CLAIM_EXPIRED" else "AUTHORITY_BLOCKED"
        return self._terminalize(artifact, event_type, "prepare_authority_failed", exc.code, occurred_at)

    @staticmethod
    def _result(artifact: Mapping[str, Any], state: str, event: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "schema_version": "vision-webfill-mapping-preview-result-v1",
            "status": artifact["mapping_preview_status"] if state == "PREVIEWED" else state,
            "state": state,
            "artifact": deepcopy(dict(artifact)),
            "lifecycle_event": deepcopy(dict(event)),
            "replayed": replayed,
            "safety": deepcopy(artifact["safety"]),
        }

    # ------------------------------ strict validators

    def _validate_artifact(self, artifact: Any, *, persisted: bool = False) -> None:
        root = _require_exact(artifact, {"schema_version", "mapping_preview_id", "created_at", "state_at_creation", "mapping_preview_status", "authority", "mapping_items", "bet_summaries", "cancelled_exclusions", "mapping_content_hash", "validation", "blocking_reasons", "record_integrity_hash", "safety"}, "mapping preview", persisted=persisted)
        _assert_json_safe(root, persisted=persisted, label="mapping preview")
        if root.get("schema_version") != PREVIEW_SCHEMA_VERSION or not isinstance(root.get("mapping_preview_id"), str) or not _PREVIEW_ID_RE.fullmatch(root["mapping_preview_id"]) or root.get("state_at_creation") != "PREVIEWED" or root.get("mapping_preview_status") not in {"VALID_MAPPING_PREVIEW", "BLOCKED_MAPPING_PREVIEW"}:
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping preview root invalid", 500 if persisted else 422)
        _require_timestamp(root["created_at"], "mapping preview", persisted=persisted)
        authority = _require_exact(root["authority"], {"prepare", "claim", "adapter_target_profile", "dom_observation", "idempotency_key_hash"}, "mapping authority", persisted=persisted)
        prepare = _require_exact(authority["prepare"], {"prepare_id", "prepare_record_integrity_hash", "deterministic_plan_hash", "authority_binding_hash"}, "Prepare authority", persisted=persisted)
        if not isinstance(prepare["prepare_id"], str) or not _PREPARE_ID_RE.fullmatch(prepare["prepare_id"]):
            raise _fail("MAPPING_SCHEMA_INVALID", "Prepare identity invalid", 500 if persisted else 422)
        for key in ("prepare_record_integrity_hash", "deterministic_plan_hash", "authority_binding_hash"):
            _require_hash(prepare[key], key, persisted=persisted)
        claim = _require_exact(authority["claim"], {"claim_id", "claim_generation", "fencing_token_hash", "consumer_id", "authenticated_principal", "claim_session_id_hash"}, "Claim authority", persisted=persisted)
        if not isinstance(claim["claim_id"], str) or not _CLAIM_ID_RE.fullmatch(claim["claim_id"]) or isinstance(claim["claim_generation"], bool) or not isinstance(claim["claim_generation"], int) or claim["claim_generation"] < 1 or not isinstance(claim["consumer_id"], str) or not _CONSUMER_ID_RE.fullmatch(claim["consumer_id"]) or not isinstance(claim["authenticated_principal"], str) or not claim["authenticated_principal"]:
            raise _fail("MAPPING_SCHEMA_INVALID", "Claim authority invalid", 500 if persisted else 422)
        _require_hash(claim["fencing_token_hash"], "fence", persisted=persisted)
        _require_hash(claim["claim_session_id_hash"], "claim session", persisted=persisted)
        adapter = _require_exact(authority["adapter_target_profile"], {"adapter_target_profile_id", "adapter_target_profile_version", "profile_integrity_hash"}, "adapter authority", persisted=persisted)
        if not isinstance(adapter["adapter_target_profile_id"], str) or not _PROFILE_ID_RE.fullmatch(adapter["adapter_target_profile_id"]) or isinstance(adapter["adapter_target_profile_version"], bool) or not isinstance(adapter["adapter_target_profile_version"], int) or adapter["adapter_target_profile_version"] < 1:
            raise _fail("MAPPING_SCHEMA_INVALID", "adapter authority invalid", 500 if persisted else 422)
        _require_hash(adapter["profile_integrity_hash"], "adapter profile", persisted=persisted)
        observation = _require_exact(authority["dom_observation"], {"dom_observation_id", "observation_hash", "page_fingerprint", "page_instance_id", "navigation_epoch"}, "observation authority", persisted=persisted)
        if not isinstance(observation["dom_observation_id"], str) or not _OBSERVATION_ID_RE.fullmatch(observation["dom_observation_id"]) or not isinstance(observation["page_instance_id"], str) or not _PAGE_INSTANCE_ID_RE.fullmatch(observation["page_instance_id"]) or isinstance(observation["navigation_epoch"], bool) or not isinstance(observation["navigation_epoch"], int) or observation["navigation_epoch"] < 1:
            raise _fail("MAPPING_SCHEMA_INVALID", "observation authority invalid", 500 if persisted else 422)
        _require_hash(observation["observation_hash"], "observation", persisted=persisted)
        _require_hash(observation["page_fingerprint"], "page fingerprint", persisted=persisted)
        _require_hash(authority["idempotency_key_hash"], "idempotency", persisted=persisted)
        items = root["mapping_items"]
        if not isinstance(items, list) or not items:
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping items invalid", 500 if persisted else 422)
        selected_claims: list[str] = []
        for index, item in enumerate(items, 1):
            item = _require_exact(item, {"mapping_index", "source", "rule_id", "candidate_target_field_ids", "selected_target_field_ids", "mapping_state", "mapping_confidence", "blocking_reason"}, "mapping item", persisted=persisted)
            if item["mapping_index"] != index or not isinstance(item["rule_id"], str) or not _RULE_ID_RE.fullmatch(item["rule_id"]) or item["mapping_state"] not in _MAPPING_STATES:
                raise _fail("MAPPING_SCHEMA_INVALID", "mapping item identity/state invalid", 500 if persisted else 422)
            source = _require_exact(item["source"], {"human_bet_id", "operation_index", "logical_pointer", "logical_action", "group_index", "item_index", "component_index", "intended_value_hash"}, "mapping source", persisted=persisted)
            if not isinstance(source["human_bet_id"], str) or not _HUMAN_BET_ID_RE.fullmatch(source["human_bet_id"]) or isinstance(source["operation_index"], bool) or not isinstance(source["operation_index"], int) or source["operation_index"] < 1 or not isinstance(source["logical_pointer"], str) or not re.fullmatch(r"^/logical_plan/operations/[0-9]+/.+", source["logical_pointer"]) or source["logical_action"] not in _ALLOWED_ACTIONS or isinstance(source["component_index"], bool) or not isinstance(source["component_index"], int) or source["component_index"] < 1:
                raise _fail("MAPPING_SCHEMA_INVALID", "mapping source invalid", 500 if persisted else 422)
            for optional in ("group_index", "item_index"):
                if source[optional] is not None and (isinstance(source[optional], bool) or not isinstance(source[optional], int) or source[optional] < 1):
                    raise _fail("MAPPING_SCHEMA_INVALID", "mapping source coordinate invalid", 500 if persisted else 422)
            _require_hash(source["intended_value_hash"], "intended value", persisted=persisted)
            for key in ("candidate_target_field_ids", "selected_target_field_ids"):
                values = item[key]
                if not isinstance(values, list) or len(values) != len(set(values)) or any(not isinstance(value, str) or not _FIELD_ID_RE.fullmatch(value) for value in values):
                    raise _fail("MAPPING_SCHEMA_INVALID", "mapping target IDs invalid", 500 if persisted else 422)
            if item["mapping_state"] == "MAPPED_UNIQUE":
                if not item["selected_target_field_ids"] or item["mapping_confidence"] != "DETERMINISTIC_EXACT" or item["blocking_reason"] is not None:
                    raise _fail("MAPPING_SCHEMA_INVALID", "unique mapping contract invalid", 500 if persisted else 422)
                selected_claims.extend(item["selected_target_field_ids"])
            elif item["selected_target_field_ids"] or item["mapping_confidence"] != "NONE" or not isinstance(item["blocking_reason"], str) or not item["blocking_reason"]:
                raise _fail("MAPPING_SCHEMA_INVALID", "blocked mapping contract invalid", 500 if persisted else 422)
        if len(selected_claims) != len(set(selected_claims)):
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping contains target multi-claim", 500 if persisted else 422)
        summaries = root["bet_summaries"]
        if not isinstance(summaries, list) or not summaries:
            raise _fail("MAPPING_SCHEMA_INVALID", "bet summaries invalid", 500 if persisted else 422)
        for index, summary in enumerate(summaries, 1):
            summary = _require_exact(summary, {"human_bet_id", "operation_index", "bet_type", "required_group_count", "mapped_group_count", "all_required_items_mapped_unique", "column_mapping_complete", "state"}, "bet summary", persisted=persisted)
            if summary["operation_index"] != index or not isinstance(summary["human_bet_id"], str) or not _HUMAN_BET_ID_RE.fullmatch(summary["human_bet_id"]) or summary["bet_type"] not in {"normal", "column"} or summary["state"] not in {"READY", "BLOCKED", "UNSUPPORTED"}:
                raise _fail("MAPPING_SCHEMA_INVALID", "bet summary identity invalid", 500 if persisted else 422)
            if any(isinstance(summary[key], bool) or not isinstance(summary[key], int) or summary[key] < (1 if key == "required_group_count" else 0) for key in ("required_group_count", "mapped_group_count")) or any(not isinstance(summary[key], bool) for key in ("all_required_items_mapped_unique", "column_mapping_complete")):
                raise _fail("MAPPING_SCHEMA_INVALID", "bet summary counts invalid", 500 if persisted else 422)
        exclusions = root["cancelled_exclusions"]
        if not isinstance(exclusions, list) or any(not isinstance(item, Mapping) or item != {"human_bet_id": item.get("human_bet_id"), "source_section": "cancelled_audit_refs", "excluded_reason": "cancelled_non_executable", "mapping_item_count": 0} or not isinstance(item.get("human_bet_id"), str) or not _HUMAN_BET_ID_RE.fullmatch(item["human_bet_id"]) for item in exclusions):
            raise _fail("MAPPING_SCHEMA_INVALID", "cancelled exclusions invalid", 500 if persisted else 422)
        validation = _require_exact(root["validation"], {"required_item_count", "mapped_unique_count", "blocked_item_count", "active_bet_count", "cancelled_exclusion_count", "multi_claim_count", "all_required_items_mapped_unique", "all_column_mappings_complete", "human_preview_available", "client_override_applied"}, "mapping validation", persisted=persisted)
        count_keys = ("required_item_count", "mapped_unique_count", "blocked_item_count", "active_bet_count", "cancelled_exclusion_count", "multi_claim_count")
        if any(isinstance(validation[key], bool) or not isinstance(validation[key], int) or validation[key] < (1 if key in {"required_item_count", "active_bet_count"} else 0) for key in count_keys) or validation["human_preview_available"] is not True or validation["client_override_applied"] is not False:
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping validation invalid", 500 if persisted else 422)
        if validation["required_item_count"] != len(items) or validation["mapped_unique_count"] != sum(item["mapping_state"] == "MAPPED_UNIQUE" for item in items) or validation["blocked_item_count"] != sum(item["mapping_state"] != "MAPPED_UNIQUE" for item in items) or validation["active_bet_count"] != len(summaries) or validation["cancelled_exclusion_count"] != len(exclusions):
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping validation counts mismatch", 500 if persisted else 422)
        if not isinstance(root["blocking_reasons"], list) or len(root["blocking_reasons"]) != len(set(root["blocking_reasons"])) or any(not isinstance(reason, str) or not reason for reason in root["blocking_reasons"]):
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping blocking reasons invalid", 500 if persisted else 422)
        if root["mapping_preview_status"] == "VALID_MAPPING_PREVIEW":
            if root["blocking_reasons"] or validation["all_required_items_mapped_unique"] is not True or validation["all_column_mappings_complete"] is not True or validation["multi_claim_count"] != 0:
                raise _fail("MAPPING_SCHEMA_INVALID", "valid mapping preview invariant failed", 500 if persisted else 422)
        elif not root["blocking_reasons"]:
            raise _fail("MAPPING_SCHEMA_INVALID", "blocked mapping preview lacks reason", 500 if persisted else 422)
        safety = {"mapping_preview_only": True, "read_only_dom_observation": True, "contains_raw_bet_values": False, "contains_current_dom_values": False, "dom_mutation_authorized": False, "click_authorized": False, "typing_authorized": False, "navigation_authorized": False, "events_authorized": False, "approved_for_fill": False, "approved_for_submit": False, "webfill_authorized": False, "submitted": False, "auto_submit": False, "queue_completed": False, "claim_completed": False}
        if root["safety"] != safety:
            raise _fail("MAPPING_SCHEMA_INVALID", "mapping safety invalid", 500 if persisted else 422)
        _require_hash(root["mapping_content_hash"], "mapping content", persisted=persisted)
        _require_hash(root["record_integrity_hash"], "mapping record", persisted=persisted)
        if canonical_sha256(self._mapping_content_projection(root)) != root["mapping_content_hash"] or canonical_sha256({key: value for key, value in root.items() if key != "record_integrity_hash"}) != root["record_integrity_hash"]:
            raise _fail("MAPPING_HASH_MISMATCH", "mapping preview hash mismatch", 409)

    @staticmethod
    def _validate_event(event: Any) -> None:
        root = _require_exact(event, {"schema_version", "event_id", "mapping_preview_id", "event_sequence", "event_type", "occurred_at", "actor", "reason_code", "upstream_state", "mapping_content_hash", "preview_record_integrity_hash", "event_integrity_hash"}, "preview event", persisted=True)
        if root.get("schema_version") != EVENT_SCHEMA_VERSION or not isinstance(root.get("event_id"), str) or not _EVENT_ID_RE.fullmatch(root["event_id"]) or not isinstance(root.get("mapping_preview_id"), str) or not _PREVIEW_ID_RE.fullmatch(root["mapping_preview_id"]) or isinstance(root.get("event_sequence"), bool) or not isinstance(root.get("event_sequence"), int) or root["event_sequence"] < 1 or not isinstance(root.get("actor"), str) or not root["actor"]:
            raise _fail("MAPPING_STORE_CORRUPT", "preview event identity invalid", 500)
        _require_timestamp(root["occurred_at"], "preview event", persisted=True)
        for key in ("mapping_content_hash", "preview_record_integrity_hash", "event_integrity_hash"):
            _require_hash(root[key], key, persisted=True)
        if root["event_type"] == "PREVIEWED":
            if root["event_sequence"] != 1 or root["reason_code"] is not None or root["upstream_state"] is not None:
                raise _fail("MAPPING_STORE_CORRUPT", "PREVIEWED event invalid", 500)
        elif root["event_type"] not in _TERMINAL_EVENTS or root["event_sequence"] != 2 or not isinstance(root["reason_code"], str) or not root["reason_code"] or not isinstance(root["upstream_state"], str) or not root["upstream_state"]:
            raise _fail("MAPPING_STORE_CORRUPT", "terminal preview event invalid", 500)
        if canonical_sha256({key: value for key, value in root.items() if key != "event_integrity_hash"}) != root["event_integrity_hash"]:
            raise _fail("MAPPING_STORE_CORRUPT", "preview event hash mismatch", 500)

    def _validate_transaction(self, transaction: Any) -> None:
        root = _require_exact(transaction, {"schema_version", "request_hash", "idempotency_key_hash", "authority_tuple_hash", "mapping_preview_id", "artifact", "previewed_event"}, "preview transaction", persisted=True)
        if root.get("schema_version") != "vision-webfill-mapping-preview-transaction-v1":
            raise _fail("MAPPING_STORE_CORRUPT", "preview transaction schema invalid", 500)
        self._validate_artifact(root["artifact"], persisted=True)
        self._validate_event(root["previewed_event"])
        for key in ("request_hash", "idempotency_key_hash", "authority_tuple_hash"):
            _require_hash(root[key], key, persisted=True)
        artifact = root["artifact"]
        event = root["previewed_event"]
        if root["mapping_preview_id"] != artifact["mapping_preview_id"] or root["idempotency_key_hash"] != artifact["authority"]["idempotency_key_hash"] or root["authority_tuple_hash"] != canonical_sha256(self._tuple_projection(artifact)) or event["mapping_preview_id"] != artifact["mapping_preview_id"] or event["event_type"] != "PREVIEWED" or event["mapping_content_hash"] != artifact["mapping_content_hash"] or event["preview_record_integrity_hash"] != artifact["record_integrity_hash"]:
            raise _fail("MAPPING_STORE_CORRUPT", "preview transaction relation invalid", 500)

    def _validate_lifecycle_transaction(self, transaction: Any) -> None:
        root = _require_exact(transaction, {"schema_version", "event"}, "preview lifecycle transaction", persisted=True)
        if root.get("schema_version") != "vision-webfill-mapping-preview-lifecycle-transaction-v1":
            raise _fail("MAPPING_STORE_CORRUPT", "preview lifecycle transaction schema invalid", 500)
        self._validate_event(root["event"])
        if root["event"]["event_type"] not in _TERMINAL_EVENTS:
            raise _fail("MAPPING_STORE_CORRUPT", "preview lifecycle transaction is not terminal", 500)

    # ------------------------------ exact request and error mapping

    @staticmethod
    def _decode_request(payload: Mapping[str, Any]) -> dict[str, Any]:
        expected = {"schema_version", "prepare_id", "expected_prepare_record_integrity_hash", "expected_deterministic_plan_hash", "expected_authority_binding_hash", "claim_id", "claim_generation", "claim_session_id", "fencing_token", "adapter_target_profile_id", "adapter_target_profile_version", "expected_adapter_target_profile_integrity_hash", "dom_observation_id", "expected_observation_hash", "expected_page_fingerprint", "expected_page_instance_id", "expected_navigation_epoch", "idempotency_key"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise _fail("MAPPING_REQUEST_INVALID", "mapping request must contain exact identity-only fields", 400)
        value = deepcopy(dict(payload))
        _assert_json_safe(value, label="mapping request", forbid_values=True)
        checks = [
            value["schema_version"] == REQUEST_SCHEMA_VERSION,
            isinstance(value["prepare_id"], str) and bool(_PREPARE_ID_RE.fullmatch(value["prepare_id"])),
            all(isinstance(value[key], str) and bool(_HASH_RE.fullmatch(value[key])) for key in ("expected_prepare_record_integrity_hash", "expected_deterministic_plan_hash", "expected_authority_binding_hash", "expected_adapter_target_profile_integrity_hash", "expected_observation_hash", "expected_page_fingerprint")),
            isinstance(value["claim_id"], str) and bool(_CLAIM_ID_RE.fullmatch(value["claim_id"])),
            isinstance(value["claim_generation"], int) and not isinstance(value["claim_generation"], bool) and value["claim_generation"] >= 1,
            isinstance(value["claim_session_id"], str) and 1 <= len(value["claim_session_id"]) <= 256,
            isinstance(value["fencing_token"], str) and bool(_FENCE_RE.fullmatch(value["fencing_token"])),
            isinstance(value["adapter_target_profile_id"], str) and bool(_PROFILE_ID_RE.fullmatch(value["adapter_target_profile_id"])),
            isinstance(value["adapter_target_profile_version"], int) and not isinstance(value["adapter_target_profile_version"], bool) and value["adapter_target_profile_version"] >= 1,
            isinstance(value["dom_observation_id"], str) and bool(_OBSERVATION_ID_RE.fullmatch(value["dom_observation_id"])),
            isinstance(value["expected_page_instance_id"], str) and bool(_PAGE_INSTANCE_ID_RE.fullmatch(value["expected_page_instance_id"])),
            isinstance(value["expected_navigation_epoch"], int) and not isinstance(value["expected_navigation_epoch"], bool) and value["expected_navigation_epoch"] >= 1,
            isinstance(value["idempotency_key"], str) and bool(_IDEMPOTENCY_RE.fullmatch(value["idempotency_key"])),
        ]
        if not all(checks):
            raise _fail("MAPPING_REQUEST_INVALID", "mapping identity field invalid", 400)
        return value

    @staticmethod
    def _require_owner(request: Mapping[str, Any], principal: Any, consumer: Any, session: Any) -> None:
        if not isinstance(principal, str) or not principal or not isinstance(consumer, str) or not _CONSUMER_ID_RE.fullmatch(consumer) or not isinstance(session, str) or not session or request["claim_session_id"] != session:
            raise _fail("MAPPING_CLAIM_INVALID", "runtime Claim owner/session invalid", 409)

    @staticmethod
    def _request_from_artifact(artifact: Mapping[str, Any], generation: int, fencing_token: str, session: str) -> dict[str, Any]:
        authority = artifact["authority"]
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "prepare_id": authority["prepare"]["prepare_id"],
            "expected_prepare_record_integrity_hash": authority["prepare"]["prepare_record_integrity_hash"],
            "expected_deterministic_plan_hash": authority["prepare"]["deterministic_plan_hash"],
            "expected_authority_binding_hash": authority["prepare"]["authority_binding_hash"],
            "claim_id": authority["claim"]["claim_id"],
            "claim_generation": generation,
            "claim_session_id": session,
            "fencing_token": fencing_token,
            "adapter_target_profile_id": authority["adapter_target_profile"]["adapter_target_profile_id"],
            "adapter_target_profile_version": authority["adapter_target_profile"]["adapter_target_profile_version"],
            "expected_adapter_target_profile_integrity_hash": authority["adapter_target_profile"]["profile_integrity_hash"],
            "dom_observation_id": authority["dom_observation"]["dom_observation_id"],
            "expected_observation_hash": authority["dom_observation"]["observation_hash"],
            "expected_page_fingerprint": authority["dom_observation"]["page_fingerprint"],
            "expected_page_instance_id": authority["dom_observation"]["page_instance_id"],
            "expected_navigation_epoch": authority["dom_observation"]["navigation_epoch"],
            "idempotency_key": "vwmi-" + "0" * 32,
        }

    @staticmethod
    def _map_prepare_error(exc: WebfillPrepareError) -> WebfillMappingError:
        if exc.code in {"PREPARE_CLAIM_EXPIRED"}:
            return _fail("MAPPING_CLAIM_INVALID", "Claim lease expired", 409)
        if exc.code in {"PREPARE_CLAIM_INVALID", "PREPARE_CLOCK_UNSAFE"}:
            return _fail("MAPPING_CLAIM_INVALID", "Claim authority invalid", 409)
        if exc.code in {"PREPARE_HASH_MISMATCH", "PREPARE_NONDETERMINISTIC"}:
            return _fail("MAPPING_PREPARE_INVALID", "Prepare hash/authority invalid", 409)
        return _fail("MAPPING_PREPARE_INVALID", f"Prepare authority failed: {exc.code}", 409)

    @staticmethod
    def _require_preview_id(preview_id: Any) -> None:
        if not isinstance(preview_id, str) or not _PREVIEW_ID_RE.fullmatch(preview_id):
            raise _fail("MAPPING_REQUEST_INVALID", "mapping preview identity invalid", 400)

    def _transaction_path(self, idempotency_key: str) -> Path:
        return self._transactions / f"{hashlib.sha256(idempotency_key.encode()).hexdigest()}.json"
