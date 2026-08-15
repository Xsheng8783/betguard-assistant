from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from betguard.vision.candidate_authority import CandidateAuthorityError
from betguard.vision.candidate_authority import canonical_sha256
from betguard.vision.validated_candidate_claims import (
    ValidatedCandidateClaimError,
    ValidatedCandidateClaimStore,
)
from betguard.vision.validated_candidate_queue import (
    ValidatedCandidateQueueError,
    ValidatedCandidateQueueStore,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class _Validator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.failures: dict[str, CandidateAuthorityError] = {}

    def validate_request(self, payload):
        request = dict(payload)
        self.calls.append(deepcopy(request))
        failure = self.failures.get(request["candidate_id"])
        if failure is not None:
            raise failure
        return {
            "schema_version": "vision-candidate-consumption-validation-v1",
            "validation_status": "VALID_CURRENT",
            "validated_at": "2026-08-15T00:00:00+00:00",
            "candidate_id": request["candidate_id"],
            "candidate_revision": request["expected_candidate_revision"],
            "canonical_content_hash": request["expected_content_hash"],
            "lifecycle_state": "CURRENT",
            "game": "539",
            "bets": [{"human_bet_id": "H-001", "number_groups": [["01"]]}],
            "cancelled_audit": [],
            "source": {"review_session_id": "review-1"},
            "authority": {"value_authority": "human_answer"},
            "safety": {
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_queue": False,
                "auto_confirm": False,
                "auto_submit": False,
            },
        }


def _identity(number: int) -> dict[str, object]:
    return {
        "candidate_id": f"vc-{number:032x}",
        "candidate_revision": number,
        "canonical_content_hash": f"{number:064x}",
    }


def _key(number: int) -> str:
    return f"qik-{number:032x}"


def _setup(tmp_path: Path, count: int = 1, *, clock=None, session_validator=None):
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    entries = []
    for number in range(1, count + 1):
        identity = _identity(number)
        key = _key(number)
        action = queue.bind_human_enqueue_action(
            authenticated_actor="human",
            interactive_session_id="ui-1",
            candidate_id=identity["candidate_id"],
            candidate_revision=identity["candidate_revision"],
            canonical_content_hash=identity["canonical_content_hash"],
            idempotency_key=key,
        )
        result = queue.enqueue(
            {
                "candidate_id": identity["candidate_id"],
                "expected_candidate_revision": identity["candidate_revision"],
                "expected_content_hash": identity["canonical_content_hash"],
                "human_enqueue_action_id": action["action_id"],
                "idempotency_key": key,
            },
            authenticated_actor="human",
            interactive_session_id="ui-1",
        )
        entries.append(result["queue_entry"])
    clock = clock or _Clock()
    claims = ValidatedCandidateClaimStore(
        queue.claim_store_root,
        queue,
        validator,
        clock=clock,
        lease_seconds=10,
        maximum_total_seconds=25,
        owner_session_validator=session_validator or (lambda _p, _c, _s: True),
    )
    return queue, claims, validator, clock, entries


def _claim(claims, number: int = 100, *, principal="worker", consumer=None, session="session-1"):
    consumer = consumer or f"vqcns-{1:032x}"
    key = _key(number)
    action = claims.bind_claim_action(
        authenticated_principal=principal,
        consumer_id=consumer,
        server_session_id=session,
        idempotency_key=key,
    )
    payload = {"action_id": action["action_id"], "idempotency_key": key}
    result = claims.claim_next(
        payload,
        authenticated_principal=principal,
        consumer_id=consumer,
        server_session_id=session,
    )
    return result, action, payload


def _bound_owner_action(claims, purpose, claim_id, number):
    method = getattr(claims, f"bind_{purpose}_action")
    key = _key(number)
    action = method(
        claim_id=claim_id,
        authenticated_principal="worker",
        consumer_id=f"vqcns-{1:032x}",
        server_session_id="session-1",
        idempotency_key=key,
    )
    return action, {"action_id": action["action_id"], "idempotency_key": key}


def _call_owner(claims, purpose, payload):
    return getattr(claims, purpose)(
        payload,
        authenticated_principal="worker",
        consumer_id=f"vqcns-{1:032x}",
        server_session_id="session-1",
    )


def _assert_claim_code(code, function, *args, **kwargs):
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        function(*args, **kwargs)
    assert raised.value.code == code


def test_claim_next_is_identity_only_and_keeps_queue_queued(tmp_path):
    queue, claims, validator, _clock, entries = _setup(tmp_path)
    result, _action, _payload = _claim(claims)
    assert result["status"] == "ACTIVE"
    assert result["queue_entry_identity"]["queue_entry_id"] == entries[0]["queue_entry_id"]
    assert queue.get_entry(entries[0]["queue_entry_id"])["state"] == "QUEUED"
    assert result["safety"]["approved_for_fill"] is False
    assert result["safety"]["approved_for_submit"] is False
    persisted = "\n".join(path.read_text("utf-8") for path in claims.base_dir.rglob("*.json"))
    for key in ('"bets"', '"number_groups"', '"multiplier"', '"layout"'):
        assert key not in persisted
    assert len(validator.calls) == 2  # enqueue and claim


def test_claim_snapshot_and_events_validate_against_normative_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    result, *_ = _claim(claims)
    schema = json.loads((Path(__file__).parents[1] / "docs/schemas/vision_validated_candidate_queue_claim_lease_v1.schema.json").read_text("utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    claim = json.loads(
        (claims.base_dir / "claims" / f"{result['claim_id']}.json").read_text("utf-8")
    )
    events = [
        json.loads(path.read_text("utf-8"))
        for path in (claims.base_dir / "events" / result["claim_id"]).glob("*.json")
    ]
    assert list(validator.iter_errors(claim)) == []
    assert all(not list(validator.iter_errors(event)) for event in events)


def test_exact_claim_retry_returns_same_fence_without_duplicate(tmp_path):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    first, action, payload = _claim(claims)
    second = claims.claim_next(payload, authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1")
    assert second["claim_id"] == first["claim_id"]
    assert second["fencing_token"] == first["fencing_token"]
    assert second["replayed"] is True
    assert len(claims.get_claim_events(first["claim_id"])) == 1


def test_fifo_selection_and_active_entry_exclusion(tmp_path):
    _queue, claims, _validator, _clock, entries = _setup(tmp_path, 2)
    first, *_ = _claim(claims, 100)
    second, *_ = _claim(claims, 101, consumer=f"vqcns-{2:032x}", session="session-2")
    assert first["queue_entry_identity"]["queue_entry_id"] == entries[0]["queue_entry_id"]
    assert second["queue_entry_identity"]["queue_entry_id"] == entries[1]["queue_entry_id"]


def test_renew_appends_event_without_mutating_snapshot(tmp_path):
    _queue, claims, validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    snapshot = (claims.base_dir / "claims" / f"{claimed['claim_id']}.json").read_bytes()
    clock.advance(5)
    _action, payload = _bound_owner_action(claims, "renew", claimed["claim_id"], 201)
    result = _call_owner(claims, "renew", payload)
    assert result["event_type"] == "RENEWED"
    assert result["event_sequence"] == 2
    assert (claims.base_dir / "claims" / f"{claimed['claim_id']}.json").read_bytes() == snapshot
    assert len(validator.calls) == 3


def test_exact_renew_retry_reuses_event(tmp_path):
    _queue, claims, _validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    clock.advance(3)
    _action, payload = _bound_owner_action(claims, "renew", claimed["claim_id"], 202)
    first = _call_owner(claims, "renew", payload)
    second = _call_owner(claims, "renew", payload)
    assert second["event_sequence"] == first["event_sequence"]
    assert second["replayed"] is True
    assert len(claims.get_claim_events(claimed["claim_id"])) == 2


@pytest.mark.parametrize("purpose,event_type", [("release", "RELEASED"), ("abandon", "ABANDONED")])
def test_explicit_terminal_owner_actions(tmp_path, purpose, event_type):
    queue, claims, _validator, _clock, entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    _action, payload = _bound_owner_action(claims, purpose, claimed["claim_id"], 210)
    result = _call_owner(claims, purpose, payload)
    assert result["event_type"] == event_type
    assert queue.get_entry(entries[0]["queue_entry_id"])["state"] == "QUEUED"


def test_lazy_expiry_then_next_generation(tmp_path):
    _queue, claims, _validator, clock, entries = _setup(tmp_path)
    first, *_ = _claim(claims, 100)
    clock.advance(10)
    state = claims.get_claim_state(first["claim_id"])
    assert state["state"] == "EXPIRED"
    second, *_ = _claim(claims, 101, consumer=f"vqcns-{2:032x}", session="session-2")
    assert second["claim_generation"] == 2
    assert second["queue_entry_identity"]["queue_entry_id"] == entries[0]["queue_entry_id"]
    assert second["fencing_token"] != first["fencing_token"]


def test_clock_rollback_fails_without_expiry_or_reassignment(tmp_path):
    _queue, claims, _validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    before = {
        path.relative_to(claims.base_dir): path.read_bytes()
        for path in claims.base_dir.rglob("*.json")
    }
    clock.value -= timedelta(seconds=1)
    _assert_claim_code("CLAIM_CLOCK_UNSAFE", claims.get_claim_state, claimed["claim_id"])
    after = {
        path.relative_to(claims.base_dir): path.read_bytes()
        for path in claims.base_dir.rglob("*.json")
    }
    assert after == before


def test_human_remove_rejected_while_claim_active(tmp_path):
    queue, claims, _validator, _clock, entries = _setup(tmp_path)
    _claim(claims)
    key = _key(300)
    action = queue.bind_human_remove_action(authenticated_actor="human", interactive_session_id="ui-1", queue_entry_id=entries[0]["queue_entry_id"], idempotency_key=key)
    with pytest.raises(ValidatedCandidateQueueError) as raised:
        queue.remove({"queue_entry_id": entries[0]["queue_entry_id"], "human_remove_action_id": action["action_id"], "idempotency_key": key}, authenticated_actor="human", interactive_session_id="ui-1")
    assert raised.value.code == "CLAIM_ACTIVE"


def test_human_remove_allowed_after_release(tmp_path):
    queue, claims, _validator, _clock, entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    _action, payload = _bound_owner_action(claims, "release", claimed["claim_id"], 301)
    _call_owner(claims, "release", payload)
    key = _key(302)
    action = queue.bind_human_remove_action(authenticated_actor="human", interactive_session_id="ui-1", queue_entry_id=entries[0]["queue_entry_id"], idempotency_key=key)
    result = queue.remove({"queue_entry_id": entries[0]["queue_entry_id"], "human_remove_action_id": action["action_id"], "idempotency_key": key}, authenticated_actor="human", interactive_session_id="ui-1")
    assert result["state"] == "REMOVED"


@pytest.mark.parametrize("extra", ["bets", "number_groups", "expires_at", "fencing_token", "consumer_id", "unknown"])
def test_mutation_request_exact_allowlist(tmp_path, extra):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    _result, _action, payload = _claim(claims)
    payload = dict(payload)
    payload[extra] = []
    _assert_claim_code("CLAIM_REQUEST_INVALID", claims.claim_next, payload, authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1")


def test_wrong_owner_and_wrong_fence_fail_closed(tmp_path):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    _assert_claim_code("CLAIM_OWNER_MISMATCH", claims.get_authoritative_claim, claim_id=claimed["claim_id"], claim_generation=claimed["claim_generation"], authenticated_principal="other", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1", fencing_token=claimed["fencing_token"])
    _assert_claim_code("CLAIM_FENCE_MISMATCH", claims.get_authoritative_claim, claim_id=claimed["claim_id"], claim_generation=claimed["claim_generation"], authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1", fencing_token="vqf-" + "0" * 64)


def test_unrecoverable_owner_session_does_not_end_or_reassign(tmp_path):
    sessions = {"session-1": True}
    validator = lambda _p, _c, session: sessions.get(session, False)
    _queue, claims, _candidate_validator, _clock, _entries = _setup(tmp_path, session_validator=validator)
    claimed, *_ = _claim(claims)
    sessions["session-1"] = False
    _assert_claim_code("CLAIM_OWNER_SESSION_UNRECOVERABLE", claims.get_authoritative_claim, claim_id=claimed["claim_id"], claim_generation=claimed["claim_generation"], authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1", fencing_token=claimed["fencing_token"])
    assert claims.get_claim_state(claimed["claim_id"])["state"] == "ACTIVE"


def test_candidate_failure_during_access_blocks_claim_and_queue(tmp_path):
    queue, claims, validator, _clock, entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    validator.failures[claimed["candidate_identity"]["candidate_id"]] = CandidateAuthorityError("CANDIDATE_STALE", "stale", 409)
    _assert_claim_code("CLAIM_AUTHORITY_INVALID", claims.get_authoritative_claim, claim_id=claimed["claim_id"], claim_generation=claimed["claim_generation"], authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1", fencing_token=claimed["fencing_token"])
    assert claims.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"
    assert queue.get_entry(entries[0]["queue_entry_id"])["state"] == "BLOCKED"


def test_invalid_fifo_head_is_blocked_and_scan_continues(tmp_path):
    queue, claims, validator, _clock, entries = _setup(tmp_path, 2)
    validator.failures[_identity(1)["candidate_id"]] = CandidateAuthorityError("CANDIDATE_STALE", "stale", 409)
    claimed, *_ = _claim(claims)
    assert queue.get_entry(entries[0]["queue_entry_id"])["state"] == "BLOCKED"
    assert claimed["queue_entry_identity"]["queue_entry_id"] == entries[1]["queue_entry_id"]


def test_restart_recovers_active_claim(tmp_path):
    queue, claims, validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    restarted_queue = ValidatedCandidateQueueStore(queue.base_dir, validator)
    restarted = ValidatedCandidateClaimStore(restarted_queue.claim_store_root, restarted_queue, validator, clock=clock, lease_seconds=10, maximum_total_seconds=25, owner_session_validator=lambda _p, _c, _s: True)
    state = restarted.get_claim_state(claimed["claim_id"])
    assert state["state"] == "ACTIVE"
    assert state["claim"]["claim_id"] == claimed["claim_id"]
    assert "fencing_token" not in state["claim"]


def test_concurrent_claimers_create_one_active_claim_per_entry(tmp_path):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    barrier = threading.Barrier(2)

    def run(number):
        consumer = f"vqcns-{number:032x}"
        key = _key(500 + number)
        action = claims.bind_claim_action(authenticated_principal=f"worker-{number}", consumer_id=consumer, server_session_id=f"session-{number}", idempotency_key=key)
        barrier.wait()
        return claims.claim_next({"action_id": action["action_id"], "idempotency_key": key}, authenticated_principal=f"worker-{number}", consumer_id=consumer, server_session_id=f"session-{number}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert len([item for item in claims.list_claims() if item["state"] == "ACTIVE"]) == 1


def test_prepare_is_diagnostic_and_contains_no_claim_fence(tmp_path):
    queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    _claim(claims)
    prepared = queue.prepare_next(prepare_action_id=f"qpa-{1:032x}")
    assert prepared is not None
    serialized = json.dumps(prepared)
    assert "fencing_token" not in serialized
    assert "claim_id" not in serialized
    assert prepared["state"] == "QUEUED"


def test_renewal_limit_is_fail_closed(tmp_path):
    _queue, claims, _validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    clock.advance(9)
    _action, first = _bound_owner_action(claims, "renew", claimed["claim_id"], 601)
    _call_owner(claims, "renew", first)
    clock.advance(9)
    _action, second = _bound_owner_action(claims, "renew", claimed["claim_id"], 602)
    _call_owner(claims, "renew", second)
    clock.advance(6)
    _action, final = _bound_owner_action(claims, "renew", claimed["claim_id"], 603)
    _assert_claim_code("CLAIM_RENEWAL_LIMIT", _call_owner, claims, "renew", final)


def test_action_issue_idempotency_conflict(tmp_path):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    key = _key(700)
    claims.bind_claim_action(authenticated_principal="worker", consumer_id=f"vqcns-{1:032x}", server_session_id="session-1", idempotency_key=key)
    _assert_claim_code("CLAIM_IDEMPOTENCY_CONFLICT", claims.bind_claim_action, authenticated_principal="other", consumer_id=f"vqcns-{2:032x}", server_session_id="session-2", idempotency_key=key)


def test_old_bound_action_is_stale_after_renew(tmp_path):
    _queue, claims, _validator, clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    _old_action, old_payload = _bound_owner_action(claims, "release", claimed["claim_id"], 710)
    clock.advance(1)
    _renew_action, renew_payload = _bound_owner_action(claims, "renew", claimed["claim_id"], 711)
    _call_owner(claims, "renew", renew_payload)
    _assert_claim_code("CLAIM_STALE", _call_owner, claims, "release", old_payload)


def test_event_transition_and_lease_time_adversarials_fail_closed(tmp_path):
    _queue, claims, _validator, _clock, _entries = _setup(tmp_path)
    claimed, *_ = _claim(claims)
    claim = json.loads(
        (claims.base_dir / "claims" / f"{claimed['claim_id']}.json").read_text("utf-8")
    )
    first = json.loads(
        next((claims.base_dir / "events" / claimed["claim_id"]).glob("*.json")).read_text("utf-8")
    )
    issued = datetime.fromisoformat(claim["issued_at"])
    expires = datetime.fromisoformat(claim["expires_at"])

    def event(event_type, occurred, lease, prefix):
        value = deepcopy(first)
        value.update(
            {
                "event_id": f"vqce-{9:032x}",
                "event_sequence": 2,
                "event_type": event_type,
                "occurred_at": occurred.isoformat(),
                "lease_expires_at": lease.isoformat() if lease else None,
                "action_id": f"{prefix}-{9:032x}",
                "reason_code": "lease_expired" if event_type == "EXPIRED" else None,
            }
        )
        source = dict(value)
        source.pop("event_integrity_hash")
        value["event_integrity_hash"] = canonical_sha256(source)
        return value

    bad_events = [
        event("CLAIMED", issued + timedelta(seconds=1), expires + timedelta(seconds=1), "qca"),
        event("RENEWED", issued - timedelta(seconds=1), expires + timedelta(seconds=1), "qra"),
        event("RENEWED", issued + timedelta(seconds=1), expires, "qra"),
        event("RENEWED", issued + timedelta(seconds=1), issued + timedelta(seconds=26), "qra"),
        event("RELEASED", expires, None, "qxa"),
        event("EXPIRED", expires - timedelta(seconds=1), None, "qea"),
    ]
    for bad in bad_events:
        _assert_claim_code(
            "CLAIM_STORE_CORRUPT",
            claims._derive_state,
            claim,
            [first, bad],
        )


def test_source_graph_has_no_legacy_or_execution_imports():
    import ast

    source = (Path(__file__).parents[1] / "src/betguard/vision/validated_candidate_claims.py").read_text("utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    for forbidden in ("batch_queue", "approved_fill_queue", "webfill", "assist_fill", "fill_plan", "manual_candidate"):
        assert not any(forbidden in name.lower() for name in imported)
    assert "def complete" not in source.lower()
