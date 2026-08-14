from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from betguard.vision.candidate_authority import CandidateAuthorityError
from betguard.vision.validated_candidate_queue import (
    ValidatedCandidateQueueError,
    ValidatedCandidateQueueStore,
)


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
            "candidate_id": request["candidate_id"],
            "candidate_revision": request["expected_candidate_revision"],
            "canonical_content_hash": request["expected_content_hash"],
            "lifecycle_state": "CURRENT",
            "game": "539",
            "bets": [{"human_bet_id": "H-001"}],
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


def _identity(n: int = 1) -> dict[str, object]:
    return {
        "candidate_id": f"vc-{n:032x}",
        "candidate_revision": n,
        "canonical_content_hash": f"{n:064x}",
    }


def _key(n: int) -> str:
    return f"qik-{n:032x}"


def _prepare_id(n: int) -> str:
    return f"qpa-{n:032x}"


def _store(tmp_path: Path, validator: _Validator | None = None):
    validator = validator or _Validator()
    return ValidatedCandidateQueueStore(tmp_path / "queue", validator), validator


def _bound_enqueue(
    store: ValidatedCandidateQueueStore,
    identity: dict[str, object],
    *,
    key_number: int,
    actor: str = "local-user",
    session: str = "ui-session-1",
):
    key = _key(key_number)
    action = store.bind_human_enqueue_action(
        authenticated_actor=actor,
        interactive_session_id=session,
        idempotency_key=key,
        candidate_id=identity["candidate_id"],
        candidate_revision=identity["candidate_revision"],
        canonical_content_hash=identity["canonical_content_hash"],
    )
    payload = {
        "candidate_id": identity["candidate_id"],
        "expected_candidate_revision": identity["candidate_revision"],
        "expected_content_hash": identity["canonical_content_hash"],
        "human_enqueue_action_id": action["action_id"],
        "idempotency_key": key,
    }
    return action, payload, actor, session


def _enqueue(
    store: ValidatedCandidateQueueStore,
    identity: dict[str, object],
    *,
    key_number: int,
):
    _action, payload, actor, session = _bound_enqueue(
        store, identity, key_number=key_number
    )
    return store.enqueue(
        payload,
        authenticated_actor=actor,
        interactive_session_id=session,
    )


def _bound_remove(store, entry_id: str, *, key_number: int = 900):
    key = _key(key_number)
    action = store.bind_human_remove_action(
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
        queue_entry_id=entry_id,
        idempotency_key=key,
    )
    payload = {
        "queue_entry_id": entry_id,
        "human_remove_action_id": action["action_id"],
        "idempotency_key": key,
    }
    return action, payload


def _assert_code(code: str, function, *args, **kwargs):
    with pytest.raises(ValidatedCandidateQueueError) as raised:
        function(*args, **kwargs)
    assert raised.value.code == code


def test_server_bound_enqueue_action_issue_is_idempotent(tmp_path):
    store, _ = _store(tmp_path)
    identity = _identity()
    first, *_ = _bound_enqueue(store, identity, key_number=1)
    second, *_ = _bound_enqueue(store, identity, key_number=1)
    assert first == second
    assert first["purpose"] == "ENQUEUE"


def test_action_issue_key_cannot_bind_another_target(tmp_path):
    store, _ = _store(tmp_path)
    _bound_enqueue(store, _identity(1), key_number=1)
    _assert_code(
        "QUEUE_IDEMPOTENCY_CONFLICT",
        _bound_enqueue,
        store,
        _identity(2),
        key_number=1,
    )


def test_enqueue_writes_identity_only_immutable_entry(tmp_path):
    store, validator = _store(tmp_path)
    result = _enqueue(store, _identity(), key_number=1)
    entry = result["queue_entry"]
    assert result["state"] == "QUEUED"
    assert result["replayed"] is False
    assert entry["candidate_identity"] == _identity()
    text = json.dumps(entry, ensure_ascii=False)
    for forbidden in (
        '"bets"', '"numbers"', '"number_groups"', '"multiplier"',
        '"layout"', '"raw_text"', '"queue_path"',
    ):
        assert forbidden not in text
    assert len(validator.calls) == 1


def test_entry_matches_normative_json_schema(tmp_path):
    from jsonschema import Draft202012Validator

    store, _ = _store(tmp_path)
    entry = _enqueue(store, _identity(), key_number=1)["queue_entry"]
    schema_path = (
        Path(__file__).parents[1]
        / "docs"
        / "schemas"
        / "vision_validated_candidate_queue_entry_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(entry)) == []


def test_persisted_queue_records_never_copy_validator_values(tmp_path):
    store, _ = _store(tmp_path)
    _enqueue(store, _identity(), key_number=1)
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in store.base_dir.glob("**/*.json")
    )
    assert "H-001" not in persisted
    for key in (
        '"bets"',
        '"numbers"',
        '"number_groups"',
        '"multiplier"',
        '"layout"',
        '"raw_text"',
    ):
        assert key not in persisted


def test_exact_enqueue_replay_returns_same_entry_without_revalidation(tmp_path):
    store, validator = _store(tmp_path)
    _action, payload, actor, session = _bound_enqueue(
        store, _identity(), key_number=1
    )
    first = store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    second = store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    assert second["queue_entry"] == first["queue_entry"]
    assert second["replayed"] is True
    assert len(validator.calls) == 1


def test_second_human_action_same_candidate_does_not_duplicate(tmp_path):
    store, validator = _store(tmp_path)
    first = _enqueue(store, _identity(), key_number=1)
    second = _enqueue(store, _identity(), key_number=2)
    assert second["queue_entry"]["queue_entry_id"] == first["queue_entry"][
        "queue_entry_id"
    ]
    assert second["replayed"] is True
    assert len(store.list_entries()) == 1
    assert len(validator.calls) == 2


@pytest.mark.parametrize(
    "extra_key",
    ["bets", "numbers", "number_groups", "multiplier", "layout", "queue_path"],
)
def test_enqueue_exact_allowlist_rejects_candidate_values(tmp_path, extra_key):
    store, validator = _store(tmp_path)
    _action, payload, actor, session = _bound_enqueue(
        store, _identity(), key_number=1
    )
    payload[extra_key] = []
    _assert_code(
        "QUEUE_REQUEST_INVALID",
        store.enqueue,
        payload,
        authenticated_actor=actor,
        interactive_session_id=session,
    )
    assert validator.calls == []
    assert store.list_entries() == []


def test_unminted_action_is_rejected_before_validator(tmp_path):
    store, validator = _store(tmp_path)
    identity = _identity()
    payload = {
        "candidate_id": identity["candidate_id"],
        "expected_candidate_revision": identity["candidate_revision"],
        "expected_content_hash": identity["canonical_content_hash"],
        "human_enqueue_action_id": "hqe-" + "9" * 32,
        "idempotency_key": _key(1),
    }
    _assert_code(
        "EXPLICIT_HUMAN_ENQUEUE_REQUIRED",
        store.enqueue,
        payload,
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
    )
    assert validator.calls == []


@pytest.mark.parametrize("changed", ["actor", "session", "target", "idempotency"])
def test_bound_action_cannot_be_retargeted(tmp_path, changed):
    store, validator = _store(tmp_path)
    _action, payload, actor, session = _bound_enqueue(
        store, _identity(), key_number=1
    )
    if changed == "actor":
        actor = "other"
    elif changed == "session":
        session = "other-session"
    elif changed == "target":
        payload["expected_content_hash"] = "f" * 64
    else:
        payload["idempotency_key"] = _key(2)
    _assert_code(
        "QUEUE_IDEMPOTENCY_CONFLICT",
        store.enqueue,
        payload,
        authenticated_actor=actor,
        interactive_session_id=session,
    )
    assert validator.calls == []


def test_candidate_validation_failure_makes_no_queue_entry(tmp_path):
    store, validator = _store(tmp_path)
    identity = _identity()
    validator.failures[identity["candidate_id"]] = CandidateAuthorityError(
        "CANDIDATE_STALE", "stale", 409
    )
    _action, payload, actor, session = _bound_enqueue(
        store, identity, key_number=1
    )
    with pytest.raises(CandidateAuthorityError) as raised:
        store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    assert raised.value.code == "CANDIDATE_STALE"
    assert store.list_entries() == []
    assert list((store.base_dir / "entry-commits").glob("*.json")) == []


def test_fifo_uses_server_sequence_not_clock_or_candidate_id(tmp_path):
    store, _ = _store(tmp_path)
    results = [
        _enqueue(store, _identity(n), key_number=n) for n in (9, 2, 7)
    ]
    assert [
        item["queue_entry"]["candidate_identity"]["candidate_id"]
        for item in store.list_entries(state="QUEUED")
    ] == [result["queue_entry"]["candidate_identity"]["candidate_id"] for result in results]
    assert [item["queue_entry"]["enqueue_sequence"] for item in store.list_entries()] == [1, 2, 3]


def test_prepare_valid_head_is_read_only_and_stays_queued(tmp_path):
    store, validator = _store(tmp_path)
    result = _enqueue(store, _identity(), key_number=1)
    entry_id = result["queue_entry"]["queue_entry_id"]
    events_before = store.get_lifecycle_events(entry_id)
    prepared = store.prepare_next(prepare_action_id=_prepare_id(1))
    assert prepared["queue_entry"] == result["queue_entry"]
    assert prepared["validation"]["validation_status"] == "VALID_CURRENT"
    assert prepared["schema_version"] == "vision-validated-candidate-queue-prepare-v1"
    assert prepared["state"] == "QUEUED"
    assert prepared["queue_entry_id"] == entry_id
    assert prepared["enqueue_sequence"] == 1
    assert prepared["candidate_id"] == _identity()["candidate_id"]
    assert prepared["candidate_revision"] == 1
    assert prepared["canonical_content_hash"] == _identity()[
        "canonical_content_hash"
    ]
    assert prepared["safety"] == {
        "read_only": True,
        "candidate_only": True,
        "approved_for_fill": False,
        "approved_for_submit": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    assert store.get_entry(entry_id)["state"] == "QUEUED"
    assert store.get_lifecycle_events(entry_id) == events_before
    assert len(validator.calls) == 2


def test_two_prepares_can_return_same_unclaimed_head(tmp_path):
    store, _ = _store(tmp_path)
    result = _enqueue(store, _identity(), key_number=1)
    first = store.prepare_next(prepare_action_id=_prepare_id(1))
    second = store.prepare_next(prepare_action_id=_prepare_id(2))
    assert first["queue_entry"] == second["queue_entry"] == result["queue_entry"]
    assert store.get_entry(result["queue_entry"]["queue_entry_id"])["state"] == "QUEUED"


def test_invalid_fifo_head_is_blocked_then_next_valid_is_returned(tmp_path):
    store, validator = _store(tmp_path)
    bad = _enqueue(store, _identity(1), key_number=1)
    good = _enqueue(store, _identity(2), key_number=2)
    validator.failures[_identity(1)["candidate_id"]] = CandidateAuthorityError(
        "CANDIDATE_STALE", "stale", 409
    )
    prepared = store.prepare_next(prepare_action_id=_prepare_id(1))
    assert prepared["queue_entry"] == good["queue_entry"]
    assert store.get_entry(bad["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"
    blocked = store.get_lifecycle_events(bad["queue_entry"]["queue_entry_id"])[-1]
    assert blocked["reason_code"] == "CANDIDATE_STALE"
    assert "bets" not in blocked


def test_prepare_blocked_event_is_not_duplicated(tmp_path):
    store, validator = _store(tmp_path)
    bad = _enqueue(store, _identity(1), key_number=1)
    validator.failures[_identity(1)["candidate_id"]] = CandidateAuthorityError(
        "CANDIDATE_INVALIDATED", "invalid", 409
    )
    assert store.prepare_next(prepare_action_id=_prepare_id(1)) is None
    assert store.prepare_next(prepare_action_id=_prepare_id(1)) is None
    events = store.get_lifecycle_events(bad["queue_entry"]["queue_entry_id"])
    assert [event["event_type"] for event in events] == ["QUEUED", "BLOCKED"]


def test_remove_queued_entry_and_exact_replay(tmp_path):
    store, _ = _store(tmp_path)
    entry_id = _enqueue(store, _identity(), key_number=1)["queue_entry"]["queue_entry_id"]
    _action, payload = _bound_remove(store, entry_id)
    first = store.remove(
        payload,
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
    )
    second = store.remove(
        payload,
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
    )
    assert first == {"queue_entry_id": entry_id, "state": "REMOVED", "replayed": False}
    assert second["replayed"] is True
    assert store.get_entry(entry_id)["state"] == "REMOVED"


def test_remove_blocked_entry_is_allowed(tmp_path):
    store, validator = _store(tmp_path)
    entry_id = _enqueue(store, _identity(), key_number=1)["queue_entry"]["queue_entry_id"]
    validator.failures[_identity()["candidate_id"]] = CandidateAuthorityError(
        "CANDIDATE_REVOKED", "revoked", 409
    )
    assert store.prepare_next(prepare_action_id=_prepare_id(1)) is None
    _action, payload = _bound_remove(store, entry_id)
    result = store.remove(
        payload,
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
    )
    assert result["state"] == "REMOVED"


def test_remove_requires_exact_payload_and_bound_action(tmp_path):
    store, _ = _store(tmp_path)
    entry_id = _enqueue(store, _identity(), key_number=1)["queue_entry"]["queue_entry_id"]
    _action, payload = _bound_remove(store, entry_id)
    payload["numbers"] = ["01"]
    _assert_code(
        "QUEUE_REQUEST_INVALID",
        store.remove,
        payload,
        authenticated_actor="local-user",
        interactive_session_id="ui-session-1",
    )


def test_restart_reloads_committed_entry_and_fifo_state(tmp_path):
    store, validator = _store(tmp_path)
    result = _enqueue(store, _identity(), key_number=1)
    restarted = ValidatedCandidateQueueStore(store.base_dir, validator)
    assert restarted.get_entry(result["queue_entry"]["queue_entry_id"])["state"] == "QUEUED"
    assert restarted.list_entries()[0]["queue_entry"] == result["queue_entry"]


def test_interrupted_enqueue_same_action_recovers_commit(tmp_path, monkeypatch):
    import betguard.vision.validated_candidate_queue as module

    store, _ = _store(tmp_path)
    _action, payload, actor, session = _bound_enqueue(store, _identity(), key_number=1)
    original = module._write_immutable_json
    failed = {"done": False}

    def interrupt_commit(path, value):
        if path.parent.name == "entry-commits" and not failed["done"]:
            failed["done"] = True
            raise OSError("simulated stop before visibility marker")
        return original(path, value)

    monkeypatch.setattr(module, "_write_immutable_json", interrupt_commit)
    with pytest.raises(OSError):
        store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    monkeypatch.setattr(module, "_write_immutable_json", original)
    restarted = ValidatedCandidateQueueStore(store.base_dir, store._validator)
    result = restarted.enqueue(
        payload, authenticated_actor=actor, interactive_session_id=session
    )
    assert result["state"] == "QUEUED"
    assert result["replayed"] is True
    assert len(restarted.list_entries()) == 1


def test_interrupted_identity_index_recovers_from_different_action(tmp_path, monkeypatch):
    import betguard.vision.validated_candidate_queue as module

    store, _ = _store(tmp_path)
    identity = _identity()
    _action, payload, actor, session = _bound_enqueue(store, identity, key_number=1)
    original = module._write_immutable_json
    failed = {"done": False}

    def interrupt_commit(path, value):
        if path.parent.name == "entry-commits" and not failed["done"]:
            failed["done"] = True
            raise OSError("simulated stop")
        return original(path, value)

    monkeypatch.setattr(module, "_write_immutable_json", interrupt_commit)
    with pytest.raises(OSError):
        store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    monkeypatch.setattr(module, "_write_immutable_json", original)
    restarted = ValidatedCandidateQueueStore(store.base_dir, store._validator)
    _a2, p2, actor2, session2 = _bound_enqueue(
        restarted, identity, key_number=2
    )
    result = restarted.enqueue(
        p2, authenticated_actor=actor2, interactive_session_id=session2
    )
    assert result["state"] == "QUEUED"
    assert result["replayed"] is True
    assert len(restarted.list_entries()) == 1


def test_entry_integrity_tamper_fails_closed(tmp_path):
    store, _ = _store(tmp_path)
    result = _enqueue(store, _identity(), key_number=1)
    entry_id = result["queue_entry"]["queue_entry_id"]
    path = store.base_dir / "entries" / f"{entry_id}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["enqueue_sequence"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")
    _assert_code("QUEUE_STORE_CORRUPT", store.get_entry, entry_id)


def test_lifecycle_sequence_gap_fails_closed(tmp_path):
    store, _ = _store(tmp_path)
    entry_id = _enqueue(store, _identity(), key_number=1)["queue_entry"]["queue_entry_id"]
    directory = store.base_dir / "lifecycle-events" / entry_id
    (directory / "000001.json").rename(directory / "000002.json")
    _assert_code("QUEUE_STORE_CORRUPT", store.get_lifecycle_events, entry_id)


def test_no_claim_or_completion_api_exists(tmp_path):
    store, _ = _store(tmp_path)
    assert not hasattr(store, "claim_next")
    assert not hasattr(store, "mark_completed")
    assert not hasattr(store, "submit")
    assert not hasattr(store, "webfill")


def test_source_imports_no_legacy_queue_or_execution_module():
    import ast
    import inspect
    import betguard.vision.validated_candidate_queue as module

    tree = ast.parse(inspect.getsource(module))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(
        token in name
        for name in imported
        for token in ("webfill", "approved_fill_queue", "batch_queue", "assist_fill")
    )


def test_simultaneous_duplicate_enqueue_commits_exactly_one_entry(tmp_path):
    store, validator = _store(tmp_path)
    _action, payload, actor, session = _bound_enqueue(
        store, _identity(), key_number=1
    )
    barrier = threading.Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return store.enqueue(
            payload,
            authenticated_actor=actor,
            interactive_session_id=session,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(run), pool.submit(run))]
    assert {result["replayed"] for result in results} == {False, True}
    assert len({result["queue_entry"]["queue_entry_id"] for result in results}) == 1
    assert len(store.list_entries()) == 1
    assert len(validator.calls) == 1


def test_simultaneous_distinct_enqueue_has_unique_fifo_sequences(tmp_path):
    store, _ = _store(tmp_path)
    requests = [
        _bound_enqueue(store, _identity(n), key_number=n)
        for n in (1, 2, 3, 4)
    ]
    barrier = threading.Barrier(len(requests))

    def run(bound):
        _action, payload, actor, session = bound
        barrier.wait(timeout=5)
        return store.enqueue(
            payload,
            authenticated_actor=actor,
            interactive_session_id=session,
        )

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        results = [future.result() for future in [pool.submit(run, item) for item in requests]]
    sequences = [result["queue_entry"]["enqueue_sequence"] for result in results]
    assert sorted(sequences) == [1, 2, 3, 4]
    assert len(set(sequences)) == 4
    assert [item["queue_entry"]["enqueue_sequence"] for item in store.list_entries()] == [1, 2, 3, 4]


_CANDIDATE_FAILURE_CODES = [
    "CANDIDATE_STALE",
    "CANDIDATE_INVALIDATED",
    "CANDIDATE_SUPERSEDED",
    "CANDIDATE_REVOKED",
    "CANDIDATE_NOT_FOUND",
    "CANDIDATE_HASH_MISMATCH",
    "CANDIDATE_SCHEMA_INVALID",
    "CANDIDATE_AUTHORITY_INVALID",
    "CANDIDATE_STRUCTURE_INVALID",
]


@pytest.mark.parametrize("candidate_code", _CANDIDATE_FAILURE_CODES)
def test_all_candidate_failures_reject_enqueue_with_zero_queue_writes(
    tmp_path, candidate_code
):
    store, validator = _store(tmp_path)
    identity = _identity()
    validator.failures[identity["candidate_id"]] = CandidateAuthorityError(
        candidate_code, "fail closed", 409
    )
    _action, payload, actor, session = _bound_enqueue(
        store, identity, key_number=1
    )
    with pytest.raises(CandidateAuthorityError) as raised:
        store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    assert raised.value.code == candidate_code
    assert store.list_entries() == []
    assert list((store.base_dir / "entry-commits").glob("*.json")) == []
    assert list((store.base_dir / "lifecycle-events").glob("**/*.json")) == []


@pytest.mark.parametrize("candidate_code", _CANDIDATE_FAILURE_CODES)
def test_all_candidate_failures_block_at_prepare_and_never_return_values(
    tmp_path, candidate_code
):
    store, validator = _store(tmp_path)
    identity = _identity()
    result = _enqueue(store, identity, key_number=1)
    validator.failures[identity["candidate_id"]] = CandidateAuthorityError(
        candidate_code, "fail closed", 409
    )
    assert store.prepare_next(prepare_action_id=_prepare_id(1)) is None
    item = store.get_entry(result["queue_entry"]["queue_entry_id"])
    assert item["state"] == "BLOCKED"
    event = store.get_lifecycle_events(result["queue_entry"]["queue_entry_id"])[-1]
    assert event["event_type"] == "BLOCKED"
    assert event["reason_code"] == candidate_code
    assert set(event).isdisjoint({"bets", "numbers", "multiplier", "layout"})


def test_cancelled_only_candidate_is_structure_invalid_and_not_enqueued(tmp_path):
    store, validator = _store(tmp_path)
    identity = _identity()
    validator.failures[identity["candidate_id"]] = CandidateAuthorityError(
        "CANDIDATE_STRUCTURE_INVALID",
        "Candidate has no executable active bet",
        422,
    )
    _action, payload, actor, session = _bound_enqueue(
        store, identity, key_number=1
    )
    with pytest.raises(CandidateAuthorityError) as raised:
        store.enqueue(payload, authenticated_actor=actor, interactive_session_id=session)
    assert raised.value.code == "CANDIDATE_STRUCTURE_INVALID"
    assert store.list_entries() == []
