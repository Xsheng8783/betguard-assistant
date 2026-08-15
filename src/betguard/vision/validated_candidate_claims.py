"""Exclusive, identity-only claims for the validated Candidate queue.

Claims are an isolated authority layer.  They never contain Candidate values
and never authorize fill, submission, WebFill, or workflow completion.  Queue
entries remain ``QUEUED`` while claimed; a Claim is the only ownership fence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

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
from betguard.vision.validated_candidate_queue import (
    ValidatedCandidateQueueError,
    ValidatedCandidateQueueStore,
)


CLAIM_SCHEMA_VERSION = "vision-validated-candidate-queue-claim-v1"
CLAIM_EVENT_SCHEMA_VERSION = "vision-validated-candidate-queue-claim-event-v1"
CLAIM_CLOCK_SCHEMA_VERSION = "vision-validated-candidate-queue-claim-clock-v1"
CLAIM_ACTION_SCHEMA_VERSION = "vision-validated-candidate-queue-claim-action-v1"
CLAIM_TRANSACTION_SCHEMA_VERSION = "vision-validated-candidate-queue-claim-transaction-v1"
CLAIM_EVENT_TRANSACTION_SCHEMA_VERSION = (
    "vision-validated-candidate-queue-claim-event-transaction-v1"
)
CLAIM_AUTHORITY_BLOCK_TRANSACTION_SCHEMA_VERSION = (
    "vision-validated-candidate-queue-claim-authority-block-transaction-v1"
)
CLAIM_ENVELOPE_SCHEMA_VERSION = "vision-validated-candidate-queue-claimed-envelope-v1"
LEASE_POLICY_VERSION = "vision-validated-candidate-lease-policy-v1"

_CLAIM_ID_RE = re.compile(r"^vqc-[a-f0-9]{32}$")
_ENTRY_ID_RE = re.compile(r"^vcq-[a-f0-9]{32}$")
_CONSUMER_ID_RE = re.compile(r"^vqcns-[a-f0-9]{32}$")
_ACTION_ID_RE = re.compile(r"^q(?:ca|ra|xa|aa)-[a-f0-9]{32}$")
_IDEMPOTENCY_RE = re.compile(r"^qik-[a-f0-9]{32}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")

_SAFETY = {
    "read_only_claim": True,
    "identity_reference_only": True,
    "candidate_values_embedded": False,
    "approved_for_fill": False,
    "approved_for_submit": False,
    "webfill_authorized": False,
    "auto_confirm": False,
    "auto_submit": False,
    "completed": False,
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
    "manual_candidate_id",
    "queue_path",
}

_TERMINAL_EVENTS = {"RELEASED", "ABANDONED", "EXPIRED", "AUTHORITY_BLOCKED"}


class ValidatedCandidateClaimError(Exception):
    """Stable, handler-friendly Claim authority error."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _error(code: str, message: str, status: int = 422) -> ValidatedCandidateClaimError:
    return ValidatedCandidateClaimError(code, message, status)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error("CLAIM_CLOCK_UNSAFE", "clock must return timezone-aware UTC", 503)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise _error("CLAIM_STORE_CORRUPT", f"{label} must be a date-time", 500)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error("CLAIM_STORE_CORRUPT", f"{label} is invalid", 500) from exc
    if parsed.tzinfo is None:
        raise _error("CLAIM_STORE_CORRUPT", f"{label} lacks timezone", 500)
    return parsed.astimezone(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _error("CLAIM_STORE_CORRUPT", f"invalid Claim artifact: {path.name}", 500) from exc
    if not isinstance(value, dict):
        raise _error("CLAIM_STORE_CORRUPT", f"Claim artifact is not an object: {path.name}", 500)
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(dict(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise _error("CLAIM_STORE_CORRUPT", f"immutable Claim collision: {path.name}", 500)
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise _error(
                    "CLAIM_STORE_CORRUPT",
                    f"immutable Claim collision: {path.name}",
                    500,
                )
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(dict(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _require_exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _error("CLAIM_SCHEMA_INVALID", f"{label} keys invalid", 500)
    return value


def _no_values(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        overlap = set(value) & _FORBIDDEN_VALUE_KEYS
        if overlap:
            raise _error(
                "CLAIM_STORE_CORRUPT",
                f"{label} contains forbidden Candidate value keys: {sorted(overlap)}",
                500,
            )
        for child in value.values():
            _no_values(child, label)
    elif isinstance(value, list):
        for child in value:
            _no_values(child, label)


class ValidatedCandidateClaimStore:
    """Filesystem-backed Claim/Lease store coordinated by one Queue lock."""

    def __init__(
        self,
        base_dir: Path | str,
        queue_store: ValidatedCandidateQueueStore,
        candidate_validator: CandidateConsumptionAuthorityValidator,
        *,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 300,
        maximum_total_seconds: int = 1800,
        owner_session_validator: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        if not isinstance(queue_store, ValidatedCandidateQueueStore):
            raise TypeError("queue_store must be ValidatedCandidateQueueStore")
        if not callable(getattr(candidate_validator, "validate_request", None)):
            raise TypeError("candidate_validator must provide validate_request")
        self.base_dir = Path(base_dir)
        if self.base_dir.resolve() != queue_store.claim_store_root.resolve():
            raise ValueError("Claim store must use queue_store.claim_store_root")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds < 1
            or isinstance(maximum_total_seconds, bool)
            or not isinstance(maximum_total_seconds, int)
            or maximum_total_seconds < lease_seconds
        ):
            raise ValueError("lease policy must be positive and bounded")
        self._queue = queue_store
        self._validator = candidate_validator
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_seconds = lease_seconds
        self._maximum_total_seconds = maximum_total_seconds
        if not callable(owner_session_validator):
            raise TypeError("owner_session_validator is required and must be callable")
        self._session_validator = owner_session_validator
        self._claims = self.base_dir / "claims"
        self._commits = self.base_dir / "claim-commits"
        self._events = self.base_dir / "events"
        self._actions = self.base_dir / "actions"
        self._action_issue = self.base_dir / "action-issue-idempotency"
        self._operations = self.base_dir / "operation-idempotency"
        self._claim_transactions = self.base_dir / "claim-transactions"
        self._event_transactions = self.base_dir / "event-transactions"
        self._authority_transactions = self.base_dir / "authority-block-transactions"
        self._authority_commits = self.base_dir / "authority-block-commits"
        self._clock_dir = self.base_dir / "clock"
        for directory in (
            self._claims,
            self._commits,
            self._events,
            self._actions,
            self._action_issue,
            self._operations,
            self._claim_transactions,
            self._event_transactions,
            self._authority_transactions,
            self._authority_commits,
            self._clock_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        _write_immutable(
            self.base_dir / "store-enabled.json",
            {"schema_version": "vision-validated-candidate-queue-claim-store-v1"},
        )
        self._queue.install_claim_activity_guard(self._has_active_claim_locked)

    @classmethod
    def from_authority_store(
        cls,
        queue_store: ValidatedCandidateQueueStore,
        authority_store: VisionCandidateAuthorityStore,
        **kwargs: Any,
    ) -> "ValidatedCandidateClaimStore":
        return cls(
            queue_store.claim_store_root,
            queue_store,
            CandidateConsumptionAuthorityValidator(authority_store),
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Server-bound action issuance
    # ------------------------------------------------------------------

    def bind_claim_action(
        self,
        *,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._bind_action(
            "CLAIM",
            None,
            authenticated_principal,
            consumer_id,
            server_session_id,
            idempotency_key,
        )

    def bind_renew_action(self, *, claim_id: str, claim_generation: int | None = None, authenticated_principal: str, consumer_id: str, server_session_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._bind_action("RENEW", claim_id, authenticated_principal, consumer_id, server_session_id, idempotency_key, claim_generation)

    def bind_release_action(self, *, claim_id: str, claim_generation: int | None = None, authenticated_principal: str, consumer_id: str, server_session_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._bind_action("RELEASE", claim_id, authenticated_principal, consumer_id, server_session_id, idempotency_key, claim_generation)

    def bind_abandon_action(self, *, claim_id: str, claim_generation: int | None = None, authenticated_principal: str, consumer_id: str, server_session_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._bind_action("ABANDON", claim_id, authenticated_principal, consumer_id, server_session_id, idempotency_key, claim_generation)

    def _bind_action(self, purpose: str, claim_id: str | None, principal: str, consumer: str, session: str, idempotency: str, expected_generation: int | None = None) -> dict[str, Any]:
        self._require_owner(principal, consumer, session)
        self._require_idempotency(idempotency)
        if not self._session_validator(principal, consumer, session):
            raise _error(
                "CLAIM_OWNER_SESSION_UNRECOVERABLE",
                "owner session is not recoverable",
                409,
            )
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            observed_at = self._observe_clock_locked()
            self._recover_locked()
            target: dict[str, Any] | None = None
            if purpose != "CLAIM":
                self._require_claim_id(claim_id)
                claim = self._load_claim(claim_id)
                if claim is None:
                    raise _error("CLAIM_NOT_FOUND", "Claim not found", 404)
                if expected_generation is not None and (
                    isinstance(expected_generation, bool)
                    or not isinstance(expected_generation, int)
                    or expected_generation != claim["claim_generation"]
                ):
                    raise _error("CLAIM_STALE", "Claim generation mismatch", 409)
                state = self._derive_state(claim, self._events_for(claim_id))
                if state["state"] != "ACTIVE":
                    raise _error("CLAIM_TERMINAL", "Claim is terminal", 409)
                self._assert_owner(claim, principal, consumer, session)
                target = self._action_target(claim, state)
            issue = {
                "purpose": purpose,
                "authenticated_principal": principal,
                "consumer_id": consumer,
                "server_session_id": session,
                "target": target,
                "idempotency_key": idempotency,
            }
            path = self._action_issue / f"{idempotency}.json"
            if path.exists():
                record = _read(path)
                _require_exact(record, {"request", "action_id"}, "Claim action issue")
                _no_values(record, "Claim action issue")
                if record.get("request") != issue:
                    raise _error("CLAIM_IDEMPOTENCY_CONFLICT", "action issue key conflict", 409)
                action = _read(self._actions / f"{record['action_id']}.json")
                self._validate_action(action)
                return deepcopy(action)
            prefix = {"CLAIM": "qca", "RENEW": "qra", "RELEASE": "qxa", "ABANDON": "qaa"}[purpose]
            action_id = f"{prefix}-{uuid.uuid4().hex}"
            action = {
                "schema_version": CLAIM_ACTION_SCHEMA_VERSION,
                "action_id": action_id,
                "purpose": purpose,
                "authenticated_principal": principal,
                "consumer_id": consumer,
                "server_session_id": session,
                "target": target,
                "idempotency_key": idempotency,
                "created_at": _iso(observed_at),
            }
            _no_values(action, "Claim action")
            _write_immutable(self._actions / f"{action_id}.json", action)
            _write_immutable(path, {"request": issue, "action_id": action_id})
            return deepcopy(action)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def claim_next(self, payload: Mapping[str, Any], *, authenticated_principal: str, consumer_id: str, server_session_id: str) -> dict[str, Any] | None:
        request = self._decode(payload)
        action = self._load_action(request, "CLAIM", authenticated_principal, consumer_id, server_session_id)
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            self._expire_all_locked(now)
            replay = self._operation_replay(request["idempotency_key"], action)
            if replay is not None:
                if replay.get("claim_id") is None:
                    return None
                return self._authoritative_locked(replay["claim_id"], authenticated_principal, consumer_id, server_session_id, now, replayed=True)
            for item in self._queue.list_entries(state="QUEUED"):
                entry = item["queue_entry"]
                if self._has_active_claim_locked(entry["queue_entry_id"]):
                    continue
                if not self._session_validator(
                    authenticated_principal, consumer_id, server_session_id
                ):
                    raise _error(
                        "CLAIM_OWNER_SESSION_UNRECOVERABLE",
                        "owner session is not recoverable",
                        409,
                    )
                identity = entry["candidate_identity"]
                try:
                    validation = self._validate_candidate(identity)
                except CandidateAuthorityError as exc:
                    self._queue.append_claim_authority_blocked_locked(
                        queue_entry_id=entry["queue_entry_id"],
                        claim_action_id=f"qba-{uuid.uuid4().hex}",
                        reason_code=exc.code,
                    )
                    continue
                transaction = self._build_claim_transaction(entry, action, validation, now)
                _write_immutable(self._claim_transactions / f"{request['idempotency_key']}.json", transaction)
                self._resume_claim_transaction(transaction)
                return self._authoritative_locked(transaction["claim"]["claim_id"], authenticated_principal, consumer_id, server_session_id, now, replayed=False, validation=validation)
            record = {"action_id": action["action_id"], "result": "NO_AVAILABLE_ENTRY", "claim_id": None}
            _write_immutable(self._operations / f"{request['idempotency_key']}.json", record)
            return None

    def renew(self, payload: Mapping[str, Any], *, authenticated_principal: str, consumer_id: str, server_session_id: str) -> dict[str, Any]:
        return self._owner_transition(payload, "RENEW", authenticated_principal, consumer_id, server_session_id)

    def release(self, payload: Mapping[str, Any], *, authenticated_principal: str, consumer_id: str, server_session_id: str) -> dict[str, Any]:
        return self._owner_transition(payload, "RELEASE", authenticated_principal, consumer_id, server_session_id)

    def abandon(self, payload: Mapping[str, Any], *, authenticated_principal: str, consumer_id: str, server_session_id: str) -> dict[str, Any]:
        return self._owner_transition(payload, "ABANDON", authenticated_principal, consumer_id, server_session_id)

    def _owner_transition(self, payload: Mapping[str, Any], purpose: str, principal: str, consumer: str, session: str) -> dict[str, Any]:
        request = self._decode(payload)
        action = self._load_action(request, purpose, principal, consumer, session)
        target = action["target"]
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            claim = self._load_claim(target["claim_id"])
            if claim is None:
                raise _error("CLAIM_NOT_FOUND", "Claim not found", 404)
            events = self._events_for(claim["claim_id"])
            state = self._derive_state(claim, events)
            replay = self._operation_replay(request["idempotency_key"], action)
            if replay is not None:
                matching = [
                    event
                    for event in self._events_for(claim["claim_id"])
                    if event["event_id"] == replay.get("event_id")
                ]
                if len(matching) != 1:
                    raise _error(
                        "CLAIM_STORE_CORRUPT",
                        "operation replay event is missing or ambiguous",
                        500,
                    )
                return self._transition_result(claim, matching[0], replayed=True)
            self._assert_action_target(action, claim, state)
            self._assert_owner(claim, principal, consumer, session)
            if not self._session_validator(principal, consumer, session):
                raise _error("CLAIM_OWNER_SESSION_UNRECOVERABLE", "owner session is not recoverable", 409)
            if state["state"] != "ACTIVE":
                raise _error("CLAIM_TERMINAL", "Claim is terminal", 409)
            if now >= state["expires_at"]:
                self._expire_claim_locked(claim, state, now)
                raise _error("CLAIM_EXPIRED", "Claim expired", 409)
            try:
                self._validate_candidate(claim["candidate_identity"])
            except CandidateAuthorityError as exc:
                self._authority_block_locked(claim, state, exc.code, now)
                raise _error("CLAIM_AUTHORITY_INVALID", f"Candidate authority failed: {exc.code}", 409) from exc
            if purpose == "RENEW":
                maximum = _parse_time(claim["issued_at"], "issued_at") + timedelta(seconds=self._maximum_total_seconds)
                expires = min(now + timedelta(seconds=self._lease_seconds), maximum)
                if expires <= state["expires_at"]:
                    raise _error("CLAIM_RENEWAL_LIMIT", "no lease headroom remains", 409)
                event_type = "RENEWED"
                lease_expires = _iso(expires)
            else:
                event_type = "RELEASED" if purpose == "RELEASE" else "ABANDONED"
                lease_expires = None
            event = self._event_record(
                claim,
                sequence=len(events) + 1,
                event_type=event_type,
                occurred_at=now,
                lease_expires_at=lease_expires,
                actor=principal,
                reason_code=None,
                action_id=action["action_id"],
                idempotency_key=request["idempotency_key"],
            )
            transaction = {
                "schema_version": CLAIM_EVENT_TRANSACTION_SCHEMA_VERSION,
                "action_id": action["action_id"],
                "idempotency_key": request["idempotency_key"],
                "claim_id": claim["claim_id"],
                "event": event,
            }
            _no_values(transaction, "Claim event transaction")
            _write_immutable(self._event_transactions / f"{request['idempotency_key']}.json", transaction)
            self._resume_event_transaction(transaction)
            return self._transition_result(claim, event, replayed=False)

    # ------------------------------------------------------------------
    # Read state and removal guard
    # ------------------------------------------------------------------

    def get_claim_state(self, claim_id: str) -> dict[str, Any] | None:
        self._require_claim_id(claim_id)
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            claim = self._load_claim(claim_id)
            if claim is None:
                return None
            state = self._derive_state(claim, self._events_for(claim_id))
            if state["state"] == "ACTIVE" and now >= state["expires_at"]:
                self._expire_claim_locked(claim, state, now)
                state = self._derive_state(claim, self._events_for(claim_id))
            if state["state"] == "ACTIVE":
                queue_item = self._queue.get_entry(
                    claim["queue_entry_identity"]["queue_entry_id"]
                )
                if queue_item is None or queue_item["state"] != "QUEUED":
                    self._authority_block_locked(
                        claim, state, "CANDIDATE_QUEUE_STATE_INVALID", now
                    )
                    state = self._derive_state(claim, self._events_for(claim_id))
                    return {
                        "claim": self._redacted_claim(claim),
                        "state": state["state"],
                        "lease_expires_at": None,
                    }
                try:
                    self._validate_candidate(claim["candidate_identity"])
                except CandidateAuthorityError as exc:
                    self._authority_block_locked(claim, state, exc.code, now)
                    raise _error(
                        "CLAIM_AUTHORITY_INVALID",
                        f"Candidate authority failed: {exc.code}",
                        409,
                    ) from exc
                state = self._derive_state(claim, self._events_for(claim_id))
            return {
                "claim": self._redacted_claim(claim),
                "state": state["state"],
                "lease_expires_at": _iso(state["expires_at"])
                if state.get("expires_at")
                else None,
            }

    def get_authoritative_claim(self, *, claim_id: str, claim_generation: int, authenticated_principal: str, consumer_id: str, server_session_id: str, fencing_token: str) -> dict[str, Any]:
        self._require_claim_id(claim_id)
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            claim = self._load_claim(claim_id)
            if claim is None:
                raise _error("CLAIM_NOT_FOUND", "Claim not found", 404)
            if claim_generation != claim["claim_generation"]:
                raise _error("CLAIM_STALE", "Claim generation mismatch", 409)
            if fencing_token != claim["fencing_token"]:
                raise _error("CLAIM_FENCE_MISMATCH", "fencing token mismatch", 409)
            return self._authoritative_locked(claim_id, authenticated_principal, consumer_id, server_session_id, now, replayed=False)

    def list_claims(self) -> list[dict[str, Any]]:
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            self._expire_all_locked(now)
            results = []
            for item in self._list_claims_locked():
                claim = item["claim"]
                state = self._derive_state(claim, self._events_for(claim["claim_id"]))
                if state["state"] == "ACTIVE":
                    queue_item = self._queue.get_entry(
                        claim["queue_entry_identity"]["queue_entry_id"]
                    )
                    if queue_item is None or queue_item["state"] != "QUEUED":
                        self._authority_block_locked(
                            claim,
                            state,
                            "CANDIDATE_QUEUE_STATE_INVALID",
                            now,
                        )
                        state = self._derive_state(
                            claim, self._events_for(claim["claim_id"])
                        )
                        results.append(
                            {
                                "claim": self._redacted_claim(claim),
                                "state": state["state"],
                            }
                        )
                        continue
                    try:
                        self._validate_candidate(claim["candidate_identity"])
                    except CandidateAuthorityError as exc:
                        self._authority_block_locked(claim, state, exc.code, now)
                        state = self._derive_state(
                            claim, self._events_for(claim["claim_id"])
                        )
                results.append(
                    {"claim": self._redacted_claim(claim), "state": state["state"]}
                )
            return results

    def _list_claims_locked(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path in sorted(self._commits.glob("*.json")):
            claim = self._load_claim(path.stem)
            if claim is not None:
                state = self._derive_state(claim, self._events_for(claim["claim_id"]))
                results.append({"claim": deepcopy(claim), "state": state["state"]})
        by_entry: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            entry_id = item["claim"]["queue_entry_identity"]["queue_entry_id"]
            by_entry.setdefault(entry_id, []).append(item)
        for history in by_entry.values():
            history.sort(key=lambda item: item["claim"]["claim_generation"])
            generations = [item["claim"]["claim_generation"] for item in history]
            if generations != list(range(1, len(history) + 1)):
                raise _error("CLAIM_STORE_CORRUPT", "Claim generation history invalid", 500)
            fences = [item["claim"]["fencing_token"] for item in history]
            if len(fences) != len(set(fences)):
                raise _error("CLAIM_STORE_CORRUPT", "Claim fencing token was reused", 500)
            active = [item for item in history if item["state"] == "ACTIVE"]
            if len(active) > 1:
                raise _error("CLAIM_STORE_CORRUPT", "multiple active Claim generations", 500)
            if active and active[0] is not history[-1]:
                raise _error("CLAIM_STORE_CORRUPT", "non-latest Claim generation is active", 500)
        return results

    def get_claim_events(self, claim_id: str) -> list[dict[str, Any]]:
        self._require_claim_id(claim_id)
        with self._queue.claim_coordination():
            self._preflight_pending_transactions_locked()
            now = self._observe_clock_locked()
            self._recover_locked()
            claim = self._load_claim(claim_id)
            if claim is None:
                return []
            state = self._derive_state(claim, self._events_for(claim_id))
            if state["state"] == "ACTIVE" and now >= state["expires_at"]:
                self._expire_claim_locked(claim, state, now)
            return [self._redacted_event(event) for event in self._events_for(claim_id)]

    def assert_entry_removable(self, queue_entry_id: str) -> None:
        if self._has_active_claim_locked(queue_entry_id):
            raise _error("CLAIM_ACTIVE", "Queue entry has an active Claim", 409)

    @staticmethod
    def _redacted_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(claim))
        result.pop("fencing_token", None)
        owner = result.get("lease_owner")
        if isinstance(owner, dict):
            owner.pop("server_session_id", None)
        return result

    @staticmethod
    def _redacted_event(event: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(event))
        result.pop("fencing_token", None)
        result.pop("action_id", None)
        return result

    # ------------------------------------------------------------------
    # Locked authority and lifecycle helpers
    # ------------------------------------------------------------------

    def _authoritative_locked(self, claim_id: str, principal: str, consumer: str, session: str, now: datetime, *, replayed: bool, validation: dict[str, Any] | None = None) -> dict[str, Any]:
        claim = self._load_claim(claim_id)
        if claim is None:
            raise _error("CLAIM_NOT_FOUND", "Claim not found", 404)
        state = self._derive_state(claim, self._events_for(claim_id))
        self._assert_owner(claim, principal, consumer, session)
        if not self._session_validator(principal, consumer, session):
            raise _error("CLAIM_OWNER_SESSION_UNRECOVERABLE", "owner session is not recoverable", 409)
        if state["state"] != "ACTIVE":
            raise _error("CLAIM_TERMINAL", "Claim is terminal", 409)
        if now >= state["expires_at"]:
            self._expire_claim_locked(claim, state, now)
            raise _error("CLAIM_EXPIRED", "Claim expired", 409)
        item = self._queue.get_entry(claim["queue_entry_identity"]["queue_entry_id"])
        if item is None or item["state"] != "QUEUED":
            self._authority_block_locked(
                claim,
                state,
                "CANDIDATE_QUEUE_STATE_INVALID",
                now,
            )
            raise _error("CLAIM_AUTHORITY_INVALID", "Queue entry is not QUEUED", 409)
        try:
            validation = validation or self._validate_candidate(claim["candidate_identity"])
        except CandidateAuthorityError as exc:
            self._authority_block_locked(claim, state, exc.code, now)
            raise _error("CLAIM_AUTHORITY_INVALID", f"Candidate authority failed: {exc.code}", 409) from exc
        return {
            "schema_version": CLAIM_ENVELOPE_SCHEMA_VERSION,
            "status": "ACTIVE",
            "claim_id": claim["claim_id"],
            "claim_generation": claim["claim_generation"],
            "fencing_token": claim["fencing_token"],
            "queue_entry_identity": deepcopy(claim["queue_entry_identity"]),
            "candidate_identity": deepcopy(claim["candidate_identity"]),
            "lease_owner": deepcopy(claim["lease_owner"]),
            "lease_expires_at": _iso(state["expires_at"]),
            "validation": deepcopy(validation),
            "replayed": replayed,
            "safety": deepcopy(_SAFETY),
        }

    def _authority_block_locked(self, claim: Mapping[str, Any], state: Mapping[str, Any], reason: str, now: datetime) -> None:
        transaction_path = self._authority_transactions / f"{claim['claim_id']}.json"
        commit_path = self._authority_commits / f"{claim['claim_id']}.json"
        if commit_path.exists():
            commit = _read(commit_path)
            _require_exact(
                commit,
                {"claim_id", "queue_entry_id", "reason_code"},
                "Claim authority-block commit",
            )
            if (
                commit["claim_id"] != claim["claim_id"]
                or commit["queue_entry_id"]
                != claim["queue_entry_identity"]["queue_entry_id"]
                or not isinstance(commit["reason_code"], str)
                or not commit["reason_code"].startswith("CANDIDATE_")
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block commit invalid", 500)
            return
        if transaction_path.exists():
            transaction = _read(transaction_path)
        else:
            action_id = f"qba-{uuid.uuid4().hex}"
            idem = f"qik-{uuid.uuid4().hex}"
            event = self._event_record(claim, sequence=state["event_sequence"] + 1, event_type="AUTHORITY_BLOCKED", occurred_at=now, lease_expires_at=None, actor="candidate-consumption-validator", reason_code=reason, action_id=action_id, idempotency_key=idem)
            transaction = {
                "schema_version": CLAIM_AUTHORITY_BLOCK_TRANSACTION_SCHEMA_VERSION,
                "claim_id": claim["claim_id"],
                "queue_entry_id": claim["queue_entry_identity"]["queue_entry_id"],
                "reason_code": reason,
                "queue_action_id": action_id,
                "event": event,
            }
            _no_values(transaction, "authority block transaction")
            _write_immutable(transaction_path, transaction)
        try:
            self._write_event(transaction["event"])
            self._queue.append_claim_authority_blocked_locked(
                queue_entry_id=transaction["queue_entry_id"],
                claim_action_id=transaction["queue_action_id"],
                reason_code=transaction["reason_code"],
            )
            _write_immutable(commit_path, {"claim_id": claim["claim_id"], "queue_entry_id": transaction["queue_entry_id"], "reason_code": transaction["reason_code"]})
        except Exception as exc:
            raise _error("CLAIM_AUTHORITY_INVALID", f"Candidate authority is unusable; block publication pending: {reason}", 409) from exc

    def _expire_all_locked(self, now: datetime) -> None:
        for item in self._list_claims_locked():
            if item["state"] != "ACTIVE":
                continue
            claim = item["claim"]
            state = self._derive_state(claim, self._events_for(claim["claim_id"]))
            if now >= state["expires_at"]:
                self._expire_claim_locked(claim, state, now)

    def _expire_claim_locked(self, claim: Mapping[str, Any], state: Mapping[str, Any], now: datetime) -> None:
        idem = f"qik-{hashlib.sha256((claim['claim_id'] + ':expire').encode()).hexdigest()[:32]}"
        action = f"qea-{hashlib.sha256((claim['claim_id'] + ':expire-action').encode()).hexdigest()[:32]}"
        transaction_path = self._event_transactions / f"{idem}.json"
        if transaction_path.exists():
            self._resume_event_transaction(_read(transaction_path))
            return
        event = self._event_record(claim, sequence=state["event_sequence"] + 1, event_type="EXPIRED", occurred_at=now, lease_expires_at=None, actor="claim-clock", reason_code="lease_expired", action_id=action, idempotency_key=idem)
        transaction = {"schema_version": CLAIM_EVENT_TRANSACTION_SCHEMA_VERSION, "action_id": action, "idempotency_key": idem, "claim_id": claim["claim_id"], "event": event}
        _write_immutable(transaction_path, transaction)
        self._resume_event_transaction(transaction)

    # ------------------------------------------------------------------
    # Transactions, records, validation
    # ------------------------------------------------------------------

    def _build_claim_transaction(self, entry: Mapping[str, Any], action: Mapping[str, Any], validation: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        generation = self._next_generation(entry["queue_entry_id"])
        claim_id = f"vqc-{uuid.uuid4().hex}"
        expiry = now + timedelta(seconds=self._lease_seconds)
        claim = {
            "schema_version": CLAIM_SCHEMA_VERSION,
            "claim_id": claim_id,
            "claim_generation": generation,
            "fencing_token": f"vqf-{secrets.token_hex(32)}",
            "queue_entry_identity": {
                "queue_entry_id": entry["queue_entry_id"],
                "enqueue_sequence": entry["enqueue_sequence"],
                "entry_integrity_hash": entry["entry_integrity_hash"],
            },
            "candidate_identity": deepcopy(entry["candidate_identity"]),
            "lease_owner": {
                "consumer_id": action["consumer_id"],
                "authenticated_principal": action["authenticated_principal"],
                "server_session_id": action["server_session_id"],
            },
            "lease_policy": {
                "policy_version": LEASE_POLICY_VERSION,
                "lease_seconds": self._lease_seconds,
                "maximum_total_seconds": self._maximum_total_seconds,
            },
            "claim_action_id": action["action_id"],
            "claim_idempotency_key_hash": hashlib.sha256(action["idempotency_key"].encode()).hexdigest(),
            "issued_at": _iso(now),
            "expires_at": _iso(expiry),
            "validation": {
                "schema_version": CONSUMPTION_VALIDATION_SCHEMA_VERSION,
                "validation_status": "VALID_CURRENT",
                "validated_at": validation.get("validated_at", _iso(now)),
            },
            "state_at_creation": "ACTIVE",
            "safety": deepcopy(_SAFETY),
        }
        claim["record_integrity_hash"] = canonical_sha256(claim)
        self._validate_claim(claim)
        event = self._event_record(claim, sequence=1, event_type="CLAIMED", occurred_at=now, lease_expires_at=claim["expires_at"], actor=action["authenticated_principal"], reason_code=None, action_id=action["action_id"], idempotency_key=action["idempotency_key"])
        return {"schema_version": CLAIM_TRANSACTION_SCHEMA_VERSION, "action_id": action["action_id"], "idempotency_key": action["idempotency_key"], "claim": claim, "event": event}

    def _resume_claim_transaction(self, transaction: Mapping[str, Any]) -> None:
        _require_exact(
            transaction,
            {"schema_version", "action_id", "idempotency_key", "claim", "event"},
            "Claim publication transaction",
        )
        if transaction["schema_version"] != CLAIM_TRANSACTION_SCHEMA_VERSION:
            raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction schema invalid", 500)
        if (
            not isinstance(transaction["action_id"], str)
            or not re.fullmatch(r"^qca-[a-f0-9]{32}$", transaction["action_id"])
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction action invalid", 500)
        self._require_idempotency(transaction["idempotency_key"])
        _no_values(transaction, "Claim publication transaction")
        claim = transaction["claim"]
        self._validate_claim(claim)
        self._validate_event(transaction["event"])
        if (
            claim["claim_action_id"] != transaction["action_id"]
            or transaction["event"]["claim_id"] != claim["claim_id"]
            or transaction["event"]["event_type"] != "CLAIMED"
            or transaction["event"]["action_id"] != transaction["action_id"]
            or transaction["event"]["event_sequence"] != 1
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction relation invalid", 500)
        _write_immutable(self._claims / f"{claim['claim_id']}.json", claim)
        self._write_event(transaction["event"])
        _write_immutable(self._operations / f"{transaction['idempotency_key']}.json", {"action_id": transaction["action_id"], "result": "CLAIMED", "claim_id": claim["claim_id"]})
        _write_immutable(self._commits / f"{claim['claim_id']}.json", {"claim_id": claim["claim_id"], "record_integrity_hash": claim["record_integrity_hash"]})

    def _resume_event_transaction(self, transaction: Mapping[str, Any]) -> None:
        _require_exact(
            transaction,
            {"schema_version", "action_id", "idempotency_key", "claim_id", "event"},
            "Claim event transaction",
        )
        if transaction["schema_version"] != CLAIM_EVENT_TRANSACTION_SCHEMA_VERSION:
            raise _error("CLAIM_SCHEMA_INVALID", "Claim event transaction schema invalid", 500)
        self._require_claim_id(transaction["claim_id"])
        self._require_idempotency(transaction["idempotency_key"])
        self._validate_event(transaction["event"])
        if (
            transaction["event"]["claim_id"] != transaction["claim_id"]
            or transaction["event"]["action_id"] != transaction["action_id"]
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim event transaction relation invalid", 500)
        _no_values(transaction, "Claim event transaction")
        self._write_event(transaction["event"])
        _write_immutable(self._operations / f"{transaction['idempotency_key']}.json", {"action_id": transaction["action_id"], "result": transaction["event"]["event_type"], "claim_id": transaction["claim_id"], "event_id": transaction["event"]["event_id"]})

    def _recover_locked(self) -> None:
        for path in sorted(self._claim_transactions.glob("*.json")):
            self._resume_claim_transaction(_read(path))
        for path in sorted(self._event_transactions.glob("*.json")):
            self._resume_event_transaction(_read(path))
        for path in sorted(self._authority_transactions.glob("*.json")):
            transaction = _read(path)
            _require_exact(
                transaction,
                {
                    "schema_version",
                    "claim_id",
                    "queue_entry_id",
                    "reason_code",
                    "queue_action_id",
                    "event",
                },
                "Claim authority-block transaction",
            )
            if (
                transaction["schema_version"]
                != CLAIM_AUTHORITY_BLOCK_TRANSACTION_SCHEMA_VERSION
                or not isinstance(transaction["queue_entry_id"], str)
                or not _ENTRY_ID_RE.fullmatch(transaction["queue_entry_id"])
                or not isinstance(transaction["reason_code"], str)
                or not transaction["reason_code"].startswith("CANDIDATE_")
                or not isinstance(transaction["queue_action_id"], str)
                or not re.fullmatch(r"^qba-[a-f0-9]{32}$", transaction["queue_action_id"])
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block transaction invalid", 500)
            self._validate_event(transaction["event"])
            if (
                transaction["event"]["claim_id"] != transaction["claim_id"]
                or transaction["event"]["queue_entry_id"]
                != transaction["queue_entry_id"]
                or transaction["event"]["event_type"] != "AUTHORITY_BLOCKED"
                or transaction["event"]["action_id"]
                != transaction["queue_action_id"]
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block relation invalid", 500)
            _no_values(transaction, "Claim authority-block transaction")
            if not (self._authority_commits / f"{transaction['claim_id']}.json").exists():
                claim = self._load_claim(transaction["claim_id"])
                if claim is None:
                    raise _error("CLAIM_STORE_CORRUPT", "authority transaction Claim missing", 500)
                state = self._derive_state(claim, self._events_for(claim["claim_id"]))
                if state["state"] == "ACTIVE":
                    self._authority_block_locked(claim, state, transaction["reason_code"], _parse_time(transaction["event"]["occurred_at"], "occurred_at"))
                else:
                    self._queue.append_claim_authority_blocked_locked(queue_entry_id=transaction["queue_entry_id"], claim_action_id=transaction["queue_action_id"], reason_code=transaction["reason_code"])
                    _write_immutable(self._authority_commits / f"{claim['claim_id']}.json", {"claim_id": claim["claim_id"], "queue_entry_id": transaction["queue_entry_id"], "reason_code": transaction["reason_code"]})
        for path in sorted(self._authority_commits.glob("*.json")):
            commit = _read(path)
            _require_exact(
                commit,
                {"claim_id", "queue_entry_id", "reason_code"},
                "Claim authority-block commit",
            )
            if (
                not isinstance(commit["claim_id"], str)
                or not _CLAIM_ID_RE.fullmatch(commit["claim_id"])
                or path.stem != commit["claim_id"]
                or not isinstance(commit["queue_entry_id"], str)
                or not _ENTRY_ID_RE.fullmatch(commit["queue_entry_id"])
                or not isinstance(commit["reason_code"], str)
                or not commit["reason_code"].startswith("CANDIDATE_")
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block commit invalid", 500)
            transaction_path = self._authority_transactions / path.name
            if not transaction_path.exists():
                raise _error("CLAIM_STORE_CORRUPT", "authority-block transaction missing", 500)

    def _preflight_pending_transactions_locked(self) -> None:
        """Validate every pending journal before clock or recovery writes."""

        for path in sorted(self._claim_transactions.glob("*.json")):
            transaction = _read(path)
            _require_exact(
                transaction,
                {"schema_version", "action_id", "idempotency_key", "claim", "event"},
                "Claim publication transaction",
            )
            if transaction["schema_version"] != CLAIM_TRANSACTION_SCHEMA_VERSION:
                raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction schema invalid", 500)
            if (
                not isinstance(transaction["action_id"], str)
                or not re.fullmatch(r"^qca-[a-f0-9]{32}$", transaction["action_id"])
                or not isinstance(transaction["idempotency_key"], str)
                or not _IDEMPOTENCY_RE.fullmatch(transaction["idempotency_key"])
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction identity invalid", 500)
            _no_values(transaction, "Claim publication transaction")
            self._validate_claim(transaction["claim"])
            self._validate_event(transaction["event"])
            if (
                transaction["claim"]["claim_action_id"] != transaction["action_id"]
                or transaction["event"]["claim_id"]
                != transaction["claim"]["claim_id"]
                or transaction["event"]["event_type"] != "CLAIMED"
                or transaction["event"]["action_id"] != transaction["action_id"]
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "Claim transaction relation invalid", 500)
        for path in sorted(self._event_transactions.glob("*.json")):
            transaction = _read(path)
            _require_exact(
                transaction,
                {"schema_version", "action_id", "idempotency_key", "claim_id", "event"},
                "Claim event transaction",
            )
            if (
                transaction["schema_version"]
                != CLAIM_EVENT_TRANSACTION_SCHEMA_VERSION
                or not isinstance(transaction["claim_id"], str)
                or not _CLAIM_ID_RE.fullmatch(transaction["claim_id"])
                or not isinstance(transaction["idempotency_key"], str)
                or not _IDEMPOTENCY_RE.fullmatch(transaction["idempotency_key"])
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "Claim event transaction invalid", 500)
            _no_values(transaction, "Claim event transaction")
            self._validate_event(transaction["event"])
            if (
                transaction["event"]["claim_id"] != transaction["claim_id"]
                or transaction["event"]["action_id"] != transaction["action_id"]
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "Claim event transaction relation invalid", 500)
        for path in sorted(self._authority_transactions.glob("*.json")):
            transaction = _read(path)
            _require_exact(
                transaction,
                {"schema_version", "claim_id", "queue_entry_id", "reason_code", "queue_action_id", "event"},
                "Claim authority-block transaction",
            )
            if (
                transaction["schema_version"]
                != CLAIM_AUTHORITY_BLOCK_TRANSACTION_SCHEMA_VERSION
                or not isinstance(transaction["claim_id"], str)
                or not _CLAIM_ID_RE.fullmatch(transaction["claim_id"])
                or not isinstance(transaction["queue_entry_id"], str)
                or not _ENTRY_ID_RE.fullmatch(transaction["queue_entry_id"])
                or not isinstance(transaction["reason_code"], str)
                or not transaction["reason_code"].startswith("CANDIDATE_")
                or not isinstance(transaction["queue_action_id"], str)
                or not re.fullmatch(r"^qba-[a-f0-9]{32}$", transaction["queue_action_id"])
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block transaction invalid", 500)
            _no_values(transaction, "Claim authority-block transaction")
            self._validate_event(transaction["event"])
            if (
                transaction["event"]["claim_id"] != transaction["claim_id"]
                or transaction["event"]["queue_entry_id"] != transaction["queue_entry_id"]
                or transaction["event"]["event_type"] != "AUTHORITY_BLOCKED"
                or transaction["event"]["action_id"] != transaction["queue_action_id"]
            ):
                raise _error("CLAIM_SCHEMA_INVALID", "authority-block relation invalid", 500)

    def _event_record(self, claim: Mapping[str, Any], *, sequence: int, event_type: str, occurred_at: datetime, lease_expires_at: str | None, actor: str, reason_code: str | None, action_id: str, idempotency_key: str) -> dict[str, Any]:
        event = {
            "schema_version": CLAIM_EVENT_SCHEMA_VERSION,
            "event_id": f"vqce-{uuid.uuid4().hex}",
            "claim_id": claim["claim_id"],
            "queue_entry_id": claim["queue_entry_identity"]["queue_entry_id"],
            "claim_generation": claim["claim_generation"],
            "event_sequence": sequence,
            "event_type": event_type,
            "occurred_at": _iso(occurred_at),
            "lease_expires_at": lease_expires_at,
            "actor": actor,
            "reason_code": reason_code,
            "action_id": action_id,
            "idempotency_key_hash": hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "fencing_token": claim["fencing_token"],
            "claim_record_integrity_hash": claim["record_integrity_hash"],
        }
        event["event_integrity_hash"] = canonical_sha256(event)
        return event

    def _write_event(self, event: Mapping[str, Any]) -> None:
        _write_immutable(self._events / event["claim_id"] / f"{event['event_sequence']:06d}.json", event)

    def _load_claim(self, claim_id: str) -> dict[str, Any] | None:
        commit_path = self._commits / f"{claim_id}.json"
        if not commit_path.exists():
            return None
        claim = _read(self._claims / f"{claim_id}.json")
        self._validate_claim(claim)
        commit = _read(commit_path)
        if commit != {"claim_id": claim_id, "record_integrity_hash": claim["record_integrity_hash"]}:
            raise _error("CLAIM_STORE_CORRUPT", "Claim commit mismatch", 500)
        queue_item = self._queue.get_entry(
            claim["queue_entry_identity"]["queue_entry_id"]
        )
        if queue_item is None:
            raise _error("CLAIM_AUTHORITY_INVALID", "Claim Queue entry missing", 500)
        entry = queue_item["queue_entry"]
        expected_queue_identity = {
            "queue_entry_id": entry["queue_entry_id"],
            "enqueue_sequence": entry["enqueue_sequence"],
            "entry_integrity_hash": entry["entry_integrity_hash"],
        }
        if (
            claim["queue_entry_identity"] != expected_queue_identity
            or claim["candidate_identity"] != entry["candidate_identity"]
        ):
            raise _error(
                "CLAIM_AUTHORITY_INVALID",
                "Claim identity does not match committed Queue Entry",
                500,
            )
        action_path = self._actions / f"{claim['claim_action_id']}.json"
        if not action_path.exists():
            raise _error("CLAIM_AUTHORITY_INVALID", "Claim action missing", 500)
        action = _read(action_path)
        self._validate_action(action)
        if (
            action["purpose"] != "CLAIM"
            or action["target"] is not None
            or action["authenticated_principal"]
            != claim["lease_owner"]["authenticated_principal"]
            or action["consumer_id"] != claim["lease_owner"]["consumer_id"]
            or action["server_session_id"]
            != claim["lease_owner"]["server_session_id"]
            or hashlib.sha256(action["idempotency_key"].encode()).hexdigest()
            != claim["claim_idempotency_key_hash"]
        ):
            raise _error("CLAIM_AUTHORITY_INVALID", "Claim action relation invalid", 500)
        return claim

    def _events_for(self, claim_id: str) -> list[dict[str, Any]]:
        directory = self._events / claim_id
        if not directory.exists():
            return []
        events = [_read(path) for path in sorted(directory.glob("*.json"))]
        for sequence, event in enumerate(events, 1):
            self._validate_event(event)
            if event.get("event_sequence") != sequence or event.get("claim_id") != claim_id:
                raise _error("CLAIM_STORE_CORRUPT", "Claim event sequence invalid", 500)
        return events

    def _validate_event(self, event: Mapping[str, Any]) -> None:
        _require_exact(
            event,
            {
                "schema_version",
                "event_id",
                "claim_id",
                "queue_entry_id",
                "claim_generation",
                "event_sequence",
                "event_type",
                "occurred_at",
                "lease_expires_at",
                "actor",
                "reason_code",
                "action_id",
                "idempotency_key_hash",
                "fencing_token",
                "claim_record_integrity_hash",
                "event_integrity_hash",
            },
            "Claim event",
        )
        event_type = event["event_type"]
        if (
            event["schema_version"] != CLAIM_EVENT_SCHEMA_VERSION
            or not isinstance(event["event_id"], str)
            or not re.fullmatch(r"^vqce-[a-f0-9]{32}$", event["event_id"])
            or not isinstance(event["claim_id"], str)
            or not _CLAIM_ID_RE.fullmatch(event["claim_id"])
            or not isinstance(event["queue_entry_id"], str)
            or not _ENTRY_ID_RE.fullmatch(event["queue_entry_id"])
            or event_type
            not in {
                "CLAIMED",
                "RENEWED",
                "RELEASED",
                "ABANDONED",
                "EXPIRED",
                "AUTHORITY_BLOCKED",
            }
            or isinstance(event["event_sequence"], bool)
            or not isinstance(event["event_sequence"], int)
            or event["event_sequence"] < 1
            or isinstance(event["claim_generation"], bool)
            or not isinstance(event["claim_generation"], int)
            or event["claim_generation"] < 1
            or not isinstance(event["action_id"], str)
            or not re.fullmatch(r"^q(?:ca|ra|xa|aa|ea|ba)-[a-f0-9]{32}$", event["action_id"])
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim event identity invalid", 500)
        expected_prefix = {
            "CLAIMED": "qca",
            "RENEWED": "qra",
            "RELEASED": "qxa",
            "ABANDONED": "qaa",
            "EXPIRED": "qea",
            "AUTHORITY_BLOCKED": "qba",
        }[event_type]
        if not event["action_id"].startswith(expected_prefix + "-"):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim event action purpose invalid", 500)
        if not isinstance(event["actor"], str) or not event["actor"]:
            raise _error("CLAIM_SCHEMA_INVALID", "Claim event actor invalid", 500)
        if event["reason_code"] is not None and (
            not isinstance(event["reason_code"], str) or not event["reason_code"]
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim event reason invalid", 500)
        if event_type in {"CLAIMED", "RENEWED", "RELEASED", "ABANDONED"}:
            if event["reason_code"] is not None:
                raise _error("CLAIM_SCHEMA_INVALID", "Claim event reason must be null", 500)
        elif not isinstance(event["reason_code"], str) or not event["reason_code"]:
            raise _error("CLAIM_SCHEMA_INVALID", "terminal reason is required", 500)
        _parse_time(event["occurred_at"], "event occurred_at")
        if event_type in {"CLAIMED", "RENEWED"}:
            _parse_time(event["lease_expires_at"], "event lease_expires_at")
        elif event["lease_expires_at"] is not None:
            raise _error("CLAIM_STORE_CORRUPT", "terminal Claim event expiry invalid", 500)
        for name in (
            "idempotency_key_hash",
            "claim_record_integrity_hash",
            "event_integrity_hash",
        ):
            if not isinstance(event[name], str) or not _HASH_RE.fullmatch(event[name]):
                raise _error("CLAIM_STORE_CORRUPT", f"Claim event {name} invalid", 500)
        if not isinstance(event["fencing_token"], str) or not re.fullmatch(
            r"^vqf-[a-f0-9]{64}$", event["fencing_token"]
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim event fence invalid", 500)
        integrity = event["event_integrity_hash"]
        source = dict(event)
        source.pop("event_integrity_hash")
        if canonical_sha256(source) != integrity:
            raise _error("CLAIM_STORE_CORRUPT", "Claim event integrity mismatch", 500)
        _no_values(event, "Claim event")

    def _derive_state(self, claim: Mapping[str, Any], events: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not events or events[0].get("event_type") != "CLAIMED":
            raise _error("CLAIM_STORE_CORRUPT", "Claim must start with CLAIMED", 500)
        expires: datetime | None = None
        state = "ACTIVE"
        for index, event in enumerate(events, 1):
            if event.get("claim_generation") != claim["claim_generation"] or event.get("fencing_token") != claim["fencing_token"] or event.get("claim_record_integrity_hash") != claim["record_integrity_hash"]:
                raise _error("CLAIM_STORE_CORRUPT", "Claim event fence mismatch", 500)
            event_type = event["event_type"]
            if index > 1 and event_type == "CLAIMED":
                raise _error("CLAIM_STORE_CORRUPT", "CLAIMED may only be event one", 500)
            if index > 1 and state != "ACTIVE":
                raise _error("CLAIM_STORE_CORRUPT", "event follows terminal Claim", 500)
            occurred_at = _parse_time(event["occurred_at"], "event occurred_at")
            if index == 1:
                if occurred_at != _parse_time(claim["issued_at"], "issued_at"):
                    raise _error("CLAIM_STORE_CORRUPT", "CLAIMED time mismatch", 500)
            elif occurred_at < prior_occurred_at:
                raise _error("CLAIM_STORE_CORRUPT", "Claim event time moved backward", 500)
            if event_type in {"CLAIMED", "RENEWED"}:
                next_expires = _parse_time(event["lease_expires_at"], "lease_expires_at")
                if next_expires <= occurred_at:
                    raise _error("CLAIM_STORE_CORRUPT", "Claim event expiry is not future", 500)
                if event_type == "CLAIMED" and next_expires != _parse_time(
                    claim["expires_at"], "expires_at"
                ):
                    raise _error("CLAIM_STORE_CORRUPT", "CLAIMED expiry mismatch", 500)
                if event_type == "RENEWED" and expires is not None:
                    if occurred_at >= expires:
                        raise _error("CLAIM_STORE_CORRUPT", "renewal occurred after expiry", 500)
                    if next_expires <= expires:
                        raise _error("CLAIM_STORE_CORRUPT", "renewal expiry did not increase", 500)
                maximum = _parse_time(claim["issued_at"], "issued_at") + timedelta(
                    seconds=claim["lease_policy"]["maximum_total_seconds"]
                )
                if next_expires > maximum:
                    raise _error("CLAIM_STORE_CORRUPT", "Claim expiry exceeds policy", 500)
                expires = next_expires
                state = "ACTIVE"
            elif event_type in _TERMINAL_EVENTS:
                if event.get("lease_expires_at") is not None:
                    raise _error("CLAIM_STORE_CORRUPT", "terminal Claim event has expiry", 500)
                if expires is None:
                    raise _error("CLAIM_STORE_CORRUPT", "terminal Claim lacks prior expiry", 500)
                if event_type == "EXPIRED":
                    if occurred_at < expires:
                        raise _error("CLAIM_STORE_CORRUPT", "EXPIRED occurred before expiry", 500)
                elif occurred_at >= expires:
                    raise _error("CLAIM_STORE_CORRUPT", "owner/authority terminal event occurred after expiry", 500)
                expires = None
                state = event_type
            else:
                raise _error("CLAIM_STORE_CORRUPT", "unknown Claim event", 500)
            prior_occurred_at = occurred_at
        return {"state": state, "expires_at": expires, "event_sequence": len(events)}

    def _validate_claim(self, claim: Mapping[str, Any]) -> None:
        expected = {"schema_version", "claim_id", "claim_generation", "fencing_token", "queue_entry_identity", "candidate_identity", "lease_owner", "lease_policy", "claim_action_id", "claim_idempotency_key_hash", "issued_at", "expires_at", "validation", "state_at_creation", "record_integrity_hash", "safety"}
        _require_exact(claim, expected, "Claim")
        if claim["schema_version"] != CLAIM_SCHEMA_VERSION or not _CLAIM_ID_RE.fullmatch(str(claim["claim_id"])) or isinstance(claim["claim_generation"], bool) or not isinstance(claim["claim_generation"], int) or claim["claim_generation"] < 1:
            raise _error("CLAIM_STORE_CORRUPT", "Claim identity invalid", 500)
        if not isinstance(claim["fencing_token"], str) or not re.fullmatch(
            r"^vqf-[a-f0-9]{64}$", claim["fencing_token"]
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim fence invalid", 500)
        if not isinstance(claim["claim_action_id"], str) or not re.fullmatch(
            r"^qca-[a-f0-9]{32}$", claim["claim_action_id"]
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim action invalid", 500)
        if not isinstance(claim["claim_idempotency_key_hash"], str) or not _HASH_RE.fullmatch(
            claim["claim_idempotency_key_hash"]
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim idempotency hash invalid", 500)
        queue_identity = _require_exact(
            claim["queue_entry_identity"],
            {"queue_entry_id", "enqueue_sequence", "entry_integrity_hash"},
            "Claim Queue identity",
        )
        if (
            not isinstance(queue_identity["queue_entry_id"], str)
            or not _ENTRY_ID_RE.fullmatch(queue_identity["queue_entry_id"])
            or isinstance(queue_identity["enqueue_sequence"], bool)
            or not isinstance(queue_identity["enqueue_sequence"], int)
            or queue_identity["enqueue_sequence"] < 1
            or not isinstance(queue_identity["entry_integrity_hash"], str)
            or not _HASH_RE.fullmatch(queue_identity["entry_integrity_hash"])
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim Queue identity invalid", 500)
        candidate = _require_exact(
            claim["candidate_identity"],
            {"candidate_id", "candidate_revision", "canonical_content_hash"},
            "Claim Candidate identity",
        )
        if (
            not isinstance(candidate["candidate_id"], str)
            or not re.fullmatch(r"^vc-[a-f0-9]{32}$", candidate["candidate_id"])
            or isinstance(candidate["candidate_revision"], bool)
            or not isinstance(candidate["candidate_revision"], int)
            or candidate["candidate_revision"] < 1
            or not isinstance(candidate["canonical_content_hash"], str)
            or not _HASH_RE.fullmatch(candidate["canonical_content_hash"])
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim Candidate identity invalid", 500)
        owner = _require_exact(
            claim["lease_owner"],
            {"consumer_id", "authenticated_principal", "server_session_id"},
            "Claim owner",
        )
        if (
            not isinstance(owner["authenticated_principal"], str)
            or not owner["authenticated_principal"]
            or not isinstance(owner["server_session_id"], str)
            or not owner["server_session_id"]
            or not isinstance(owner["consumer_id"], str)
            or not _CONSUMER_ID_RE.fullmatch(owner["consumer_id"])
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim owner invalid", 500)
        policy = _require_exact(
            claim["lease_policy"],
            {"policy_version", "lease_seconds", "maximum_total_seconds"},
            "Claim policy",
        )
        if (
            policy["policy_version"] != LEASE_POLICY_VERSION
            or isinstance(policy["lease_seconds"], bool)
            or not isinstance(policy["lease_seconds"], int)
            or policy["lease_seconds"] < 1
            or isinstance(policy["maximum_total_seconds"], bool)
            or not isinstance(policy["maximum_total_seconds"], int)
            or policy["maximum_total_seconds"] < policy["lease_seconds"]
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "Claim policy invalid", 500)
        validation = _require_exact(
            claim["validation"],
            {"schema_version", "validation_status", "validated_at"},
            "Claim validation identity",
        )
        if (
            validation["schema_version"] != CONSUMPTION_VALIDATION_SCHEMA_VERSION
            or validation["validation_status"] != "VALID_CURRENT"
        ):
            raise _error("CLAIM_AUTHORITY_INVALID", "Claim validation identity invalid", 500)
        _parse_time(validation["validated_at"], "validated_at")
        if claim["safety"] != _SAFETY or claim["state_at_creation"] != "ACTIVE":
            raise _error("CLAIM_AUTHORITY_INVALID", "Claim safety invalid", 500)
        integrity = claim["record_integrity_hash"]
        source = dict(claim)
        source.pop("record_integrity_hash")
        if not isinstance(integrity, str) or canonical_sha256(source) != integrity:
            raise _error("CLAIM_STORE_CORRUPT", "Claim integrity mismatch", 500)
        issued_at = _parse_time(claim["issued_at"], "issued_at")
        expires_at = _parse_time(claim["expires_at"], "expires_at")
        if (
            expires_at <= issued_at
            or expires_at
            > issued_at + timedelta(seconds=policy["maximum_total_seconds"])
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim expiry invalid", 500)
        _no_values(claim, "Claim")

    def _next_generation(self, entry_id: str) -> int:
        generations = [item["claim"]["claim_generation"] for item in self._list_claims_locked() if item["claim"]["queue_entry_identity"]["queue_entry_id"] == entry_id]
        if generations and sorted(generations) != list(range(1, max(generations) + 1)):
            raise _error("CLAIM_STORE_CORRUPT", "Claim generation gap", 500)
        return max(generations, default=0) + 1

    def _has_active_claim_locked(self, entry_id: str) -> bool:
        self._preflight_pending_transactions_locked()
        now = self._observe_clock_locked()
        self._recover_locked()
        for item in self._list_claims_locked():
            claim = item["claim"]
            if claim["queue_entry_identity"]["queue_entry_id"] != entry_id:
                continue
            state = self._derive_state(claim, self._events_for(claim["claim_id"]))
            if state["state"] != "ACTIVE":
                continue
            if now >= state["expires_at"]:
                self._expire_claim_locked(claim, state, now)
                continue
            try:
                self._validate_candidate(claim["candidate_identity"])
            except CandidateAuthorityError as exc:
                self._authority_block_locked(claim, state, exc.code, now)
                raise _error(
                    "CLAIM_AUTHORITY_INVALID",
                    f"Candidate authority failed: {exc.code}",
                    409,
                ) from exc
            return True
        return False

    # ------------------------------------------------------------------
    # Clock, request, action, and validation helpers
    # ------------------------------------------------------------------

    def _observe_clock_locked(self) -> datetime:
        now = _utc(self._clock())
        path = self._clock_dir / "state.json"
        if path.exists():
            prior = _read(path)
            _require_exact(
                prior,
                {
                    "schema_version",
                    "store_id",
                    "observation_sequence",
                    "last_observed_utc",
                    "record_integrity_hash",
                },
                "Claim clock state",
            )
            integrity = prior.get("record_integrity_hash")
            source = dict(prior)
            source.pop("record_integrity_hash", None)
            if (
                prior["schema_version"] != CLAIM_CLOCK_SCHEMA_VERSION
                or not isinstance(prior["store_id"], str)
                or not re.fullmatch(r"^vqcs-[a-f0-9]{32}$", prior["store_id"])
                or isinstance(prior["observation_sequence"], bool)
                or not isinstance(prior["observation_sequence"], int)
                or prior["observation_sequence"] < 1
                or not isinstance(integrity, str)
                or canonical_sha256(source) != integrity
            ):
                raise _error("CLAIM_CLOCK_UNSAFE", "clock watermark integrity failed", 503)
            last = _parse_time(prior.get("last_observed_utc"), "last_observed_utc")
            if now < last:
                raise _error("CLAIM_CLOCK_UNSAFE", "clock moved behind persisted watermark", 503)
            sequence = prior["observation_sequence"] + 1
            store_id = prior["store_id"]
        else:
            sequence = 1
            store_id = f"vqcs-{uuid.uuid4().hex}"
        latest_durable = None
        for item in self._list_claims_locked():
            claim = item["claim"]
            observed = [_parse_time(claim["issued_at"], "issued_at")]
            observed.extend(
                _parse_time(event["occurred_at"], "occurred_at")
                for event in self._events_for(claim["claim_id"])
            )
            candidate_latest = max(observed)
            latest_durable = (
                candidate_latest
                if latest_durable is None
                else max(latest_durable, candidate_latest)
            )
        if latest_durable is not None and now < latest_durable:
            raise _error(
                "CLAIM_CLOCK_UNSAFE",
                "clock is behind latest durable Claim event",
                503,
            )
        state = {"schema_version": CLAIM_CLOCK_SCHEMA_VERSION, "store_id": store_id, "observation_sequence": sequence, "last_observed_utc": _iso(now)}
        state["record_integrity_hash"] = canonical_sha256(state)
        _write_atomic(path, state)
        return now

    def _decode(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(payload, Mapping) or set(payload) != {"action_id", "idempotency_key"}:
            raise _error("CLAIM_REQUEST_INVALID", "Claim mutation accepts only action_id and idempotency_key", 400)
        action_id, idem = payload["action_id"], payload["idempotency_key"]
        if not isinstance(action_id, str) or not _ACTION_ID_RE.fullmatch(action_id):
            raise _error("CLAIM_ACTION_REQUIRED", "server-bound Claim action required", 403)
        self._require_idempotency(idem)
        return {"action_id": action_id, "idempotency_key": idem}

    def _load_action(self, request: Mapping[str, str], purpose: str, principal: str, consumer: str, session: str) -> dict[str, Any]:
        path = self._actions / f"{request['action_id']}.json"
        if not path.exists():
            raise _error("CLAIM_ACTION_REQUIRED", "Claim action was not minted by this server", 403)
        action = _read(path)
        self._validate_action(action)
        if action.get("purpose") != purpose or action.get("authenticated_principal") != principal or action.get("consumer_id") != consumer or action.get("server_session_id") != session or action.get("idempotency_key") != request["idempotency_key"]:
            raise _error("CLAIM_IDEMPOTENCY_CONFLICT", "Claim action binding mismatch", 409)
        return action

    def _validate_action(self, action: Mapping[str, Any]) -> None:
        _require_exact(
            action,
            {
                "schema_version",
                "action_id",
                "purpose",
                "authenticated_principal",
                "consumer_id",
                "server_session_id",
                "target",
                "idempotency_key",
                "created_at",
            },
            "Claim action",
        )
        purpose = action["purpose"]
        prefix = {"CLAIM": "qca", "RENEW": "qra", "RELEASE": "qxa", "ABANDON": "qaa"}.get(purpose)
        if (
            action["schema_version"] != CLAIM_ACTION_SCHEMA_VERSION
            or prefix is None
            or not isinstance(action["action_id"], str)
            or not re.fullmatch(rf"^{prefix}-[a-f0-9]{{32}}$", action["action_id"])
        ):
            raise _error("CLAIM_STORE_CORRUPT", "Claim action identity invalid", 500)
        self._require_owner(
            action["authenticated_principal"],
            action["consumer_id"],
            action["server_session_id"],
        )
        self._require_idempotency(action["idempotency_key"])
        _parse_time(action["created_at"], "action created_at")
        if purpose == "CLAIM":
            if action["target"] is not None:
                raise _error("CLAIM_STORE_CORRUPT", "CLAIM action target must be null", 500)
        else:
            target = _require_exact(
                action["target"],
                {
                    "claim_id",
                    "queue_entry_identity",
                    "candidate_identity",
                    "claim_generation",
                    "fencing_token",
                    "expected_event_sequence",
                },
                "Claim action target",
            )
            self._require_claim_id(target["claim_id"])
            if (
                isinstance(target["claim_generation"], bool)
                or not isinstance(target["claim_generation"], int)
                or target["claim_generation"] < 1
                or isinstance(target["expected_event_sequence"], bool)
                or not isinstance(target["expected_event_sequence"], int)
                or target["expected_event_sequence"] < 1
            ):
                raise _error("CLAIM_STORE_CORRUPT", "Claim action target sequence invalid", 500)
        _no_values(action, "Claim action")

    def _operation_replay(self, idem: str, action: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self._operations / f"{idem}.json"
        if not path.exists():
            return None
        record = _read(path)
        allowed_shapes = (
            {"action_id", "result", "claim_id"},
            {"action_id", "result", "claim_id", "event_id"},
        )
        if set(record) not in allowed_shapes:
            raise _error("CLAIM_SCHEMA_INVALID", "operation replay keys invalid", 500)
        if record.get("action_id") != action["action_id"]:
            raise _error("CLAIM_IDEMPOTENCY_CONFLICT", "operation idempotency conflict", 409)
        claim_id = record.get("claim_id")
        if claim_id is not None and (
            not isinstance(claim_id, str) or not _CLAIM_ID_RE.fullmatch(claim_id)
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "operation replay Claim invalid", 500)
        if "event_id" in record and (
            not isinstance(record["event_id"], str)
            or not re.fullmatch(r"^vqce-[a-f0-9]{32}$", record["event_id"])
        ):
            raise _error("CLAIM_SCHEMA_INVALID", "operation replay event invalid", 500)
        _no_values(record, "Claim operation replay")
        return record

    def _action_target(self, claim: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        return {"claim_id": claim["claim_id"], "queue_entry_identity": deepcopy(claim["queue_entry_identity"]), "candidate_identity": deepcopy(claim["candidate_identity"]), "claim_generation": claim["claim_generation"], "fencing_token": claim["fencing_token"], "expected_event_sequence": state["event_sequence"]}

    def _assert_action_target(self, action: Mapping[str, Any], claim: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        if action.get("target") != self._action_target(claim, state):
            raise _error("CLAIM_STALE", "bound action fence is stale", 409)

    def _assert_owner(self, claim: Mapping[str, Any], principal: str, consumer: str, session: str) -> None:
        owner = claim["lease_owner"]
        if owner["authenticated_principal"] != principal or owner["consumer_id"] != consumer or owner["server_session_id"] != session:
            raise _error("CLAIM_OWNER_MISMATCH", "Claim owner/session mismatch", 403)

    def _validate_candidate(self, identity: Mapping[str, Any]) -> dict[str, Any]:
        request = {
            "candidate_id": identity["candidate_id"],
            "expected_candidate_revision": identity["candidate_revision"],
            "expected_content_hash": identity["canonical_content_hash"],
        }
        validation = self._validator.validate_request(request)
        if not isinstance(validation, Mapping):
            raise CandidateAuthorityError(
                "CANDIDATE_AUTHORITY_INVALID",
                "Gate 3B-2 returned non-object",
                500,
            )
        if not (
            validation.get("schema_version")
            == CONSUMPTION_VALIDATION_SCHEMA_VERSION
            and validation.get("validation_status") == "VALID_CURRENT"
            and validation.get("lifecycle_state") == "CURRENT"
            and validation.get("candidate_id") == identity["candidate_id"]
            and validation.get("candidate_revision") == identity["candidate_revision"]
            and validation.get("canonical_content_hash")
            == identity["canonical_content_hash"]
            and validation.get("safety")
            == {
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_queue": False,
                "auto_confirm": False,
                "auto_submit": False,
            }
        ):
            raise CandidateAuthorityError(
                "CANDIDATE_AUTHORITY_INVALID",
                "Gate 3B-2 envelope is not exact VALID_CURRENT",
                500,
            )
        return deepcopy(dict(validation))

    def _require_owner(self, principal: Any, consumer: Any, session: Any) -> None:
        if not isinstance(principal, str) or not principal or not isinstance(session, str) or not session or not isinstance(consumer, str) or not _CONSUMER_ID_RE.fullmatch(consumer):
            raise _error("CLAIM_REQUEST_INVALID", "owner identity invalid", 400)

    def _require_claim_id(self, value: Any) -> None:
        if not isinstance(value, str) or not _CLAIM_ID_RE.fullmatch(value):
            raise _error("CLAIM_REQUEST_INVALID", "claim_id invalid", 400)

    def _require_idempotency(self, value: Any) -> None:
        if not isinstance(value, str) or not _IDEMPOTENCY_RE.fullmatch(value):
            raise _error("CLAIM_REQUEST_INVALID", "idempotency_key invalid", 400)

    def _transition_result(self, claim: Mapping[str, Any], event: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {"claim_id": claim["claim_id"], "claim_generation": claim["claim_generation"], "event_type": event["event_type"], "event_sequence": event["event_sequence"], "lease_expires_at": event["lease_expires_at"], "replayed": replayed, "safety": deepcopy(_SAFETY)}
