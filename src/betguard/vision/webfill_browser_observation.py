"""Trusted read-only browser observation adapter core for Gate 3C-4.

The module defines a narrow port and a synthetic-driver-testable capture
service.  It never launches or navigates a browser and has no DOM mutation,
fill, submit, Queue-completion, or Claim-completion surface.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, runtime_checkable

from betguard.vision.candidate_authority import canonical_json_bytes, canonical_sha256
from betguard.vision.webfill_mapping_preview import (
    WebfillAdapterTargetProfileStore,
    WebfillDomObservationStore,
    WebfillMappingError,
    _field_identity_projection,
    _form_fingerprint_projection,
    _form_identity_projection,
    _page_fingerprint_projection,
)
from betguard.vision.webfill_target_profiles import WebfillTargetProfileError, WebfillTargetProfileStore


CAPTURE_REQUEST_SCHEMA_VERSION = "vision-webfill-browser-observation-capture-request-v1"
PAGE_IDENTITY_SCHEMA_VERSION = "vision-webfill-browser-page-identity-v1"
CAPTURE_RESULT_SCHEMA_VERSION = "vision-webfill-browser-observation-capture-result-v1"
AUDIT_EVENT_SCHEMA_VERSION = "vision-webfill-browser-observation-audit-event-v1"

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_ACTION_RE = re.compile(r"^vwoca-[a-f0-9]{32}$")
_CAPTURE_RE = re.compile(r"^vwoc-[a-f0-9]{32}$")
_EVENT_RE = re.compile(r"^vwocae-[a-f0-9]{32}$")
_CONNECTION_RE = re.compile(r"^vwbc-[a-f0-9]{32}$")
_TAB_HANDLE_RE = re.compile(r"^vwth-[a-f0-9]{32}$")
_FRAME_HANDLE_RE = re.compile(r"^vwfh-[a-f0-9]{32}$")
_PROFILE_RE = re.compile(r"^watp-[a-z0-9][a-z0-9._-]{2,63}$")
_LOGICAL_PROFILE_RE = re.compile(r"^wtp-[a-z0-9][a-z0-9._-]{2,63}$")
_SITE_RE = re.compile(r"^site-[a-z0-9][a-z0-9._-]{2,63}$")
_IDEMPOTENCY_RE = re.compile(r"^vwoci-[a-f0-9]{32,128}$")
_RUNTIME_RE = re.compile(r"^vwbr-[a-f0-9]{32}$")
_SESSION_RE = re.compile(r"^vwbs-[a-f0-9]{32}$")
_TAB_RE = re.compile(r"^vwti-[a-f0-9]{32}$")
_FRAME_RE = re.compile(r"^vwfi-[a-f0-9]{32}$")
_DOCUMENT_RE = re.compile(r"^vwdi-[a-f0-9]{32}$")
_PAGE_RE = re.compile(r"^vwpi-[a-f0-9]{32}$")

_REQUEST_KEYS = {
    "schema_version",
    "capture_action_id",
    "browser_connection_id",
    "target_tab_handle",
    "target_frame_handle",
    "expected_site_id",
    "expected_game",
    "adapter_target_profile_id",
    "adapter_target_profile_version",
    "expected_adapter_target_profile_integrity_hash",
    "logical_target_profile_id",
    "logical_target_profile_version",
    "expected_logical_target_profile_integrity_hash",
    "expected_origin_path_policy_hash",
    "expected_page_form_contract_hash",
    "expected_navigation_epoch",
    "capture_idempotency_key",
}
_FORBIDDEN_KEYS = {
    "bets",
    "numbers",
    "number_groups",
    "multiplier",
    "special_play",
    "continuation",
    "logical_plan",
    "selector",
    "selector_override",
    "selectors",
    "dom",
    "dom_html",
    "html",
    "raw_html",
    "raw_value",
    "input_value",
    "field_value",
    "values",
    "occupancy_result",
    "page_marker",
    "page_fingerprint",
    "script",
    "javascript",
    "url",
    "query",
    "fragment",
    "cookie",
    "local_storage",
    "session_storage",
    "screenshot",
    "credential",
    "password",
    "browser_token",
    "requested_result_state",
    "fill",
    "submit",
}
_SAFE_REASON_CODES = {
    "AUTHORITY_BLOCKED",
    "WRONG_TARGET",
    "WRONG_PAGE",
    "WRONG_GAME",
    "PROFILE_MISMATCH",
    "NAVIGATION_RACE",
    "DOM_UNSTABLE",
    "SELECTOR_MISSING",
    "SELECTOR_AMBIGUOUS",
    "UNSUPPORTED_FRAME",
    "UNSUPPORTED_CONTROL",
    "SENSITIVE_FIELD",
    "OBSERVATION_STALE",
    "CAPTURE_TIMEOUT",
    "HASH_MISMATCH",
    "TRANSACTION_RECOVERY_REQUIRED",
}
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class WebfillBrowserObservationError(Exception):
    """Stable value-free capture error."""

    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _error(code: str, message: str, status: int = 422) -> WebfillBrowserObservationError:
    return WebfillBrowserObservationError(code, message, status)


@runtime_checkable
class ReadOnlyBrowserObservationPort(Protocol):
    """Non-mutating port implemented by a separately authorized provider.

    The port returns safe page identities and sanitized allowlisted structures
    only.  It cannot accept scripts, selector overrides, values, or commands.
    """

    def get_page_state(
        self,
        *,
        browser_connection_id: str,
        target_tab_handle: str,
        target_frame_handle: str,
    ) -> Mapping[str, Any]: ...

    def read_allowlisted_structure(
        self,
        *,
        browser_connection_id: str,
        target_tab_handle: str,
        target_frame_handle: str,
        adapter_profile: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _assert_nfc_json(value: Any, *, label: str, reject_forbidden: bool = False) -> None:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float):
        raise _error("CAPTURE_SCHEMA_INVALID", f"{label} contains float")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise _error("CAPTURE_SCHEMA_INVALID", f"{label} is not NFC")
        return
    if isinstance(value, list):
        for item in value:
            _assert_nfc_json(item, label=label, reject_forbidden=reject_forbidden)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or unicodedata.normalize("NFC", key) != key:
                raise _error("CAPTURE_SCHEMA_INVALID", f"{label} key invalid")
            if reject_forbidden and key.lower() in _FORBIDDEN_KEYS:
                raise _error("CAPTURE_REQUEST_INVALID", f"{label} contains prohibited field", 400)
            _assert_nfc_json(item, label=label, reject_forbidden=reject_forbidden)
        return
    raise _error("CAPTURE_SCHEMA_INVALID", f"{label} contains unsupported value")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error("CAPTURE_STORE_CORRUPT", f"cannot read {path.name}", 500) from exc
    if not isinstance(value, dict):
        raise _error("CAPTURE_STORE_CORRUPT", f"{path.name} root invalid", 500)
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(dict(value))
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise _error("CAPTURE_STORE_CORRUPT", f"immutable record exists: {path.name}", 500) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _store_lock(root: Path) -> Iterator[None]:
    key = str(root.resolve())
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
    if not process_lock.acquire(timeout=5.0):
        raise _error("CAPTURE_STORE_BUSY", "capture store is busy", 409)
    stream = None
    try:
        lock_path = root / "locks" / "browser-observation.lock"
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
                    raise _error("CAPTURE_STORE_BUSY", "capture store is busy", 409) from exc
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


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _error("CAPTURE_STORE_CORRUPT", "trusted clock invalid", 500)
    return value.astimezone(timezone.utc)


class WebfillBrowserObservationAdapter:
    """Server-authorized stable capture and immutable Observation ingestion."""

    def __init__(
        self,
        base_dir: Path | str,
        observation_store: WebfillDomObservationStore,
        adapter_profile_store: WebfillAdapterTargetProfileStore,
        logical_profile_store: WebfillTargetProfileStore,
        port: ReadOnlyBrowserObservationPort,
        *,
        clock: Callable[[], datetime] | None = None,
        action_ttl_seconds: int = 300,
        maximum_attempts: int = 2,
        capture_timeout_milliseconds: int = 5000,
    ) -> None:
        if not isinstance(observation_store, WebfillDomObservationStore):
            raise TypeError("observation_store must be WebfillDomObservationStore")
        if not isinstance(adapter_profile_store, WebfillAdapterTargetProfileStore):
            raise TypeError("adapter_profile_store must be WebfillAdapterTargetProfileStore")
        if not isinstance(logical_profile_store, WebfillTargetProfileStore):
            raise TypeError("logical_profile_store must be WebfillTargetProfileStore")
        if not isinstance(port, ReadOnlyBrowserObservationPort):
            raise TypeError("port must implement ReadOnlyBrowserObservationPort")
        if action_ttl_seconds < 1 or maximum_attempts != 2 or not 1 <= capture_timeout_milliseconds <= 30000:
            raise ValueError("capture policy invalid")
        self.base_dir = Path(base_dir)
        self._observations = observation_store
        self._adapter_profiles = adapter_profile_store
        self._logical_profiles = logical_profile_store
        self._port = port
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._action_ttl = action_ttl_seconds
        self._maximum_attempts = maximum_attempts
        self._timeout_ms = capture_timeout_milliseconds
        self._actions = self.base_dir / "capture-actions"
        self._action_idempotency = self.base_dir / "capture-action-idempotency"
        self._results = self.base_dir / "capture-results"
        self._commits = self.base_dir / "capture-commits"
        self._events = self.base_dir / "capture-audit-events"
        self._transactions = self.base_dir / "capture-transactions"
        self._idempotency = self.base_dir / "capture-idempotency"
        self._current = self.base_dir / "current-captures"
        for directory in (
            self._actions,
            self._action_idempotency,
            self._results,
            self._commits,
            self._events,
            self._transactions,
            self._idempotency,
            self._current,
            self.base_dir / "locks",
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def origin_path_policy_hash(adapter_profile: Mapping[str, Any]) -> str:
        site = adapter_profile["site_contract"]
        return canonical_sha256(
            {
                "schema_version": "vision-webfill-browser-origin-path-policy-v1",
                "site_id": site["site_id"],
                "game": site["game"],
                "origin_identity_hash": site["origin_identity_hash"],
                "normalized_path_identity_hash": site["normalized_path_identity_hash"],
            }
        )

    @staticmethod
    def page_form_contract_hash(adapter_profile: Mapping[str, Any]) -> str:
        return canonical_sha256(
            {
                "schema_version": "vision-webfill-browser-page-form-contract-v1",
                "site_contract": adapter_profile["site_contract"],
                "form_contract": adapter_profile["form_contract"],
            }
        )

    def bind_capture_action(
        self,
        *,
        authenticated_principal: str,
        interactive_session_id: str,
        browser_connection_id: str,
        target_tab_handle: str,
        target_frame_handle: str,
        expected_site_id: str,
        expected_game: str,
        adapter_target_profile_id: str,
        adapter_target_profile_version: int,
        expected_adapter_target_profile_integrity_hash: str,
        logical_target_profile_id: str,
        logical_target_profile_version: int,
        expected_logical_target_profile_integrity_hash: str,
        expected_navigation_epoch: int,
        capture_idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_runtime_actor(authenticated_principal, interactive_session_id)
        if not isinstance(capture_idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(capture_idempotency_key):
            raise _error("CAPTURE_REQUEST_INVALID", "capture idempotency identity invalid", 400)
        try:
            profile = self._adapter_profiles.get_profile(
                adapter_target_profile_id, adapter_target_profile_version, require_active=True
            )
            logical = self._logical_profiles.get_profile(
                logical_target_profile_id, logical_target_profile_version, require_active=True
            )
        except (WebfillMappingError, WebfillTargetProfileError) as exc:
            raise _error("PROFILE_MISMATCH", "capture profile authority invalid", 409) from exc
        if (
            profile["profile_integrity_hash"] != expected_adapter_target_profile_integrity_hash
            or logical["profile_integrity_hash"] != expected_logical_target_profile_integrity_hash
            or profile["logical_target_profile_identity"]
            != {
                "target_profile_id": logical["target_profile_id"],
                "target_profile_version": logical["target_profile_version"],
                "profile_integrity_hash": logical["profile_integrity_hash"],
            }
            or profile["site_contract"]["site_id"] != expected_site_id
            or profile["site_contract"]["game"] != expected_game
        ):
            raise _error("PROFILE_MISMATCH", "capture profile identity mismatch", 409)
        request = {
            "schema_version": CAPTURE_REQUEST_SCHEMA_VERSION,
            "capture_action_id": "vwoca-" + uuid.uuid4().hex,
            "browser_connection_id": browser_connection_id,
            "target_tab_handle": target_tab_handle,
            "target_frame_handle": target_frame_handle,
            "expected_site_id": expected_site_id,
            "expected_game": expected_game,
            "adapter_target_profile_id": adapter_target_profile_id,
            "adapter_target_profile_version": adapter_target_profile_version,
            "expected_adapter_target_profile_integrity_hash": expected_adapter_target_profile_integrity_hash,
            "logical_target_profile_id": logical_target_profile_id,
            "logical_target_profile_version": logical_target_profile_version,
            "expected_logical_target_profile_integrity_hash": expected_logical_target_profile_integrity_hash,
            "expected_origin_path_policy_hash": self.origin_path_policy_hash(profile),
            "expected_page_form_contract_hash": self.page_form_contract_hash(profile),
            "expected_navigation_epoch": expected_navigation_epoch,
            "capture_idempotency_key": capture_idempotency_key,
        }
        request = self._decode_request(request)
        now = _utc_now(self._clock)
        action = {
            "schema_version": "vision-webfill-browser-observation-capture-action-v1",
            "capture_action_id": request["capture_action_id"],
            "capture_id": "vwoc-" + uuid.uuid4().hex,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self._action_ttl)).isoformat(),
            "authenticated_principal_hash": hashlib.sha256(authenticated_principal.encode()).hexdigest(),
            "interactive_session_hash": hashlib.sha256(interactive_session_id.encode()).hexdigest(),
            "request_identity": {key: value for key, value in request.items() if key != "capture_idempotency_key"},
            "idempotency_key_hash": hashlib.sha256(capture_idempotency_key.encode()).hexdigest(),
            "action_integrity_hash": "",
        }
        action["action_integrity_hash"] = canonical_sha256(
            {key: value for key, value in action.items() if key != "action_integrity_hash"}
        )
        with _store_lock(self.base_dir):
            idem_path = self._action_idempotency / f"{action['idempotency_key_hash']}.json"
            if idem_path.exists():
                index = _read_json(idem_path)
                existing = self._load_action(index.get("capture_action_id"))
                if existing["request_identity"] != action["request_identity"] or existing[
                    "authenticated_principal_hash"
                ] != action["authenticated_principal_hash"] or existing["interactive_session_hash"] != action[
                    "interactive_session_hash"
                ]:
                    raise _error("CAPTURE_IDEMPOTENCY_CONFLICT", "capture action idempotency conflict", 409)
                return self._request_from_action(existing, capture_idempotency_key)
            _write_immutable(self._actions / f"{action['capture_action_id']}.json", action)
            _write_immutable(idem_path, {"capture_action_id": action["capture_action_id"]})
            self._write_event(
                self._event(
                    action["capture_id"],
                    1,
                    "CAPTURE_AUTHORIZED",
                    now,
                    "TRUSTED_SERVER",
                    self._authority_binding_hash_from_action(action),
                    None,
                    None,
                    None,
                )
            )
        return deepcopy(request)

    def capture(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_principal: str,
        interactive_session_id: str,
    ) -> dict[str, Any]:
        request = self._decode_request(payload)
        self._require_runtime_actor(authenticated_principal, interactive_session_id)
        with _store_lock(self.base_dir):
            self._preflight_transactions()
            action = self._load_action(request["capture_action_id"])
            self._assert_action(action, request, authenticated_principal, interactive_session_id)
            now = _utc_now(self._clock)
            if now >= datetime.fromisoformat(action["expires_at"]):
                raise _error("AUTHORITY_BLOCKED", "capture action expired", 409)
            current_events = self._events_for(action["capture_id"])
            if current_events[-1]["event_type"] == "CAPTURE_FAILED":
                raise _error("AUTHORITY_BLOCKED", "capture action is terminal", 409)
            started = self._event(
                action["capture_id"],
                2,
                "CAPTURE_STARTED",
                now,
                "TRUSTED_ADAPTER",
                self._authority_binding_hash_from_action(action),
                None,
                None,
                None,
            )
            self._write_event(started)
            try:
                profile, logical = self._load_profiles(request)
                transaction_path = self._transactions / f"{action['idempotency_key_hash']}.json"
                stable = self._stable_capture(request, profile)
                current_profile, current_logical = self._load_profiles(request)
                if current_profile != profile or current_logical != logical:
                    raise _error("PROFILE_MISMATCH", "capture Profile changed during capture", 409)
                replay = self._lookup_replay(request)
                if replay is not None:
                    self._assert_live_result(replay, stable, profile)
                    observation = self._observations.get_observation(
                        replay["observation_reference"]["dom_observation_id"], require_current=False
                    )
                    self._observations.register_observation(observation, activate=True)
                    _write_atomic(
                        self._current / f"{request['expected_site_id']}.json",
                        self._current_pointer(replay),
                    )
                    return self._result(replay, replayed=True)
                if transaction_path.exists():
                    transaction = _read_json(transaction_path)
                    self._validate_transaction(transaction)
                    if transaction["request_hash"] != self._request_hash(request):
                        raise _error("CAPTURE_IDEMPOTENCY_CONFLICT", "pending capture request differs", 409)
                    self._assert_transaction_fresh(transaction, stable, profile)
                else:
                    transaction = self._build_transaction(
                        action,
                        request,
                        profile,
                        logical,
                        stable,
                        authenticated_principal,
                        interactive_session_id,
                    )
                    _write_immutable(transaction_path, transaction)
                self._resume_transaction(transaction)
                final_state = self._page_state(request)
                try:
                    self._assert_same_page(stable["final_state"], final_state, "OBSERVATION_STALE")
                except WebfillBrowserObservationError as exc:
                    self._mark_stale(transaction["result"], exc.code)
                    raise
                self._observations.register_observation(transaction["observation"], activate=True)
                pointer = self._current_pointer(transaction["result"])
                _write_atomic(self._current / f"{request['expected_site_id']}.json", pointer)
                return self._result(transaction["result"], replayed=False)
            except WebfillBrowserObservationError as exc:
                if exc.code in _SAFE_REASON_CODES:
                    self._write_failure_once(action, exc.code)
                raise

    def get_capture_state(self, capture_id: str) -> dict[str, Any]:
        with _store_lock(self.base_dir):
            self._preflight_transactions()
            result = self._load_committed_result(capture_id)
            state = self._derive_state(capture_id)
            if state == "COMMITTED":
                try:
                    request = self._request_from_result(result)
                    profile, _ = self._load_profiles(request)
                    live = self._page_state(request)
                    self._assert_live_result(result, {"final_state": live}, profile)
                    pointer = self._load_current(result["browser_page_identity"]["site_id"])
                    if pointer != self._current_pointer(result):
                        raise _error("OBSERVATION_STALE", "capture is not current", 409)
                except WebfillBrowserObservationError as exc:
                    self._mark_stale(result, exc.code)
                    state = "STALE"
            return {
                "schema_version": "vision-webfill-browser-observation-capture-state-v1",
                "state": state,
                "capture_result": deepcopy(result),
                "safety": deepcopy(result["safety"]),
            }

    @contextmanager
    def authoritative_observation(self, capture_id: str) -> Iterator[dict[str, Any]]:
        """Hold capture-store coordination and verify TOCTOU before/after use."""

        with _store_lock(self.base_dir):
            self._preflight_transactions()
            result = self._load_committed_result(capture_id)
            if self._derive_state(capture_id) != "COMMITTED":
                raise _error("OBSERVATION_STALE", "capture is not authoritative", 409)
            request = self._request_from_result(result)
            profile, _ = self._load_profiles(request)
            start = self._page_state(request)
            self._assert_live_result(result, {"final_state": start}, profile)
            observation = self._observations.get_observation(
                result["observation_reference"]["dom_observation_id"], require_current=True
            )
            try:
                yield deepcopy(observation)
            finally:
                end = self._page_state(request)
                try:
                    self._assert_same_page(start, end, "OBSERVATION_STALE")
                    self._assert_live_result(result, {"final_state": end}, profile)
                except WebfillBrowserObservationError as exc:
                    self._mark_stale(result, exc.code)
                    raise

    # ------------------------------ stable capture

    def _stable_capture(self, request: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout_ms / 1000.0
        last_code = "DOM_UNSTABLE"
        for attempt in range(1, self._maximum_attempts + 1):
            if time.monotonic() >= deadline:
                raise _error("CAPTURE_TIMEOUT", "capture timed out", 409)
            start_time = _utc_now(self._clock)
            start = self._page_state(request)
            self._assert_page_authority(start, request, profile)
            first = self._structure(request, profile)
            middle = self._page_state(request)
            try:
                self._assert_same_page(start, middle, "NAVIGATION_RACE")
            except WebfillBrowserObservationError as exc:
                last_code = self._stability_error_code(start, middle, exc.code)
                continue
            second = self._structure(request, profile)
            end = self._page_state(request)
            try:
                self._assert_same_page(start, end, "NAVIGATION_RACE")
            except WebfillBrowserObservationError as exc:
                last_code = self._stability_error_code(start, end, exc.code)
                continue
            if canonical_json_bytes(first) != canonical_json_bytes(second):
                last_code = "DOM_UNSTABLE"
                continue
            observation = self._build_observation(request, start, first, _utc_now(self._clock))
            contract = profile["form_contract"]
            forms = [form for form in observation["forms"] if form["form_id"] == contract["expected_form_id"]]
            if not forms:
                raise _error("SELECTOR_MISSING", "required form identity missing", 409)
            if len(forms) > 1:
                raise _error("SELECTOR_AMBIGUOUS", "required form identity ambiguous", 409)
            if observation["page_fingerprint"] != profile["site_contract"]["expected_page_fingerprint"]:
                raise _error("WRONG_PAGE", "captured page fingerprint does not match Profile", 409)
            if forms[0]["form_fingerprint"] != contract["expected_form_fingerprint"]:
                raise _error("WRONG_PAGE", "captured form identity does not match Profile", 409)
            final_state = self._page_state(request)
            self._assert_same_page(start, final_state, "NAVIGATION_RACE")
            ended = _utc_now(self._clock)
            return {
                "attempt": attempt,
                "started_at": start_time,
                "ended_at": ended,
                "start_state": start,
                "final_state": final_state,
                "structure": first,
                "observation": observation,
            }
        raise _error(last_code, "page did not produce a stable capture", 409)

    def _page_state(self, request: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = self._port.get_page_state(
                browser_connection_id=request["browser_connection_id"],
                target_tab_handle=request["target_tab_handle"],
                target_frame_handle=request["target_frame_handle"],
            )
        except Exception as exc:
            raise _error("WRONG_TARGET", "target page is unavailable", 409) from exc
        expected = {
            "schema_version",
            "browser_runtime_id",
            "browser_session_id",
            "browser_connection_id",
            "connection_generation",
            "target_tab_handle",
            "target_frame_handle",
            "tab_instance_id",
            "frame_instance_id",
            "document_instance_id",
            "page_instance_id",
            "navigation_epoch",
            "dom_mutation_generation",
            "site_id",
            "game",
            "origin_identity_hash",
            "normalized_path_identity_hash",
            "frame_scope",
            "connected",
            "runtime_continuity_proven",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise _error("WRONG_TARGET", "target page identity shape invalid", 409)
        state = deepcopy(dict(value))
        _assert_nfc_json(state, label="page identity")
        patterns = {
            "browser_runtime_id": _RUNTIME_RE,
            "browser_session_id": _SESSION_RE,
            "browser_connection_id": _CONNECTION_RE,
            "target_tab_handle": _TAB_HANDLE_RE,
            "target_frame_handle": _FRAME_HANDLE_RE,
            "tab_instance_id": _TAB_RE,
            "frame_instance_id": _FRAME_RE,
            "document_instance_id": _DOCUMENT_RE,
            "page_instance_id": _PAGE_RE,
        }
        if any(not isinstance(state[key], str) or not pattern.fullmatch(state[key]) for key, pattern in patterns.items()):
            raise _error("WRONG_TARGET", "target page identity invalid", 409)
        for key, minimum in (("connection_generation", 1), ("navigation_epoch", 1), ("dom_mutation_generation", 0)):
            if isinstance(state[key], bool) or not isinstance(state[key], int) or state[key] < minimum:
                raise _error("WRONG_TARGET", "target page generation invalid", 409)
        if state["connected"] is not True or state["runtime_continuity_proven"] is not True:
            raise _error("WRONG_TARGET", "target runtime continuity unavailable", 409)
        if state["frame_scope"] != "MAIN_SAME_ORIGIN_LIGHT_DOM":
            raise _error("UNSUPPORTED_FRAME", "target frame scope unsupported", 409)
        for key in ("origin_identity_hash", "normalized_path_identity_hash"):
            if not isinstance(state[key], str) or not _HASH_RE.fullmatch(state[key]):
                raise _error("WRONG_PAGE", "target page identity hash invalid", 409)
        return state

    def _structure(self, request: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = self._port.read_allowlisted_structure(
                browser_connection_id=request["browser_connection_id"],
                target_tab_handle=request["target_tab_handle"],
                target_frame_handle=request["target_frame_handle"],
                adapter_profile=deepcopy(profile),
            )
        except WebfillBrowserObservationError:
            raise
        except Exception as exc:
            raise _error("DOM_UNSTABLE", "allowlisted structure read failed", 409) from exc
        expected = {
            "schema_version",
            "page_marker_hashes",
            "forms",
            "sensitive_field_detected",
            "unsupported_control_detected",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise _error("DOM_UNSTABLE", "sanitized structure shape invalid", 409)
        structure = deepcopy(dict(value))
        _assert_nfc_json(structure, label="sanitized structure", reject_forbidden=True)
        if structure["schema_version"] != "vision-webfill-read-only-structure-v1":
            raise _error("DOM_UNSTABLE", "sanitized structure schema invalid", 409)
        if structure["sensitive_field_detected"] is True:
            raise _error("SENSITIVE_FIELD", "required sensitive control blocked", 409)
        if structure["unsupported_control_detected"] is True:
            raise _error("UNSUPPORTED_CONTROL", "required control unsupported", 409)
        if structure["sensitive_field_detected"] is not False or structure["unsupported_control_detected"] is not False:
            raise _error("DOM_UNSTABLE", "sanitized structure flags invalid", 409)
        if not isinstance(structure["forms"], list) or not structure["forms"]:
            raise _error("SELECTOR_MISSING", "required form missing", 409)
        selector_claims: dict[tuple[str, str], int] = {}
        for form in structure["forms"]:
            if not isinstance(form, Mapping) or not isinstance(form.get("fields"), list):
                raise _error("DOM_UNSTABLE", "sanitized form invalid", 409)
            for field in form["fields"]:
                if not isinstance(field, Mapping) or not isinstance(field.get("selector_candidates"), list):
                    raise _error("DOM_UNSTABLE", "sanitized field invalid", 409)
                for selector in field["selector_candidates"]:
                    if not isinstance(selector, Mapping):
                        raise _error("DOM_UNSTABLE", "sanitized selector invalid", 409)
                    key = (selector.get("selector_kind"), selector.get("selector_text"))
                    selector_claims[key] = selector_claims.get(key, 0) + 1
        if any(count > 1 for count in selector_claims.values()):
            raise _error("SELECTOR_AMBIGUOUS", "structural selector is ambiguous", 409)
        return structure

    @staticmethod
    def _assert_page_authority(state: Mapping[str, Any], request: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
        if state["browser_connection_id"] != request["browser_connection_id"] or state["target_tab_handle"] != request[
            "target_tab_handle"
        ] or state["target_frame_handle"] != request["target_frame_handle"]:
            raise _error("WRONG_TARGET", "target alias mismatch", 409)
        if state["site_id"] != request["expected_site_id"] or state["site_id"] != profile["site_contract"]["site_id"]:
            raise _error("WRONG_PAGE", "site identity mismatch", 409)
        if state["game"] != request["expected_game"] or state["game"] != profile["site_contract"]["game"]:
            raise _error("WRONG_GAME", "game identity mismatch", 409)
        if state["navigation_epoch"] != request["expected_navigation_epoch"]:
            raise _error("OBSERVATION_STALE", "navigation epoch mismatch", 409)
        if state["origin_identity_hash"] != profile["site_contract"]["origin_identity_hash"] or state[
            "normalized_path_identity_hash"
        ] != profile["site_contract"]["normalized_path_identity_hash"]:
            raise _error("WRONG_PAGE", "origin/path policy mismatch", 409)

    @staticmethod
    def _assert_same_page(start: Mapping[str, Any], end: Mapping[str, Any], code: str) -> None:
        keys = (
            "browser_runtime_id",
            "browser_session_id",
            "browser_connection_id",
            "connection_generation",
            "tab_instance_id",
            "frame_instance_id",
            "document_instance_id",
            "page_instance_id",
            "navigation_epoch",
            "dom_mutation_generation",
            "site_id",
            "game",
            "origin_identity_hash",
            "normalized_path_identity_hash",
        )
        if any(start[key] != end[key] for key in keys):
            raise _error(code, "page identity changed during capture", 409)

    @staticmethod
    def _stability_error_code(start: Mapping[str, Any], end: Mapping[str, Any], fallback: str) -> str:
        differences = {key for key in start if key in end and start[key] != end[key]}
        if differences == {"dom_mutation_generation"}:
            return "DOM_UNSTABLE"
        return fallback

    def _build_observation(
        self, request: Mapping[str, Any], state: Mapping[str, Any], structure: Mapping[str, Any], observed_at: datetime
    ) -> dict[str, Any]:
        forms: list[dict[str, Any]] = []
        for form_position, source_form in enumerate(structure["forms"], 1):
            expected_form_keys = {"document_order", "method_category", "structural_marker_hashes", "fields"}
            if set(source_form) != expected_form_keys or source_form["document_order"] != form_position:
                raise _error("DOM_UNSTABLE", "sanitized form ordering invalid", 409)
            form = {
                "form_id": "",
                "document_order": source_form["document_order"],
                "method_category": source_form["method_category"],
                "structural_marker_hashes": deepcopy(source_form["structural_marker_hashes"]),
                "fields": [],
                "form_fingerprint": "",
            }
            for field_position, source_field in enumerate(source_form["fields"], 1):
                expected_field_keys = {
                    "document_order",
                    "tag_name",
                    "input_type",
                    "label_text",
                    "accessible_name",
                    "selector_candidates",
                    "structural_marker_hashes",
                    "visible",
                    "disabled",
                    "readonly",
                    "occupancy",
                }
                if set(source_field) != expected_field_keys or source_field["document_order"] != field_position:
                    raise _error("DOM_UNSTABLE", "sanitized field ordering invalid", 409)
                field = deepcopy(dict(source_field))
                field["field_id"] = ""
                field["field_identity_hash"] = ""
                field["field_identity_hash"] = canonical_sha256(_field_identity_projection(form, field))
                field["field_id"] = "vwf-" + field["field_identity_hash"][:32]
                form["fields"].append(field)
            form_hash = canonical_sha256(_form_identity_projection(form))
            form["form_id"] = "vwform-" + form_hash[:32]
            form["form_fingerprint"] = canonical_sha256(_form_fingerprint_projection(form))
            forms.append(form)
        observation = {
            "schema_version": "vision-webfill-dom-observation-v1",
            "dom_observation_id": "vwdo-" + uuid.uuid4().hex,
            "observed_at": observed_at.isoformat(),
            "page_instance_id": state["page_instance_id"],
            "navigation_epoch": state["navigation_epoch"],
            "page_identity": {
                "site_id": state["site_id"],
                "game": state["game"],
                "origin_identity_hash": state["origin_identity_hash"],
                "normalized_path_identity_hash": state["normalized_path_identity_hash"],
                "structural_marker_hashes": deepcopy(structure["page_marker_hashes"]),
            },
            "forms": forms,
            "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1",
            "page_fingerprint": "",
            "observation_hash": "",
            "safety": {
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
            },
        }
        observation["page_fingerprint"] = canonical_sha256(_page_fingerprint_projection(observation))
        observation["observation_hash"] = canonical_sha256(
            {key: value for key, value in observation.items() if key != "observation_hash"}
        )
        try:
            WebfillDomObservationStore._validate(observation)
        except WebfillMappingError as exc:
            raise _error("WRONG_PAGE", "captured structure is not a valid Observation", 409) from exc
        return observation

    # ------------------------------ transaction and immutable result

    def _build_transaction(
        self,
        action: Mapping[str, Any],
        request: Mapping[str, Any],
        profile: Mapping[str, Any],
        logical: Mapping[str, Any],
        stable: Mapping[str, Any],
        principal: str,
        session: str,
        *,
        capture_id: str | None = None,
        observation_id: str | None = None,
        created_at: str | None = None,
        observation_generation: int | None = None,
    ) -> dict[str, Any]:
        observation = deepcopy(stable["observation"])
        if observation_id is not None:
            observation["dom_observation_id"] = observation_id
            observation["observation_hash"] = canonical_sha256(
                {key: value for key, value in observation.items() if key != "observation_hash"}
            )
        generation = observation_generation or self._next_observation_generation(stable["final_state"])
        captured_at = stable["ended_at"].isoformat()
        page_identity = {
            "schema_version": PAGE_IDENTITY_SCHEMA_VERSION,
            "browser_runtime_id": stable["final_state"]["browser_runtime_id"],
            "browser_session_id": stable["final_state"]["browser_session_id"],
            "browser_connection_id": stable["final_state"]["browser_connection_id"],
            "connection_generation": stable["final_state"]["connection_generation"],
            "tab_instance_id": stable["final_state"]["tab_instance_id"],
            "frame_instance_id": stable["final_state"]["frame_instance_id"],
            "document_instance_id": stable["final_state"]["document_instance_id"],
            "page_instance_id": stable["final_state"]["page_instance_id"],
            "navigation_epoch": stable["final_state"]["navigation_epoch"],
            "observation_generation": generation,
            "dom_mutation_generation": stable["final_state"]["dom_mutation_generation"],
            "site_id": stable["final_state"]["site_id"],
            "game": stable["final_state"]["game"],
            "origin_identity_hash": stable["final_state"]["origin_identity_hash"],
            "normalized_path_identity_hash": stable["final_state"]["normalized_path_identity_hash"],
            "captured_at": captured_at,
            "identity_integrity_hash": "",
            "safety": {
                "opaque_server_aliases_only": True,
                "raw_browser_target_id_persisted": False,
                "raw_connection_token_persisted": False,
                "raw_url_persisted": False,
                "query_or_fragment_persisted": False,
                "cookie_or_storage_identity_persisted": False,
                "credential_persisted": False,
            },
        }
        page_identity["identity_integrity_hash"] = canonical_sha256(
            {key: value for key, value in page_identity.items() if key != "identity_integrity_hash"}
        )
        authority = {
            "capture_action_id_hash": hashlib.sha256(action["capture_action_id"].encode()).hexdigest(),
            "capture_request_hash": self._request_hash(request),
            "authenticated_principal_hash": hashlib.sha256(principal.encode()).hexdigest(),
            "interactive_session_hash": hashlib.sha256(session.encode()).hexdigest(),
            "adapter_target_profile": {
                "adapter_target_profile_id": profile["adapter_target_profile_id"],
                "adapter_target_profile_version": profile["adapter_target_profile_version"],
                "profile_integrity_hash": profile["profile_integrity_hash"],
            },
            "logical_target_profile": {
                "target_profile_id": logical["target_profile_id"],
                "target_profile_version": logical["target_profile_version"],
                "profile_integrity_hash": logical["profile_integrity_hash"],
            },
            "origin_path_policy_hash": request["expected_origin_path_policy_hash"],
            "page_form_contract_hash": request["expected_page_form_contract_hash"],
            "idempotency_key_hash": hashlib.sha256(request["capture_idempotency_key"].encode()).hexdigest(),
        }
        stable_capture = {
            "attempt": stable["attempt"],
            "maximum_attempts": self._maximum_attempts,
            "timeout_milliseconds": self._timeout_ms,
            "started_at": stable["started_at"].isoformat(),
            "ended_at": stable["ended_at"].isoformat(),
            "read_count": 2,
            "first_structure_hash": observation["page_fingerprint"],
            "second_structure_hash": observation["page_fingerprint"],
            "start_navigation_epoch": stable["start_state"]["navigation_epoch"],
            "end_navigation_epoch": stable["final_state"]["navigation_epoch"],
            "start_dom_mutation_generation": stable["start_state"]["dom_mutation_generation"],
            "end_dom_mutation_generation": stable["final_state"]["dom_mutation_generation"],
            "document_identity_stable": True,
            "page_identity_stable": True,
            "structure_equal": True,
            "final_precommit_check_passed": True,
            "postcommit_check_passed": True,
        }
        reference = {
            "dom_observation_id": observation["dom_observation_id"],
            "observation_hash": observation["observation_hash"],
            "page_fingerprint": observation["page_fingerprint"],
            "page_instance_id": observation["page_instance_id"],
            "navigation_epoch": observation["navigation_epoch"],
            "observation_generation": generation,
            "dom_mutation_generation": stable["final_state"]["dom_mutation_generation"],
            "committed": True,
        }
        result = {
            "schema_version": CAPTURE_RESULT_SCHEMA_VERSION,
            "capture_id": capture_id or action["capture_id"],
            "created_at": created_at or captured_at,
            "state_at_creation": "COMMITTED",
            "authority": authority,
            "browser_page_identity": page_identity,
            "stable_capture": stable_capture,
            "observation_reference": reference,
            "capture_content_hash": "",
            "record_integrity_hash": "",
            "safety": {
                "read_only_capture": True,
                "identity_reference_only": True,
                "raw_dom_values_exposed": False,
                "raw_dom_values_persisted": False,
                "raw_dom_values_hashed": False,
                "raw_html_persisted": False,
                "cookie_or_storage_read": False,
                "screenshot_captured": False,
                "arbitrary_script_executed": False,
                "browser_launched": False,
                "navigation_performed": False,
                "dom_mutation_performed": False,
                "click_performed": False,
                "typing_performed": False,
                "events_dispatched": False,
                "fill_authorized": False,
                "submit_authorized": False,
                "queue_or_claim_completed": False,
            },
        }
        result["capture_content_hash"] = canonical_sha256(self._capture_content_projection(result))
        result["record_integrity_hash"] = canonical_sha256(
            {key: value for key, value in result.items() if key != "record_integrity_hash"}
        )
        committed_event = self._event(
            result["capture_id"],
            3,
            "CAPTURE_COMMITTED",
            stable["ended_at"],
            "TRUSTED_ADAPTER",
            self._authority_binding_hash(result),
            page_identity["identity_integrity_hash"],
            {
                "dom_observation_id": reference["dom_observation_id"],
                "observation_hash": reference["observation_hash"],
                "page_fingerprint": reference["page_fingerprint"],
                "page_instance_id": reference["page_instance_id"],
                "navigation_epoch": reference["navigation_epoch"],
            },
            None,
        )
        transaction = {
            "schema_version": "vision-webfill-browser-observation-capture-transaction-v1",
            "request_hash": self._request_hash(request),
            "idempotency_key_hash": authority["idempotency_key_hash"],
            "capture_id": result["capture_id"],
            "observation": observation,
            "result": result,
            "committed_event": committed_event,
        }
        self._validate_transaction(transaction)
        return transaction

    def _resume_transaction(self, transaction: Mapping[str, Any]) -> None:
        self._validate_transaction(transaction)
        observation = transaction["observation"]
        result = transaction["result"]
        self._observations.register_observation(observation, activate=False)
        outputs = [
            (self._results / f"{result['capture_id']}.json", result),
            (self._events / result["capture_id"] / "000003.json", transaction["committed_event"]),
            (
                self._idempotency / f"{transaction['idempotency_key_hash']}.json",
                {
                    "capture_id": result["capture_id"],
                    "request_hash": transaction["request_hash"],
                    "record_integrity_hash": result["record_integrity_hash"],
                },
            ),
        ]
        for path, value in outputs:
            if path.exists():
                if _read_json(path) != value:
                    raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture transaction output conflict", 500)
            else:
                _write_immutable(path, value)
        commit = {
            "capture_id": result["capture_id"],
            "observation_hash": observation["observation_hash"],
            "record_integrity_hash": result["record_integrity_hash"],
        }
        commit_path = self._commits / f"{result['capture_id']}.json"
        if commit_path.exists():
            if _read_json(commit_path) != commit:
                raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture commit conflict", 500)
        else:
            _write_immutable(commit_path, commit)

    def _preflight_transactions(self) -> None:
        for path in sorted(self._transactions.glob("*.json")):
            transaction = _read_json(path)
            self._validate_transaction(transaction)
            if path.stem != transaction["idempotency_key_hash"]:
                raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture transaction filename mismatch", 500)
            self._preflight_outputs(transaction)

    def _preflight_outputs(self, transaction: Mapping[str, Any]) -> None:
        observation = transaction["observation"]
        result = transaction["result"]
        expected = [
            (self._results / f"{result['capture_id']}.json", result),
            (self._events / result["capture_id"] / "000003.json", transaction["committed_event"]),
            (
                self._idempotency / f"{transaction['idempotency_key_hash']}.json",
                {
                    "capture_id": result["capture_id"],
                    "request_hash": transaction["request_hash"],
                    "record_integrity_hash": result["record_integrity_hash"],
                },
            ),
            (
                self._commits / f"{result['capture_id']}.json",
                {
                    "capture_id": result["capture_id"],
                    "observation_hash": observation["observation_hash"],
                    "record_integrity_hash": result["record_integrity_hash"],
                },
            ),
        ]
        for path, value in expected:
            if path.exists() and _read_json(path) != value:
                raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture partial output conflict", 500)

    def _lookup_replay(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        idem_hash = hashlib.sha256(request["capture_idempotency_key"].encode()).hexdigest()
        path = self._idempotency / f"{idem_hash}.json"
        if not path.exists():
            return None
        index = _read_json(path)
        expected = {"capture_id", "request_hash", "record_integrity_hash"}
        if set(index) != expected or index["request_hash"] != self._request_hash(request):
            raise _error("CAPTURE_IDEMPOTENCY_CONFLICT", "capture idempotency conflict", 409)
        result = self._load_committed_result(index["capture_id"])
        if result["record_integrity_hash"] != index["record_integrity_hash"]:
            raise _error("HASH_MISMATCH", "capture replay integrity mismatch", 409)
        return result

    def _load_committed_result(self, capture_id: str) -> dict[str, Any]:
        if not isinstance(capture_id, str) or not _CAPTURE_RE.fullmatch(capture_id):
            raise _error("CAPTURE_NOT_FOUND", "capture identity invalid", 404)
        result_path = self._results / f"{capture_id}.json"
        commit_path = self._commits / f"{capture_id}.json"
        if not result_path.exists() or not commit_path.exists():
            raise _error("CAPTURE_NOT_FOUND", "committed capture not found", 404)
        result = _read_json(result_path)
        self._validate_result(result)
        commit = _read_json(commit_path)
        expected = {
            "capture_id": capture_id,
            "observation_hash": result["observation_reference"]["observation_hash"],
            "record_integrity_hash": result["record_integrity_hash"],
        }
        if commit != expected:
            raise _error("HASH_MISMATCH", "capture commit mismatch", 409)
        return result

    # ------------------------------ freshness and audit

    def _assert_live_result(
        self, result: Mapping[str, Any], stable: Mapping[str, Any], profile: Mapping[str, Any]
    ) -> None:
        state = stable["final_state"]
        identity = result["browser_page_identity"]
        for key in (
            "browser_runtime_id",
            "browser_session_id",
            "browser_connection_id",
            "connection_generation",
            "tab_instance_id",
            "frame_instance_id",
            "document_instance_id",
            "page_instance_id",
            "navigation_epoch",
            "dom_mutation_generation",
            "site_id",
            "game",
            "origin_identity_hash",
            "normalized_path_identity_hash",
        ):
            if identity[key] != state[key]:
                raise _error("OBSERVATION_STALE", "capture page identity changed", 409)
        adapter = result["authority"]["adapter_target_profile"]
        if adapter != {
            "adapter_target_profile_id": profile["adapter_target_profile_id"],
            "adapter_target_profile_version": profile["adapter_target_profile_version"],
            "profile_integrity_hash": profile["profile_integrity_hash"],
        }:
            raise _error("PROFILE_MISMATCH", "capture Profile changed", 409)
        observation = stable.get("observation")
        if observation is not None and (
            observation["page_fingerprint"] != result["observation_reference"]["page_fingerprint"]
            or observation["page_instance_id"] != result["observation_reference"]["page_instance_id"]
            or observation["navigation_epoch"] != result["observation_reference"]["navigation_epoch"]
        ):
            raise _error("OBSERVATION_STALE", "captured structure changed", 409)

    def _mark_stale(self, result: Mapping[str, Any], reason: str) -> None:
        events = self._events_for(result["capture_id"])
        if events[-1]["event_type"] == "OBSERVATION_STALE":
            return
        if events[-1]["event_type"] != "CAPTURE_COMMITTED":
            return
        event = self._event(
            result["capture_id"],
            4,
            "OBSERVATION_STALE",
            _utc_now(self._clock),
            "TRUSTED_ADAPTER",
            self._authority_binding_hash(result),
            result["browser_page_identity"]["identity_integrity_hash"],
            {
                "dom_observation_id": result["observation_reference"]["dom_observation_id"],
                "observation_hash": result["observation_reference"]["observation_hash"],
                "page_fingerprint": result["observation_reference"]["page_fingerprint"],
                "page_instance_id": result["observation_reference"]["page_instance_id"],
                "navigation_epoch": result["observation_reference"]["navigation_epoch"],
            },
            "OBSERVATION_STALE",
        )
        self._write_event(event)

    def _write_failure_once(self, action: Mapping[str, Any], reason: str) -> None:
        events = self._events_for(action["capture_id"])
        if events[-1]["event_type"] in {"CAPTURE_FAILED", "CAPTURE_COMMITTED", "OBSERVATION_STALE"}:
            return
        event = self._event(
            action["capture_id"],
            3,
            "CAPTURE_FAILED",
            _utc_now(self._clock),
            "TRUSTED_ADAPTER",
            self._authority_binding_hash_from_action(action),
            None,
            None,
            reason if reason in _SAFE_REASON_CODES else "AUTHORITY_BLOCKED",
        )
        self._write_event(event)

    def _event(
        self,
        capture_id: str,
        sequence: int,
        event_type: str,
        occurred_at: datetime,
        actor_kind: str,
        authority_binding_hash: str,
        page_identity_hash: str | None,
        observation_reference: Mapping[str, Any] | None,
        reason: str | None,
    ) -> dict[str, Any]:
        event = {
            "schema_version": AUDIT_EVENT_SCHEMA_VERSION,
            "event_id": "vwocae-" + uuid.uuid4().hex,
            "capture_id": capture_id,
            "event_sequence": sequence,
            "event_type": event_type,
            "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
            "actor_kind": actor_kind,
            "authority_binding_hash": authority_binding_hash,
            "browser_page_identity_hash": page_identity_hash,
            "observation_reference": deepcopy(observation_reference),
            "reason_code": reason,
            "safe_context_hashes": [],
            "event_integrity_hash": "",
            "safety": {
                "value_free": True,
                "html_free": True,
                "url_free": True,
                "selector_free": True,
                "cookie_storage_free": True,
                "credential_free": True,
                "browser_token_free": True,
                "screenshot_free": True,
                "fixed_error_codes_only": True,
            },
        }
        event["event_integrity_hash"] = canonical_sha256(
            {key: value for key, value in event.items() if key != "event_integrity_hash"}
        )
        self._validate_event(event)
        return event

    def _write_event(self, event: Mapping[str, Any]) -> None:
        self._validate_event(event)
        path = self._events / event["capture_id"] / f"{event['event_sequence']:06d}.json"
        if path.exists():
            existing = _read_json(path)
            comparable = {key: value for key, value in event.items() if key not in {"event_id", "occurred_at", "event_integrity_hash"}}
            existing_comparable = {
                key: value for key, value in existing.items() if key not in {"event_id", "occurred_at", "event_integrity_hash"}
            }
            if comparable != existing_comparable:
                raise _error("CAPTURE_STORE_CORRUPT", "capture audit event conflict", 500)
            return
        _write_immutable(path, event)

    def _events_for(self, capture_id: str) -> list[dict[str, Any]]:
        directory = self._events / capture_id
        events = [_read_json(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []
        for index, event in enumerate(events, 1):
            self._validate_event(event)
            if event["capture_id"] != capture_id or event["event_sequence"] != index:
                raise _error("CAPTURE_STORE_CORRUPT", "capture audit sequence invalid", 500)
        if not events or events[0]["event_type"] != "CAPTURE_AUTHORIZED":
            raise _error("CAPTURE_STORE_CORRUPT", "capture audit root invalid", 500)
        return events

    def _derive_state(self, capture_id: str) -> str:
        event_type = self._events_for(capture_id)[-1]["event_type"]
        return {
            "CAPTURE_AUTHORIZED": "AUTHORIZED",
            "CAPTURE_STARTED": "CAPTURING",
            "CAPTURE_COMMITTED": "COMMITTED",
            "CAPTURE_FAILED": "FAILED",
            "OBSERVATION_STALE": "STALE",
            "RECOVERY_REQUIRED": "FAILED",
        }[event_type]

    # ------------------------------ strict validation/helpers

    @staticmethod
    def _decode_request(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != _REQUEST_KEYS:
            raise _error("CAPTURE_REQUEST_INVALID", "capture request must be exact identity-only", 400)
        value = deepcopy(dict(payload))
        _assert_nfc_json(value, label="capture request", reject_forbidden=True)
        checks = [
            value["schema_version"] == CAPTURE_REQUEST_SCHEMA_VERSION,
            isinstance(value["capture_action_id"], str) and bool(_ACTION_RE.fullmatch(value["capture_action_id"])),
            isinstance(value["browser_connection_id"], str) and bool(_CONNECTION_RE.fullmatch(value["browser_connection_id"])),
            isinstance(value["target_tab_handle"], str) and bool(_TAB_HANDLE_RE.fullmatch(value["target_tab_handle"])),
            isinstance(value["target_frame_handle"], str) and bool(_FRAME_HANDLE_RE.fullmatch(value["target_frame_handle"])),
            isinstance(value["expected_site_id"], str) and bool(_SITE_RE.fullmatch(value["expected_site_id"])),
            isinstance(value["expected_game"], str) and 1 <= len(value["expected_game"]) <= 64,
            isinstance(value["adapter_target_profile_id"], str) and bool(_PROFILE_RE.fullmatch(value["adapter_target_profile_id"])),
            isinstance(value["adapter_target_profile_version"], int) and not isinstance(value["adapter_target_profile_version"], bool) and value["adapter_target_profile_version"] >= 1,
            isinstance(value["logical_target_profile_id"], str) and bool(_LOGICAL_PROFILE_RE.fullmatch(value["logical_target_profile_id"])),
            isinstance(value["logical_target_profile_version"], int) and not isinstance(value["logical_target_profile_version"], bool) and value["logical_target_profile_version"] >= 1,
            isinstance(value["expected_navigation_epoch"], int) and not isinstance(value["expected_navigation_epoch"], bool) and value["expected_navigation_epoch"] >= 1,
            isinstance(value["capture_idempotency_key"], str) and bool(_IDEMPOTENCY_RE.fullmatch(value["capture_idempotency_key"])),
        ]
        for key in (
            "expected_adapter_target_profile_integrity_hash",
            "expected_logical_target_profile_integrity_hash",
            "expected_origin_path_policy_hash",
            "expected_page_form_contract_hash",
        ):
            checks.append(isinstance(value[key], str) and bool(_HASH_RE.fullmatch(value[key])))
        if not all(checks):
            raise _error("CAPTURE_REQUEST_INVALID", "capture request identity invalid", 400)
        return value

    def _load_profiles(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            profile = self._adapter_profiles.get_profile(
                request["adapter_target_profile_id"], request["adapter_target_profile_version"], require_active=True
            )
            logical = self._logical_profiles.get_profile(
                request["logical_target_profile_id"], request["logical_target_profile_version"], require_active=True
            )
        except (WebfillMappingError, WebfillTargetProfileError) as exc:
            raise _error("PROFILE_MISMATCH", "capture Profile authority unavailable", 409) from exc
        if (
            profile["profile_integrity_hash"] != request["expected_adapter_target_profile_integrity_hash"]
            or logical["profile_integrity_hash"] != request["expected_logical_target_profile_integrity_hash"]
            or self.origin_path_policy_hash(profile) != request["expected_origin_path_policy_hash"]
            or self.page_form_contract_hash(profile) != request["expected_page_form_contract_hash"]
            or profile["logical_target_profile_identity"]
            != {
                "target_profile_id": logical["target_profile_id"],
                "target_profile_version": logical["target_profile_version"],
                "profile_integrity_hash": logical["profile_integrity_hash"],
            }
        ):
            raise _error("PROFILE_MISMATCH", "capture Profile identity/hash mismatch", 409)
        return profile, logical

    def _load_action(self, action_id: Any) -> dict[str, Any]:
        if not isinstance(action_id, str) or not _ACTION_RE.fullmatch(action_id):
            raise _error("AUTHORITY_BLOCKED", "capture action identity invalid", 403)
        path = self._actions / f"{action_id}.json"
        if not path.exists():
            raise _error("AUTHORITY_BLOCKED", "capture action not found", 403)
        action = _read_json(path)
        expected = {
            "schema_version",
            "capture_action_id",
            "capture_id",
            "issued_at",
            "expires_at",
            "authenticated_principal_hash",
            "interactive_session_hash",
            "request_identity",
            "idempotency_key_hash",
            "action_integrity_hash",
        }
        if set(action) != expected or action["schema_version"] != "vision-webfill-browser-observation-capture-action-v1" or action["capture_action_id"] != action_id or not _CAPTURE_RE.fullmatch(action["capture_id"]):
            raise _error("CAPTURE_STORE_CORRUPT", "capture action shape invalid", 500)
        if action["action_integrity_hash"] != canonical_sha256(
            {key: value for key, value in action.items() if key != "action_integrity_hash"}
        ):
            raise _error("HASH_MISMATCH", "capture action hash mismatch", 409)
        return action

    def _assert_action(
        self, action: Mapping[str, Any], request: Mapping[str, Any], principal: str, session: str
    ) -> None:
        if (
            action["request_identity"] != {key: value for key, value in request.items() if key != "capture_idempotency_key"}
            or action["idempotency_key_hash"] != hashlib.sha256(request["capture_idempotency_key"].encode()).hexdigest()
            or action["authenticated_principal_hash"] != hashlib.sha256(principal.encode()).hexdigest()
            or action["interactive_session_hash"] != hashlib.sha256(session.encode()).hexdigest()
        ):
            raise _error("AUTHORITY_BLOCKED", "capture action authority mismatch", 403)

    @staticmethod
    def _request_hash(request: Mapping[str, Any]) -> str:
        return canonical_sha256({key: value for key, value in request.items() if key != "capture_idempotency_key"})

    @staticmethod
    def _request_from_action(action: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        request = deepcopy(dict(action["request_identity"]))
        request["capture_idempotency_key"] = idempotency_key
        return request

    def _request_from_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        action_hash = result["authority"]["capture_action_id_hash"]
        matches = []
        for path in self._actions.glob("*.json"):
            action = _read_json(path)
            if hashlib.sha256(action["capture_action_id"].encode()).hexdigest() == action_hash:
                matches.append(action)
        if len(matches) != 1:
            raise _error("CAPTURE_STORE_CORRUPT", "capture action relation missing", 500)
        action = matches[0]
        request = deepcopy(dict(action["request_identity"]))
        request["capture_idempotency_key"] = "vwoci-" + "0" * 32
        return request

    @staticmethod
    def _require_runtime_actor(principal: Any, session: Any) -> None:
        if not isinstance(principal, str) or not principal or not isinstance(session, str) or not session:
            raise _error("AUTHORITY_BLOCKED", "authenticated human session required", 403)

    @staticmethod
    def _capture_content_projection(result: Mapping[str, Any]) -> dict[str, Any]:
        authority = deepcopy(dict(result["authority"]))
        authority.pop("idempotency_key_hash")
        return {
            "schema_version": "vision-webfill-browser-observation-capture-content-v1",
            "authority": authority,
            "browser_page_identity": result["browser_page_identity"],
            "stable_capture": result["stable_capture"],
            "observation_reference": result["observation_reference"],
        }

    @staticmethod
    def _authority_binding_hash(result: Mapping[str, Any]) -> str:
        return canonical_sha256(
            {
                "schema_version": "vision-webfill-browser-observation-authority-binding-v1",
                "authority": result["authority"],
                "browser_page_identity_hash": result["browser_page_identity"]["identity_integrity_hash"],
            }
        )

    @staticmethod
    def _authority_binding_hash_from_action(action: Mapping[str, Any]) -> str:
        return canonical_sha256(
            {
                "schema_version": "vision-webfill-browser-observation-action-binding-v1",
                "request_identity": action["request_identity"],
                "authenticated_principal_hash": action["authenticated_principal_hash"],
                "interactive_session_hash": action["interactive_session_hash"],
            }
        )

    def _next_observation_generation(self, state: Mapping[str, Any]) -> int:
        current_path = self._current / f"{state['site_id']}.json"
        if not current_path.exists():
            return 1
        pointer = _read_json(current_path)
        current = self._load_committed_result(pointer.get("capture_id"))
        identity = current["browser_page_identity"]
        if identity["page_instance_id"] == state["page_instance_id"]:
            return identity["observation_generation"] + 1
        return 1

    @staticmethod
    def _current_pointer(result: Mapping[str, Any]) -> dict[str, Any]:
        identity = result["browser_page_identity"]
        reference = result["observation_reference"]
        return {
            "schema_version": "vision-webfill-browser-observation-current-v1",
            "site_id": identity["site_id"],
            "capture_id": result["capture_id"],
            "capture_record_integrity_hash": result["record_integrity_hash"],
            "dom_observation_id": reference["dom_observation_id"],
            "observation_hash": reference["observation_hash"],
            "page_instance_id": reference["page_instance_id"],
            "navigation_epoch": reference["navigation_epoch"],
            "observation_generation": reference["observation_generation"],
            "dom_mutation_generation": reference["dom_mutation_generation"],
        }

    def _load_current(self, site_id: str) -> dict[str, Any]:
        path = self._current / f"{site_id}.json"
        if not path.exists():
            raise _error("OBSERVATION_STALE", "current capture pointer missing", 409)
        return _read_json(path)

    def _validate_result(self, result: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "capture_id",
            "created_at",
            "state_at_creation",
            "authority",
            "browser_page_identity",
            "stable_capture",
            "observation_reference",
            "capture_content_hash",
            "record_integrity_hash",
            "safety",
        }
        if set(result) != expected or result["schema_version"] != CAPTURE_RESULT_SCHEMA_VERSION or not _CAPTURE_RE.fullmatch(result["capture_id"]) or result["state_at_creation"] != "COMMITTED":
            raise _error("CAPTURE_SCHEMA_INVALID", "capture result shape invalid", 500)
        _assert_nfc_json(result, label="capture result")
        if result["capture_content_hash"] != canonical_sha256(self._capture_content_projection(result)) or result[
            "record_integrity_hash"
        ] != canonical_sha256({key: value for key, value in result.items() if key != "record_integrity_hash"}):
            raise _error("HASH_MISMATCH", "capture result hash mismatch", 409)
        expected_safety = {
            "read_only_capture": True,
            "identity_reference_only": True,
            "raw_dom_values_exposed": False,
            "raw_dom_values_persisted": False,
            "raw_dom_values_hashed": False,
            "raw_html_persisted": False,
            "cookie_or_storage_read": False,
            "screenshot_captured": False,
            "arbitrary_script_executed": False,
            "browser_launched": False,
            "navigation_performed": False,
            "dom_mutation_performed": False,
            "click_performed": False,
            "typing_performed": False,
            "events_dispatched": False,
            "fill_authorized": False,
            "submit_authorized": False,
            "queue_or_claim_completed": False,
        }
        if result["safety"] != expected_safety:
            raise _error("CAPTURE_SCHEMA_INVALID", "capture safety invalid", 500)
        identity = result["browser_page_identity"]
        if identity["identity_integrity_hash"] != canonical_sha256(
            {key: value for key, value in identity.items() if key != "identity_integrity_hash"}
        ):
            raise _error("HASH_MISMATCH", "page identity hash mismatch", 409)

    def _validate_event(self, event: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "event_id",
            "capture_id",
            "event_sequence",
            "event_type",
            "occurred_at",
            "actor_kind",
            "authority_binding_hash",
            "browser_page_identity_hash",
            "observation_reference",
            "reason_code",
            "safe_context_hashes",
            "event_integrity_hash",
            "safety",
        }
        if set(event) != expected or event["schema_version"] != AUDIT_EVENT_SCHEMA_VERSION or not _EVENT_RE.fullmatch(event["event_id"]) or not _CAPTURE_RE.fullmatch(event["capture_id"]):
            raise _error("CAPTURE_STORE_CORRUPT", "capture audit shape invalid", 500)
        if event["event_type"] in {"CAPTURE_FAILED", "OBSERVATION_STALE", "RECOVERY_REQUIRED"}:
            if event["reason_code"] not in _SAFE_REASON_CODES:
                raise _error("CAPTURE_STORE_CORRUPT", "capture audit reason invalid", 500)
        elif event["reason_code"] is not None:
            raise _error("CAPTURE_STORE_CORRUPT", "capture audit success reason invalid", 500)
        if event["event_integrity_hash"] != canonical_sha256(
            {key: value for key, value in event.items() if key != "event_integrity_hash"}
        ):
            raise _error("CAPTURE_STORE_CORRUPT", "capture audit hash mismatch", 500)

    def _validate_transaction(self, transaction: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "request_hash",
            "idempotency_key_hash",
            "capture_id",
            "observation",
            "result",
            "committed_event",
        }
        if set(transaction) != expected or transaction["schema_version"] != "vision-webfill-browser-observation-capture-transaction-v1":
            raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture transaction shape invalid", 500)
        for key in ("request_hash", "idempotency_key_hash"):
            if not isinstance(transaction[key], str) or not _HASH_RE.fullmatch(transaction[key]):
                raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture transaction hash invalid", 500)
        try:
            WebfillDomObservationStore._validate(transaction["observation"], persisted=True)
        except WebfillMappingError as exc:
            raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture Observation invalid", 500) from exc
        self._validate_result(transaction["result"])
        self._validate_event(transaction["committed_event"])
        result = transaction["result"]
        observation = transaction["observation"]
        if (
            transaction["capture_id"] != result["capture_id"]
            or result["observation_reference"]["dom_observation_id"] != observation["dom_observation_id"]
            or result["observation_reference"]["observation_hash"] != observation["observation_hash"]
            or transaction["committed_event"]["capture_id"] != result["capture_id"]
            or transaction["committed_event"]["event_type"] != "CAPTURE_COMMITTED"
            or transaction["committed_event"]["observation_reference"]["observation_hash"] != observation["observation_hash"]
        ):
            raise _error("TRANSACTION_RECOVERY_REQUIRED", "capture transaction relation invalid", 500)

    def _assert_transaction_fresh(
        self,
        transaction: Mapping[str, Any],
        stable: Mapping[str, Any],
        profile: Mapping[str, Any],
    ) -> None:
        self._assert_live_result(transaction["result"], stable, profile)
        persisted = transaction["observation"]
        observed = stable["observation"]
        if (
            persisted["page_fingerprint"] != observed["page_fingerprint"]
            or persisted["page_instance_id"] != observed["page_instance_id"]
            or persisted["navigation_epoch"] != observed["navigation_epoch"]
            or persisted["forms"] != observed["forms"]
        ):
            raise _error("HASH_MISMATCH", "pending capture structure changed", 409)

    def _result(self, result: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "schema_version": "vision-webfill-browser-observation-capture-response-v1",
            "status": "COMMITTED",
            "capture_id": result["capture_id"],
            "observation_reference": deepcopy(result["observation_reference"]),
            "replayed": replayed,
            "safety": deepcopy(result["safety"]),
        }
