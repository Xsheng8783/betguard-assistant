"""Independent adversarial Gate 3B-3B-1 Claim/lease authority regressions.

This suite intentionally exercises only the Candidate authority, Gate 3B-2,
the identity-only Queue, and the isolated Claim authority.  It must never
import or invoke a fill, browser, submit, or legacy Queue consumer.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from betguard.vision.candidate_authority import (
    CandidateAuthorityError,
    VisionCandidateAuthorityStore,
    canonical_json_bytes,
    canonical_sha256,
)
from betguard.vision.candidate_consumption import (
    CandidateConsumptionAuthorityValidator,
)
from betguard.vision.validated_candidate_claims import (
    ValidatedCandidateClaimError,
    ValidatedCandidateClaimStore,
)
from betguard.vision.validated_candidate_queue import (
    ValidatedCandidateQueueError,
    ValidatedCandidateQueueStore,
)
from betguard.vision import validated_candidate_claims as claims_module
from betguard.vision import validated_candidate_queue as queue_module


class _Validator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failures: dict[str, CandidateAuthorityError] = {}

    def validate_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(dict(payload))
        self.calls.append(request)
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
            "source": {"review_session_id": "review-claim-qa"},
            "authority": {"value_authority": "human_answer"},
            "safety": {
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_queue": False,
                "auto_confirm": False,
                "auto_submit": False,
            },
        }


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _identity(n: int = 1) -> dict[str, Any]:
    return {
        "candidate_id": f"vc-{n:032x}",
        "candidate_revision": n,
        "canonical_content_hash": f"{n:064x}",
    }


def _enqueue(store: ValidatedCandidateQueueStore, n: int = 1) -> dict[str, Any]:
    identity = _identity(n)
    key = f"qik-{n:032x}"
    action = store.bind_human_enqueue_action(
        authenticated_actor="claim-qa-user",
        interactive_session_id="claim-qa-session",
        candidate_id=identity["candidate_id"],
        candidate_revision=identity["candidate_revision"],
        canonical_content_hash=identity["canonical_content_hash"],
        idempotency_key=key,
    )
    return store.enqueue(
        {
            "candidate_id": identity["candidate_id"],
            "expected_candidate_revision": identity["candidate_revision"],
            "expected_content_hash": identity["canonical_content_hash"],
            "human_enqueue_action_id": action["action_id"],
            "idempotency_key": key,
        },
        authenticated_actor="claim-qa-user",
        interactive_session_id="claim-qa-session",
    )


def _remove_request(
    store: ValidatedCandidateQueueStore, queue_entry_id: str
) -> tuple[dict[str, Any], str]:
    key = "qik-" + "f" * 32
    action = store.bind_human_remove_action(
        authenticated_actor="claim-qa-user",
        interactive_session_id="claim-qa-session",
        queue_entry_id=queue_entry_id,
        idempotency_key=key,
    )
    return {
        "queue_entry_id": queue_entry_id,
        "human_remove_action_id": action["action_id"],
        "idempotency_key": key,
    }, key


_PRINCIPAL = "claim-qa-user"
_CONSUMER = "vqcns-" + "c" * 32
_SESSION = "claim-qa-server-session"


def _claim_store(
    queue: ValidatedCandidateQueueStore,
    validator: _Validator,
    clock: _Clock,
    *,
    session_validator=None,
    lease_seconds: int = 30,
) -> ValidatedCandidateClaimStore:
    if session_validator is None:
        session_validator = lambda _principal, _consumer, _session: True
    return ValidatedCandidateClaimStore(
        queue.claim_store_root,
        queue,
        validator,
        clock=clock,
        lease_seconds=lease_seconds,
        maximum_total_seconds=120,
        owner_session_validator=session_validator,
    )


def _claim_next(
    store: ValidatedCandidateClaimStore,
    *,
    key_number: int = 100,
    principal: str = _PRINCIPAL,
    consumer: str = _CONSUMER,
    session: str = _SESSION,
) -> dict[str, Any] | None:
    key = f"qik-{key_number:032x}"
    action = store.bind_claim_action(
        authenticated_principal=principal,
        consumer_id=consumer,
        server_session_id=session,
        idempotency_key=key,
    )
    return store.claim_next(
        {"action_id": action["action_id"], "idempotency_key": key},
        authenticated_principal=principal,
        consumer_id=consumer,
        server_session_id=session,
    )


def _bound_owner_action(
    store: ValidatedCandidateClaimStore,
    purpose: str,
    claim_id: str,
    key_number: int,
) -> tuple[dict[str, str], dict[str, str]]:
    key = f"qik-{key_number:032x}"
    issuer = {
        "RENEW": store.bind_renew_action,
        "RELEASE": store.bind_release_action,
        "ABANDON": store.bind_abandon_action,
    }[purpose]
    action = issuer(
        claim_id=claim_id,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    return action, {"action_id": action["action_id"], "idempotency_key": key}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _human_bet(
    human_bet_id: str,
    *,
    groups: list[list[str]],
    rules: list[str] | None = None,
    bet_type: str = "normal",
    continuation: bool = False,
    cancelled: bool = False,
) -> dict[str, Any]:
    return {
        "human_bet_id": human_bet_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups),
        "multiplier": {
            "ordered_rules": list(rules if rules is not None else ["2X1"]),
            "scope": "bet",
            "resolved": False,
        },
        "special_play": {
            "kind": "none",
            "raw_text": None,
            "scope": None,
            "resolved": False,
        },
        "continuation": {"present": continuation, "resolved": False},
        "cancelled": cancelled,
        "active": not cancelled,
        "human_confirmed": False,
    }


def _real_candidate(
    root: Path, sample_id: str, bets: list[dict[str, Any]]
) -> tuple[VisionCandidateAuthorityStore, dict[str, Any]]:
    authority = VisionCandidateAuthorityStore(root)
    review = authority.create_human_review(
        review_session_id=f"review-{sample_id}",
        source_image_id=sample_id,
        source_image_hash="a" * 64,
        game="539",
        bets=deepcopy(bets),
        machine_evidence_refs=[
            {
                "provider_id": "qwen-dashscope",
                "model": "qwen3-vl-plus",
                "request_id": f"cache-{sample_id}",
                "cache_hit": True,
                "evidence_hash": "b" * 64,
                "artifact_ref": None,
                "value_authority": False,
            }
        ],
        blocking_unresolved_count=sum(1 for bet in bets if bet["active"]),
        actor="claim-qa-reviewer",
    )
    review = authority.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[bet["human_bet_id"] for bet in review["bets"]],
        actor="claim-qa-human",
    )
    created = authority.create_candidate(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        idempotency_key=(
            f"{review['review_session_id']}:{review['human_answer_revision']}:"
            f"{review['human_answer_hash']}"
        ),
        actor="claim-qa-candidate",
    )
    return authority, created["candidate"]


def _enqueue_real_candidate(
    queue: ValidatedCandidateQueueStore, candidate: dict[str, Any], number: int
) -> dict[str, Any]:
    key = f"qik-{number:032x}"
    action = queue.bind_human_enqueue_action(
        authenticated_actor="claim-qa-human",
        interactive_session_id="claim-qa-ui-session",
        candidate_id=candidate["candidate_id"],
        candidate_revision=candidate["revision"],
        canonical_content_hash=candidate["canonical_content_hash"],
        idempotency_key=key,
    )
    return queue.enqueue(
        {
            "candidate_id": candidate["candidate_id"],
            "expected_candidate_revision": candidate["revision"],
            "expected_content_hash": candidate["canonical_content_hash"],
            "human_enqueue_action_id": action["action_id"],
            "idempotency_key": key,
        },
        authenticated_actor="claim-qa-human",
        interactive_session_id="claim-qa-ui-session",
    )


def test_claim_store_marker_without_runtime_guard_blocks_direct_queue_remove(
    tmp_path: Path,
) -> None:
    """Restart/misconfiguration must fail closed, not bypass an active Claim."""

    queue_root = tmp_path / "queue"
    store = ValidatedCandidateQueueStore(queue_root, _Validator())
    queued = _enqueue(store)
    entry_id = queued["queue_entry"]["queue_entry_id"]
    payload, _key = _remove_request(store, entry_id)
    marker = store.claim_store_root / "store-enabled.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")
    before_events = store.get_lifecycle_events(entry_id)

    restarted_without_claim_store = ValidatedCandidateQueueStore(queue_root, _Validator())
    with pytest.raises(ValidatedCandidateQueueError) as raised:
        restarted_without_claim_store.remove(
            payload,
            authenticated_actor="claim-qa-user",
            interactive_session_id="claim-qa-session",
        )

    assert raised.value.code == "CLAIM_GUARD_UNAVAILABLE"
    assert restarted_without_claim_store.get_lifecycle_events(entry_id) == before_events
    assert restarted_without_claim_store.get_entry(entry_id)["state"] == "QUEUED"


def test_prepare_next_is_diagnostic_only_and_contains_no_claim_authority(
    tmp_path: Path,
) -> None:
    store = ValidatedCandidateQueueStore(tmp_path / "queue", _Validator())
    queued = _enqueue(store)
    entry_id = queued["queue_entry"]["queue_entry_id"]
    before_events = store.get_lifecycle_events(entry_id)

    prepared = store.prepare_next(prepare_action_id="qpa-" + "1" * 32)

    assert prepared is not None
    assert prepared["state"] == "QUEUED"
    assert prepared["queue_entry_id"] == entry_id
    assert prepared["safety"] == {
        "read_only": True,
        "candidate_only": True,
        "approved_for_fill": False,
        "approved_for_submit": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    forbidden = {
        "claim_id",
        "generation",
        "fencing_token",
        "lease",
        "owner",
        "approved_for_fill",
        "webfill_authorized",
    }
    assert not (forbidden - {"approved_for_fill"}) & set(prepared)
    assert prepared["safety"]["approved_for_fill"] is False
    assert store.get_lifecycle_events(entry_id) == before_events
    assert store.get_entry(entry_id)["state"] == "QUEUED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bets", [{"number_groups": [["39"]]}]),
        ("numbers", ["39"]),
        ("multiplier", "4X9"),
        ("queue_entry_id", "vcq-" + "9" * 32),
        ("claim_generation", 99),
        ("fencing_token", "vqf-" + "9" * 64),
        ("lease_seconds", 999999),
        ("expires_at", "2099-01-01T00:00:00+00:00"),
        ("server_session_id", "client-invented"),
    ],
)
def test_claim_mutation_decoder_rejects_every_client_override_without_writes(
    tmp_path: Path, field: str, value: Any
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    key = "qik-" + "2" * 32
    action = claims.bind_claim_action(
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.claim_next(
            {"action_id": action["action_id"], "idempotency_key": key, field: value},
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )

    assert raised.value.code == "CLAIM_REQUEST_INVALID"
    assert _tree_bytes(tmp_path) == before


def test_two_consumers_cannot_claim_one_queue_entry(tmp_path: Path) -> None:
    validator = _Validator()
    queue_a = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue_a)
    clock = _Clock()
    claims_a = _claim_store(queue_a, validator, clock)
    queue_b = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims_b = _claim_store(queue_b, validator, clock)
    barrier = threading.Barrier(2)

    def attempt(store: ValidatedCandidateClaimStore, number: int) -> dict[str, Any] | None:
        key = f"qik-{number:032x}"
        action = store.bind_claim_action(
            authenticated_principal=f"user-{number}",
            consumer_id=f"vqcns-{number:032x}",
            server_session_id=f"session-{number}",
            idempotency_key=key,
        )
        barrier.wait(timeout=5)
        return store.claim_next(
            {"action_id": action["action_id"], "idempotency_key": key},
            authenticated_principal=f"user-{number}",
            consumer_id=f"vqcns-{number:032x}",
            server_session_id=f"session-{number}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: attempt(*pair), [(claims_a, 301), (claims_b, 302)]))

    successful = [result for result in results if result is not None]
    assert len(successful) == 1
    assert len(claims_a.list_claims()) == 1
    assert claims_a.list_claims()[0]["state"] == "ACTIVE"


def test_unrecoverable_session_cannot_create_committed_claim(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(
        queue,
        validator,
        _Clock(),
        session_validator=lambda _principal, _consumer, _session: False,
    )

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        _claim_next(claims)

    assert raised.value.code == "CLAIM_OWNER_SESSION_UNRECOVERABLE"
    assert claims.list_claims() == []


def test_direct_queue_remove_rejects_active_claim_without_any_event(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claim = _claim_next(claims)
    assert claim is not None
    entry_id = queued["queue_entry"]["queue_entry_id"]
    payload, _key = _remove_request(queue, entry_id)
    queue_before = queue.get_lifecycle_events(entry_id)
    claim_before = claims.get_claim_events(claim["claim_id"])

    with pytest.raises(ValidatedCandidateQueueError) as raised:
        queue.remove(
            payload,
            authenticated_actor="claim-qa-user",
            interactive_session_id="claim-qa-session",
        )

    assert raised.value.code == "CLAIM_ACTIVE"
    assert queue.get_lifecycle_events(entry_id) == queue_before
    assert claims.get_claim_events(claim["claim_id"]) == claim_before


def test_direct_queue_remove_materializes_expiry_then_allows_remove(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock, lease_seconds=10)
    claim = _claim_next(claims)
    assert claim is not None
    entry_id = queued["queue_entry"]["queue_entry_id"]
    clock.advance(10)
    payload, _key = _remove_request(queue, entry_id)

    removed = queue.remove(
        payload,
        authenticated_actor="claim-qa-user",
        interactive_session_id="claim-qa-session",
    )

    assert removed["state"] == "REMOVED"
    assert claims.get_claim_state(claim["claim_id"])["state"] == "EXPIRED"


def test_public_diagnostic_reads_do_not_expose_owner_session_or_fencing_token(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None

    diagnostic = claims.get_claim_state(claimed["claim_id"])
    listed = claims.list_claims()

    for value in (diagnostic, listed):
        rendered = json.dumps(value, sort_keys=True)
        assert claimed["fencing_token"] not in rendered
        assert _SESSION not in rendered


def test_exact_renew_replay_returns_its_own_event_after_a_later_renew(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock)
    claimed = _claim_next(claims)
    assert claimed is not None
    action1, payload1 = _bound_owner_action(claims, "RENEW", claimed["claim_id"], 401)
    del action1
    clock.advance(5)
    renewed1 = claims.renew(
        payload1,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    _action2, payload2 = _bound_owner_action(claims, "RENEW", claimed["claim_id"], 402)
    clock.advance(5)
    renewed2 = claims.renew(
        payload2,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )

    replayed1 = claims.renew(
        payload1,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )

    assert renewed1["event_sequence"] != renewed2["event_sequence"]
    assert replayed1 == {**renewed1, "replayed": True}


def test_candidate_invalidation_terminalizes_claim_and_blocks_queue(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    candidate_id = claimed["candidate_identity"]["candidate_id"]
    validator.failures[candidate_id] = CandidateAuthorityError(
        "CANDIDATE_REVOKED", "revoked", 409
    )

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )

    assert raised.value.code == "CLAIM_AUTHORITY_INVALID"
    assert claims.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"
    assert queue.get_entry(queued["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"
    assert claims.get_claim_events(claimed["claim_id"])[-1]["reason_code"] == "CANDIDATE_REVOKED"


def test_claim_source_has_no_webfill_completion_or_legacy_consumer_imports() -> None:
    source = Path("src/betguard/vision/validated_candidate_claims.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    for forbidden_import in (
        "betguard.webfill",
        "approved_fill_queue",
        "_manual_candidates",
        "playwright",
        "selenium",
    ):
        assert forbidden_import not in lowered
    # ``completed=false`` is a required normative safety flag.  What is
    # forbidden is a COMPLETED lifecycle/action or completion mutation.
    assert '"completed": false' in lowered
    assert 'event_type="completed"' not in lowered
    assert '"completed",' not in lowered
    assert "def complete" not in lowered
    assert "def submit" not in lowered


def test_wrong_owner_session_and_fence_never_authorize_claim(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    before = claims.get_claim_events(claimed["claim_id"])

    attempts = [
        (
            "CLAIM_OWNER_MISMATCH",
            dict(
                claim_id=claimed["claim_id"],
                claim_generation=claimed["claim_generation"],
                authenticated_principal="another-user",
                consumer_id=_CONSUMER,
                server_session_id=_SESSION,
                fencing_token=claimed["fencing_token"],
            ),
        ),
        (
            "CLAIM_OWNER_MISMATCH",
            dict(
                claim_id=claimed["claim_id"],
                claim_generation=claimed["claim_generation"],
                authenticated_principal=_PRINCIPAL,
                consumer_id="vqcns-" + "d" * 32,
                server_session_id=_SESSION,
                fencing_token=claimed["fencing_token"],
            ),
        ),
        (
            "CLAIM_OWNER_MISMATCH",
            dict(
                claim_id=claimed["claim_id"],
                claim_generation=claimed["claim_generation"],
                authenticated_principal=_PRINCIPAL,
                consumer_id=_CONSUMER,
                server_session_id="wrong-session",
                fencing_token=claimed["fencing_token"],
            ),
        ),
        (
            "CLAIM_FENCE_MISMATCH",
            dict(
                claim_id=claimed["claim_id"],
                claim_generation=claimed["claim_generation"],
                authenticated_principal=_PRINCIPAL,
                consumer_id=_CONSUMER,
                server_session_id=_SESSION,
                fencing_token="vqf-" + "0" * 64,
            ),
        ),
    ]
    for code, kwargs in attempts:
        with pytest.raises(ValidatedCandidateClaimError) as raised:
            claims.get_authoritative_claim(**kwargs)
        assert raised.value.code == code
        assert claims.get_claim_events(claimed["claim_id"]) == before


def test_exact_expiry_is_single_terminal_event_and_new_generation_has_new_fence(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock, lease_seconds=10)
    first = _claim_next(claims, key_number=510)
    assert first is not None
    clock.advance(10)

    for _ in range(2):
        with pytest.raises(ValidatedCandidateClaimError) as raised:
            claims.get_authoritative_claim(
                    claim_id=first["claim_id"],
                    claim_generation=first["claim_generation"],
                authenticated_principal=_PRINCIPAL,
                consumer_id=_CONSUMER,
                server_session_id=_SESSION,
                fencing_token=first["fencing_token"],
            )
        assert raised.value.code in {"CLAIM_EXPIRED", "CLAIM_TERMINAL"}

    assert [
        event["event_type"] for event in claims.get_claim_events(first["claim_id"])
    ].count("EXPIRED") == 1
    second = _claim_next(claims, key_number=511)
    assert second is not None
    assert second["claim_generation"] == first["claim_generation"] + 1
    assert second["fencing_token"] != first["fencing_token"]


def test_clock_rollback_rejects_before_any_persisted_mutation(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock)
    claimed = _claim_next(claims)
    assert claimed is not None
    clock.advance(5)
    claims.get_claim_state(claimed["claim_id"])
    clock.advance(-1)
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.bind_renew_action(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            idempotency_key="qik-" + "6" * 32,
        )

    assert raised.value.code == "CLAIM_CLOCK_UNSAFE"
    assert _tree_bytes(tmp_path) == before


def test_clock_rollback_rejects_new_claim_action_issue_without_artifact(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock)
    claimed = _claim_next(claims)
    assert claimed is not None
    clock.advance(5)
    claims.get_claim_state(claimed["claim_id"])
    clock.advance(-1)
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.bind_claim_action(
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            idempotency_key="qik-" + "e" * 32,
        )
    assert raised.value.code == "CLAIM_CLOCK_UNSAFE"
    assert _tree_bytes(tmp_path) == before


def test_action_issue_uses_single_trusted_clock_observation(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock)
    claimed = _claim_next(claims)
    assert claimed is not None
    safe = clock.now
    observations = iter([safe, safe - timedelta(seconds=1)])
    claims._clock = lambda: next(observations)  # type: ignore[attr-defined]

    action = claims.bind_claim_action(
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key="qik-" + "d" * 32,
    )

    assert action["created_at"] == safe.isoformat()
    # A second clock read would have produced a timestamp behind the durable
    # watermark.  Leaving it unconsumed proves issuance used the trusted
    # observation for both the high-watermark check and created_at.
    assert next(observations) == safe - timedelta(seconds=1)


def test_restart_preserves_active_identity_but_rejects_unrecoverable_old_session(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    clock = _Clock()
    queue1 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue1)
    claims1 = _claim_store(queue1, validator, clock)
    claimed = _claim_next(claims1)
    assert claimed is not None

    queue2 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims2 = _claim_store(
        queue2,
        validator,
        clock,
        session_validator=lambda _principal, _consumer, _session: False,
    )
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims2.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    assert raised.value.code == "CLAIM_OWNER_SESSION_UNRECOVERABLE"
    assert claims2.get_claim_state(claimed["claim_id"])["state"] == "ACTIVE"
    before = _tree_bytes(tmp_path)
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        _claim_next(claims2, key_number=601)
    assert raised.value.code == "CLAIM_OWNER_SESSION_UNRECOVERABLE"
    assert _tree_bytes(tmp_path) == before

    queue3 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims3 = _claim_store(
        queue3,
        validator,
        clock,
        session_validator=lambda principal, _consumer, _session: principal == "new-user",
    )
    assert _claim_next(
        claims3,
        key_number=602,
        principal="new-user",
        consumer="vqcns-" + "6" * 32,
        session="new-session",
    ) is None


def test_queue_already_blocked_terminalizes_active_claim_before_authority_use(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    with queue.claim_coordination():
        queue.append_claim_authority_blocked_locked(
            queue_entry_id=queued["queue_entry"]["queue_entry_id"],
            claim_action_id="qba-" + "7" * 32,
            reason_code="CANDIDATE_STALE",
        )

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )

    assert raised.value.code == "CLAIM_AUTHORITY_INVALID"
    assert claims.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"


def test_cross_ledger_crash_recovers_claim_and_queue_authority_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _Validator()
    queue1 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue1)
    clock = _Clock()
    claims1 = _claim_store(queue1, validator, clock)
    claimed = _claim_next(claims1)
    assert claimed is not None
    candidate_id = claimed["candidate_identity"]["candidate_id"]
    validator.failures[candidate_id] = CandidateAuthorityError(
        "CANDIDATE_INVALIDATED", "invalidated", 409
    )
    real_append = queue1.append_claim_authority_blocked_locked
    calls = 0

    def fail_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected queue-event failure")
        return real_append(**kwargs)

    monkeypatch.setattr(queue1, "append_claim_authority_blocked_locked", fail_once)
    with pytest.raises(ValidatedCandidateClaimError):
        claims1.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    # The Claim side is already unusable even though Queue publication failed.
    assert claims1.get_claim_events(claimed["claim_id"])[-1]["event_type"] == "AUTHORITY_BLOCKED"

    queue2 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims2 = _claim_store(queue2, validator, clock)
    assert claims2.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"
    assert queue2.get_entry(queued["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"


def test_prepare_blocks_stale_active_entry_then_claim_finalizes_without_action_conflict(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    clock = _Clock()
    claims = _claim_store(queue, validator, clock)
    claimed = _claim_next(claims)
    assert claimed is not None
    candidate_id = claimed["candidate_identity"]["candidate_id"]
    validator.failures[candidate_id] = CandidateAuthorityError(
        "CANDIDATE_STALE", "stale", 409
    )

    assert queue.prepare_next(prepare_action_id="qpa-" + "8" * 32) is None
    assert queue.get_entry(queued["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    assert raised.value.code == "CLAIM_AUTHORITY_INVALID"
    assert claims.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"

    restarted_queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    restarted_claims = _claim_store(restarted_queue, validator, clock)
    assert restarted_claims.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"
    assert restarted_queue.get_entry(queued["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"


@pytest.mark.parametrize(
    ("sample_id", "bets"),
    [
        (
            "sample-007",
            [
                *[
                    _human_bet(f"H-{index:03d}", groups=[[f"{index:02d}"]])
                    for index in range(1, 25)
                ],
                _human_bet("H-025", groups=[["25"]], cancelled=True),
            ],
        ),
        (
            "sample-008",
            [
                _human_bet("H-003", groups=[["08", "01", "04"]]),
                _human_bet("H-004", groups=[["08", "16", "26"]]),
                _human_bet("H-006", groups=[["09"]]),
            ],
        ),
        (
            "sample-010",
            [_human_bet("H-010", groups=[["32", "34", "35"]], rules=["2X2", "3X5"])],
        ),
        (
            "sample-011",
            [
                _human_bet("H-002", groups=[["30", "35", "36", "38"]], rules=["3/4X1"]),
                _human_bet("H-011", groups=[["21", "35"], ["23"], ["34"], ["37"]], bet_type="column"),
                _human_bet("H-012", groups=[["34"], ["23"], ["35"], ["27", "37"]], bet_type="column"),
            ],
        ),
        (
            "sample-014",
            [
                _human_bet("H-005", groups=[["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]], rules=["2/3/4X0.1"], bet_type="column", continuation=True),
                _human_bet("H-006", groups=[["34"], ["03", "13"], ["16", "36"]], rules=["2X1"], bet_type="column", continuation=True),
                _human_bet("H-011", groups=[["12"], ["15"], ["34"], ["13", "20"]], rules=["2X3", "3X1"], bet_type="column", continuation=True),
                _human_bet("H-099", groups=[["12", "15"]], cancelled=True),
            ],
        ),
    ],
)
def test_real_candidate_shapes_survive_queue_and_claim_without_rewriting(
    tmp_path: Path, sample_id: str, bets: list[dict[str, Any]]
) -> None:
    authority, candidate = _real_candidate(
        tmp_path / sample_id / "authority", sample_id, bets
    )
    validator = CandidateConsumptionAuthorityValidator(authority)
    direct = validator.validate(
        candidate_id=candidate["candidate_id"],
        expected_candidate_revision=candidate["revision"],
        expected_content_hash=candidate["canonical_content_hash"],
    )
    queue = ValidatedCandidateQueueStore(tmp_path / sample_id / "queue", validator)
    queued = _enqueue_real_candidate(queue, candidate, 700 + len(sample_id))
    claims = ValidatedCandidateClaimStore.from_authority_store(
        queue,
        authority,
        clock=_Clock(),
        lease_seconds=30,
        maximum_total_seconds=120,
        owner_session_validator=lambda _principal, _consumer, _session: True,
    )
    claimed = _claim_next(claims, key_number=800 + len(sample_id))
    assert claimed is not None

    assert claimed["validation"] == direct
    assert claimed["candidate_identity"] == queued["queue_entry"]["candidate_identity"]
    assert claimed["validation"]["bets"] == candidate["active_bets"]
    assert claimed["validation"]["cancelled_audit"] == candidate["cancelled_audit"]
    for path in queue.claim_store_root.rglob("*.json"):
        rendered = path.read_text(encoding="utf-8")
        assert '"number_groups"' not in rendered
        assert '"multiplier"' not in rendered
        assert '"active_bets"' not in rendered


@pytest.mark.parametrize(
    ("object_key", "field", "bad_value"),
    [
        ("queue_entry_identity", "enqueue_sequence", True),
        ("candidate_identity", "candidate_revision", True),
        ("lease_owner", "consumer_id", "not-a-consumer-id"),
        ("lease_policy", "lease_seconds", 0),
    ],
)
def test_recomputed_hash_nested_claim_tamper_fails_schema_closed(
    tmp_path: Path, object_key: str, field: str, bad_value: Any
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    claim_path = next((queue.claim_store_root / "claims").glob("*.json"))
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim[object_key][field] = bad_value
    source = dict(claim)
    source.pop("record_integrity_hash")
    claim["record_integrity_hash"] = canonical_sha256(source)
    _write_json(claim_path, claim)
    commit_path = queue.claim_store_root / "claim-commits" / claim_path.name
    _write_json(
        commit_path,
        {
            "claim_id": claim["claim_id"],
            "record_integrity_hash": claim["record_integrity_hash"],
        },
    )

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    assert raised.value.code in {"CLAIM_SCHEMA_INVALID", "CLAIM_STORE_CORRUPT"}


def test_recomputed_hash_unknown_event_field_fails_schema_closed(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    event_path = next((queue.claim_store_root / "events").rglob("*.json"))
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["unknown_authority"] = True
    source = dict(event)
    source.pop("event_integrity_hash")
    event["event_integrity_hash"] = canonical_sha256(source)
    _write_json(event_path, event)

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_claim_state(claimed["claim_id"])
    assert raised.value.code == "CLAIM_SCHEMA_INVALID"


def test_persisted_claim_event_and_clock_match_normative_schema(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    schema = json.loads(
        Path(
            "docs/schemas/vision_validated_candidate_queue_claim_lease_v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    normative = Draft202012Validator(schema)
    artifacts = [
        next((queue.claim_store_root / "claims").glob("*.json")),
        next((queue.claim_store_root / "events").rglob("*.json")),
        queue.claim_store_root / "clock" / "state.json",
    ]
    for path in artifacts:
        value = json.loads(path.read_text(encoding="utf-8"))
        errors = list(normative.iter_errors(value))
        assert errors == [], f"{path.name}: {[error.message for error in errors]}"


def test_unknown_bound_action_field_fails_schema_closed_without_event(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    action, payload = _bound_owner_action(claims, "RENEW", claimed["claim_id"], 901)
    action_path = queue.claim_store_root / "actions" / f"{action['action_id']}.json"
    tampered = json.loads(action_path.read_text(encoding="utf-8"))
    tampered["client_values"] = {"numbers": ["39"]}
    _write_json(action_path, tampered)
    before = claims.get_claim_events(claimed["claim_id"])

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.renew(
            payload,
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )
    assert raised.value.code == "CLAIM_SCHEMA_INVALID"
    assert claims.get_claim_events(claimed["claim_id"]) == before


def test_recomputed_hash_clock_unknown_field_fails_closed_without_mutation(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    clock_path = queue.claim_store_root / "clock" / "state.json"
    state = json.loads(clock_path.read_text(encoding="utf-8"))
    state["unknown"] = "accepted-if-only-hash-is-checked"
    source = dict(state)
    source.pop("record_integrity_hash")
    state["record_integrity_hash"] = canonical_sha256(source)
    _write_json(clock_path, state)
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_claim_state(claimed["claim_id"])
    assert raised.value.code in {"CLAIM_CLOCK_UNSAFE", "CLAIM_SCHEMA_INVALID"}
    assert _tree_bytes(tmp_path) == before


def test_claim_transaction_unknown_field_is_not_recovered_or_authorized(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue1 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue1)
    clock = _Clock()
    claims1 = _claim_store(queue1, validator, clock)
    claimed = _claim_next(claims1)
    assert claimed is not None
    transaction_path = next(
        (queue1.claim_store_root / "claim-transactions").glob("*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction["unknown"] = {"candidate_snapshot": {"bets": [["39"]]}}
    _write_json(transaction_path, transaction)
    before = _tree_bytes(tmp_path)

    queue2 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims2 = _claim_store(queue2, validator, clock)
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims2.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    assert raised.value.code == "CLAIM_SCHEMA_INVALID"
    assert _tree_bytes(tmp_path) == before


def test_invalid_head_queue_block_recovers_event_before_marker_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    candidate_id = queued["queue_entry"]["candidate_identity"]["candidate_id"]
    validator.failures[candidate_id] = CandidateAuthorityError(
        "CANDIDATE_STALE", "stale", 409
    )
    claims = _claim_store(queue, validator, _Clock())
    key = "qik-" + "a" * 32
    action = claims.bind_claim_action(
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    payload = {"action_id": action["action_id"], "idempotency_key": key}
    real_write = queue_module._write_immutable_json
    failed = False

    def fail_marker_once(path: Path, value: dict[str, Any]) -> None:
        nonlocal failed
        if path.parent.name == "claim-authority-block-idempotency" and not failed:
            failed = True
            raise OSError("injected post-event marker failure")
        real_write(path, value)

    monkeypatch.setattr(queue_module, "_write_immutable_json", fail_marker_once)
    with pytest.raises(Exception):
        claims.claim_next(
            payload,
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )
    monkeypatch.setattr(queue_module, "_write_immutable_json", real_write)

    assert claims.claim_next(
        payload,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    ) is None
    entry_id = queued["queue_entry"]["queue_entry_id"]
    assert queue.get_entry(entry_id)["state"] == "BLOCKED"
    assert [event["event_type"] for event in queue.get_lifecycle_events(entry_id)] == [
        "QUEUED",
        "BLOCKED",
    ]


def test_authority_block_recovers_queue_event_before_final_marker_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _Validator()
    queue1 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue1)
    clock = _Clock()
    claims1 = _claim_store(queue1, validator, clock)
    claimed = _claim_next(claims1)
    assert claimed is not None
    candidate_id = claimed["candidate_identity"]["candidate_id"]
    validator.failures[candidate_id] = CandidateAuthorityError(
        "CANDIDATE_REVOKED", "revoked", 409
    )
    real_write = claims_module._write_immutable
    failed = False

    def fail_commit_once(path: Path, value: dict[str, Any]) -> None:
        nonlocal failed
        if path.parent.name == "authority-block-commits" and not failed:
            failed = True
            raise OSError("injected final coordination commit failure")
        real_write(path, value)

    monkeypatch.setattr(claims_module, "_write_immutable", fail_commit_once)
    with pytest.raises(ValidatedCandidateClaimError):
        claims1.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    monkeypatch.setattr(claims_module, "_write_immutable", real_write)
    assert queue1.get_entry(queued["queue_entry"]["queue_entry_id"])["state"] == "BLOCKED"

    queue2 = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    claims2 = _claim_store(queue2, validator, clock)
    assert claims2.get_claim_state(claimed["claim_id"])["state"] == "AUTHORITY_BLOCKED"
    assert (queue2.claim_store_root / "authority-block-commits" / f"{claimed['claim_id']}.json").exists()


def test_wrong_generation_is_rejected_even_with_correct_fencing_token(tmp_path: Path) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    with pytest.raises(ValidatedCandidateClaimError) as raised:
        claims.get_authoritative_claim(
            claim_id=claimed["claim_id"],
            claim_generation=claimed["claim_generation"] + 1,
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
            fencing_token=claimed["fencing_token"],
        )
    assert raised.value.code in {"CLAIM_FENCE_MISMATCH", "CLAIM_STALE"}


def test_explicit_release_then_new_human_action_can_remove_queue_entry(
    tmp_path: Path,
) -> None:
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue(queue)
    claims = _claim_store(queue, validator, _Clock())
    claimed = _claim_next(claims)
    assert claimed is not None
    _action, payload = _bound_owner_action(claims, "RELEASE", claimed["claim_id"], 950)
    released = claims.release(
        payload,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert released["event_type"] == "RELEASED"
    entry_id = queued["queue_entry"]["queue_entry_id"]
    remove_payload, _key = _remove_request(queue, entry_id)
    removed = queue.remove(
        remove_payload,
        authenticated_actor="claim-qa-user",
        interactive_session_id="claim-qa-session",
    )
    assert removed["state"] == "REMOVED"
