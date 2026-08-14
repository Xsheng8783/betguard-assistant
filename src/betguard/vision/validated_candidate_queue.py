"""Identity-only queue authority for validated Vision Candidates.

This module is intentionally isolated from every legacy queue, fill, browser,
and submission package.  A queue entry stores only immutable Candidate
identity.  Candidate values are available solely through the read-only Gate
3B-2 validator envelope returned by :meth:`prepare_next`.

Successful preparation is not a claim or dequeue: the entry remains QUEUED.
This v1 domain deliberately has no completion, fill, or submit operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from betguard.vision.candidate_authority import (
    CandidateAuthorityError,
    VisionCandidateAuthorityStore,
    canonical_json_bytes,
    canonical_sha256,
)
from betguard.vision.candidate_consumption import (
    CONSUMPTION_VALIDATION_SCHEMA_VERSION,
    CandidateConsumptionAuthorityValidator,
)


QUEUE_ENTRY_SCHEMA_VERSION = "vision-validated-candidate-queue-entry-v1"
QUEUE_EVENT_SCHEMA_VERSION = "vision-validated-candidate-queue-event-v1"
QUEUE_ACTION_SCHEMA_VERSION = "vision-validated-candidate-queue-action-v1"
QUEUE_TRANSACTION_SCHEMA_VERSION = (
    "vision-validated-candidate-queue-enqueue-transaction-v1"
)
PREPARED_QUEUE_ENVELOPE_SCHEMA_VERSION = (
    "vision-validated-candidate-queue-prepare-v1"
)

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CANDIDATE_ID_RE = re.compile(r"^vc-[a-f0-9]{32}$")
_ENTRY_ID_RE = re.compile(r"^vcq-[a-f0-9]{32}$")
_ENQUEUE_ACTION_ID_RE = re.compile(r"^hqe-[a-f0-9]{32}$")
_REMOVE_ACTION_ID_RE = re.compile(r"^hqr-[a-f0-9]{32}$")
_PREPARE_ACTION_ID_RE = re.compile(r"^qpa-[a-f0-9]{32}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^qik-[a-f0-9]{32}$")

_ENTRY_SAFETY = {
    "identity_reference_only": True,
    "candidate_values_embedded": False,
    "approved_for_fill": False,
    "submitted": False,
    "webfill_authorized": False,
    "auto_confirm": False,
    "auto_submit": False,
    "completed_means_submitted": False,
    "legacy_queue_compatible": False,
}

_FORBIDDEN_VALUE_KEYS = {
    "bets",
    "active_bets",
    "cancelled_audit",
    "numbers",
    "number_groups",
    "multiplier",
    "multiplier_rules",
    "layout",
    "continuation",
    "special_play",
    "raw_text",
    "model_raw_text",
    "machine_evidence_refs",
    "queue_path",
    "manual_candidate_id",
    "approved_fill_queue",
}

_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class ValidatedCandidateQueueError(Exception):
    """Stable, handler-friendly queue authority error."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _error(
    code: str, message: str, status: int = 422
) -> ValidatedCandidateQueueError:
    return ValidatedCandidateQueueError(code, message, status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if not isinstance(value, Mapping):
        raise _error("QUEUE_STORE_CORRUPT", f"{label} must be an object", 500)
    actual = set(value)
    if actual != expected:
        raise _error(
            "QUEUE_STORE_CORRUPT",
            f"{label} keys invalid; missing={sorted(expected-actual)}, "
            f"unknown={sorted(actual-expected)}",
            500,
        )


def _require_nonempty(value: Any, label: str, *, request: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        code = "QUEUE_REQUEST_INVALID" if request else "QUEUE_STORE_CORRUPT"
        status = 400 if request else 500
        raise _error(code, f"{label} must be a non-empty string", status)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error(
            "QUEUE_STORE_CORRUPT", f"cannot read {path.name}: {exc}", 500
        ) from exc
    if not isinstance(value, dict):
        raise _error("QUEUE_STORE_CORRUPT", f"{path.name} root is not an object", 500)
    return value


def _write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically publish a complete immutable JSON record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(dict(value))
    if path.exists():
        if path.read_bytes() != content:
            raise _error(
                "QUEUE_STORE_CORRUPT",
                f"immutable record conflicts with {path.name}",
                500,
            )
        return
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise _error(
                    "QUEUE_STORE_CORRUPT",
                    f"immutable record conflicts with {path.name}",
                    500,
                )
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assert_no_value_keys(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        overlap = set(value) & _FORBIDDEN_VALUE_KEYS
        if overlap:
            raise _error(
                "QUEUE_STORE_CORRUPT",
                f"{label} contains forbidden Candidate value keys: {sorted(overlap)}",
                500,
            )
        for child in value.values():
            _assert_no_value_keys(child, label=label)
    elif isinstance(value, list):
        for child in value:
            _assert_no_value_keys(child, label=label)


class ValidatedCandidateQueueStore:
    """Filesystem-backed, identity-only validated Candidate queue."""

    def __init__(
        self,
        base_dir: Path | str,
        candidate_validator: CandidateConsumptionAuthorityValidator,
    ) -> None:
        if not callable(getattr(candidate_validator, "validate_request", None)):
            raise TypeError("candidate_validator must provide validate_request")
        self.base_dir = Path(base_dir)
        self._validator = candidate_validator
        self._entries_dir = self.base_dir / "entries"
        self._entry_commits_dir = self.base_dir / "entry-commits"
        self._events_dir = self.base_dir / "lifecycle-events"
        self._enqueue_idempotency_dir = self.base_dir / "enqueue-idempotency"
        self._prepare_idempotency_dir = self.base_dir / "prepare-idempotency"
        self._human_actions_dir = self.base_dir / "human-actions"
        self._action_issue_idempotency_dir = (
            self.base_dir / "human-action-issue-idempotency"
        )
        self._transactions_dir = self.base_dir / "enqueue-transactions"
        self._identity_index_dir = self.base_dir / "candidate-identity"
        self._locks_dir = self.base_dir / "locks"
        self._sequence_dir = self.base_dir / "sequence"
        for directory in (
            self._entries_dir,
            self._entry_commits_dir,
            self._events_dir,
            self._enqueue_idempotency_dir,
            self._prepare_idempotency_dir,
            self._human_actions_dir,
            self._action_issue_idempotency_dir,
            self._transactions_dir,
            self._identity_index_dir,
            self._locks_dir,
            self._sequence_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_authority_store(
        cls,
        base_dir: Path | str,
        authority_store: VisionCandidateAuthorityStore,
    ) -> "ValidatedCandidateQueueStore":
        """Build the isolated queue without exposing Gate 3B-2 to callers."""

        return cls(
            base_dir,
            CandidateConsumptionAuthorityValidator(authority_store),
        )

    # ------------------------------------------------------------------
    # Server-bound human actions
    # ------------------------------------------------------------------

    def bind_human_enqueue_action(
        self,
        *,
        authenticated_actor: str,
        interactive_session_id: str,
        candidate_id: str,
        candidate_revision: int,
        canonical_content_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Mint an immutable, single-target enqueue action on the server."""

        actor = _require_nonempty(authenticated_actor, "authenticated_actor", request=True)
        session_id = _require_nonempty(
            interactive_session_id, "interactive_session_id", request=True
        )
        identity = self._validated_candidate_identity(
            candidate_id, candidate_revision, canonical_content_hash, request=True
        )
        self._require_idempotency_key(idempotency_key, request=True)
        issue_request = {
            "purpose": "ENQUEUE",
            "authenticated_actor": actor,
            "interactive_session_id": session_id,
            "target": identity,
            "idempotency_key": idempotency_key,
        }
        with self._queue_lock():
            replay = self._load_action_issue(idempotency_key)
            if replay is not None:
                if replay["request"] != issue_request:
                    raise _error(
                        "QUEUE_IDEMPOTENCY_CONFLICT",
                        "action issue idempotency key is bound to another request",
                        409,
                    )
                return deepcopy(self._load_action(replay["action_id"]))
            action_id = f"hqe-{uuid.uuid4().hex}"
            action = {
                "schema_version": QUEUE_ACTION_SCHEMA_VERSION,
                "action_id": action_id,
                "purpose": "ENQUEUE",
                "authenticated_actor": actor,
                "interactive_session_id": session_id,
                "target": identity,
                "idempotency_key": idempotency_key,
                "created_at": _now(),
            }
            _write_immutable_json(self._action_path(action_id), action)
            self._write_action_issue(idempotency_key, issue_request, action_id)
            return deepcopy(action)

    def bind_human_remove_action(
        self,
        *,
        authenticated_actor: str,
        interactive_session_id: str,
        queue_entry_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Mint an immutable, single-target removal action on the server."""

        actor = _require_nonempty(authenticated_actor, "authenticated_actor", request=True)
        session_id = _require_nonempty(
            interactive_session_id, "interactive_session_id", request=True
        )
        self._require_entry_id(queue_entry_id, request=True)
        self._require_idempotency_key(idempotency_key, request=True)
        issue_request = {
            "purpose": "REMOVE",
            "authenticated_actor": actor,
            "interactive_session_id": session_id,
            "target": {"queue_entry_id": queue_entry_id},
            "idempotency_key": idempotency_key,
        }
        with self._queue_lock():
            replay = self._load_action_issue(idempotency_key)
            if replay is not None:
                if replay["request"] != issue_request:
                    raise _error(
                        "QUEUE_IDEMPOTENCY_CONFLICT",
                        "action issue idempotency key is bound to another request",
                        409,
                    )
                return deepcopy(self._load_action(replay["action_id"]))
            action_id = f"hqr-{uuid.uuid4().hex}"
            action = {
                "schema_version": QUEUE_ACTION_SCHEMA_VERSION,
                "action_id": action_id,
                "purpose": "REMOVE",
                "authenticated_actor": actor,
                "interactive_session_id": session_id,
                "target": {"queue_entry_id": queue_entry_id},
                "idempotency_key": idempotency_key,
                "created_at": _now(),
            }
            _write_immutable_json(self._action_path(action_id), action)
            self._write_action_issue(idempotency_key, issue_request, action_id)
            return deepcopy(action)

    # ------------------------------------------------------------------
    # Mutating commands
    # ------------------------------------------------------------------

    def enqueue(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_actor: str,
        interactive_session_id: str,
    ) -> dict[str, Any]:
        """Enqueue one exact Candidate identity after Gate 3B-2 validation."""

        request = self._decode_enqueue_request(payload)
        actor = _require_nonempty(authenticated_actor, "authenticated_actor", request=True)
        session_id = _require_nonempty(
            interactive_session_id, "interactive_session_id", request=True
        )
        action = self._load_and_validate_action(
            request["human_enqueue_action_id"],
            purpose="ENQUEUE",
            actor=actor,
            interactive_session_id=session_id,
            target={
                "candidate_id": request["candidate_id"],
                "candidate_revision": request["expected_candidate_revision"],
                "canonical_content_hash": request["expected_content_hash"],
            },
            idempotency_key=request["idempotency_key"],
        )
        with self._queue_lock():
            replay = self._load_enqueue_idempotency(request["idempotency_key"])
            if replay is not None:
                self._assert_enqueue_replay_matches(replay, request, action)
                entry_state = self.get_entry(replay["queue_entry_id"])
                if entry_state is None:
                    transaction = self._load_enqueue_transaction(
                        request["idempotency_key"]
                    )
                    if transaction is None:
                        raise _error(
                            "QUEUE_STORE_CORRUPT",
                            "enqueue replay points to no committed entry or transaction",
                            500,
                        )
                    self._resume_enqueue_transaction(transaction)
                    entry_state = self.get_entry(replay["queue_entry_id"])
                if entry_state is None:
                    raise _error("QUEUE_STORE_CORRUPT", "enqueue recovery failed", 500)
                if entry_state["state"] != "QUEUED":
                    raise _error(
                        "QUEUE_IDENTITY_TERMINAL",
                        "Candidate identity already has a terminal queue entry",
                        409,
                    )
                return {
                    "queue_entry": entry_state["queue_entry"],
                    "state": "QUEUED",
                    "replayed": True,
                }

            validation = self._validator.validate_request(
                {
                    "candidate_id": request["candidate_id"],
                    "expected_candidate_revision": request[
                        "expected_candidate_revision"
                    ],
                    "expected_content_hash": request["expected_content_hash"],
                }
            )
            self._validate_gate3b2_envelope(validation, request)
            identity = {
                "candidate_id": validation["candidate_id"],
                "candidate_revision": validation["candidate_revision"],
                "canonical_content_hash": validation["canonical_content_hash"],
            }

            existing = self._entry_for_candidate_identity(identity)
            if existing is not None:
                if existing["state"] != "QUEUED":
                    raise _error(
                        "QUEUE_IDENTITY_TERMINAL",
                        "Candidate identity cannot be re-enqueued",
                        409,
                    )
                idempotency = self._enqueue_idempotency_record(
                    action, request, existing["queue_entry"]["queue_entry_id"]
                )
                _write_immutable_json(
                    self._enqueue_idempotency_path(request["idempotency_key"]),
                    idempotency,
                )
                return {
                    "queue_entry": existing["queue_entry"],
                    "state": "QUEUED",
                    "replayed": True,
                }

            transaction = self._load_enqueue_transaction(request["idempotency_key"])
            if transaction is None:
                transaction = self._build_enqueue_transaction(
                    identity=identity,
                    action=action,
                    request=request,
                )
                _write_immutable_json(
                    self._enqueue_transaction_path(request["idempotency_key"]),
                    transaction,
                )
            self._resume_enqueue_transaction(transaction)
            result = self.get_entry(transaction["entry"]["queue_entry_id"])
            if result is None:
                raise _error("QUEUE_STORE_CORRUPT", "enqueue commit is invisible", 500)
            return {
                "queue_entry": result["queue_entry"],
                "state": result["state"],
                "replayed": False,
            }

    def remove(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_actor: str,
        interactive_session_id: str,
    ) -> dict[str, Any]:
        """Append an explicit human REMOVED event."""

        if not isinstance(payload, Mapping) or set(payload) != {
            "queue_entry_id",
            "human_remove_action_id",
            "idempotency_key",
        }:
            raise _error(
                "QUEUE_REQUEST_INVALID",
                "remove accepts only queue identity, bound action, and idempotency key",
                400,
            )
        queue_entry_id = payload["queue_entry_id"]
        self._require_entry_id(queue_entry_id, request=True)
        self._require_idempotency_key(payload["idempotency_key"], request=True)
        actor = _require_nonempty(authenticated_actor, "authenticated_actor", request=True)
        session_id = _require_nonempty(
            interactive_session_id, "interactive_session_id", request=True
        )
        action = self._load_and_validate_action(
            payload["human_remove_action_id"],
            purpose="REMOVE",
            actor=actor,
            interactive_session_id=session_id,
            target={"queue_entry_id": queue_entry_id},
            idempotency_key=payload["idempotency_key"],
        )
        with self._queue_lock():
            current = self.get_entry(queue_entry_id)
            if current is None:
                raise _error("QUEUE_ENTRY_NOT_FOUND", "queue entry was not found", 404)
            events = self.get_lifecycle_events(queue_entry_id)
            matching = [
                event
                for event in events
                if event["event_type"] == "REMOVED"
                and event["action_id_hash"]
                == hashlib.sha256(action["action_id"].encode("utf-8")).hexdigest()
            ]
            if matching:
                return {
                    "queue_entry_id": queue_entry_id,
                    "state": "REMOVED",
                    "replayed": True,
                }
            if current["state"] not in {"QUEUED", "BLOCKED"}:
                raise _error(
                    "QUEUE_ENTRY_NOT_REMOVABLE",
                    f"queue entry state {current['state']} cannot be removed",
                    409,
                )
            if any(event["event_type"] == "REMOVED" for event in events):
                raise _error(
                    "QUEUE_IDEMPOTENCY_CONFLICT",
                    "queue entry was removed by another human action",
                    409,
                )
            self._append_event(
                current["queue_entry"],
                event_type="REMOVED",
                actor=actor,
                reason_code="explicit_human_remove",
                action_id=action["action_id"],
            )
            return {
                "queue_entry_id": queue_entry_id,
                "state": "REMOVED",
                "replayed": False,
            }

    def prepare_next(self, *, prepare_action_id: str) -> dict[str, Any] | None:
        """Return the first currently valid FIFO entry without claiming it.

        Invalid heads become BLOCKED and scanning continues.  A valid result
        writes no event and remains QUEUED.
        """

        if not isinstance(prepare_action_id, str) or not _PREPARE_ACTION_ID_RE.fullmatch(
            prepare_action_id
        ):
            raise _error(
                "QUEUE_REQUEST_INVALID",
                "prepare_action_id must be a server-minted qpa identity",
                400,
            )
        with self._queue_lock():
            for item in self.list_entries(state="QUEUED"):
                entry = item["queue_entry"]
                identity = entry["candidate_identity"]
                try:
                    validation = self._validator.validate_request(
                        {
                            "candidate_id": identity["candidate_id"],
                            "expected_candidate_revision": identity[
                                "candidate_revision"
                            ],
                            "expected_content_hash": identity[
                                "canonical_content_hash"
                            ],
                        }
                    )
                    self._validate_gate3b2_envelope(
                        validation,
                        {
                            "candidate_id": identity["candidate_id"],
                            "expected_candidate_revision": identity[
                                "candidate_revision"
                            ],
                            "expected_content_hash": identity[
                                "canonical_content_hash"
                            ],
                        },
                    )
                except CandidateAuthorityError as exc:
                    self._append_blocked_once(entry, prepare_action_id, exc.code)
                    continue
                identity = entry["candidate_identity"]
                return {
                    "schema_version": PREPARED_QUEUE_ENVELOPE_SCHEMA_VERSION,
                    "prepare_status": "VALID_CURRENT",
                    "state": "QUEUED",
                    "queue_entry_id": entry["queue_entry_id"],
                    "enqueue_sequence": entry["enqueue_sequence"],
                    "candidate_id": identity["candidate_id"],
                    "candidate_revision": identity["candidate_revision"],
                    "canonical_content_hash": identity[
                        "canonical_content_hash"
                    ],
                    "queue_entry": deepcopy(entry),
                    "validation": deepcopy(validation),
                    "safety": {
                        "read_only": True,
                        "candidate_only": True,
                        "approved_for_fill": False,
                        "approved_for_submit": False,
                        "auto_confirm": False,
                        "auto_submit": False,
                    },
                }
            return None

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get_entry(self, queue_entry_id: str) -> dict[str, Any] | None:
        self._require_entry_id(queue_entry_id, request=True)
        commit_path = self._entry_commit_path(queue_entry_id)
        if not commit_path.exists():
            return None
        entry_path = self._entry_path(queue_entry_id)
        if not entry_path.exists():
            raise _error("QUEUE_STORE_CORRUPT", "committed entry snapshot missing", 500)
        entry = _read_json(entry_path)
        self._validate_entry(entry)
        self._validate_commit(entry, _read_json(commit_path))
        events = self.get_lifecycle_events(queue_entry_id)
        if any(
            event["entry_integrity_hash"] != entry["entry_integrity_hash"]
            for event in events
        ) or events[0]["action_id_hash"] != hashlib.sha256(
            entry["enqueue_authority"]["human_enqueue_action_id"].encode("utf-8")
        ).hexdigest():
            raise _error(
                "QUEUE_STORE_CORRUPT",
                "queue lifecycle event does not belong to immutable entry",
                500,
            )
        state = self._derive_state(events)
        return {"queue_entry": deepcopy(entry), "state": state}

    def list_entries(self, *, state: str | None = None) -> list[dict[str, Any]]:
        if state is not None and state not in {"QUEUED", "BLOCKED", "REMOVED"}:
            raise _error("QUEUE_REQUEST_INVALID", "unknown queue state filter", 400)
        results: list[dict[str, Any]] = []
        for commit_path in self._entry_commits_dir.glob("*.json"):
            commit = _read_json(commit_path)
            queue_entry_id = commit.get("queue_entry_id")
            if not isinstance(queue_entry_id, str):
                raise _error("QUEUE_STORE_CORRUPT", "commit entry id invalid", 500)
            item = self.get_entry(queue_entry_id)
            if item is not None and (state is None or item["state"] == state):
                results.append(item)
        results.sort(key=lambda item: item["queue_entry"]["enqueue_sequence"])
        seen_sequences: set[int] = set()
        for item in results:
            sequence = item["queue_entry"]["enqueue_sequence"]
            if sequence in seen_sequences:
                raise _error("QUEUE_STORE_CORRUPT", "duplicate FIFO sequence", 500)
            seen_sequences.add(sequence)
        return deepcopy(results)

    def get_lifecycle_events(self, queue_entry_id: str) -> list[dict[str, Any]]:
        self._require_entry_id(queue_entry_id, request=True)
        directory = self._events_dir / queue_entry_id
        if not directory.exists():
            return []
        paths = sorted(directory.glob("*.json"))
        events = [_read_json(path) for path in paths]
        for expected_sequence, (path, event) in enumerate(zip(paths, events), start=1):
            self._validate_event(event, queue_entry_id)
            if (
                path.name != f"{expected_sequence:06d}.json"
                or event["event_sequence"] != expected_sequence
            ):
                raise _error("QUEUE_STORE_CORRUPT", "queue event sequence gap", 500)
        self._derive_state(events)
        return deepcopy(events)

    # ------------------------------------------------------------------
    # Enqueue transaction/recovery
    # ------------------------------------------------------------------

    def _build_enqueue_transaction(
        self,
        *,
        identity: dict[str, Any],
        action: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        sequence = self._allocate_sequence()
        entry_id = f"vcq-{uuid.uuid4().hex}"
        created_at = _now()
        entry_without_hash = {
            "schema_version": QUEUE_ENTRY_SCHEMA_VERSION,
            "queue_entry_id": entry_id,
            "enqueue_sequence": sequence,
            "candidate_identity": deepcopy(identity),
            "enqueue_authority": {
                "human_enqueue_action_id": action["action_id"],
                "requested_by": action["authenticated_actor"],
                "explicit_human_enqueue": True,
                "candidate_validation_schema_version": (
                    CONSUMPTION_VALIDATION_SCHEMA_VERSION
                ),
                "candidate_validation_status": "VALID_CURRENT",
                "validated_at": created_at,
            },
            "state_at_creation": "QUEUED",
            "created_at": created_at,
            "safety": deepcopy(_ENTRY_SAFETY),
        }
        entry = dict(
            entry_without_hash,
            entry_integrity_hash=canonical_sha256(entry_without_hash),
        )
        initial_event = self._event_record(
            entry,
            event_sequence=1,
            event_type="QUEUED",
            actor=action["authenticated_actor"],
            reason_code=None,
            action_id=action["action_id"],
        )
        idempotency = self._enqueue_idempotency_record(action, request, entry_id)
        identity_record = {
            "candidate_identity": deepcopy(identity),
            "queue_entry_id": entry_id,
            "entry_integrity_hash": entry["entry_integrity_hash"],
        }
        commit = {
            "queue_entry_id": entry_id,
            "entry_integrity_hash": entry["entry_integrity_hash"],
            "human_enqueue_action_id_hash": hashlib.sha256(
                action["action_id"].encode("utf-8")
            ).hexdigest(),
        }
        transaction = {
            "schema_version": QUEUE_TRANSACTION_SCHEMA_VERSION,
            "human_enqueue_action_id": action["action_id"],
            "idempotency_key": request["idempotency_key"],
            "entry": entry,
            "initial_event": initial_event,
            "enqueue_idempotency": idempotency,
            "identity_record": identity_record,
            "commit": commit,
        }
        _assert_no_value_keys(transaction, label="enqueue transaction")
        return transaction

    def _resume_enqueue_transaction(self, transaction: Mapping[str, Any]) -> None:
        self._validate_enqueue_transaction(transaction)
        entry = transaction["entry"]
        entry_id = entry["queue_entry_id"]
        idempotency_key = transaction["idempotency_key"]
        _write_immutable_json(self._entry_path(entry_id), entry)
        _write_immutable_json(
            self._event_path(entry_id, 1), transaction["initial_event"]
        )
        _write_immutable_json(
            self._enqueue_idempotency_path(idempotency_key),
            transaction["enqueue_idempotency"],
        )
        _write_immutable_json(
            self._identity_index_path(entry["candidate_identity"]),
            transaction["identity_record"],
        )
        self._advance_sequence_marker(entry["enqueue_sequence"] + 1)
        # Commit marker is deliberately last: reads ignore every partial state.
        _write_immutable_json(self._entry_commit_path(entry_id), transaction["commit"])

    def _load_enqueue_transaction(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._enqueue_transaction_path(idempotency_key)
        if not path.exists():
            return None
        transaction = _read_json(path)
        self._validate_enqueue_transaction(transaction)
        return transaction

    def _validate_enqueue_transaction(self, value: Mapping[str, Any]) -> None:
        _require_exact_keys(
            value,
            {
                "schema_version",
                "human_enqueue_action_id",
                "idempotency_key",
                "entry",
                "initial_event",
                "enqueue_idempotency",
                "identity_record",
                "commit",
            },
            "enqueue transaction",
        )
        if value["schema_version"] != QUEUE_TRANSACTION_SCHEMA_VERSION:
            raise _error("QUEUE_STORE_CORRUPT", "transaction schema invalid", 500)
        if not _ENQUEUE_ACTION_ID_RE.fullmatch(value["human_enqueue_action_id"]):
            raise _error("QUEUE_STORE_CORRUPT", "transaction action invalid", 500)
        if not _IDEMPOTENCY_KEY_RE.fullmatch(value["idempotency_key"]):
            raise _error("QUEUE_STORE_CORRUPT", "transaction idempotency invalid", 500)
        self._validate_entry(value["entry"])
        self._validate_event(
            value["initial_event"], value["entry"]["queue_entry_id"]
        )
        if value["initial_event"]["event_type"] != "QUEUED":
            raise _error("QUEUE_STORE_CORRUPT", "initial event is not QUEUED", 500)
        if value["initial_event"]["entry_integrity_hash"] != value["entry"][
            "entry_integrity_hash"
        ]:
            raise _error("QUEUE_STORE_CORRUPT", "transaction event mismatch", 500)
        self._validate_enqueue_idempotency(value["enqueue_idempotency"])
        self._validate_identity_record(value["identity_record"])
        self._validate_commit(value["entry"], value["commit"])
        entry = value["entry"]
        action_id = value["human_enqueue_action_id"]
        action_hash = hashlib.sha256(action_id.encode("utf-8")).hexdigest()
        if (
            entry["enqueue_authority"]["human_enqueue_action_id"] != action_id
            or value["initial_event"]["action_id_hash"] != action_hash
            or value["enqueue_idempotency"]["human_enqueue_action_id"]
            != action_id
            or value["enqueue_idempotency"]["idempotency_key"]
            != value["idempotency_key"]
            or value["enqueue_idempotency"]["candidate_identity"]
            != entry["candidate_identity"]
            or value["enqueue_idempotency"]["queue_entry_id"]
            != entry["queue_entry_id"]
            or value["identity_record"]["candidate_identity"]
            != entry["candidate_identity"]
            or value["identity_record"]["queue_entry_id"]
            != entry["queue_entry_id"]
            or value["identity_record"]["entry_integrity_hash"]
            != entry["entry_integrity_hash"]
            or value["commit"]["human_enqueue_action_id_hash"] != action_hash
        ):
            raise _error(
                "QUEUE_STORE_CORRUPT",
                "enqueue transaction identity relation mismatch",
                500,
            )
        _assert_no_value_keys(value, label="enqueue transaction")

    # ------------------------------------------------------------------
    # Validation and lifecycle helpers
    # ------------------------------------------------------------------

    def _decode_enqueue_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise _error("QUEUE_REQUEST_INVALID", "enqueue request must be an object", 400)
        expected = {
            "candidate_id",
            "expected_candidate_revision",
            "expected_content_hash",
            "human_enqueue_action_id",
            "idempotency_key",
        }
        if set(payload) != expected:
            raise _error(
                "QUEUE_REQUEST_INVALID",
                "enqueue accepts only exact Candidate identity and bound human action",
                400,
            )
        identity = self._validated_candidate_identity(
            payload["candidate_id"],
            payload["expected_candidate_revision"],
            payload["expected_content_hash"],
            request=True,
        )
        action_id = payload["human_enqueue_action_id"]
        if not isinstance(action_id, str) or not _ENQUEUE_ACTION_ID_RE.fullmatch(
            action_id
        ):
            raise _error(
                "EXPLICIT_HUMAN_ENQUEUE_REQUIRED",
                "server-bound human enqueue action is required",
                403,
            )
        self._require_idempotency_key(payload["idempotency_key"], request=True)
        return {
            "candidate_id": identity["candidate_id"],
            "expected_candidate_revision": identity["candidate_revision"],
            "expected_content_hash": identity["canonical_content_hash"],
            "human_enqueue_action_id": action_id,
            "idempotency_key": payload["idempotency_key"],
        }

    @staticmethod
    def _validated_candidate_identity(
        candidate_id: Any,
        revision: Any,
        content_hash: Any,
        *,
        request: bool,
    ) -> dict[str, Any]:
        code = "QUEUE_REQUEST_INVALID" if request else "QUEUE_STORE_CORRUPT"
        status = 400 if request else 500
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID_RE.fullmatch(
            candidate_id
        ):
            raise _error(code, "candidate_id invalid", status)
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise _error(code, "candidate_revision invalid", status)
        if not isinstance(content_hash, str) or not _HASH_RE.fullmatch(content_hash):
            raise _error(code, "canonical_content_hash invalid", status)
        return {
            "candidate_id": candidate_id,
            "candidate_revision": revision,
            "canonical_content_hash": content_hash,
        }

    def _validate_gate3b2_envelope(
        self, validation: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        if not isinstance(validation, Mapping):
            raise _error("QUEUE_STORE_CORRUPT", "validator returned non-object", 500)
        exact = (
            validation.get("schema_version") == CONSUMPTION_VALIDATION_SCHEMA_VERSION
            and validation.get("validation_status") == "VALID_CURRENT"
            and validation.get("lifecycle_state") == "CURRENT"
            and validation.get("candidate_id") == request["candidate_id"]
            and validation.get("candidate_revision")
            == request["expected_candidate_revision"]
            and validation.get("canonical_content_hash")
            == request["expected_content_hash"]
            and validation.get("safety")
            == {
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_queue": False,
                "auto_confirm": False,
                "auto_submit": False,
            }
        )
        if not exact:
            raise _error(
                "QUEUE_VALIDATOR_CONTRACT_INVALID",
                "Gate 3B-2 validator envelope is not exact VALID_CURRENT",
                500,
            )

    def _validate_entry(self, entry: Mapping[str, Any]) -> None:
        _require_exact_keys(
            entry,
            {
                "schema_version",
                "queue_entry_id",
                "enqueue_sequence",
                "candidate_identity",
                "enqueue_authority",
                "state_at_creation",
                "created_at",
                "entry_integrity_hash",
                "safety",
            },
            "Queue Entry",
        )
        if entry["schema_version"] != QUEUE_ENTRY_SCHEMA_VERSION:
            raise _error("QUEUE_STORE_CORRUPT", "Queue Entry schema invalid", 500)
        self._require_entry_id(entry["queue_entry_id"], request=False)
        if (
            not isinstance(entry["enqueue_sequence"], int)
            or isinstance(entry["enqueue_sequence"], bool)
            or entry["enqueue_sequence"] < 1
        ):
            raise _error("QUEUE_STORE_CORRUPT", "enqueue sequence invalid", 500)
        identity = entry["candidate_identity"]
        _require_exact_keys(
            identity,
            {"candidate_id", "candidate_revision", "canonical_content_hash"},
            "Candidate identity",
        )
        self._validated_candidate_identity(
            identity["candidate_id"],
            identity["candidate_revision"],
            identity["canonical_content_hash"],
            request=False,
        )
        authority = entry["enqueue_authority"]
        _require_exact_keys(
            authority,
            {
                "human_enqueue_action_id",
                "requested_by",
                "explicit_human_enqueue",
                "candidate_validation_schema_version",
                "candidate_validation_status",
                "validated_at",
            },
            "enqueue authority",
        )
        if (
            not isinstance(authority["human_enqueue_action_id"], str)
            or not _ENQUEUE_ACTION_ID_RE.fullmatch(
                authority["human_enqueue_action_id"]
            )
            or authority["explicit_human_enqueue"] is not True
            or authority["candidate_validation_schema_version"]
            != CONSUMPTION_VALIDATION_SCHEMA_VERSION
            or authority["candidate_validation_status"] != "VALID_CURRENT"
        ):
            raise _error("QUEUE_STORE_CORRUPT", "enqueue authority invalid", 500)
        _require_nonempty(authority["requested_by"], "requested_by")
        _require_nonempty(authority["validated_at"], "validated_at")
        if entry["state_at_creation"] != "QUEUED":
            raise _error("QUEUE_STORE_CORRUPT", "initial queue state invalid", 500)
        _require_nonempty(entry["created_at"], "created_at")
        if entry["safety"] != _ENTRY_SAFETY:
            raise _error("QUEUE_STORE_CORRUPT", "Queue Entry safety invalid", 500)
        content = {
            key: deepcopy(value)
            for key, value in entry.items()
            if key != "entry_integrity_hash"
        }
        if canonical_sha256(content) != entry["entry_integrity_hash"]:
            raise _error("QUEUE_STORE_CORRUPT", "Queue Entry integrity mismatch", 500)
        _assert_no_value_keys(entry, label="Queue Entry")

    def _validate_event(self, event: Mapping[str, Any], entry_id: str) -> None:
        _require_exact_keys(
            event,
            {
                "schema_version",
                "event_id",
                "queue_entry_id",
                "event_sequence",
                "event_type",
                "occurred_at",
                "actor",
                "reason_code",
                "action_id_hash",
                "entry_integrity_hash",
            },
            "queue lifecycle event",
        )
        if (
            event["schema_version"] != QUEUE_EVENT_SCHEMA_VERSION
            or event["queue_entry_id"] != entry_id
            or not isinstance(event["event_id"], str)
            or not re.fullmatch(r"^vqe-[a-f0-9]{32}$", event["event_id"])
            or not isinstance(event["event_sequence"], int)
            or isinstance(event["event_sequence"], bool)
            or event["event_sequence"] < 1
            or event["event_type"] not in {"QUEUED", "BLOCKED", "REMOVED"}
            or not isinstance(event["action_id_hash"], str)
            or not _HASH_RE.fullmatch(event["action_id_hash"])
            or not isinstance(event["entry_integrity_hash"], str)
            or not _HASH_RE.fullmatch(event["entry_integrity_hash"])
        ):
            raise _error("QUEUE_STORE_CORRUPT", "queue lifecycle event invalid", 500)
        _require_nonempty(event["occurred_at"], "occurred_at")
        _require_nonempty(event["actor"], "actor")
        if event["reason_code"] is not None and not isinstance(
            event["reason_code"], str
        ):
            raise _error("QUEUE_STORE_CORRUPT", "event reason invalid", 500)
        _assert_no_value_keys(event, label="queue lifecycle event")

    @staticmethod
    def _derive_state(events: list[Mapping[str, Any]]) -> str:
        state: str | None = None
        for event in events:
            event_type = event["event_type"]
            if state is None and event_type == "QUEUED":
                state = "QUEUED"
            elif state == "QUEUED" and event_type in {"BLOCKED", "REMOVED"}:
                state = event_type
            elif state == "BLOCKED" and event_type == "REMOVED":
                state = "REMOVED"
            else:
                raise _error(
                    "QUEUE_STORE_CORRUPT",
                    f"invalid queue lifecycle transition {state}->{event_type}",
                    500,
                )
        if state is None:
            raise _error("QUEUE_STORE_CORRUPT", "Queue Entry has no initial event", 500)
        return state

    def _append_event(
        self,
        entry: Mapping[str, Any],
        *,
        event_type: str,
        actor: str,
        reason_code: str | None,
        action_id: str,
    ) -> dict[str, Any]:
        events = self.get_lifecycle_events(entry["queue_entry_id"])
        current = self._derive_state(events)
        allowed = (
            (current == "QUEUED" and event_type in {"BLOCKED", "REMOVED"})
            or (current == "BLOCKED" and event_type == "REMOVED")
        )
        if not allowed:
            raise _error(
                "QUEUE_ENTRY_NOT_REMOVABLE",
                f"invalid queue transition {current}->{event_type}",
                409,
            )
        event = self._event_record(
            entry,
            event_sequence=len(events) + 1,
            event_type=event_type,
            actor=actor,
            reason_code=reason_code,
            action_id=action_id,
        )
        _write_immutable_json(
            self._event_path(entry["queue_entry_id"], event["event_sequence"]),
            event,
        )
        return event

    def _append_blocked_once(
        self, entry: Mapping[str, Any], prepare_action_id: str, reason_code: str
    ) -> None:
        idempotency_path = self._prepare_idempotency_path(
            prepare_action_id, entry["queue_entry_id"]
        )
        expected = {
            "prepare_action_id": prepare_action_id,
            "queue_entry_id": entry["queue_entry_id"],
            "reason_code": reason_code,
        }
        if idempotency_path.exists():
            if _read_json(idempotency_path) != expected:
                raise _error(
                    "QUEUE_IDEMPOTENCY_CONFLICT",
                    "prepare action has a conflicting blocked result",
                    409,
                )
            return
        self._append_event(
            entry,
            event_type="BLOCKED",
            actor="candidate-consumption-validator",
            reason_code=reason_code,
            action_id=prepare_action_id,
        )
        _write_immutable_json(idempotency_path, expected)

    def _event_record(
        self,
        entry: Mapping[str, Any],
        *,
        event_sequence: int,
        event_type: str,
        actor: str,
        reason_code: str | None,
        action_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": QUEUE_EVENT_SCHEMA_VERSION,
            "event_id": f"vqe-{uuid.uuid4().hex}",
            "queue_entry_id": entry["queue_entry_id"],
            "event_sequence": event_sequence,
            "event_type": event_type,
            "occurred_at": _now(),
            "actor": actor,
            "reason_code": reason_code,
            "action_id_hash": hashlib.sha256(action_id.encode("utf-8")).hexdigest(),
            "entry_integrity_hash": entry["entry_integrity_hash"],
        }

    # ------------------------------------------------------------------
    # Records, actions, identity, paths, and locking
    # ------------------------------------------------------------------

    @staticmethod
    def _require_idempotency_key(value: Any, *, request: bool) -> None:
        if not isinstance(value, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(value):
            raise _error(
                "QUEUE_REQUEST_INVALID" if request else "QUEUE_STORE_CORRUPT",
                "idempotency_key must be a qik identity",
                400 if request else 500,
            )

    def _load_action(self, action_id: str) -> dict[str, Any]:
        path = self._action_path(action_id)
        if not path.exists():
            raise _error("QUEUE_STORE_CORRUPT", "bound human action missing", 500)
        return _read_json(path)

    def _load_action_issue(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._action_issue_path(idempotency_key)
        if not path.exists():
            return None
        record = _read_json(path)
        _require_exact_keys(record, {"idempotency_key", "request", "action_id"}, "action issue")
        if record["idempotency_key"] != idempotency_key:
            raise _error("QUEUE_STORE_CORRUPT", "action issue identity mismatch", 500)
        if not isinstance(record["action_id"], str) or not (
            _ENQUEUE_ACTION_ID_RE.fullmatch(record["action_id"])
            or _REMOVE_ACTION_ID_RE.fullmatch(record["action_id"])
        ):
            raise _error("QUEUE_STORE_CORRUPT", "action issue action invalid", 500)
        request = record["request"]
        _require_exact_keys(
            request,
            {
                "purpose",
                "authenticated_actor",
                "interactive_session_id",
                "target",
                "idempotency_key",
            },
            "action issue request",
        )
        if (
            request["purpose"] not in {"ENQUEUE", "REMOVE"}
            or request["idempotency_key"] != idempotency_key
        ):
            raise _error("QUEUE_STORE_CORRUPT", "action issue request invalid", 500)
        self._load_and_validate_action(
            record["action_id"],
            purpose=request["purpose"],
            actor=request["authenticated_actor"],
            interactive_session_id=request["interactive_session_id"],
            target=request["target"],
            idempotency_key=idempotency_key,
        )
        return record

    def _write_action_issue(
        self,
        idempotency_key: str,
        request: Mapping[str, Any],
        action_id: str,
    ) -> None:
        record = {
            "idempotency_key": idempotency_key,
            "request": deepcopy(dict(request)),
            "action_id": action_id,
        }
        _write_immutable_json(self._action_issue_path(idempotency_key), record)

    def _load_and_validate_action(
        self,
        action_id: str,
        *,
        purpose: str,
        actor: str,
        interactive_session_id: str,
        target: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected_re = (
            _ENQUEUE_ACTION_ID_RE if purpose == "ENQUEUE" else _REMOVE_ACTION_ID_RE
        )
        if not isinstance(action_id, str) or not expected_re.fullmatch(action_id):
            code = (
                "EXPLICIT_HUMAN_ENQUEUE_REQUIRED"
                if purpose == "ENQUEUE"
                else "EXPLICIT_HUMAN_REMOVE_REQUIRED"
            )
            raise _error(code, "server-bound human action is required", 403)
        path = self._action_path(action_id)
        if not path.exists():
            code = (
                "EXPLICIT_HUMAN_ENQUEUE_REQUIRED"
                if purpose == "ENQUEUE"
                else "EXPLICIT_HUMAN_REMOVE_REQUIRED"
            )
            raise _error(code, "human action was not minted by this server", 403)
        action = _read_json(path)
        _require_exact_keys(
            action,
            {
                "schema_version",
                "action_id",
                "purpose",
                "authenticated_actor",
                "interactive_session_id",
                "target",
                "idempotency_key",
                "created_at",
            },
            "human queue action",
        )
        if (
            action["schema_version"] != QUEUE_ACTION_SCHEMA_VERSION
            or action["action_id"] != action_id
            or action["purpose"] != purpose
            or action["authenticated_actor"] != actor
            or action["interactive_session_id"] != interactive_session_id
            or action["target"] != dict(target)
            or action["idempotency_key"] != idempotency_key
        ):
            raise _error(
                "QUEUE_IDEMPOTENCY_CONFLICT",
                "human action binding does not match this request",
                409,
            )
        _require_nonempty(action["created_at"], "action created_at")
        self._require_idempotency_key(action["idempotency_key"], request=False)
        _assert_no_value_keys(action, label="human queue action")
        return action

    def _entry_for_candidate_identity(
        self, identity: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        index_path = self._identity_index_path(identity)
        if not index_path.exists():
            # Interrupted transactions are also identity reservations.
            for path in self._transactions_dir.glob("*.json"):
                transaction = _read_json(path)
                self._validate_enqueue_transaction(transaction)
                if transaction["entry"]["candidate_identity"] == dict(identity):
                    self._resume_enqueue_transaction(transaction)
                    return self.get_entry(transaction["entry"]["queue_entry_id"])
            return None
        record = _read_json(index_path)
        self._validate_identity_record(record)
        if record["candidate_identity"] != dict(identity):
            raise _error("QUEUE_STORE_CORRUPT", "identity index collision", 500)
        item = self.get_entry(record["queue_entry_id"])
        if item is None:
            for path in self._transactions_dir.glob("*.json"):
                transaction = _read_json(path)
                self._validate_enqueue_transaction(transaction)
                if (
                    transaction["entry"]["queue_entry_id"]
                    == record["queue_entry_id"]
                    and transaction["entry"]["candidate_identity"] == dict(identity)
                ):
                    self._resume_enqueue_transaction(transaction)
                    item = self.get_entry(record["queue_entry_id"])
                    break
            if item is None:
                raise _error(
                    "QUEUE_STORE_CORRUPT",
                    "identity index has no recoverable enqueue transaction",
                    500,
                )
        return item

    def _enqueue_idempotency_record(
        self,
        action: Mapping[str, Any],
        request: Mapping[str, Any],
        entry_id: str,
    ) -> dict[str, Any]:
        return {
            "human_enqueue_action_id": action["action_id"],
            "idempotency_key": request["idempotency_key"],
            "authenticated_actor": action["authenticated_actor"],
            "interactive_session_id": action["interactive_session_id"],
            "candidate_identity": {
                "candidate_id": request["candidate_id"],
                "candidate_revision": request["expected_candidate_revision"],
                "canonical_content_hash": request["expected_content_hash"],
            },
            "queue_entry_id": entry_id,
        }

    def _load_enqueue_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._enqueue_idempotency_path(idempotency_key)
        if not path.exists():
            return None
        value = _read_json(path)
        self._validate_enqueue_idempotency(value)
        return value

    def _validate_enqueue_idempotency(self, value: Mapping[str, Any]) -> None:
        _require_exact_keys(
            value,
            {
                "human_enqueue_action_id",
                "idempotency_key",
                "authenticated_actor",
                "interactive_session_id",
                "candidate_identity",
                "queue_entry_id",
            },
            "enqueue idempotency",
        )
        if not _ENQUEUE_ACTION_ID_RE.fullmatch(value["human_enqueue_action_id"]):
            raise _error("QUEUE_STORE_CORRUPT", "enqueue action identity invalid", 500)
        if not _IDEMPOTENCY_KEY_RE.fullmatch(value["idempotency_key"]):
            raise _error("QUEUE_STORE_CORRUPT", "enqueue idempotency key invalid", 500)
        _require_nonempty(value["authenticated_actor"], "authenticated_actor")
        _require_nonempty(value["interactive_session_id"], "interactive_session_id")
        identity = value["candidate_identity"]
        _require_exact_keys(
            identity,
            {"candidate_id", "candidate_revision", "canonical_content_hash"},
            "idempotency Candidate identity",
        )
        self._validated_candidate_identity(
            identity["candidate_id"],
            identity["candidate_revision"],
            identity["canonical_content_hash"],
            request=False,
        )
        self._require_entry_id(value["queue_entry_id"], request=False)

    def _assert_enqueue_replay_matches(
        self,
        replay: Mapping[str, Any],
        request: Mapping[str, Any],
        action: Mapping[str, Any],
    ) -> None:
        expected = self._enqueue_idempotency_record(
            action, request, replay["queue_entry_id"]
        )
        if replay != expected:
            raise _error(
                "QUEUE_IDEMPOTENCY_CONFLICT",
                "enqueue action is already bound to another request",
                409,
            )

    def _validate_identity_record(self, value: Mapping[str, Any]) -> None:
        _require_exact_keys(
            value,
            {"candidate_identity", "queue_entry_id", "entry_integrity_hash"},
            "Candidate identity index",
        )
        identity = value["candidate_identity"]
        _require_exact_keys(
            identity,
            {"candidate_id", "candidate_revision", "canonical_content_hash"},
            "indexed Candidate identity",
        )
        self._validated_candidate_identity(
            identity["candidate_id"],
            identity["candidate_revision"],
            identity["canonical_content_hash"],
            request=False,
        )
        self._require_entry_id(value["queue_entry_id"], request=False)
        if not isinstance(value["entry_integrity_hash"], str) or not _HASH_RE.fullmatch(
            value["entry_integrity_hash"]
        ):
            raise _error("QUEUE_STORE_CORRUPT", "indexed integrity hash invalid", 500)

    def _validate_commit(
        self, entry: Mapping[str, Any], commit: Mapping[str, Any]
    ) -> None:
        _require_exact_keys(
            commit,
            {
                "queue_entry_id",
                "entry_integrity_hash",
                "human_enqueue_action_id_hash",
            },
            "Queue Entry commit",
        )
        if (
            commit["queue_entry_id"] != entry["queue_entry_id"]
            or commit["entry_integrity_hash"] != entry["entry_integrity_hash"]
            or not isinstance(commit["human_enqueue_action_id_hash"], str)
            or not _HASH_RE.fullmatch(commit["human_enqueue_action_id_hash"])
            or commit["human_enqueue_action_id_hash"]
            != hashlib.sha256(
                entry["enqueue_authority"]["human_enqueue_action_id"].encode(
                    "utf-8"
                )
            ).hexdigest()
        ):
            raise _error("QUEUE_STORE_CORRUPT", "Queue Entry commit mismatch", 500)

    def _allocate_sequence(self) -> int:
        maximum = 0
        for path in self._entries_dir.glob("*.json"):
            value = _read_json(path)
            sequence = value.get("enqueue_sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise _error("QUEUE_STORE_CORRUPT", "stored sequence invalid", 500)
            maximum = max(maximum, sequence)
        for path in self._transactions_dir.glob("*.json"):
            value = _read_json(path)
            sequence = value.get("entry", {}).get("enqueue_sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise _error("QUEUE_STORE_CORRUPT", "transaction sequence invalid", 500)
            maximum = max(maximum, sequence)
        marker_path = self._sequence_dir / "next.json"
        if marker_path.exists():
            marker = _read_json(marker_path)
            if set(marker) != {"next_sequence"} or not isinstance(
                marker["next_sequence"], int
            ):
                raise _error("QUEUE_STORE_CORRUPT", "sequence marker invalid", 500)
            maximum = max(maximum, marker["next_sequence"] - 1)
        return maximum + 1

    def _advance_sequence_marker(self, next_sequence: int) -> None:
        path = self._sequence_dir / "next.json"
        if path.exists():
            current = _read_json(path)
            if set(current) != {"next_sequence"} or not isinstance(
                current["next_sequence"], int
            ):
                raise _error("QUEUE_STORE_CORRUPT", "sequence marker invalid", 500)
            next_sequence = max(next_sequence, current["next_sequence"])
        _write_atomic_json(path, {"next_sequence": next_sequence})

    @staticmethod
    def _require_entry_id(value: Any, *, request: bool) -> None:
        if not isinstance(value, str) or not _ENTRY_ID_RE.fullmatch(value):
            raise _error(
                "QUEUE_REQUEST_INVALID" if request else "QUEUE_STORE_CORRUPT",
                "queue_entry_id invalid",
                400 if request else 500,
            )

    @contextmanager
    def _queue_lock(self) -> Iterator[None]:
        key = str(self.base_dir.resolve())
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        acquired = process_lock.acquire(timeout=5.0)
        if not acquired:
            raise _error("QUEUE_STORE_BUSY", "queue store is busy", 409)
        lock_path = self._locks_dir / "queue.lock"
        lock_stream = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_stream = open(lock_path, "a+b")
            if lock_stream.tell() == 0:
                lock_stream.write(b"\0")
                lock_stream.flush()
            self._lock_stream(lock_stream)
            yield
        finally:
            if lock_stream is not None:
                self._unlock_stream(lock_stream)
                lock_stream.close()
            process_lock.release()

    @staticmethod
    def _lock_stream(stream: Any) -> None:
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
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise _error("QUEUE_STORE_BUSY", "queue store is busy", 409) from exc
                time.sleep(0.01)

    @staticmethod
    def _unlock_stream(stream: Any) -> None:
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

    def _action_path(self, action_id: str) -> Path:
        return self._human_actions_dir / f"{action_id}.json"

    def _action_issue_path(self, idempotency_key: str) -> Path:
        return self._action_issue_idempotency_dir / f"{idempotency_key}.json"

    def _entry_path(self, entry_id: str) -> Path:
        return self._entries_dir / f"{entry_id}.json"

    def _entry_commit_path(self, entry_id: str) -> Path:
        return self._entry_commits_dir / f"{entry_id}.json"

    def _event_path(self, entry_id: str, sequence: int) -> Path:
        return self._events_dir / entry_id / f"{sequence:06d}.json"

    def _enqueue_idempotency_path(self, idempotency_key: str) -> Path:
        return self._enqueue_idempotency_dir / f"{idempotency_key}.json"

    def _prepare_idempotency_path(self, action_id: str, entry_id: str) -> Path:
        return self._prepare_idempotency_dir / (
            f"{_safe_name(action_id + ':' + entry_id)}.json"
        )

    def _enqueue_transaction_path(self, idempotency_key: str) -> Path:
        return self._transactions_dir / f"{idempotency_key}.json"

    def _identity_index_path(self, identity: Mapping[str, Any]) -> Path:
        return self._identity_index_dir / f"{canonical_sha256(dict(identity))}.json"
