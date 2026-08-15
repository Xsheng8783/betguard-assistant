"""Gate 3C-0 identity-only, dry-run logical Webfill prepare authority.

This module produces an immutable semantic plan.  It has no browser, DOM,
adapter, fill, submit, Queue completion, or external-provider integration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from betguard.vision.candidate_authority import canonical_json_bytes, canonical_sha256
from betguard.vision.validated_candidate_claims import (
    ValidatedCandidateClaimError,
    ValidatedCandidateClaimStore,
)
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from betguard.vision.webfill_target_profiles import (
    WebfillTargetProfileError,
    WebfillTargetProfileStore,
)


PREPARE_REQUEST_SCHEMA_VERSION = "vision-webfill-prepare-request-v1"
PREPARE_ARTIFACT_SCHEMA_VERSION = "vision-webfill-prepare-artifact-v1"
PREPARE_EVENT_SCHEMA_VERSION = "vision-webfill-prepare-lifecycle-event-v1"
PREPARE_RESULT_SCHEMA_VERSION = "vision-webfill-prepare-result-v1"
LOGICAL_PLAN_SCHEMA_VERSION = "vision-webfill-logical-plan-v1"
AUTHORITY_BINDING_SCHEMA_VERSION = "vision-webfill-prepare-authority-binding-v1"

_QUEUE_ID_RE = re.compile(r"^vcq-[a-f0-9]{32}$")
_CLAIM_ID_RE = re.compile(r"^vqc-[a-f0-9]{32}$")
_CONSUMER_ID_RE = re.compile(r"^vqcns-[a-f0-9]{32}$")
_FENCE_RE = re.compile(r"^vqf-[a-f0-9]{64}$")
_CANDIDATE_ID_RE = re.compile(r"^vc-[a-f0-9]{32}$")
_PROFILE_ID_RE = re.compile(r"^wtp-[a-z0-9][a-z0-9._-]{2,63}$")
_IDEMPOTENCY_RE = re.compile(r"^wpi-[a-f0-9]{32,128}$")
_PREPARE_ID_RE = re.compile(r"^vwp-[a-f0-9]{32}$")
_EVENT_ID_RE = re.compile(r"^vwpe-[a-f0-9]{32}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_HUMAN_BET_RE = re.compile(r"^H-[0-9]{3,}$")
_NUMBER_RE = re.compile(r"^[0-9]{2}$")
_MULTIPLIER_RE = re.compile(r"^[234](?:/[234]){0,2}X(?:0\.[0-9]+|[1-9][0-9]*(?:\.[0-9]+)?)$")
_TERMINAL_EVENTS = {"AUTHORITY_BLOCKED", "EXPIRED", "INVALIDATED", "SUPERSEDED", "REVOKED"}
_FORBIDDEN_KEYS = {
    "selector", "selectors", "url", "cookie", "cookies", "dom", "html",
    "browser", "submit", "submitted_payload", "api_key", "fencing_token",
    "claim_session_id", "idempotency_key",
}


class WebfillPrepareError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _fail(code: str, message: str, status: int = 422) -> WebfillPrepareError:
    return WebfillPrepareError(code, message, status)


def _assert_safe(value: Any, *, persisted: bool = False, label: str = "value") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise _fail("PREPARE_SCHEMA_INVALID", f"{label} contains floating point")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise _fail("PREPARE_SCHEMA_INVALID", f"{label} is not NFC")
        return
    if isinstance(value, list):
        for item in value:
            _assert_safe(item, persisted=persisted, label=label)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or unicodedata.normalize("NFC", key) != key:
                raise _fail("PREPARE_SCHEMA_INVALID", f"{label} key is invalid")
            if persisted and key.lower() in _FORBIDDEN_KEYS:
                raise _fail("PREPARE_STORE_CORRUPT", f"{label} persisted a forbidden secret/execution field", 500)
            _assert_safe(item, persisted=persisted, label=label)
        return
    raise _fail("PREPARE_SCHEMA_INVALID", f"{label} contains non-JSON value")


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    _assert_safe(value, persisted=True, label=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", f"immutable record exists: {path.name}", 409) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("PREPARE_STORE_CORRUPT", f"cannot read {path.name}", 500) from exc
    if not isinstance(value, dict):
        raise _fail("PREPARE_STORE_CORRUPT", f"{path.name} root is not object", 500)
    _assert_safe(value, persisted=True, label=path.name)
    return value


class WebfillPrepareStore:
    """Immutable dry-run Prepare artifacts bound to Queue, Claim and Candidate."""

    def __init__(
        self,
        base_dir: Path | str,
        queue_store: ValidatedCandidateQueueStore,
        claim_store: ValidatedCandidateClaimStore,
        profile_store: WebfillTargetProfileStore,
        *,
        administrative_principal_validator: Callable[[str], bool] | None = None,
    ) -> None:
        if not isinstance(queue_store, ValidatedCandidateQueueStore):
            raise TypeError("queue_store must be ValidatedCandidateQueueStore")
        if not isinstance(claim_store, ValidatedCandidateClaimStore):
            raise TypeError("claim_store must be ValidatedCandidateClaimStore")
        if not isinstance(profile_store, WebfillTargetProfileStore):
            raise TypeError("profile_store must be WebfillTargetProfileStore")
        self.base_dir = Path(base_dir)
        self._queue = queue_store
        self._claims = claim_store
        self._profiles = profile_store
        self._administrative_principal_validator = administrative_principal_validator
        self._artifacts = self.base_dir / "artifacts"
        self._commits = self.base_dir / "artifact-commits"
        self._events = self.base_dir / "lifecycle-events"
        self._idempotency = self.base_dir / "idempotency"
        self._tuples = self.base_dir / "authority-tuple-index"
        self._transactions = self.base_dir / "prepare-transactions"
        self._lifecycle_transactions = self.base_dir / "lifecycle-transactions"
        for directory in (self._artifacts, self._commits, self._events, self._idempotency, self._tuples, self._transactions, self._lifecycle_transactions):
            directory.mkdir(parents=True, exist_ok=True)

    def create_prepare(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        request = self._decode_request(payload)
        self._require_runtime_owner(authenticated_principal, consumer_id, server_session_id, request)
        with self._claims.prepare_coordination(
            preflight_callback=self._preflight_all_transactions,
            coordination_error_mapper=self._map_claim_coordination_error,
        ) as coordination:
            with self._profile_coordination() as profiles:
                claim = self._authoritative_locked(coordination, request, authenticated_principal, consumer_id, server_session_id)
                queue = self._exact_queue_locked(request, claim)
                validation = claim["validation"]
                self._validate_candidate_envelope(validation, request, queue)
                profile = self._profile_locked(
                    profiles,
                    request["target_profile_id"],
                    request["target_profile_version"],
                )
                plan = self._compile_plan(validation, profile)
                pending_path = self._transaction_path(request["idempotency_key"])
                if pending_path.exists():
                    transaction = _read(pending_path)
                    self._validate_transaction(transaction)
                    if transaction["request_hash"] != canonical_sha256(request):
                        raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "pending Prepare request differs", 409)
                    self._assert_artifact_request_binding(transaction["artifact"], request, claim, queue, profile)
                    self._assert_live_projection(transaction["artifact"], validation, plan)
                    self._resume_transaction(transaction)
                    return self._result(transaction["artifact"], "PREPARED", transaction["prepared_event"], replayed=True)
                pending_tuple = self._lookup_pending_tuple_replay(
                    request, claim, queue, profile, validation, plan
                )
                if pending_tuple is not None:
                    state = self._derive_state(pending_tuple)
                    return self._result(
                        pending_tuple,
                        state["state"],
                        state["lifecycle_event"],
                        replayed=True,
                    )
                replay = self._lookup_replay(request)
                if replay is not None:
                    self._assert_replay_fresh_locked(replay, request, coordination, profiles, authenticated_principal, consumer_id, server_session_id)
                    replay_state = self._derive_state(replay)
                    return self._result(replay, replay_state["state"], replay_state["lifecycle_event"], replayed=True)
                artifact = self._build_artifact(request, claim, queue, validation, profile, plan, coordination.observed_at)
                tuple_replay = self._lookup_tuple_replay(artifact, request)
                if tuple_replay is not None:
                    replay_state = self._derive_state(tuple_replay)
                    return self._result(tuple_replay, replay_state["state"], replay_state["lifecycle_event"], replayed=True)
                transaction = self._build_transaction(request, artifact)
                _write_immutable(self._transaction_path(request["idempotency_key"]), transaction)
                self._resume_transaction(transaction)
                return self._result(artifact, "PREPARED", transaction["prepared_event"], replayed=False)

    @contextmanager
    def mapping_coordination(
        self,
        *,
        preflight_callback: Callable[[], None],
    ) -> Iterator["_WebfillPrepareMappingCoordinator"]:
        """Hold Queue/Claim and logical-profile authority through mapping commit.

        Gate 3C-2 uses this narrow surface instead of re-entering the public
        ``get_prepare_state`` path while the Claim lock is already held.  Its
        callback runs before the Claim clock or upstream recovery can write.
        """

        if not callable(preflight_callback):
            raise TypeError("preflight_callback must be callable")

        def combined_preflight() -> None:
            preflight_callback()
            self._preflight_all_transactions()

        with self._claims.prepare_coordination(
            preflight_callback=combined_preflight,
            coordination_error_mapper=self._map_claim_coordination_error,
        ) as coordination:
            with self._profile_coordination() as profiles:
                yield _WebfillPrepareMappingCoordinator(self, coordination, profiles)

    def get_prepare_state(
        self,
        prepare_id: str,
        *,
        claim_generation: int,
        fencing_token: str,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        artifact = self._load_committed(prepare_id)
        self._preflight_all_transactions()
        current = self._derive_state(artifact)
        if current["state"] != "PREPARED":
            return self._result(artifact, current["state"], current["lifecycle_event"], replayed=False)
        try:
            with self._claims.prepare_coordination(
                preflight_callback=self._preflight_all_transactions,
                coordination_error_mapper=self._map_claim_coordination_error,
            ) as coordination:
                try:
                    with self._profile_coordination() as profiles:
                        self._recover_lifecycle_for(prepare_id)
                        queue_item = self._queue.get_entry(
                            artifact["authority"]["queue"]["queue_entry_id"]
                        )
                        if queue_item is None or queue_item["state"] != "QUEUED":
                            queue_state = (
                                queue_item["state"] if queue_item is not None else "NOT_FOUND"
                            )
                            return self._terminalize(
                                artifact,
                                "AUTHORITY_BLOCKED",
                                "queue_not_queued",
                                queue_state,
                                coordination.observed_at,
                            )
                        claim = coordination.get_authoritative_claim_locked(
                            claim_id=artifact["authority"]["claim"]["claim_id"],
                            claim_generation=claim_generation,
                            authenticated_principal=authenticated_principal,
                            consumer_id=consumer_id,
                            server_session_id=server_session_id,
                            fencing_token=fencing_token,
                        )
                        self._assert_artifact_authority(artifact, claim)
                        queue_request = {
                            "queue_entry_id": artifact["authority"]["queue"]["queue_entry_id"],
                            "expected_queue_revision": artifact["authority"]["queue"]["queue_revision"],
                            "expected_candidate_id": artifact["authority"]["candidate"]["candidate_id"],
                            "expected_canonical_content_hash": artifact["authority"]["candidate"]["canonical_content_hash"],
                        }
                        self._exact_queue_locked(queue_request, claim)
                        active = self._active_profile_locked(
                            profiles,
                            artifact["authority"]["target_profile"]["target_profile_id"],
                        )
                        if active["target_profile_version"] != artifact["authority"]["target_profile"]["target_profile_version"] or active["profile_integrity_hash"] != artifact["authority"]["target_profile"]["profile_integrity_hash"]:
                            return self._terminalize(artifact, "SUPERSEDED", "target_profile_advanced", "TARGET_PROFILE_SUPERSEDED", coordination.observed_at)
                        live_request = {
                            "expected_candidate_id": artifact["authority"]["candidate"]["candidate_id"],
                            "expected_canonical_content_hash": artifact["authority"]["candidate"]["canonical_content_hash"],
                        }
                        live_queue = {
                            "entry": queue_item["queue_entry"],
                            "revision": artifact["authority"]["queue"]["queue_revision"],
                        }
                        self._validate_candidate_envelope(
                            claim["validation"], live_request, live_queue
                        )
                        live_plan = self._compile_plan(claim["validation"], active)
                        live_plan_hash = canonical_sha256(live_plan)
                        if live_plan != artifact["logical_plan"] or live_plan_hash != artifact["deterministic_plan_hash"]:
                            raise _fail(
                                "PREPARE_HASH_MISMATCH",
                                "persisted logical plan differs from live validated Candidate projection",
                                409,
                            )
                        source = claim["validation"]["source"]
                        provenance = artifact["provenance"]
                        expected_provenance = {
                            "review_session_id": source["review_session_id"],
                            "human_answer_revision": source["human_answer_revision"],
                            "human_answer_hash": source["human_answer_hash"],
                            "source_image_hash": source["source_image_hash"],
                        }
                        if any(provenance[key] != value for key, value in expected_provenance.items()):
                            raise _fail(
                                "PREPARE_HASH_MISMATCH",
                                "Prepare provenance differs from live Candidate authority",
                                409,
                            )
                except ValidatedCandidateClaimError as exc:
                    lifecycle = coordination.get_claim_lifecycle_state_locked(
                        artifact["authority"]["claim"]["claim_id"]
                    )
                    if lifecycle["state"] == "ACTIVE":
                        raise _fail(
                            "PREPARE_CLAIM_INVALID",
                            f"Claim identity/fence failed: {exc.code}",
                            409,
                        ) from exc
                    upstream = lifecycle["last_event_type"]
                    mapping = {
                        "EXPIRED": "EXPIRED",
                        "RELEASED": "INVALIDATED",
                        "ABANDONED": "INVALIDATED",
                        "AUTHORITY_BLOCKED": "AUTHORITY_BLOCKED",
                    }
                    event_type = mapping.get(upstream, "AUTHORITY_BLOCKED")
                    return self._terminalize(
                        artifact,
                        event_type,
                        lifecycle["last_reason_code"] or exc.code,
                        upstream,
                        coordination.observed_at,
                    )
        except ValidatedCandidateClaimError as exc:
            code = "PREPARE_CLOCK_UNSAFE" if exc.code == "CLAIM_CLOCK_UNSAFE" else "PREPARE_STORE_CORRUPT"
            raise _fail(code, f"Claim coordination failed: {exc.code}", 409 if code == "PREPARE_CLOCK_UNSAFE" else 500) from exc
        return self._result(artifact, "PREPARED", current["lifecycle_event"], replayed=False)

    def get_lifecycle_events(self, prepare_id: str) -> list[dict[str, Any]]:
        self._require_prepare_id(prepare_id)
        self._preflight_all_transactions()
        if not (self._commits / f"{prepare_id}.json").exists():
            return []
        directory = self._events / prepare_id
        events = [_read(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []
        for index, event in enumerate(events, 1):
            self._validate_event(event)
            if event["event_sequence"] != index or event["prepare_id"] != prepare_id:
                raise _fail("PREPARE_STORE_CORRUPT", "Prepare lifecycle sequence invalid", 500)
        if events and events[0]["event_type"] != "PREPARED":
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare lifecycle lacks PREPARED", 500)
        if len(events) > 2 or (len(events) == 2 and events[1]["event_type"] not in _TERMINAL_EVENTS):
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare lifecycle transition invalid", 500)
        artifact = _read(self._artifacts / f"{prepare_id}.json")
        self._validate_artifact(artifact)
        for event in events:
            if event["authority_binding_hash"] != artifact["authority_binding_hash"] or event["deterministic_plan_hash"] != artifact["deterministic_plan_hash"] or event["prepare_record_integrity_hash"] != artifact["record_integrity_hash"]:
                raise _fail("PREPARE_STORE_CORRUPT", "Prepare event authority relation invalid", 500)
        return deepcopy(events)

    def revoke_prepare(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_principal: str,
    ) -> dict[str, Any]:
        """Append a trusted administrative REVOKED event; never mutate Artifact."""

        expected = {"schema_version", "prepare_id", "reason_code"}
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schema_version") != "vision-webfill-prepare-revoke-request-v1" or not isinstance(payload.get("prepare_id"), str) or not _PREPARE_ID_RE.fullmatch(payload["prepare_id"]) or not isinstance(payload.get("reason_code"), str) or not payload["reason_code"]:
            raise _fail("PREPARE_REQUEST_INVALID", "revocation request invalid", 400)
        _assert_safe(payload)
        try:
            trusted_admin = (
                isinstance(authenticated_principal, str)
                and bool(authenticated_principal)
                and callable(self._administrative_principal_validator)
                and bool(self._administrative_principal_validator(authenticated_principal))
            )
        except Exception:
            trusted_admin = False
        if not trusted_admin:
            raise _fail("PREPARE_CLAIM_INVALID", "trusted administrative principal required", 403)
        artifact = self._load_committed(payload["prepare_id"])
        with self._claims.prepare_coordination(
            preflight_callback=self._preflight_all_transactions,
            coordination_error_mapper=self._map_claim_coordination_error,
        ) as coordination:
            self._recover_lifecycle_for(artifact["prepare_id"])
            current = self._derive_state(artifact)
            if current["state"] != "PREPARED":
                if current["state"] == "REVOKED" and current["lifecycle_event"]["reason_code"] == payload["reason_code"] and current["lifecycle_event"]["actor"] == authenticated_principal:
                    return self._result(artifact, "REVOKED", current["lifecycle_event"], replayed=True)
                raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "Prepare is already terminal", 409)
            return self._terminalize(
                artifact,
                "REVOKED",
                payload["reason_code"],
                "TRUSTED_ADMINISTRATIVE_REVOCATION",
                coordination.observed_at,
                actor=authenticated_principal,
            )

    # ------------------------------ authority and compilation

    def _authoritative_locked(self, coordination: Any, request: Mapping[str, Any], principal: str, consumer: str, session: str) -> dict[str, Any]:
        try:
            return coordination.get_authoritative_claim_locked(
                claim_id=request["claim_id"], claim_generation=request["claim_generation"],
                authenticated_principal=principal, consumer_id=consumer,
                server_session_id=session, fencing_token=request["fencing_token"],
            )
        except ValidatedCandidateClaimError as exc:
            code = "PREPARE_CLAIM_EXPIRED" if exc.code == "CLAIM_EXPIRED" else "PREPARE_CLAIM_INVALID"
            if exc.code == "CLAIM_AUTHORITY_INVALID":
                lifecycle = coordination.get_claim_lifecycle_state_locked(
                    request["claim_id"]
                )
                reason = lifecycle.get("last_reason_code") or ""
                if reason == "CANDIDATE_QUEUE_STATE_INVALID":
                    code = "PREPARE_QUEUE_STALE"
                elif reason.startswith("CANDIDATE_"):
                    code = "PREPARE_CANDIDATE_INVALID"
            raise _fail(code, f"Claim authority failed: {exc.code}", 409) from exc

    def _exact_queue_locked(self, request: Mapping[str, Any], claim: Mapping[str, Any]) -> dict[str, Any]:
        queue_id = request["queue_entry_id"]
        item = self._queue.get_entry(queue_id)
        if item is None or item["state"] != "QUEUED":
            raise _fail("PREPARE_QUEUE_STALE", "Queue entry is not QUEUED", 409)
        entry = item["queue_entry"]
        events = self._queue.get_lifecycle_events(queue_id)
        revision = events[-1]["event_sequence"]
        if revision != request["expected_queue_revision"]:
            raise _fail("PREPARE_QUEUE_STALE", "Queue revision mismatch", 409)
        claim_queue = claim["queue_entry_identity"]
        identity = entry["candidate_identity"]
        if queue_id != claim_queue["queue_entry_id"] or entry["enqueue_sequence"] != claim_queue["enqueue_sequence"] or entry["entry_integrity_hash"] != claim_queue["entry_integrity_hash"]:
            raise _fail("PREPARE_CLAIM_INVALID", "Claim Queue identity mismatch", 409)
        if identity["candidate_id"] != request["expected_candidate_id"] or identity["canonical_content_hash"] != request["expected_canonical_content_hash"]:
            raise _fail("PREPARE_CANDIDATE_INVALID", "Queue Candidate identity mismatch", 409)
        return {"entry": entry, "revision": revision}

    @staticmethod
    def _validate_candidate_envelope(validation: Mapping[str, Any], request: Mapping[str, Any], queue: Mapping[str, Any]) -> None:
        safety = validation.get("safety")
        if validation.get("validation_status") != "VALID_CURRENT" or not isinstance(safety, Mapping) or safety != {"candidate_only": True, "approved_for_fill": False, "approved_for_queue": False, "auto_confirm": False, "auto_submit": False}:
            raise _fail("PREPARE_CANDIDATE_INVALID", "Gate 3B-2 envelope invalid", 409)
        identity = queue["entry"]["candidate_identity"]
        if validation.get("candidate_id") != request["expected_candidate_id"] or validation.get("canonical_content_hash") != request["expected_canonical_content_hash"] or validation.get("candidate_revision") != identity["candidate_revision"]:
            raise _fail("PREPARE_CANDIDATE_INVALID", "validated Candidate identity mismatch", 409)

    def _compile_plan(self, validation: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        if validation["game"] != profile["game"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "target profile game mismatch")
        active = validation["bets"]
        cancelled = validation["cancelled_audit"]
        if not isinstance(active, list) or not active:
            raise _fail("PREPARE_STRUCTURE_INVALID", "Candidate has no executable active bets")
        caps = profile["capabilities"]
        if len(active) > caps["maximum_active_bets"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "active bet count exceeds target")
        seen_ids: set[str] = set()
        operations = [self._operation(index, bet, validation["game"], caps, seen_ids) for index, bet in enumerate(active, 1)]
        refs = []
        for bet in cancelled:
            human_id = bet.get("human_bet_id")
            if not isinstance(human_id, str) or not _HUMAN_BET_RE.fullmatch(human_id) or human_id in seen_ids:
                raise _fail("PREPARE_STRUCTURE_INVALID", "cancelled Human Bet identity invalid")
            seen_ids.add(human_id)
            if bet.get("active") is not False or bet.get("cancelled") is not True or bet.get("executable") is not False:
                raise _fail("PREPARE_STRUCTURE_INVALID", "cancelled audit authority invalid")
            refs.append({"human_bet_id": human_id, "candidate_section": "cancelled_audit", "excluded_reason": "cancelled_non_executable"})
        candidate_identity = {"candidate_id": validation["candidate_id"], "candidate_revision": validation["candidate_revision"], "canonical_content_hash": validation["canonical_content_hash"]}
        profile_identity = {"target_profile_id": profile["target_profile_id"], "target_profile_version": profile["target_profile_version"], "profile_integrity_hash": profile["profile_integrity_hash"]}
        plan = {
            "schema_version": LOGICAL_PLAN_SCHEMA_VERSION,
            "game": validation["game"],
            "source_candidate_identity": candidate_identity,
            "target_profile_identity": profile_identity,
            "ordering_policy": {"bet_order": "candidate_active_bets", "group_order": "preserve", "number_order": "preserve", "multiplier_rule_order": "preserve"},
            "operations": operations,
            "cancelled_audit_refs": refs,
        }
        _assert_safe(plan)
        return plan

    def _operation(self, index: int, bet: Mapping[str, Any], game: str, caps: Mapping[str, Any], seen_ids: set[str]) -> dict[str, Any]:
        required = {"human_bet_id", "bet_type", "number_groups", "multiplier", "special_play", "continuation", "cancelled", "active", "executable", "value_authority"}
        if not isinstance(bet, Mapping) or not required <= set(bet):
            raise _fail("PREPARE_STRUCTURE_INVALID", "Candidate active bet shape invalid")
        human_id = bet["human_bet_id"]
        if not isinstance(human_id, str) or not _HUMAN_BET_RE.fullmatch(human_id) or human_id in seen_ids:
            raise _fail("PREPARE_STRUCTURE_INVALID", "active Human Bet identity invalid")
        seen_ids.add(human_id)
        bet_type = bet["bet_type"]
        groups = deepcopy(bet["number_groups"])
        if bet_type not in {"normal", "column"}:
            raise _fail("PREPARE_STRUCTURE_INVALID", "bet type invalid")
        if bet_type not in caps["supported_bet_types"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "bet type unsupported")
        if not isinstance(groups, list) or not groups or (bet_type == "normal" and len(groups) != 1) or (bet_type == "column" and len(groups) < 2):
            raise _fail("PREPARE_STRUCTURE_INVALID", "bet grouping unsupported or invalid")
        if len(groups) > caps["maximum_number_groups_per_bet"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "number group count exceeds target")
        upper = 39 if game == "539" else 49
        all_numbers: list[str] = []
        for group in groups:
            if not isinstance(group, list) or not group or len(group) != len(set(group)):
                raise _fail("PREPARE_STRUCTURE_INVALID", "number group invalid")
            if len(group) > caps["maximum_numbers_per_group"]:
                raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "numbers per group exceed target")
            for number in group:
                if not isinstance(number, str) or not _NUMBER_RE.fullmatch(number) or not 1 <= int(number) <= upper:
                    raise _fail("PREPARE_STRUCTURE_INVALID", "number literal invalid")
                all_numbers.append(number)
        if len(all_numbers) != len(set(all_numbers)):
            raise _fail("PREPARE_STRUCTURE_INVALID", "duplicate number across groups")
        multiplier = deepcopy(bet["multiplier"])
        if not isinstance(multiplier, Mapping) or set(multiplier) != {"ordered_rules", "scope", "resolved"} or multiplier["resolved"] is not True or not isinstance(multiplier["ordered_rules"], list) or not multiplier["ordered_rules"] or len(multiplier["ordered_rules"]) != len(set(multiplier["ordered_rules"])) or any(not isinstance(rule, str) or not _MULTIPLIER_RE.fullmatch(rule) for rule in multiplier["ordered_rules"]) or (multiplier["scope"] is not None and (not isinstance(multiplier["scope"], str) or not multiplier["scope"])):
            raise _fail("PREPARE_SCOPE_UNRESOLVED", "multiplier invalid or unresolved")
        if len(multiplier["ordered_rules"]) > 1 and not caps["supports_multiple_multiplier_rules"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "multiple multiplier rules unsupported")
        special = deepcopy(bet["special_play"])
        if not isinstance(special, Mapping) or set(special) != {"kind", "raw_text", "scope", "resolved"} or special["resolved"] is not True or not isinstance(special["kind"], str) or not special["kind"] or (special["raw_text"] is not None and not isinstance(special["raw_text"], str)):
            raise _fail("PREPARE_SCOPE_UNRESOLVED", "special play invalid or unresolved")
        if special["kind"] not in caps["supported_special_play_kinds"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "special play unsupported")
        if special["kind"] == "none":
            if special["raw_text"] is not None or special["scope"] is not None:
                raise _fail("PREPARE_SCOPE_UNRESOLVED", "none special play must have no scope")
        elif not isinstance(special["scope"], str) or not special["scope"]:
            raise _fail("PREPARE_SCOPE_UNRESOLVED", "special play scope unresolved")
        continuation = deepcopy(bet["continuation"])
        if not isinstance(continuation, Mapping) or continuation.get("resolved") is not True or not isinstance(continuation.get("present"), bool):
            raise _fail("PREPARE_CONTINUATION_UNSUPPORTED", "continuation invalid or unresolved")
        if continuation["present"] and not caps["supports_continuation"]:
            raise _fail("PREPARE_UNSUPPORTED_CAPABILITY", "continuation unsupported")
        continuation = {"present": continuation["present"], "resolved": True, "binding": "within_human_bet"}
        if bet["cancelled"] is not False or bet["active"] is not True or bet["executable"] is not True or bet["value_authority"] != "human_answer":
            raise _fail("PREPARE_STRUCTURE_INVALID", "active Candidate authority flags invalid")
        return {"operation_index": index, "operation_type": "LOGICAL_BET_ENTRY", "human_bet_id": human_id, "bet_type": bet_type, "number_groups": groups, "multiplier": multiplier, "special_play": special, "continuation": continuation, "cancelled": False, "active": True, "source_executable": True, "value_authority": "validated_candidate"}

    # ------------------------------ build, persistence, replay

    def _build_artifact(self, request: Mapping[str, Any], claim: Mapping[str, Any], queue: Mapping[str, Any], validation: Mapping[str, Any], profile: Mapping[str, Any], plan: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
        queue_entry = queue["entry"]
        queue_identity = {"queue_entry_id": queue_entry["queue_entry_id"], "enqueue_sequence": queue_entry["enqueue_sequence"], "queue_revision": queue["revision"], "entry_integrity_hash": queue_entry["entry_integrity_hash"]}
        candidate_identity = deepcopy(queue_entry["candidate_identity"])
        claim_binding = {"claim_id": claim["claim_id"], "claim_generation": claim["claim_generation"], "fencing_token_hash": hashlib.sha256(request["fencing_token"].encode()).hexdigest(), "consumer_id": claim["lease_owner"]["consumer_id"], "authenticated_principal": claim["lease_owner"]["authenticated_principal"], "claim_session_id_hash": hashlib.sha256(request["claim_session_id"].encode()).hexdigest(), "lease_expires_at_at_prepare": claim["lease_expires_at"]}
        profile_identity = {"target_profile_id": profile["target_profile_id"], "target_profile_version": profile["target_profile_version"], "profile_integrity_hash": profile["profile_integrity_hash"]}
        authority = {"queue": queue_identity, "candidate": candidate_identity, "claim": claim_binding, "target_profile": profile_identity, "idempotency_key_hash": hashlib.sha256(request["idempotency_key"].encode()).hexdigest()}
        plan_hash = canonical_sha256(plan)
        binding_projection = {"schema_version": AUTHORITY_BINDING_SCHEMA_VERSION, "queue": queue_identity, "candidate": candidate_identity, "claim": {key: value for key, value in claim_binding.items() if key != "lease_expires_at_at_prepare"}, "target_profile": profile_identity, "deterministic_plan_hash": plan_hash}
        source = validation["source"]
        artifact = {
            "schema_version": PREPARE_ARTIFACT_SCHEMA_VERSION,
            "prepare_id": f"vwp-{uuid.uuid4().hex}",
            "created_at": observed_at.astimezone(timezone.utc).isoformat(),
            "state_at_creation": "PREPARED",
            "authority": authority,
            "logical_plan": deepcopy(plan),
            "deterministic_plan_hash": plan_hash,
            "authority_binding_hash": canonical_sha256(binding_projection),
            "validation": {"status": "VALID_DRY_RUN", "validated_operation_count": len(plan["operations"]), "source_active_bet_count": len(validation["bets"]), "source_cancelled_bet_count": len(validation["cancelled_audit"]), "all_source_bets_accounted": True, "adapter_compilation_status": "NOT_COMPILED"},
            "executable": False,
            "blocking_reasons": [],
            "provenance": {"value_authority": "validated_candidate_human_answer", "candidate_validation_schema_version": validation["schema_version"], "candidate_validation_status": validation["validation_status"], "candidate_validated_at": observed_at.astimezone(timezone.utc).isoformat(), "review_session_id": source["review_session_id"], "human_answer_revision": source["human_answer_revision"], "human_answer_hash": source["human_answer_hash"], "source_image_hash": source["source_image_hash"], "machine_evidence_used_as_values": False},
            "record_integrity_hash": "",
            "safety": {"dry_run_only": True, "semantic_plan_only": True, "adapter_compiled": False, "dom_mapping_present": False, "browser_automation_authorized": False, "approved_for_fill": False, "approved_for_submit": False, "webfill_authorized": False, "submitted": False, "auto_submit": False, "queue_completed": False},
        }
        artifact["record_integrity_hash"] = canonical_sha256({key: value for key, value in artifact.items() if key != "record_integrity_hash"})
        self._validate_artifact(artifact)
        return artifact

    def _build_transaction(self, request: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
        tuple_projection = self._tuple_projection(artifact)
        return {"schema_version": "vision-webfill-prepare-transaction-v1", "request_hash": canonical_sha256(request), "idempotency_key_hash": hashlib.sha256(request["idempotency_key"].encode()).hexdigest(), "authority_tuple_hash": canonical_sha256(tuple_projection), "prepare_id": artifact["prepare_id"], "artifact": deepcopy(artifact), "prepared_event": self._event(artifact, 1, "PREPARED", artifact["created_at"], "gate3c0_prepare", None, None)}

    def _resume_transaction(self, transaction: Mapping[str, Any]) -> None:
        self._validate_transaction(transaction)
        artifact = transaction["artifact"]
        prepare_id = artifact["prepare_id"]
        paths = [(self._artifacts / f"{prepare_id}.json", artifact), (self._events / prepare_id / "000001.json", transaction["prepared_event"]), (self._idempotency / f"{transaction['idempotency_key_hash']}.json", {"prepare_id": prepare_id, "request_hash": transaction["request_hash"], "authority_tuple_hash": transaction["authority_tuple_hash"], "record_integrity_hash": artifact["record_integrity_hash"]}), (self._tuples / f"{transaction['authority_tuple_hash']}.json", {"prepare_id": prepare_id, "record_integrity_hash": artifact["record_integrity_hash"]})]
        for path, value in paths:
            if path.exists():
                if _read(path) != value:
                    raise _fail("PREPARE_STORE_CORRUPT", f"transaction record conflict: {path.name}", 500)
            else:
                _write_immutable(path, value)
        commit = {"prepare_id": prepare_id, "record_integrity_hash": artifact["record_integrity_hash"], "authority_binding_hash": artifact["authority_binding_hash"]}
        commit_path = self._commits / f"{prepare_id}.json"
        if commit_path.exists():
            if _read(commit_path) != commit:
                raise _fail("PREPARE_STORE_CORRUPT", "Prepare commit mismatch", 500)
        else:
            _write_immutable(commit_path, commit)

    def _lookup_replay(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        idem_hash = hashlib.sha256(request["idempotency_key"].encode()).hexdigest()
        path = self._idempotency / f"{idem_hash}.json"
        if not path.exists():
            return None
        record = _read(path)
        if record.get("request_hash") != canonical_sha256(request):
            raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "idempotency key was reused for another request", 409)
        return self._load_committed(record["prepare_id"])

    def _lookup_tuple_replay(self, artifact: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any] | None:
        tuple_hash = canonical_sha256(self._tuple_projection(artifact))
        path = self._tuples / f"{tuple_hash}.json"
        if not path.exists():
            return None
        record = _read(path)
        existing = self._load_committed(record["prepare_id"])
        if canonical_sha256(self._tuple_projection(existing)) != tuple_hash:
            raise _fail("PREPARE_STORE_CORRUPT", "authority tuple index mismatch", 500)
        if existing["deterministic_plan_hash"] != artifact["deterministic_plan_hash"]:
            raise _fail("PREPARE_NONDETERMINISTIC", "same authority tuple produced another logical plan", 409)
        idem_hash = hashlib.sha256(request["idempotency_key"].encode()).hexdigest()
        idem = {"prepare_id": existing["prepare_id"], "request_hash": canonical_sha256(request), "authority_tuple_hash": tuple_hash, "record_integrity_hash": existing["record_integrity_hash"]}
        idem_path = self._idempotency / f"{idem_hash}.json"
        if idem_path.exists():
            if _read(idem_path) != idem:
                raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "idempotency record conflict", 409)
        else:
            _write_immutable(idem_path, idem)
        return existing

    def _lookup_pending_tuple_replay(self, request: Mapping[str, Any], claim: Mapping[str, Any], queue: Mapping[str, Any], profile: Mapping[str, Any], validation: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any] | None:
        tuple_projection = self._live_tuple_projection(request, claim, queue, profile)
        tuple_hash = canonical_sha256(tuple_projection)
        for path in sorted(self._transactions.glob("*.json")):
            transaction = _read(path)
            self._validate_transaction(transaction)
            if transaction["authority_tuple_hash"] != tuple_hash:
                continue
            artifact = transaction["artifact"]
            self._assert_artifact_authority(artifact, claim)
            self._assert_live_projection(artifact, validation, plan)
            self._resume_transaction(transaction)
            idem_hash = hashlib.sha256(request["idempotency_key"].encode()).hexdigest()
            idem = {"prepare_id": artifact["prepare_id"], "request_hash": canonical_sha256(request), "authority_tuple_hash": tuple_hash, "record_integrity_hash": artifact["record_integrity_hash"]}
            idem_path = self._idempotency / f"{idem_hash}.json"
            if idem_path.exists():
                if _read(idem_path) != idem:
                    raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "idempotency record conflict", 409)
            else:
                _write_immutable(idem_path, idem)
            return artifact
        return None

    @staticmethod
    def _live_tuple_projection(request: Mapping[str, Any], claim: Mapping[str, Any], queue: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "queue": {"queue_entry_id": queue["entry"]["queue_entry_id"], "enqueue_sequence": queue["entry"]["enqueue_sequence"], "queue_revision": queue["revision"], "entry_integrity_hash": queue["entry"]["entry_integrity_hash"]},
            "candidate": deepcopy(queue["entry"]["candidate_identity"]),
            "claim": {"claim_id": claim["claim_id"], "claim_generation": claim["claim_generation"], "fencing_token_hash": hashlib.sha256(request["fencing_token"].encode()).hexdigest(), "consumer_id": claim["lease_owner"]["consumer_id"], "authenticated_principal": claim["lease_owner"]["authenticated_principal"], "claim_session_id_hash": hashlib.sha256(request["claim_session_id"].encode()).hexdigest()},
            "target_profile": {"target_profile_id": profile["target_profile_id"], "target_profile_version": profile["target_profile_version"], "profile_integrity_hash": profile["profile_integrity_hash"]},
        }

    def _preflight_all_transactions(self) -> None:
        for path in sorted(self._transactions.glob("*.json")):
            transaction = _read(path)
            self._validate_transaction(transaction)
            if path.stem != transaction["idempotency_key_hash"]:
                raise _fail("PREPARE_SCHEMA_INVALID", "Prepare transaction filename binding invalid", 500)
            self._preflight_transaction_outputs(transaction)
        lifecycle_by_prepare: dict[str, dict[str, Any]] = {}
        for path in sorted(self._lifecycle_transactions.glob("*.json")):
            transaction = _read(path)
            if set(transaction) != {"schema_version", "event"} or transaction["schema_version"] != "vision-webfill-prepare-lifecycle-transaction-v1":
                raise _fail("PREPARE_SCHEMA_INVALID", "lifecycle transaction invalid", 500)
            event = transaction["event"]
            self._validate_event(event)
            if path.stem != f"{event['prepare_id']}-{event['event_type']}":
                raise _fail("PREPARE_SCHEMA_INVALID", "lifecycle transaction filename binding invalid", 500)
            if event["event_type"] not in _TERMINAL_EVENTS or event["event_sequence"] != 2:
                raise _fail("PREPARE_SCHEMA_INVALID", "lifecycle transaction must contain one terminal event", 500)
            prepare_id = event["prepare_id"]
            if prepare_id in lifecycle_by_prepare and lifecycle_by_prepare[prepare_id] != event:
                raise _fail("PREPARE_STORE_CORRUPT", "multiple pending terminal transitions conflict", 500)
            lifecycle_by_prepare[prepare_id] = event
            artifact_path = self._artifacts / f"{prepare_id}.json"
            commit_path = self._commits / f"{prepare_id}.json"
            if not artifact_path.exists() or not commit_path.exists():
                raise _fail("PREPARE_STORE_CORRUPT", "lifecycle transaction has no committed Prepare", 500)
            artifact = _read(artifact_path)
            self._validate_artifact(artifact)
            commit = _read(commit_path)
            if commit != {"prepare_id": prepare_id, "record_integrity_hash": artifact["record_integrity_hash"], "authority_binding_hash": artifact["authority_binding_hash"]}:
                raise _fail("PREPARE_STORE_CORRUPT", "lifecycle transaction Prepare commit mismatch", 500)
            if event["authority_binding_hash"] != artifact["authority_binding_hash"] or event["deterministic_plan_hash"] != artifact["deterministic_plan_hash"] or event["prepare_record_integrity_hash"] != artifact["record_integrity_hash"]:
                raise _fail("PREPARE_STORE_CORRUPT", "lifecycle transaction authority relation invalid", 500)
            event_path = self._events / prepare_id / "000002.json"
            if event_path.exists() and _read(event_path) != event:
                raise _fail("PREPARE_STORE_CORRUPT", "persisted terminal event conflicts with transaction", 500)

    def _recover_all_transactions(self) -> None:
        for path in sorted(self._transactions.glob("*.json")):
            self._resume_transaction(_read(path))
        for path in sorted(self._lifecycle_transactions.glob("*.json")):
            event = _read(path)["event"]
            target = self._events / event["prepare_id"] / f"{event['event_sequence']:06d}.json"
            if target.exists():
                if _read(target) != event:
                    raise _fail("PREPARE_STORE_CORRUPT", "lifecycle recovery conflict", 500)
            else:
                _write_immutable(target, event)

    def _preflight_transaction_outputs(self, transaction: Mapping[str, Any]) -> None:
        artifact = transaction["artifact"]
        prepare_id = artifact["prepare_id"]
        expected_records = [
            (self._artifacts / f"{prepare_id}.json", artifact),
            (self._events / prepare_id / "000001.json", transaction["prepared_event"]),
            (
                self._idempotency / f"{transaction['idempotency_key_hash']}.json",
                {
                    "prepare_id": prepare_id,
                    "request_hash": transaction["request_hash"],
                    "authority_tuple_hash": transaction["authority_tuple_hash"],
                    "record_integrity_hash": artifact["record_integrity_hash"],
                },
            ),
            (
                self._tuples / f"{transaction['authority_tuple_hash']}.json",
                {
                    "prepare_id": prepare_id,
                    "record_integrity_hash": artifact["record_integrity_hash"],
                },
            ),
            (
                self._commits / f"{prepare_id}.json",
                {
                    "prepare_id": prepare_id,
                    "record_integrity_hash": artifact["record_integrity_hash"],
                    "authority_binding_hash": artifact["authority_binding_hash"],
                },
            ),
        ]
        for path, expected in expected_records:
            if path.exists() and _read(path) != expected:
                raise _fail(
                    "PREPARE_STORE_CORRUPT",
                    f"pending transaction conflicts with partial output {path.name}",
                    500,
                )

    def _recover_lifecycle_for(self, prepare_id: str) -> None:
        for path in sorted(self._lifecycle_transactions.glob(f"{prepare_id}-*.json")):
            event = _read(path)["event"]
            target = self._events / prepare_id / f"{event['event_sequence']:06d}.json"
            if target.exists():
                if _read(target) != event:
                    raise _fail("PREPARE_STORE_CORRUPT", "lifecycle recovery conflict", 500)
            else:
                _write_immutable(target, event)

    # ------------------------------ lifecycle and validation helpers

    def _derive_state(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        events = self.get_lifecycle_events(artifact["prepare_id"])
        if not events:
            raise _fail("PREPARE_STORE_CORRUPT", "committed Prepare lacks lifecycle", 500)
        return {"state": events[-1]["event_type"], "lifecycle_event": events[-1]}

    def _terminalize(self, artifact: Mapping[str, Any], event_type: str, reason: str, upstream: str, occurred_at: datetime, *, actor: str = "gate3c0_authority") -> dict[str, Any]:
        current = self._derive_state(artifact)
        if current["state"] != "PREPARED":
            return self._result(artifact, current["state"], current["lifecycle_event"], replayed=False)
        event = self._event(artifact, 2, event_type, occurred_at.astimezone(timezone.utc).isoformat(), actor, reason, upstream)
        transaction = {"schema_version": "vision-webfill-prepare-lifecycle-transaction-v1", "event": event}
        path = self._lifecycle_transactions / f"{artifact['prepare_id']}-{event_type}.json"
        if path.exists():
            stored = _read(path)
            if set(stored) != {"schema_version", "event"} or stored["schema_version"] != "vision-webfill-prepare-lifecycle-transaction-v1":
                raise _fail("PREPARE_STORE_CORRUPT", "lifecycle transaction invalid", 500)
            self._validate_event(stored["event"])
            if stored["event"]["prepare_id"] != artifact["prepare_id"] or stored["event"]["event_type"] != event_type:
                raise _fail("PREPARE_STORE_CORRUPT", "lifecycle transaction relation invalid", 500)
            if stored["event"]["reason_code"] != reason or stored["event"]["upstream_state"] != upstream or stored["event"]["actor"] != actor:
                raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "terminal lifecycle request differs", 409)
            event = stored["event"]
        else:
            _write_immutable(path, transaction)
        self._recover_lifecycle_for(artifact["prepare_id"])
        return self._result(artifact, event_type, event, replayed=False)

    @staticmethod
    def _result(artifact: Mapping[str, Any], state: str, event: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "schema_version": PREPARE_RESULT_SCHEMA_VERSION,
            "status": "VALID_PREPARED" if state == "PREPARED" else state,
            "state": state,
            "artifact": deepcopy(dict(artifact)),
            "lifecycle_event": deepcopy(dict(event)),
            "replayed": replayed,
            "safety": deepcopy(artifact["safety"]),
        }

    def _event(self, artifact: Mapping[str, Any], sequence: int, event_type: str, occurred_at: str, actor: str, reason: str | None, upstream: str | None) -> dict[str, Any]:
        event = {"schema_version": PREPARE_EVENT_SCHEMA_VERSION, "event_id": f"vwpe-{uuid.uuid4().hex}", "prepare_id": artifact["prepare_id"], "event_sequence": sequence, "event_type": event_type, "occurred_at": occurred_at, "actor": actor, "reason_code": reason, "upstream_state": upstream, "authority_binding_hash": artifact["authority_binding_hash"], "deterministic_plan_hash": artifact["deterministic_plan_hash"], "prepare_record_integrity_hash": artifact["record_integrity_hash"], "event_integrity_hash": ""}
        event["event_integrity_hash"] = canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"})
        self._validate_event(event)
        return event

    def _load_committed(self, prepare_id: str) -> dict[str, Any]:
        self._require_prepare_id(prepare_id)
        commit_path = self._commits / f"{prepare_id}.json"
        artifact_path = self._artifacts / f"{prepare_id}.json"
        if not commit_path.exists() or not artifact_path.exists():
            raise _fail("PREPARE_NOT_FOUND", "committed Prepare was not found", 404)
        artifact = _read(artifact_path)
        self._validate_artifact(artifact)
        commit = _read(commit_path)
        if commit != {"prepare_id": prepare_id, "record_integrity_hash": artifact["record_integrity_hash"], "authority_binding_hash": artifact["authority_binding_hash"]}:
            raise _fail("PREPARE_HASH_MISMATCH", "Prepare commit mismatch", 409)
        return artifact

    def _validate_artifact(self, artifact: Mapping[str, Any]) -> None:
        expected = {"schema_version", "prepare_id", "created_at", "state_at_creation", "authority", "logical_plan", "deterministic_plan_hash", "authority_binding_hash", "validation", "executable", "blocking_reasons", "provenance", "record_integrity_hash", "safety"}
        if not isinstance(artifact, Mapping) or set(artifact) != expected or artifact.get("schema_version") != PREPARE_ARTIFACT_SCHEMA_VERSION or artifact.get("state_at_creation") != "PREPARED" or artifact.get("executable") is not False or artifact.get("blocking_reasons") != []:
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare artifact root invalid", 500)
        self._require_prepare_id(artifact["prepare_id"])
        _assert_safe(artifact, persisted=False)
        self._require_timestamp(artifact["created_at"], "Prepare created_at")
        authority = artifact["authority"]
        if not isinstance(authority, Mapping) or set(authority) != {"queue", "candidate", "claim", "target_profile", "idempotency_key_hash"}:
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare authority shape invalid", 500)
        exact_shapes = {
            "queue": {"queue_entry_id", "enqueue_sequence", "queue_revision", "entry_integrity_hash"},
            "candidate": {"candidate_id", "candidate_revision", "canonical_content_hash"},
            "claim": {"claim_id", "claim_generation", "fencing_token_hash", "consumer_id", "authenticated_principal", "claim_session_id_hash", "lease_expires_at_at_prepare"},
            "target_profile": {"target_profile_id", "target_profile_version", "profile_integrity_hash"},
        }
        if any(not isinstance(authority[key], Mapping) or set(authority[key]) != keys for key, keys in exact_shapes.items()):
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare authority nested shape invalid", 500)
        if any(isinstance(authority["queue"][key], bool) or not isinstance(authority["queue"][key], int) or authority["queue"][key] < 1 for key in ("enqueue_sequence", "queue_revision")) or isinstance(authority["candidate"]["candidate_revision"], bool) or not isinstance(authority["candidate"]["candidate_revision"], int) or authority["candidate"]["candidate_revision"] < 1 or isinstance(authority["claim"]["claim_generation"], bool) or not isinstance(authority["claim"]["claim_generation"], int) or authority["claim"]["claim_generation"] < 1:
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare authority revision invalid", 500)
        if not _QUEUE_ID_RE.fullmatch(authority["queue"].get("queue_entry_id", "")) or not _CANDIDATE_ID_RE.fullmatch(authority["candidate"].get("candidate_id", "")) or not _CLAIM_ID_RE.fullmatch(authority["claim"].get("claim_id", "")) or not _CONSUMER_ID_RE.fullmatch(authority["claim"].get("consumer_id", "")) or not isinstance(authority["claim"].get("authenticated_principal"), str) or not authority["claim"]["authenticated_principal"] or not _PROFILE_ID_RE.fullmatch(authority["target_profile"].get("target_profile_id", "")) or isinstance(authority["target_profile"].get("target_profile_version"), bool) or not isinstance(authority["target_profile"].get("target_profile_version"), int) or authority["target_profile"]["target_profile_version"] < 1:
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare authority identity invalid", 500)
        self._require_timestamp(authority["claim"]["lease_expires_at_at_prepare"], "Claim expiry")
        hashes = [authority["queue"]["entry_integrity_hash"], authority["candidate"]["canonical_content_hash"], authority["claim"]["fencing_token_hash"], authority["claim"]["claim_session_id_hash"], authority["target_profile"]["profile_integrity_hash"], authority["idempotency_key_hash"]]
        if any(not isinstance(value, str) or not _HASH_RE.fullmatch(value) for value in hashes):
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare authority hash invalid", 500)
        plan = artifact["logical_plan"]
        if not isinstance(plan, Mapping) or set(plan) != {"schema_version", "game", "source_candidate_identity", "target_profile_identity", "ordering_policy", "operations", "cancelled_audit_refs"} or plan["schema_version"] != LOGICAL_PLAN_SCHEMA_VERSION or not isinstance(plan["game"], str) or not plan["game"] or plan["source_candidate_identity"] != authority["candidate"] or plan["target_profile_identity"] != authority["target_profile"]:
            raise _fail("PREPARE_SCHEMA_INVALID", "logical plan authority invalid", 500)
        if plan["ordering_policy"] != {"bet_order": "candidate_active_bets", "group_order": "preserve", "number_order": "preserve", "multiplier_rule_order": "preserve"} or not isinstance(plan["operations"], list) or not plan["operations"]:
            raise _fail("PREPARE_SCHEMA_INVALID", "logical plan ordering/operations invalid", 500)
        operation_keys = {"operation_index", "operation_type", "human_bet_id", "bet_type", "number_groups", "multiplier", "special_play", "continuation", "cancelled", "active", "source_executable", "value_authority"}
        seen_human_ids: set[str] = set()
        for index, operation in enumerate(plan["operations"], 1):
            if not isinstance(operation, Mapping) or set(operation) != operation_keys or isinstance(operation["operation_index"], bool) or not isinstance(operation["operation_index"], int) or operation["operation_index"] != index or operation["operation_type"] != "LOGICAL_BET_ENTRY" or operation["cancelled"] is not False or operation["active"] is not True or operation["source_executable"] is not True or operation["value_authority"] != "validated_candidate":
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation invalid", 500)
            if not isinstance(operation["human_bet_id"], str) or not _HUMAN_BET_RE.fullmatch(operation["human_bet_id"]) or operation["human_bet_id"] in seen_human_ids or operation["bet_type"] not in {"normal", "column"}:
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation identity invalid", 500)
            seen_human_ids.add(operation["human_bet_id"])
            groups = operation["number_groups"]
            if not isinstance(groups, list) or not groups or (operation["bet_type"] == "normal" and len(groups) != 1) or (operation["bet_type"] == "column" and len(groups) < 2):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation groups invalid", 500)
            flat: list[str] = []
            for group in groups:
                if not isinstance(group, list) or not group or any(not isinstance(number, str) or not _NUMBER_RE.fullmatch(number) for number in group) or len(group) != len(set(group)):
                    raise _fail("PREPARE_SCHEMA_INVALID", "logical operation number group invalid", 500)
                flat.extend(group)
            if len(flat) != len(set(flat)):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation duplicate number", 500)
            multiplier = operation["multiplier"]
            if not isinstance(multiplier, Mapping) or set(multiplier) != {"ordered_rules", "scope", "resolved"} or multiplier["resolved"] is not True or not isinstance(multiplier["ordered_rules"], list) or not multiplier["ordered_rules"] or len(multiplier["ordered_rules"]) != len(set(multiplier["ordered_rules"])) or any(not isinstance(rule, str) or not _MULTIPLIER_RE.fullmatch(rule) for rule in multiplier["ordered_rules"]) or (multiplier["scope"] is not None and (not isinstance(multiplier["scope"], str) or not multiplier["scope"])):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation multiplier invalid", 500)
            special = operation["special_play"]
            if not isinstance(special, Mapping) or set(special) != {"kind", "raw_text", "scope", "resolved"} or not isinstance(special["kind"], str) or not special["kind"] or special["resolved"] is not True or (special["raw_text"] is not None and not isinstance(special["raw_text"], str)):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation special play invalid", 500)
            if (special["kind"] == "none" and (special["raw_text"] is not None or special["scope"] is not None)) or (special["kind"] != "none" and (not isinstance(special["scope"], str) or not special["scope"])):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation special scope invalid", 500)
            continuation = operation["continuation"]
            if not isinstance(continuation, Mapping) or continuation != {"present": continuation.get("present"), "resolved": True, "binding": "within_human_bet"} or not isinstance(continuation.get("present"), bool):
                raise _fail("PREPARE_SCHEMA_INVALID", "logical operation continuation invalid", 500)
        ref_keys = {"human_bet_id", "candidate_section", "excluded_reason"}
        if not isinstance(plan["cancelled_audit_refs"], list) or any(not isinstance(ref, Mapping) or set(ref) != ref_keys or not isinstance(ref["human_bet_id"], str) or not _HUMAN_BET_RE.fullmatch(ref["human_bet_id"]) or ref["human_bet_id"] in seen_human_ids or ref["candidate_section"] != "cancelled_audit" or ref["excluded_reason"] != "cancelled_non_executable" for ref in plan["cancelled_audit_refs"]):
            raise _fail("PREPARE_SCHEMA_INVALID", "cancelled audit reference invalid", 500)
        cancelled_ids = [ref["human_bet_id"] for ref in plan["cancelled_audit_refs"]]
        if len(cancelled_ids) != len(set(cancelled_ids)):
            raise _fail("PREPARE_SCHEMA_INVALID", "duplicate cancelled audit reference", 500)
        if canonical_sha256(artifact["logical_plan"]) != artifact["deterministic_plan_hash"]:
            raise _fail("PREPARE_HASH_MISMATCH", "logical plan hash mismatch", 409)
        binding_projection = {"schema_version": AUTHORITY_BINDING_SCHEMA_VERSION, "queue": authority["queue"], "candidate": authority["candidate"], "claim": {key: value for key, value in authority["claim"].items() if key != "lease_expires_at_at_prepare"}, "target_profile": authority["target_profile"], "deterministic_plan_hash": artifact["deterministic_plan_hash"]}
        if canonical_sha256(binding_projection) != artifact["authority_binding_hash"]:
            raise _fail("PREPARE_HASH_MISMATCH", "authority binding hash mismatch", 409)
        validation = artifact["validation"]
        validation_keys = {"status", "validated_operation_count", "source_active_bet_count", "source_cancelled_bet_count", "all_source_bets_accounted", "adapter_compilation_status"}
        if not isinstance(validation, Mapping) or set(validation) != validation_keys or validation["status"] != "VALID_DRY_RUN" or validation["adapter_compilation_status"] != "NOT_COMPILED" or validation["all_source_bets_accounted"] is not True or any(isinstance(validation[key], bool) or not isinstance(validation[key], int) or validation[key] < (1 if key != "source_cancelled_bet_count" else 0) for key in ("validated_operation_count", "source_active_bet_count", "source_cancelled_bet_count")) or validation["validated_operation_count"] != len(plan["operations"]) or validation["source_active_bet_count"] != len(plan["operations"]) or validation["source_cancelled_bet_count"] != len(plan["cancelled_audit_refs"]):
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare validation summary invalid", 500)
        provenance = artifact["provenance"]
        provenance_keys = {"value_authority", "candidate_validation_schema_version", "candidate_validation_status", "candidate_validated_at", "review_session_id", "human_answer_revision", "human_answer_hash", "source_image_hash", "machine_evidence_used_as_values"}
        if not isinstance(provenance, Mapping) or set(provenance) != provenance_keys or provenance["value_authority"] != "validated_candidate_human_answer" or provenance["candidate_validation_schema_version"] != "vision-candidate-consumption-validation-v1" or provenance["candidate_validation_status"] != "VALID_CURRENT" or provenance["machine_evidence_used_as_values"] is not False or not isinstance(provenance["review_session_id"], str) or not provenance["review_session_id"] or isinstance(provenance["human_answer_revision"], bool) or not isinstance(provenance["human_answer_revision"], int) or provenance["human_answer_revision"] < 1 or any(not isinstance(provenance[key], str) or not _HASH_RE.fullmatch(provenance[key]) for key in ("human_answer_hash", "source_image_hash")):
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare provenance invalid", 500)
        self._require_timestamp(provenance["candidate_validated_at"], "Candidate validated_at")
        if canonical_sha256({key: value for key, value in artifact.items() if key != "record_integrity_hash"}) != artifact["record_integrity_hash"]:
            raise _fail("PREPARE_HASH_MISMATCH", "Prepare record hash mismatch", 409)
        if artifact["safety"] != {"dry_run_only": True, "semantic_plan_only": True, "adapter_compiled": False, "dom_mapping_present": False, "browser_automation_authorized": False, "approved_for_fill": False, "approved_for_submit": False, "webfill_authorized": False, "submitted": False, "auto_submit": False, "queue_completed": False}:
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare safety contract invalid", 500)

    def _validate_event(self, event: Mapping[str, Any]) -> None:
        expected = {"schema_version", "event_id", "prepare_id", "event_sequence", "event_type", "occurred_at", "actor", "reason_code", "upstream_state", "authority_binding_hash", "deterministic_plan_hash", "prepare_record_integrity_hash", "event_integrity_hash"}
        if not isinstance(event, Mapping) or set(event) != expected or event.get("schema_version") != PREPARE_EVENT_SCHEMA_VERSION:
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare event shape invalid", 500)
        source = dict(event); integrity = source.pop("event_integrity_hash")
        if not isinstance(event.get("event_id"), str) or not _EVENT_ID_RE.fullmatch(event["event_id"]) or not isinstance(event.get("prepare_id"), str) or not _PREPARE_ID_RE.fullmatch(event["prepare_id"]) or isinstance(event.get("event_sequence"), bool) or not isinstance(event.get("event_sequence"), int) or event["event_sequence"] < 1 or not isinstance(event.get("actor"), str) or not event["actor"] or any(not isinstance(event.get(key), str) or not _HASH_RE.fullmatch(event[key]) for key in ("authority_binding_hash", "deterministic_plan_hash", "prepare_record_integrity_hash", "event_integrity_hash")):
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare event identity invalid", 500)
        self._require_timestamp(event["occurred_at"], "Prepare event occurred_at", store=True)
        if canonical_sha256(source) != integrity:
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare event hash invalid", 500)
        if event["event_type"] == "PREPARED":
            if event["event_sequence"] != 1 or event["reason_code"] is not None or event["upstream_state"] is not None:
                raise _fail("PREPARE_STORE_CORRUPT", "PREPARED event invalid", 500)
        elif event["event_type"] not in _TERMINAL_EVENTS or event["event_sequence"] != 2 or not isinstance(event["reason_code"], str) or not event["reason_code"] or not isinstance(event["upstream_state"], str) or not event["upstream_state"]:
            raise _fail("PREPARE_STORE_CORRUPT", "terminal Prepare event invalid", 500)

    def _validate_transaction(self, transaction: Mapping[str, Any]) -> None:
        expected = {"schema_version", "request_hash", "idempotency_key_hash", "authority_tuple_hash", "prepare_id", "artifact", "prepared_event"}
        if not isinstance(transaction, Mapping) or set(transaction) != expected or transaction.get("schema_version") != "vision-webfill-prepare-transaction-v1":
            raise _fail("PREPARE_SCHEMA_INVALID", "Prepare transaction invalid", 500)
        self._validate_artifact(transaction["artifact"])
        self._validate_event(transaction["prepared_event"])
        if any(not isinstance(transaction[key], str) or not _HASH_RE.fullmatch(transaction[key]) for key in ("request_hash", "idempotency_key_hash", "authority_tuple_hash")) or transaction["idempotency_key_hash"] != transaction["artifact"]["authority"]["idempotency_key_hash"] or transaction["prepare_id"] != transaction["artifact"]["prepare_id"] or transaction["prepared_event"]["prepare_id"] != transaction["prepare_id"] or transaction["prepared_event"]["event_type"] != "PREPARED" or transaction["prepared_event"]["event_sequence"] != 1 or transaction["prepared_event"]["authority_binding_hash"] != transaction["artifact"]["authority_binding_hash"] or transaction["prepared_event"]["deterministic_plan_hash"] != transaction["artifact"]["deterministic_plan_hash"] or transaction["prepared_event"]["prepare_record_integrity_hash"] != transaction["artifact"]["record_integrity_hash"] or canonical_sha256(self._tuple_projection(transaction["artifact"])) != transaction["authority_tuple_hash"]:
            raise _fail("PREPARE_STORE_CORRUPT", "Prepare transaction relation invalid", 500)

    @staticmethod
    def _require_timestamp(value: Any, label: str, *, store: bool = False) -> None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise _fail("PREPARE_STORE_CORRUPT" if store else "PREPARE_SCHEMA_INVALID", f"{label} invalid", 500) from exc
        if not isinstance(value, str) or "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _fail("PREPARE_STORE_CORRUPT" if store else "PREPARE_SCHEMA_INVALID", f"{label} must be timezone-aware RFC3339", 500)

    @staticmethod
    def _tuple_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
        authority = artifact["authority"]
        return {"queue": authority["queue"], "candidate": authority["candidate"], "claim": {key: value for key, value in authority["claim"].items() if key != "lease_expires_at_at_prepare"}, "target_profile": authority["target_profile"]}

    def _assert_replay_fresh_locked(self, artifact: Mapping[str, Any], request: Mapping[str, Any], coordination: Any, profiles: Any, principal: str, consumer: str, session: str) -> None:
        claim = self._authoritative_locked(coordination, request, principal, consumer, session)
        self._assert_artifact_authority(artifact, claim)
        queue = self._exact_queue_locked(request, claim)
        self._validate_candidate_envelope(claim["validation"], request, queue)
        profile = self._profile_locked(
            profiles,
            request["target_profile_id"],
            request["target_profile_version"],
        )
        if profile["profile_integrity_hash"] != artifact["authority"]["target_profile"]["profile_integrity_hash"]:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "replay profile identity changed", 409)
        self._assert_live_projection(
            artifact,
            claim["validation"],
            self._compile_plan(claim["validation"], profile),
        )

    @staticmethod
    def _assert_live_projection(artifact: Mapping[str, Any], validation: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
        if artifact["logical_plan"] != plan or artifact["deterministic_plan_hash"] != canonical_sha256(plan):
            raise _fail("PREPARE_NONDETERMINISTIC", "persisted plan differs from live validated Candidate projection", 409)
        source = validation["source"]
        provenance = artifact["provenance"]
        expected = {"review_session_id": source["review_session_id"], "human_answer_revision": source["human_answer_revision"], "human_answer_hash": source["human_answer_hash"], "source_image_hash": source["source_image_hash"]}
        if any(provenance.get(key) != value for key, value in expected.items()):
            raise _fail("PREPARE_NONDETERMINISTIC", "persisted provenance differs from live Candidate authority", 409)

    @staticmethod
    def _assert_artifact_authority(artifact: Mapping[str, Any], claim: Mapping[str, Any]) -> None:
        bound = artifact["authority"]["claim"]
        if claim["claim_id"] != bound["claim_id"] or claim["claim_generation"] != bound["claim_generation"] or hashlib.sha256(claim["fencing_token"].encode()).hexdigest() != bound["fencing_token_hash"]:
            raise _fail("PREPARE_CLAIM_INVALID", "Prepare Claim binding mismatch", 409)

    def _assert_artifact_request_binding(self, artifact: Mapping[str, Any], request: Mapping[str, Any], claim: Mapping[str, Any], queue: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
        self._assert_artifact_authority(artifact, claim)
        authority = artifact["authority"]
        if authority["queue"] != {"queue_entry_id": queue["entry"]["queue_entry_id"], "enqueue_sequence": queue["entry"]["enqueue_sequence"], "queue_revision": queue["revision"], "entry_integrity_hash": queue["entry"]["entry_integrity_hash"]} or authority["candidate"] != queue["entry"]["candidate_identity"] or authority["target_profile"] != {"target_profile_id": profile["target_profile_id"], "target_profile_version": profile["target_profile_version"], "profile_integrity_hash": profile["profile_integrity_hash"]} or authority["idempotency_key_hash"] != hashlib.sha256(request["idempotency_key"].encode()).hexdigest():
            raise _fail("PREPARE_IDEMPOTENCY_CONFLICT", "pending Prepare authority differs", 409)

    # ------------------------------ public request validation

    def _decode_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        expected = {"schema_version", "queue_entry_id", "claim_id", "claim_session_id", "claim_generation", "fencing_token", "expected_candidate_id", "expected_canonical_content_hash", "expected_queue_revision", "target_profile_id", "target_profile_version", "idempotency_key"}
        legacy = {"manual_candidate_id", "approved_fill_queue", "queue_path", "webfill", "fill_plan"}
        if isinstance(payload, Mapping) and set(payload) & legacy:
            raise _fail(
                "LEGACY_WEBFILL_INTEROP_FORBIDDEN",
                "legacy fill/queue inputs are not interoperable with Gate 3C Prepare",
                400,
            )
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise _fail("PREPARE_REQUEST_INVALID", "Prepare request must contain identity-only exact fields", 400)
        value = deepcopy(dict(payload)); _assert_safe(value)
        checks = [value["schema_version"] == PREPARE_REQUEST_SCHEMA_VERSION, isinstance(value["queue_entry_id"], str) and bool(_QUEUE_ID_RE.fullmatch(value["queue_entry_id"])), isinstance(value["claim_id"], str) and bool(_CLAIM_ID_RE.fullmatch(value["claim_id"])), isinstance(value["claim_session_id"], str) and 1 <= len(value["claim_session_id"]) <= 256, isinstance(value["claim_generation"], int) and not isinstance(value["claim_generation"], bool) and value["claim_generation"] >= 1, isinstance(value["fencing_token"], str) and bool(_FENCE_RE.fullmatch(value["fencing_token"])), isinstance(value["expected_candidate_id"], str) and bool(_CANDIDATE_ID_RE.fullmatch(value["expected_candidate_id"])), isinstance(value["expected_canonical_content_hash"], str) and bool(_HASH_RE.fullmatch(value["expected_canonical_content_hash"])), isinstance(value["expected_queue_revision"], int) and not isinstance(value["expected_queue_revision"], bool) and value["expected_queue_revision"] >= 1, isinstance(value["target_profile_id"], str) and bool(_PROFILE_ID_RE.fullmatch(value["target_profile_id"])), isinstance(value["target_profile_version"], int) and not isinstance(value["target_profile_version"], bool) and value["target_profile_version"] >= 1, isinstance(value["idempotency_key"], str) and bool(_IDEMPOTENCY_RE.fullmatch(value["idempotency_key"]))]
        if not all(checks):
            raise _fail("PREPARE_REQUEST_INVALID", "Prepare identity field invalid", 400)
        return value

    @staticmethod
    def _require_runtime_owner(principal: Any, consumer: Any, session: Any, request: Mapping[str, Any]) -> None:
        if any(not isinstance(item, str) or not item for item in (principal, consumer, session)) or session != request["claim_session_id"]:
            raise _fail("PREPARE_CLAIM_INVALID", "runtime Claim owner/session mismatch", 409)

    @staticmethod
    def _profile_locked(profiles: Any, profile_id: str, version: int) -> dict[str, Any]:
        try:
            return profiles.get_profile_locked(
                profile_id, version, require_active=True
            )
        except WebfillTargetProfileError as exc:
            raise _fail(exc.code, exc.message, exc.http_status) from exc

    @contextmanager
    def _profile_coordination(self) -> Iterator[Any]:
        try:
            with self._profiles.coordination() as profiles:
                yield profiles
        except WebfillTargetProfileError as exc:
            raise _fail(exc.code, exc.message, exc.http_status) from exc

    @staticmethod
    def _active_profile_locked(profiles: Any, profile_id: str) -> dict[str, Any]:
        try:
            return profiles.get_active_profile_locked(profile_id)
        except WebfillTargetProfileError as exc:
            raise _fail(exc.code, exc.message, exc.http_status) from exc

    @staticmethod
    def _map_claim_coordination_error(exc: ValidatedCandidateClaimError) -> Exception:
        if exc.code == "CLAIM_CLOCK_UNSAFE":
            return _fail("PREPARE_CLOCK_UNSAFE", exc.message, 409)
        return _fail("PREPARE_STORE_CORRUPT", f"Claim coordination failed: {exc.code}", 500)

    @staticmethod
    def _require_prepare_id(prepare_id: Any) -> None:
        if not isinstance(prepare_id, str) or not _PREPARE_ID_RE.fullmatch(prepare_id):
            raise _fail("PREPARE_REQUEST_INVALID", "prepare_id invalid", 400)

    def _transaction_path(self, idempotency: str) -> Path:
        return self._transactions / f"{hashlib.sha256(idempotency.encode()).hexdigest()}.json"


class _WebfillPrepareMappingCoordinator:
    """Narrow, non-reentrant Gate 3C mapping authority integration surface."""

    def __init__(self, store: WebfillPrepareStore, claims: Any, profiles: Any) -> None:
        self._store = store
        self._claims = claims
        self._profiles = profiles

    @property
    def observed_at(self) -> datetime:
        return self._claims.observed_at

    def get_valid_prepared_locked(
        self,
        *,
        prepare_id: str,
        expected_record_integrity_hash: str,
        expected_deterministic_plan_hash: str,
        expected_authority_binding_hash: str,
        claim_id: str,
        claim_generation: int,
        fencing_token: str,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> dict[str, Any]:
        store = self._store
        artifact = store._load_committed(prepare_id)
        store._recover_lifecycle_for(prepare_id)
        current = store._derive_state(artifact)
        if current["state"] != "PREPARED":
            return store._result(artifact, current["state"], current["lifecycle_event"], replayed=False)
        if (
            artifact["record_integrity_hash"] != expected_record_integrity_hash
            or artifact["deterministic_plan_hash"] != expected_deterministic_plan_hash
            or artifact["authority_binding_hash"] != expected_authority_binding_hash
        ):
            raise _fail("PREPARE_HASH_MISMATCH", "Prepare identity comparison failed", 409)
        bound_claim = artifact["authority"]["claim"]
        if bound_claim["claim_id"] != claim_id:
            raise _fail("PREPARE_CLAIM_INVALID", "Prepare Claim identity mismatch", 409)
        try:
            claim = self._claims.get_authoritative_claim_locked(
                claim_id=claim_id,
                claim_generation=claim_generation,
                authenticated_principal=authenticated_principal,
                consumer_id=consumer_id,
                server_session_id=server_session_id,
                fencing_token=fencing_token,
            )
        except ValidatedCandidateClaimError as exc:
            lifecycle = self._claims.get_claim_lifecycle_state_locked(claim_id)
            if lifecycle["state"] == "ACTIVE":
                raise _fail("PREPARE_CLAIM_INVALID", f"Claim identity/fence failed: {exc.code}", 409) from exc
            upstream = lifecycle["last_event_type"]
            mapping = {
                "EXPIRED": "EXPIRED",
                "RELEASED": "INVALIDATED",
                "ABANDONED": "INVALIDATED",
                "AUTHORITY_BLOCKED": "AUTHORITY_BLOCKED",
            }
            return store._terminalize(
                artifact,
                mapping.get(upstream, "AUTHORITY_BLOCKED"),
                lifecycle["last_reason_code"] or exc.code,
                upstream,
                self.observed_at,
            )
        store._assert_artifact_authority(artifact, claim)
        queue_item = store._queue.get_entry(artifact["authority"]["queue"]["queue_entry_id"])
        if queue_item is None or queue_item["state"] != "QUEUED":
            return store._terminalize(
                artifact,
                "AUTHORITY_BLOCKED",
                "queue_not_queued",
                queue_item["state"] if queue_item else "NOT_FOUND",
                self.observed_at,
            )
        request = {
            "queue_entry_id": artifact["authority"]["queue"]["queue_entry_id"],
            "expected_queue_revision": artifact["authority"]["queue"]["queue_revision"],
            "expected_candidate_id": artifact["authority"]["candidate"]["candidate_id"],
            "expected_canonical_content_hash": artifact["authority"]["candidate"]["canonical_content_hash"],
        }
        queue = store._exact_queue_locked(request, claim)
        active = store._active_profile_locked(
            self._profiles,
            artifact["authority"]["target_profile"]["target_profile_id"],
        )
        bound_profile = artifact["authority"]["target_profile"]
        if (
            active["target_profile_version"] != bound_profile["target_profile_version"]
            or active["profile_integrity_hash"] != bound_profile["profile_integrity_hash"]
        ):
            return store._terminalize(
                artifact,
                "SUPERSEDED",
                "target_profile_advanced",
                "TARGET_PROFILE_SUPERSEDED",
                self.observed_at,
            )
        store._validate_candidate_envelope(claim["validation"], request, queue)
        live_plan = store._compile_plan(claim["validation"], active)
        store._assert_live_projection(artifact, claim["validation"], live_plan)
        source = claim["validation"]["source"]
        provenance = artifact["provenance"]
        expected_provenance = {
            "review_session_id": source["review_session_id"],
            "human_answer_revision": source["human_answer_revision"],
            "human_answer_hash": source["human_answer_hash"],
            "source_image_hash": source["source_image_hash"],
        }
        if any(provenance[key] != value for key, value in expected_provenance.items()):
            raise _fail("PREPARE_HASH_MISMATCH", "Prepare provenance differs from live Candidate authority", 409)
        return store._result(artifact, "PREPARED", current["lifecycle_event"], replayed=False)
