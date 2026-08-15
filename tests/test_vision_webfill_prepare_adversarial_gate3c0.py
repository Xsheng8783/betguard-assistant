"""Independent Gate 3C-0 security and authority-boundary regressions.

This suite intentionally stays outside the production implementation tests.
It exercises only isolated ``tmp_path`` stores and never imports a browser,
Webfill executor, submit path, or external model provider.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from betguard.vision.candidate_authority import (
    VisionCandidateAuthorityStore,
    canonical_sha256,
)
from betguard.vision.candidate_consumption import (
    CandidateConsumptionAuthorityValidator,
)
from betguard.vision.validated_candidate_claims import (
    ValidatedCandidateClaimStore,
)
from betguard.vision.validated_candidate_queue import (
    ValidatedCandidateQueueStore,
)
from betguard.vision.webfill_prepare import WebfillPrepareError, WebfillPrepareStore
from betguard.vision.webfill_target_profiles import (
    WebfillTargetProfileError,
    WebfillTargetProfileStore,
)


def _profile(*, version: int = 1) -> dict:
    profile = {
        "schema_version": "vision-webfill-target-profile-v1",
        "target_profile_id": "wtp-betguard-539-logical",
        "target_profile_version": version,
        "game": "539",
        "capabilities": {
            "supported_bet_types": ["normal", "column"],
            "supported_special_play_kinds": ["none", "half_car"],
            "supports_continuation": True,
            "supports_multiple_multiplier_rules": True,
            "maximum_active_bets": 100,
            "maximum_number_groups_per_bet": 12,
            "maximum_numbers_per_group": 39,
        },
        "ordering_contract": {
            "bet_order": "preserve_candidate_active_bets",
            "group_order": "preserve",
            "number_order": "preserve",
            "multiplier_rule_order": "preserve",
        },
        "adapter_boundary": {
            "logical_contract_only": True,
            "dom_mapping_present": False,
            "browser_execution_authorized": False,
            "submit_authorized": False,
        },
        "created_at": datetime(2026, 8, 15, 8, tzinfo=timezone.utc).isoformat(),
    }
    profile["profile_integrity_hash"] = canonical_sha256(profile)
    return profile


def _rehash(profile: dict) -> dict:
    result = deepcopy(profile)
    result.pop("profile_integrity_hash", None)
    result["profile_integrity_hash"] = canonical_sha256(result)
    return result


def _assert_profile_error(code: str, callback, *args, **kwargs) -> None:
    with pytest.raises(WebfillTargetProfileError) as caught:
        callback(*args, **kwargs)
    assert caught.value.code == code


_PRINCIPAL = "gate3c0-human"
_CONSUMER = "vqcns-" + "c" * 32
_SESSION = "gate3c0-server-session"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 15, 9, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _human_bet(
    human_bet_id: str,
    *,
    groups: list[list[str]],
    bet_type: str = "normal",
    rules: list[str] | None = None,
    continuation: bool = False,
    cancelled: bool = False,
    special_kind: str = "none",
    special_raw_text: str | None = None,
    special_scope: str | None = None,
) -> dict[str, Any]:
    return {
        "human_bet_id": human_bet_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups),
        "multiplier": {
            "ordered_rules": list(rules or ["2X1"]),
            "scope": "bet",
            "resolved": False,
        },
        "special_play": {
            "kind": special_kind,
            "raw_text": special_raw_text,
            "scope": special_scope,
            "resolved": False,
        },
        "continuation": {"present": continuation, "resolved": False},
        "cancelled": cancelled,
        "active": not cancelled,
        "human_confirmed": False,
    }


def _authority_candidate(
    root: Path, bets: list[dict[str, Any]], *, sample_id: str = "gate3c0"
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
                "request_id": f"cached-{sample_id}",
                "cache_hit": True,
                "evidence_hash": "b" * 64,
                "artifact_ref": None,
                "value_authority": False,
            }
        ],
        blocking_unresolved_count=sum(1 for bet in bets if bet["active"]),
        actor="gate3c0-reviewer",
    )
    review = authority.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[bet["human_bet_id"] for bet in review["bets"]],
        actor="gate3c0-human",
    )
    created = authority.create_candidate(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        idempotency_key=(
            f"{review['review_session_id']}:{review['human_answer_revision']}:"
            f"{review['human_answer_hash']}"
        ),
        actor="gate3c0-candidate",
    )
    return authority, created["candidate"]


def _enqueue_candidate(
    queue: ValidatedCandidateQueueStore, candidate: dict[str, Any]
) -> dict[str, Any]:
    key = "qik-" + "1" * 32
    action = queue.bind_human_enqueue_action(
        authenticated_actor=_PRINCIPAL,
        interactive_session_id="gate3c0-ui",
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
        authenticated_actor=_PRINCIPAL,
        interactive_session_id="gate3c0-ui",
    )


def _claim_candidate(
    claims: ValidatedCandidateClaimStore,
) -> dict[str, Any]:
    key = "qik-" + "2" * 32
    action = claims.bind_claim_action(
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    claimed = claims.claim_next(
        {"action_id": action["action_id"], "idempotency_key": key},
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert claimed is not None
    return claimed


def _happy_prepare_fixture(tmp_path: Path) -> dict[str, Any]:
    bets = [
        _human_bet("H-001", groups=[["01", "02"]]),
        _human_bet(
            "H-002",
            groups=[["03"], ["04", "05"]],
            bet_type="column",
            rules=["2X2", "3X5"],
            continuation=True,
        ),
        _human_bet("H-003", groups=[["06"]], cancelled=True),
    ]
    authority, candidate = _authority_candidate(tmp_path / "authority", bets)
    validator = CandidateConsumptionAuthorityValidator(authority)
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue_candidate(queue, candidate)
    clock = _Clock()
    claims = ValidatedCandidateClaimStore(
        queue.claim_store_root,
        queue,
        validator,
        clock=clock,
        lease_seconds=60,
        maximum_total_seconds=300,
        owner_session_validator=lambda _principal, _consumer, _session: True,
    )
    claimed = _claim_candidate(claims)
    profiles = WebfillTargetProfileStore(tmp_path / "profiles")
    profile = _profile()
    profiles.register_profile(profile, activate=True)
    prepares = WebfillPrepareStore(tmp_path / "prepares", queue, claims, profiles)
    entry = queued["queue_entry"]
    request = {
        "schema_version": "vision-webfill-prepare-request-v1",
        "queue_entry_id": entry["queue_entry_id"],
        "claim_id": claimed["claim_id"],
        "claim_session_id": _SESSION,
        "claim_generation": claimed["claim_generation"],
        "fencing_token": claimed["fencing_token"],
        "expected_candidate_id": candidate["candidate_id"],
        "expected_canonical_content_hash": candidate["canonical_content_hash"],
        "expected_queue_revision": 1,
        "target_profile_id": profile["target_profile_id"],
        "target_profile_version": profile["target_profile_version"],
        "idempotency_key": "wpi-" + "3" * 32,
    }
    return {
        "authority": authority,
        "candidate": candidate,
        "queue": queue,
        "queued": queued,
        "claims": claims,
        "claimed": claimed,
        "clock": clock,
        "profiles": profiles,
        "profile": profile,
        "prepares": prepares,
        "request": request,
    }


def _prepare_bets_fixture(
    tmp_path: Path,
    bets: list[dict[str, Any]],
    *,
    sample_id: str,
) -> dict[str, Any]:
    authority, candidate = _authority_candidate(
        tmp_path / "authority", bets, sample_id=sample_id
    )
    validator = CandidateConsumptionAuthorityValidator(authority)
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    queued = _enqueue_candidate(queue, candidate)
    clock = _Clock()
    claims = ValidatedCandidateClaimStore(
        queue.claim_store_root,
        queue,
        validator,
        clock=clock,
        lease_seconds=60,
        maximum_total_seconds=300,
        owner_session_validator=lambda _principal, _consumer, _session: True,
    )
    claimed = _claim_candidate(claims)
    profiles = WebfillTargetProfileStore(tmp_path / "profiles")
    profile = _profile()
    profiles.register_profile(profile, activate=True)
    prepares = WebfillPrepareStore(tmp_path / "prepares", queue, claims, profiles)
    entry = queued["queue_entry"]
    request = {
        "schema_version": "vision-webfill-prepare-request-v1",
        "queue_entry_id": entry["queue_entry_id"],
        "claim_id": claimed["claim_id"],
        "claim_session_id": _SESSION,
        "claim_generation": claimed["claim_generation"],
        "fencing_token": claimed["fencing_token"],
        "expected_candidate_id": candidate["candidate_id"],
        "expected_canonical_content_hash": candidate["canonical_content_hash"],
        "expected_queue_revision": 1,
        "target_profile_id": profile["target_profile_id"],
        "target_profile_version": profile["target_profile_version"],
        "idempotency_key": "wpi-" + "6" * 32,
    }
    return {
        "authority": authority,
        "candidate": candidate,
        "queue": queue,
        "queued": queued,
        "claims": claims,
        "claimed": claimed,
        "clock": clock,
        "profiles": profiles,
        "profile": profile,
        "prepares": prepares,
        "request": request,
    }


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _rehash_event(event: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(event)
    result.pop("event_integrity_hash", None)
    result["event_integrity_hash"] = canonical_sha256(result)
    return result


def _authority_value_bytes(root: Path) -> dict[str, bytes]:
    """Exclude only the Claim anti-rollback observation watermark.

    Authority reads are required to advance that coordination record.  Candidate,
    HumanReview, Queue Entry/lifecycle, Claim snapshot/lifecycle, and evidence
    records remain byte-identical and are the value-authority preservation target.
    """
    return {
        relative: content
        for relative, content in _tree_bytes(root).items()
        if relative.replace("\\", "/")
        != "claim-leases-v1/clock/state.json"
        and relative.replace("\\", "/") != "clock/state.json"
    }


def _create(fixture: dict[str, Any], request: dict[str, Any] | None = None) -> dict:
    return fixture["prepares"].create_prepare(
        deepcopy(request or fixture["request"]),
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )


def _rehash_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(artifact)
    result["deterministic_plan_hash"] = canonical_sha256(result["logical_plan"])
    authority = result["authority"]
    binding = {
        "schema_version": "vision-webfill-prepare-authority-binding-v1",
        "queue": authority["queue"],
        "candidate": authority["candidate"],
        "claim": {
            key: value
            for key, value in authority["claim"].items()
            if key != "lease_expires_at_at_prepare"
        },
        "target_profile": authority["target_profile"],
        "deterministic_plan_hash": result["deterministic_plan_hash"],
    }
    result["authority_binding_hash"] = canonical_sha256(binding)
    result.pop("record_integrity_hash", None)
    result["record_integrity_hash"] = canonical_sha256(result)
    return result


def _release_claim(fixture: dict[str, Any]) -> None:
    claimed = fixture["claimed"]
    claims = fixture["claims"]
    key = "qik-" + "4" * 32
    action = claims.bind_release_action(
        claim_id=claimed["claim_id"],
        claim_generation=claimed["claim_generation"],
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    result = claims.release(
        {"action_id": action["action_id"], "idempotency_key": key},
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert result["event_type"] == "RELEASED"


def _remove_queue_entry(fixture: dict[str, Any]) -> None:
    queue = fixture["queue"]
    entry_id = fixture["queued"]["queue_entry"]["queue_entry_id"]
    key = "qik-" + "5" * 32
    action = queue.bind_human_remove_action(
        authenticated_actor=_PRINCIPAL,
        interactive_session_id="gate3c0-ui",
        queue_entry_id=entry_id,
        idempotency_key=key,
    )
    result = queue.remove(
        {
            "queue_entry_id": entry_id,
            "human_remove_action_id": action["action_id"],
            "idempotency_key": key,
        },
        authenticated_actor=_PRINCIPAL,
        interactive_session_id="gate3c0-ui",
    )
    assert result["state"] == "REMOVED"


@pytest.mark.parametrize(
    "created_at",
    ["2026-08-15", "2026-08-15T08:00:00"],
)
def test_profile_rejects_schema_invalid_naive_or_date_only_timestamp(
    tmp_path: Path, created_at: str
) -> None:
    store = WebfillTargetProfileStore(tmp_path)
    profile = _profile()
    profile["created_at"] = created_at

    _assert_profile_error(
        "PREPARE_TARGET_PROFILE_INTEGRITY_INVALID",
        store.register_profile,
        _rehash(profile),
    )
    assert not any((tmp_path / "target-profiles").rglob("*.json"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supported_bet_types", [["normal"]]),
        ("supported_special_play_kinds", [["none"]]),
    ],
)
def test_profile_nested_unhashable_capability_value_fails_with_stable_code(
    tmp_path: Path, field: str, value: list
) -> None:
    store = WebfillTargetProfileStore(tmp_path)
    profile = _profile()
    profile["capabilities"][field] = value

    _assert_profile_error(
        "PREPARE_TARGET_PROFILE_INTEGRITY_INVALID",
        store.register_profile,
        _rehash(profile),
    )


def test_get_active_profile_rejects_path_traversal_before_filesystem_read(
    tmp_path: Path,
) -> None:
    store = WebfillTargetProfileStore(tmp_path)
    # This file is inside tmp_path, but lies outside the active-pointer
    # directory.  An invalid ID must be rejected before it can select it.
    (tmp_path / "evil.json").write_text("{}", encoding="utf-8")

    _assert_profile_error(
        "PREPARE_TARGET_PROFILE_NOT_FOUND",
        store.get_active_profile,
        "../evil",
    )


def test_same_profile_concurrent_registration_is_exact_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import betguard.vision.webfill_target_profiles as module

    store = WebfillTargetProfileStore(tmp_path)
    profile = _profile()
    original_write = module._write_immutable
    rendezvous = Barrier(2)

    def simultaneous_write(path: Path, value: dict) -> None:
        rendezvous.wait(timeout=5)
        original_write(path, value)

    monkeypatch.setattr(module, "_write_immutable", simultaneous_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _index: store.register_profile(profile), range(2))
        )

    assert results == [profile, profile]
    stored = store.get_profile(
        profile["target_profile_id"],
        profile["target_profile_version"],
        require_active=False,
    )
    assert stored == profile
    assert len(list((tmp_path / "target-profiles").rglob("*.json"))) == 1


def test_real_authority_chain_builds_exact_dry_run_and_commit_visibility(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepares = fixture["prepares"]
    request = fixture["request"]
    result = prepares.create_prepare(
        request,
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )

    safety = {
        "dry_run_only": True,
        "semantic_plan_only": True,
        "adapter_compiled": False,
        "dom_mapping_present": False,
        "browser_automation_authorized": False,
        "approved_for_fill": False,
        "approved_for_submit": False,
        "webfill_authorized": False,
        "submitted": False,
        "auto_submit": False,
        "queue_completed": False,
    }
    assert set(result) == {
        "schema_version",
        "status",
        "state",
        "artifact",
        "lifecycle_event",
        "replayed",
        "safety",
    }
    assert result["schema_version"] == "vision-webfill-prepare-result-v1"
    assert result["status"] == "VALID_PREPARED"
    assert result["state"] == "PREPARED"
    assert result["replayed"] is False
    assert result["safety"] == safety

    artifact = result["artifact"]
    assert artifact["safety"] == safety
    assert artifact["executable"] is False
    assert artifact["blocking_reasons"] == []
    plan = artifact["logical_plan"]
    assert [operation["human_bet_id"] for operation in plan["operations"]] == [
        "H-001",
        "H-002",
    ]
    assert plan["operations"][0]["bet_type"] == "normal"
    assert plan["operations"][0]["number_groups"] == [["01", "02"]]
    assert plan["operations"][1]["bet_type"] == "column"
    assert plan["operations"][1]["number_groups"] == [["03"], ["04", "05"]]
    assert plan["operations"][1]["multiplier"]["ordered_rules"] == [
        "2X2",
        "3X5",
    ]
    assert plan["operations"][1]["continuation"] == {
        "present": True,
        "resolved": True,
        "binding": "within_human_bet",
    }
    assert plan["cancelled_audit_refs"] == [
        {
            "human_bet_id": "H-003",
            "candidate_section": "cancelled_audit",
            "excluded_reason": "cancelled_non_executable",
        }
    ]
    executable_numbers = [
        number
        for operation in plan["operations"]
        for group in operation["number_groups"]
        for number in group
    ]
    assert "06" not in executable_numbers
    assert set(plan["cancelled_audit_refs"][0]) == {
        "human_bet_id",
        "candidate_section",
        "excluded_reason",
    }

    prepare_id = artifact["prepare_id"]
    events = prepares.get_lifecycle_events(prepare_id)
    assert events == [result["lifecycle_event"]]
    assert events[0]["event_type"] == "PREPARED"
    root = Path(prepares.base_dir)
    assert (root / "artifact-commits" / f"{prepare_id}.json").is_file()
    assert (root / "artifacts" / f"{prepare_id}.json").is_file()
    assert (root / "lifecycle-events" / prepare_id / "000001.json").is_file()
    persisted = "".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.json")
        if path.is_file()
    )
    assert request["fencing_token"] not in persisted
    assert request["claim_session_id"] not in persisted
    assert request["idempotency_key"] not in persisted


def test_same_authority_tuple_under_different_key_replays_one_artifact(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    first = _create(fixture)
    second_request = deepcopy(fixture["request"])
    second_request["idempotency_key"] = "wpi-" + "4" * 32
    second = _create(fixture, second_request)

    assert second["status"] == "VALID_PREPARED"
    assert second["replayed"] is True
    assert second["artifact"] == first["artifact"]
    root = Path(fixture["prepares"].base_dir)
    assert len(list((root / "artifacts").glob("*.json"))) == 1
    assert len(list((root / "artifact-commits").glob("*.json"))) == 1
    assert len(list((root / "authority-tuple-index").glob("*.json"))) == 1
    assert len(list((root / "idempotency").glob("*.json"))) == 2


def test_crashed_pending_tuple_retried_under_new_key_cannot_duplicate_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepares = fixture["prepares"]
    original_resume = prepares._resume_transaction

    def crash_before_publication(_transaction: dict) -> None:
        raise RuntimeError("simulated lost process before publication")

    monkeypatch.setattr(prepares, "_resume_transaction", crash_before_publication)
    with pytest.raises(RuntimeError, match="simulated lost process"):
        _create(fixture)
    root = Path(prepares.base_dir)
    assert len(list((root / "prepare-transactions").glob("*.json"))) == 1
    assert not list((root / "artifact-commits").glob("*.json"))

    monkeypatch.setattr(prepares, "_resume_transaction", original_resume)
    retry = deepcopy(fixture["request"])
    retry["idempotency_key"] = "wpi-" + "5" * 32
    result = _create(fixture, retry)

    assert result["status"] == "VALID_PREPARED"
    assert result["replayed"] is True
    assert len(list((root / "artifacts").glob("*.json"))) == 1
    assert len(list((root / "artifact-commits").glob("*.json"))) == 1
    assert len(list((root / "authority-tuple-index").glob("*.json"))) == 1
    assert len(list((root / "prepare-transactions").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "flatten_column",
        "empty_multiplier",
        "bad_continuation",
        "machine_provenance",
    ],
)
def test_recomputed_hash_nested_semantic_tamper_still_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    artifact = _create(fixture)["artifact"]
    tampered = deepcopy(artifact)
    if mutation == "flatten_column":
        tampered["logical_plan"]["operations"][1]["number_groups"] = [
            ["03", "04", "05"]
        ]
    elif mutation == "empty_multiplier":
        tampered["logical_plan"]["operations"][1]["multiplier"][
            "ordered_rules"
        ] = []
    elif mutation == "bad_continuation":
        tampered["logical_plan"]["operations"][1]["continuation"][
            "binding"
        ] = "another_human_bet"
    else:
        tampered["provenance"]["machine_evidence_used_as_values"] = True
    tampered = _rehash_artifact(tampered)

    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"]._validate_artifact(tampered)
    assert caught.value.code in {"PREPARE_SCHEMA_INVALID", "PREPARE_HASH_MISMATCH"}


def test_queue_removed_after_claim_release_takes_authority_block_precedence(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepared = _create(fixture)
    _release_claim(fixture)
    _remove_queue_entry(fixture)

    result = fixture["prepares"].get_prepare_state(
        prepared["artifact"]["prepare_id"],
        claim_generation=fixture["claimed"]["claim_generation"],
        fencing_token=fixture["claimed"]["fencing_token"],
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )

    assert result["status"] == "AUTHORITY_BLOCKED"
    assert result["state"] == "AUTHORITY_BLOCKED"
    assert result["lifecycle_event"]["upstream_state"] == "REMOVED"


def _five_sample_bets(sample_id: str) -> list[dict[str, Any]]:
    if sample_id == "sample-007":
        active = [
            _human_bet(
                f"H-{index:03d}",
                groups=[[f"{((index - 1) % 39) + 1:02d}"]],
                rules=["2X1"],
            )
            for index in range(1, 25)
        ]
        return active + [_human_bet("H-025", groups=[["39"]], cancelled=True)]
    if sample_id == "sample-008":
        return [
            _human_bet(
                "H-001",
                groups=[["08", "18"], ["28"], ["38"]],
                bet_type="column",
                rules=["3X1"],
            ),
            _human_bet("H-002", groups=[["09", "19"]], rules=["2X2"]),
        ]
    if sample_id == "sample-010":
        return [
            _human_bet(
                "H-001",
                groups=[["32", "34", "35"]],
                rules=["2X2", "3X5"],
                special_kind="half_car",
                special_raw_text="各半車",
                special_scope="bet",
            )
        ]
    if sample_id == "sample-011":
        return [
            _human_bet(
                "H-001",
                groups=[["30"], ["35"], ["36"], ["38"]],
                bet_type="column",
                rules=["3/4X1"],
                continuation=True,
            )
        ]
    if sample_id == "sample-014":
        return [
            _human_bet(
                "H-001",
                groups=[["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                bet_type="column",
                rules=["2/3/4X0.1"],
                continuation=True,
            ),
            _human_bet(
                "H-002",
                groups=[["12"], ["15"], ["34"], ["13", "20"]],
                bet_type="column",
                rules=["2X3", "3X1"],
                continuation=True,
                special_kind="half_car",
                special_raw_text="各半車",
                special_scope="bet",
            ),
            _human_bet("H-003", groups=[["06"]], cancelled=True),
        ]
    raise AssertionError(sample_id)


@pytest.mark.parametrize(
    "sample_id",
    ["sample-007", "sample-008", "sample-010", "sample-011", "sample-014"],
)
def test_five_real_shape_authority_chain_preserves_only_candidate_values(
    tmp_path: Path, sample_id: str
) -> None:
    bets = _five_sample_bets(sample_id)
    fixture = _prepare_bets_fixture(tmp_path, bets, sample_id=sample_id)
    upstream_roots = [
        Path(fixture["authority"].base_dir),
        Path(fixture["queue"].base_dir),
        Path(fixture["claims"].base_dir),
    ]
    before = [_authority_value_bytes(root) for root in upstream_roots]
    clock_path = Path(fixture["claims"].base_dir) / "clock" / "state.json"
    clock_before = json.loads(clock_path.read_text(encoding="utf-8"))

    result = _create(fixture)
    plan = result["artifact"]["logical_plan"]
    active = fixture["candidate"]["active_bets"]
    cancelled = fixture["candidate"]["cancelled_audit"]
    assert [operation["human_bet_id"] for operation in plan["operations"]] == [
        bet["human_bet_id"] for bet in active
    ]
    for operation, source in zip(plan["operations"], active, strict=True):
        assert operation["bet_type"] == source["bet_type"]
        assert operation["number_groups"] == source["number_groups"]
        assert operation["multiplier"] == source["multiplier"]
        assert operation["special_play"] == source["special_play"]
        assert operation["continuation"] == {
            "present": source["continuation"]["present"],
            "resolved": True,
            "binding": "within_human_bet",
        }
    assert plan["cancelled_audit_refs"] == [
        {
            "human_bet_id": bet["human_bet_id"],
            "candidate_section": "cancelled_audit",
            "excluded_reason": "cancelled_non_executable",
        }
        for bet in cancelled
    ]
    assert fixture["prepares"]._compile_plan(
        fixture["claimed"]["validation"], fixture["profile"]
    ) == plan
    assert canonical_sha256(plan) == result["artifact"]["deterministic_plan_hash"]
    assert result["artifact"]["provenance"]["machine_evidence_used_as_values"] is False
    assert "qwen-dashscope" not in str(plan)
    assert [_authority_value_bytes(root) for root in upstream_roots] == before
    clock_after = json.loads(clock_path.read_text(encoding="utf-8"))
    assert clock_after["observation_sequence"] == clock_before["observation_sequence"] + 1
    assert datetime.fromisoformat(clock_after["last_observed_utc"]) >= datetime.fromisoformat(
        clock_before["last_observed_utc"]
    )
    clock_integrity = clock_after.pop("record_integrity_hash")
    assert canonical_sha256(clock_after) == clock_integrity


@pytest.mark.parametrize(
    ("matrix_id", "mutation", "expected_code"),
    [
        ("F13", "multiplier_unresolved", "PREPARE_SCOPE_UNRESOLVED"),
        ("F14", "special_unresolved", "PREPARE_SCOPE_UNRESOLVED"),
        ("F15a", "groups_empty", "PREPARE_STRUCTURE_INVALID"),
        ("F15b", "group_empty", "PREPARE_STRUCTURE_INVALID"),
        ("F16", "normal_multiple_groups", "PREPARE_STRUCTURE_INVALID"),
        ("F17", "column_single_group", "PREPARE_STRUCTURE_INVALID"),
        ("F18", "cancelled_in_operations", "PREPARE_STRUCTURE_INVALID"),
        ("F19", "inactive_in_operations", "PREPARE_STRUCTURE_INVALID"),
        ("F20", "continuation_unresolved", "PREPARE_CONTINUATION_UNSUPPORTED"),
        ("F21", "duplicate_human_bet_id", "PREPARE_STRUCTURE_INVALID"),
        ("F29", "unsupported_bet_type", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F30", "unsupported_special", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F31a", "continuation_unsupported", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F31b", "multiple_rules_unsupported", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F32a", "active_limit", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F32b", "group_limit", "PREPARE_UNSUPPORTED_CAPABILITY"),
        ("F32c", "number_limit", "PREPARE_UNSUPPORTED_CAPABILITY"),
    ],
)
def test_matrix_compiler_failures_are_exact_and_never_guess(
    tmp_path: Path, matrix_id: str, mutation: str, expected_code: str
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    validation = deepcopy(fixture["claimed"]["validation"])
    profile = deepcopy(fixture["profile"])
    first, second = validation["bets"]

    if mutation == "multiplier_unresolved":
        first["multiplier"]["resolved"] = False
    elif mutation == "special_unresolved":
        first["special_play"] = {
            "kind": "half_car",
            "raw_text": "各半車",
            "scope": "bet",
            "resolved": False,
        }
    elif mutation == "groups_empty":
        first["number_groups"] = []
    elif mutation == "group_empty":
        first["number_groups"] = [[]]
    elif mutation == "normal_multiple_groups":
        first["number_groups"] = [["01"], ["02"]]
    elif mutation == "column_single_group":
        second["number_groups"] = [["03", "04", "05"]]
    elif mutation == "cancelled_in_operations":
        first.update(cancelled=True, active=False, executable=False)
    elif mutation == "inactive_in_operations":
        first.update(active=False, executable=False)
    elif mutation == "continuation_unresolved":
        second["continuation"] = {"present": True, "resolved": False}
    elif mutation == "duplicate_human_bet_id":
        second["human_bet_id"] = first["human_bet_id"]
    elif mutation == "unsupported_bet_type":
        profile["capabilities"]["supported_bet_types"] = ["normal"]
    elif mutation == "unsupported_special":
        second["special_play"] = {
            "kind": "half_car",
            "raw_text": "各半車",
            "scope": "bet",
            "resolved": True,
        }
        profile["capabilities"]["supported_special_play_kinds"] = ["none"]
    elif mutation == "continuation_unsupported":
        profile["capabilities"]["supports_continuation"] = False
    elif mutation == "multiple_rules_unsupported":
        profile["capabilities"]["supports_multiple_multiplier_rules"] = False
    elif mutation == "active_limit":
        profile["capabilities"]["maximum_active_bets"] = 1
    elif mutation == "group_limit":
        profile["capabilities"]["maximum_number_groups_per_bet"] = 1
    elif mutation == "number_limit":
        profile["capabilities"]["maximum_numbers_per_group"] = 1
    else:  # pragma: no cover - the matrix above is closed
        raise AssertionError((matrix_id, mutation))

    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"]._compile_plan(validation, profile)
    assert caught.value.code == expected_code, matrix_id
    assert not any(Path(fixture["prepares"].base_dir, "artifacts").glob("*.json"))


def test_fully_rehashed_persisted_plan_cannot_replace_live_candidate_values(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    created = _create(fixture)
    original = created["artifact"]
    root = Path(fixture["prepares"].base_dir)
    prepare_id = original["prepare_id"]

    artifact = deepcopy(original)
    artifact["logical_plan"]["operations"][0]["number_groups"] = [["07", "02"]]
    artifact = _rehash_artifact(artifact)
    _write_json(root / "artifacts" / f"{prepare_id}.json", artifact)
    _write_json(
        root / "artifact-commits" / f"{prepare_id}.json",
        {
            "prepare_id": prepare_id,
            "authority_binding_hash": artifact["authority_binding_hash"],
            "record_integrity_hash": artifact["record_integrity_hash"],
        },
    )

    event_path = root / "lifecycle-events" / prepare_id / "000001.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event.update(
        authority_binding_hash=artifact["authority_binding_hash"],
        deterministic_plan_hash=artifact["deterministic_plan_hash"],
        prepare_record_integrity_hash=artifact["record_integrity_hash"],
    )
    event = _rehash_event(event)
    _write_json(event_path, event)

    transaction_path = next((root / "prepare-transactions").glob("*.json"))
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction["artifact"] = artifact
    transaction["prepared_event"] = event
    _write_json(transaction_path, transaction)
    tuple_path = next((root / "authority-tuple-index").glob("*.json"))
    tuple_record = json.loads(tuple_path.read_text(encoding="utf-8"))
    tuple_record["record_integrity_hash"] = artifact["record_integrity_hash"]
    _write_json(tuple_path, tuple_record)
    idempotency_path = next((root / "idempotency").glob("*.json"))
    idempotency = json.loads(idempotency_path.read_text(encoding="utf-8"))
    idempotency["record_integrity_hash"] = artifact["record_integrity_hash"]
    _write_json(idempotency_path, idempotency)

    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"].get_prepare_state(
            prepare_id,
            claim_generation=fixture["claimed"]["claim_generation"],
            fencing_token=fixture["claimed"]["fencing_token"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )
    assert caught.value.code == "PREPARE_HASH_MISMATCH"
    assert fixture["candidate"]["active_bets"][0]["number_groups"] == [["01", "02"]]


def test_missing_target_profile_is_exact_not_found_and_creates_no_artifact(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    request = deepcopy(fixture["request"])
    request["target_profile_id"] = "wtp-missing-profile"
    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture, request)
    assert caught.value.code == "PREPARE_TARGET_PROFILE_NOT_FOUND"
    assert not list((Path(fixture["prepares"].base_dir) / "artifacts").glob("*.json"))


def test_prepare_expiry_maps_exact_claim_terminal_and_keeps_artifact_immutable(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepared = _create(fixture)
    artifact_before = deepcopy(prepared["artifact"])
    fixture["clock"].advance(61)
    state = fixture["prepares"].get_prepare_state(
        artifact_before["prepare_id"],
        claim_generation=fixture["claimed"]["claim_generation"],
        fencing_token=fixture["claimed"]["fencing_token"],
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert state["status"] == "EXPIRED"
    assert state["state"] == "EXPIRED"
    assert state["lifecycle_event"]["upstream_state"] == "EXPIRED"
    assert state["artifact"] == artifact_before
    assert [event["event_type"] for event in fixture["prepares"].get_lifecycle_events(
        artifact_before["prepare_id"]
    )] == ["PREPARED", "EXPIRED"]


def test_live_human_answer_change_blocks_claim_queue_and_prepare(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepared = _create(fixture)
    authority = fixture["authority"]
    review_id = fixture["candidate"]["source"]["review_session_id"]
    review = authority.get_human_review(review_id)
    assert review is not None
    replacement = deepcopy(review["bets"])
    replacement[0]["number_groups"] = [["07", "02"]]
    authority.replace_human_review(
        review_session_id=review_id,
        expected_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        bets=replacement,
        machine_evidence_refs=None,
        blocking_unresolved_count=0,
        actor="gate3c0-human-edit",
    )

    state = fixture["prepares"].get_prepare_state(
        prepared["artifact"]["prepare_id"],
        claim_generation=fixture["claimed"]["claim_generation"],
        fencing_token=fixture["claimed"]["fencing_token"],
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert state["status"] == "AUTHORITY_BLOCKED"
    assert state["state"] == "AUTHORITY_BLOCKED"
    assert fixture["queue"].get_entry(
        fixture["queued"]["queue_entry"]["queue_entry_id"]
    )["state"] == "BLOCKED"
    assert fixture["claims"].get_claim_state(fixture["claimed"]["claim_id"])[
        "state"
    ] == "AUTHORITY_BLOCKED"


def test_human_answer_change_before_prepare_is_candidate_invalid_zero_write(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    authority = fixture["authority"]
    review_id = fixture["candidate"]["source"]["review_session_id"]
    review = authority.get_human_review(review_id)
    assert review is not None
    replacement = deepcopy(review["bets"])
    replacement[0]["number_groups"] = [["07", "02"]]
    authority.replace_human_review(
        review_session_id=review_id,
        expected_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        bets=replacement,
        machine_evidence_refs=None,
        blocking_unresolved_count=0,
        actor="gate3c0-human-edit-before-prepare",
    )
    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture)
    assert caught.value.code == "PREPARE_CANDIDATE_INVALID"
    assert not list((Path(fixture["prepares"].base_dir) / "artifacts").glob("*.json"))


def test_trusted_revoke_is_terminal_and_third_transition_is_store_corruption(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepares = WebfillPrepareStore(
        fixture["prepares"].base_dir,
        fixture["queue"],
        fixture["claims"],
        fixture["profiles"],
        administrative_principal_validator=lambda principal: principal == "security-admin",
    )
    fixture["prepares"] = prepares
    artifact = _create(fixture)["artifact"]
    payload = {
        "schema_version": "vision-webfill-prepare-revoke-request-v1",
        "prepare_id": artifact["prepare_id"],
        "reason_code": "security_revocation",
    }
    revoked = prepares.revoke_prepare(payload, authenticated_principal="security-admin")
    replay = prepares.revoke_prepare(payload, authenticated_principal="security-admin")
    assert revoked["status"] == "REVOKED"
    assert replay["replayed"] is True
    conflicting = deepcopy(payload)
    conflicting["reason_code"] = "different_reason"
    with pytest.raises(WebfillPrepareError) as caught:
        prepares.revoke_prepare(conflicting, authenticated_principal="security-admin")
    assert caught.value.code == "PREPARE_IDEMPOTENCY_CONFLICT"

    events = prepares.get_lifecycle_events(artifact["prepare_id"])
    third = deepcopy(events[-1])
    third["event_id"] = "vwpe-" + "f" * 32
    third["event_sequence"] = 3
    third = _rehash_event(third)
    _write_json(
        Path(prepares.base_dir)
        / "lifecycle-events"
        / artifact["prepare_id"]
        / "000003.json",
        third,
    )
    with pytest.raises(WebfillPrepareError) as corrupted:
        prepares.get_lifecycle_events(artifact["prepare_id"])
    assert corrupted.value.code == "PREPARE_STORE_CORRUPT"


@pytest.mark.parametrize(
    "legacy_field",
    ["manual_candidate_id", "approved_fill_queue", "queue_path", "webfill", "fill_plan"],
)
def test_legacy_or_execution_authority_cannot_enter_prepare_request(
    tmp_path: Path, legacy_field: str
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    request = deepcopy(fixture["request"])
    request[legacy_field] = "forbidden"
    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture, request)
    assert caught.value.code == "LEGACY_WEBFILL_INTEROP_FORBIDDEN"
    assert not list((Path(fixture["prepares"].base_dir) / "artifacts").glob("*.json"))


def test_prepare_public_surface_has_no_execute_fill_submit_or_complete_method() -> None:
    forbidden = {
        "execute",
        "fill",
        "submit",
        "complete",
        "mark_completed",
        "compile_adapter",
    }
    assert forbidden.isdisjoint(set(dir(WebfillPrepareStore)))


def test_prepare_module_has_no_browser_executor_submit_or_external_provider_import() -> None:
    import betguard.vision.webfill_prepare as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden_import_fragments = {
        "webfill_executor",
        "approved_fill",
        "manual_candidate",
        "browser",
        "selenium",
        "playwright",
        "qwen",
        "dashscope",
        "requests",
        "urllib",
    }
    assert all(
        fragment not in imported
        for imported in imports
        for fragment in forbidden_import_fragments
    )
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {
        "execute",
        "fill",
        "submit",
        "complete",
        "mark_completed",
        "urlopen",
        "post",
    }.isdisjoint(called_attributes)


def test_restart_after_full_commit_reads_byte_identical_artifact_and_event(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    created = _create(fixture)
    prepare_id = created["artifact"]["prepare_id"]
    persisted_before = _tree_bytes(Path(fixture["prepares"].base_dir))

    authority = VisionCandidateAuthorityStore(fixture["authority"].base_dir)
    validator = CandidateConsumptionAuthorityValidator(authority)
    queue = ValidatedCandidateQueueStore(fixture["queue"].base_dir, validator)
    claims = ValidatedCandidateClaimStore(
        queue.claim_store_root,
        queue,
        validator,
        clock=fixture["clock"],
        lease_seconds=60,
        maximum_total_seconds=300,
        owner_session_validator=lambda _principal, _consumer, _session: True,
    )
    profiles = WebfillTargetProfileStore(fixture["profiles"].base_dir)
    restarted = WebfillPrepareStore(
        fixture["prepares"].base_dir, queue, claims, profiles
    )
    state = restarted.get_prepare_state(
        prepare_id,
        claim_generation=fixture["claimed"]["claim_generation"],
        fencing_token=fixture["claimed"]["fencing_token"],
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert state["status"] == "VALID_PREPARED"
    assert state["artifact"] == created["artifact"]
    assert state["lifecycle_event"] == created["lifecycle_event"]
    assert _tree_bytes(Path(restarted.base_dir)) == persisted_before


def test_clean_recompile_is_deterministic_and_ignores_machine_evidence_values(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    validation = deepcopy(fixture["claimed"]["validation"])
    compiler_a = fixture["prepares"]
    compiler_b = WebfillPrepareStore(
        tmp_path / "independent-compiler",
        fixture["queue"],
        fixture["claims"],
        fixture["profiles"],
    )
    plan_a = compiler_a._compile_plan(validation, fixture["profile"])
    validation["machine_evidence_refs"] = [
        {
            "provider_id": "untrusted-machine",
            "numbers": [["39"]],
            "multiplier": "4X99",
        }
    ]
    plan_b = compiler_b._compile_plan(validation, fixture["profile"])
    assert plan_b == plan_a
    assert canonical_sha256(plan_b) == canonical_sha256(plan_a)
    assert "untrusted-machine" not in str(plan_b)
    assert "4X99" not in str(plan_b)


@pytest.mark.parametrize("event_type", ["STALE", "INVALIDATED", "REVOKED"])
def test_candidate_terminal_before_prepare_is_candidate_invalid_zero_write(
    tmp_path: Path, event_type: str
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    candidate = fixture["candidate"]
    if event_type == "INVALIDATED":
        fixture["authority"].append_lifecycle_event(
            candidate_id=candidate["candidate_id"],
            revision=candidate["revision"],
            event_type="STALE",
            reason_code="gate3c0_stale",
            actor="gate3c0-auditor",
        )
    elif event_type == "REVOKED":
        fixture["authority"].append_lifecycle_event(
            candidate_id=candidate["candidate_id"],
            revision=candidate["revision"],
            event_type="INVALIDATED",
            reason_code="gate3c0_invalid",
            actor="gate3c0-auditor",
        )
    fixture["authority"].append_lifecycle_event(
        candidate_id=candidate["candidate_id"],
        revision=candidate["revision"],
        event_type=event_type,
        reason_code=f"gate3c0_{event_type.lower()}",
        actor="gate3c0-auditor",
    )

    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture)
    assert caught.value.code == "PREPARE_CANDIDATE_INVALID"
    assert not list((Path(fixture["prepares"].base_dir) / "artifacts").glob("*.json"))


def test_old_claim_generation_is_permanently_rejected_after_new_generation(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    old_request = deepcopy(fixture["request"])
    _release_claim(fixture)
    fixture["clock"].advance(1)
    key = "qik-" + "7" * 32
    action = fixture["claims"].bind_claim_action(
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
        idempotency_key=key,
    )
    new_claim = fixture["claims"].claim_next(
        {"action_id": action["action_id"], "idempotency_key": key},
        authenticated_principal=_PRINCIPAL,
        consumer_id=_CONSUMER,
        server_session_id=_SESSION,
    )
    assert new_claim is not None
    assert new_claim["claim_generation"] == fixture["claimed"]["claim_generation"] + 1
    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture, old_request)
    assert caught.value.code == "PREPARE_CLAIM_INVALID"
    assert fixture["claims"].get_claim_state(new_claim["claim_id"])["state"] == "ACTIVE"
    assert not list((Path(fixture["prepares"].base_dir) / "artifacts").glob("*.json"))


def test_restart_with_unrecoverable_owner_session_cannot_create_prepare(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    validator = CandidateConsumptionAuthorityValidator(fixture["authority"])
    restarted_queue = ValidatedCandidateQueueStore(
        fixture["queue"].base_dir, validator
    )
    restarted_claims = ValidatedCandidateClaimStore(
        restarted_queue.claim_store_root,
        restarted_queue,
        validator,
        clock=fixture["clock"],
        lease_seconds=60,
        maximum_total_seconds=300,
        owner_session_validator=lambda _principal, _consumer, _session: False,
    )
    restarted = WebfillPrepareStore(
        fixture["prepares"].base_dir,
        restarted_queue,
        restarted_claims,
        fixture["profiles"],
    )
    fixture["prepares"] = restarted
    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture)
    assert caught.value.code == "PREPARE_CLAIM_INVALID"
    assert not list((Path(restarted.base_dir) / "artifacts").glob("*.json"))


def _write_lifecycle_transaction(
    prepares: WebfillPrepareStore, event: dict[str, Any]
) -> None:
    _write_json(
        Path(prepares.base_dir)
        / "lifecycle-transactions"
        / f"{event['prepare_id']}-{event['event_type']}.json",
        {
            "schema_version": "vision-webfill-prepare-lifecycle-transaction-v1",
            "event": event,
        },
    )


def test_self_hashed_lifecycle_transaction_authority_mismatch_preflights_before_clock(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    artifact = _create(fixture)["artifact"]
    clock_path = Path(fixture["claims"].base_dir) / "clock" / "state.json"
    clock_before = clock_path.read_bytes()
    event_dir = Path(fixture["prepares"].base_dir) / "lifecycle-events" / artifact["prepare_id"]
    prepared_event_before = (event_dir / "000001.json").read_bytes()
    event = fixture["prepares"]._event(
        artifact,
        2,
        "AUTHORITY_BLOCKED",
        fixture["clock"]().isoformat(),
        "gate3c0_authority",
        "candidate_stale",
        "STALE",
    )
    event["deterministic_plan_hash"] = "0" * 64
    event = _rehash_event(event)
    _write_lifecycle_transaction(fixture["prepares"], event)

    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"].get_prepare_state(
            artifact["prepare_id"],
            claim_generation=fixture["claimed"]["claim_generation"],
            fencing_token=fixture["claimed"]["fencing_token"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )
    assert caught.value.code == "PREPARE_STORE_CORRUPT"
    assert clock_path.read_bytes() == clock_before
    assert (event_dir / "000001.json").read_bytes() == prepared_event_before
    assert not (event_dir / "000002.json").exists()


def test_conflicting_pending_terminal_journals_fail_before_clock_or_event_write(
    tmp_path: Path,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    artifact = _create(fixture)["artifact"]
    clock_path = Path(fixture["claims"].base_dir) / "clock" / "state.json"
    clock_before = clock_path.read_bytes()
    for event_type, upstream in (
        ("AUTHORITY_BLOCKED", "BLOCKED"),
        ("EXPIRED", "EXPIRED"),
    ):
        event = fixture["prepares"]._event(
            artifact,
            2,
            event_type,
            fixture["clock"]().isoformat(),
            "gate3c0_authority",
            f"conflicting_{event_type.lower()}",
            upstream,
        )
        _write_lifecycle_transaction(fixture["prepares"], event)

    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"].get_prepare_state(
            artifact["prepare_id"],
            claim_generation=fixture["claimed"]["claim_generation"],
            fencing_token=fixture["claimed"]["fencing_token"],
            authenticated_principal=_PRINCIPAL,
            consumer_id=_CONSUMER,
            server_session_id=_SESSION,
        )
    assert caught.value.code == "PREPARE_STORE_CORRUPT"
    assert clock_path.read_bytes() == clock_before
    assert not (Path(fixture["prepares"].base_dir) / "lifecycle-events" / artifact["prepare_id"] / "000002.json").exists()


@pytest.mark.parametrize(
    "partial_kind", ["artifact", "event", "idempotency", "tuple", "commit"]
)
def test_valid_pending_prepare_with_conflicting_partial_record_fails_before_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partial_kind: str,
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    prepares = fixture["prepares"]
    original_resume = prepares._resume_transaction

    def crash_before_publication(_transaction: dict[str, Any]) -> None:
        raise RuntimeError("leave one valid pending Prepare journal")

    monkeypatch.setattr(prepares, "_resume_transaction", crash_before_publication)
    with pytest.raises(RuntimeError, match="valid pending"):
        _create(fixture)
    monkeypatch.setattr(prepares, "_resume_transaction", original_resume)
    root = Path(prepares.base_dir)
    transaction_path = next((root / "prepare-transactions").glob("*.json"))
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    prepare_id = transaction["prepare_id"]
    conflict_paths = {
        "artifact": root / "artifacts" / f"{prepare_id}.json",
        "event": root / "lifecycle-events" / prepare_id / "000001.json",
        "idempotency": root / "idempotency" / f"{transaction['idempotency_key_hash']}.json",
        "tuple": root / "authority-tuple-index" / f"{transaction['authority_tuple_hash']}.json",
        "commit": root / "artifact-commits" / f"{prepare_id}.json",
    }
    target = conflict_paths[partial_kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target, {"conflicting_partial": partial_kind})
    clock_path = Path(fixture["claims"].base_dir) / "clock" / "state.json"
    clock_before = clock_path.read_bytes()
    prepare_before = _tree_bytes(root)

    with pytest.raises(WebfillPrepareError) as caught:
        _create(fixture)
    assert caught.value.code == "PREPARE_STORE_CORRUPT"
    assert clock_path.read_bytes() == clock_before
    assert _tree_bytes(root) == prepare_before


@pytest.mark.parametrize(
    "mutation",
    [
        "omit_operation",
        "duplicate_operation",
        "reorder_operations",
        "reorder_groups",
        "reorder_numbers",
        "reorder_multiplier_rules",
    ],
)
def test_operation_accounting_and_order_tamper_never_matches_live_candidate(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _happy_prepare_fixture(tmp_path)
    created = _create(fixture)
    artifact = deepcopy(created["artifact"])
    operations = artifact["logical_plan"]["operations"]
    if mutation == "omit_operation":
        operations.pop()
        artifact["validation"]["validated_operation_count"] = 1
        artifact["validation"]["source_active_bet_count"] = 1
    elif mutation == "duplicate_operation":
        operations[1] = deepcopy(operations[0])
        operations[1]["operation_index"] = 2
    elif mutation == "reorder_operations":
        operations.reverse()
        for index, operation in enumerate(operations, 1):
            operation["operation_index"] = index
    elif mutation == "reorder_groups":
        operations[1]["number_groups"].reverse()
    elif mutation == "reorder_numbers":
        operations[0]["number_groups"][0].reverse()
    elif mutation == "reorder_multiplier_rules":
        operations[1]["multiplier"]["ordered_rules"].reverse()
    artifact = _rehash_artifact(artifact)
    if mutation == "duplicate_operation":
        with pytest.raises(WebfillPrepareError) as malformed:
            fixture["prepares"]._validate_artifact(artifact)
        assert malformed.value.code == "PREPARE_SCHEMA_INVALID"
        return
    fixture["prepares"]._validate_artifact(artifact)

    live_plan = fixture["prepares"]._compile_plan(
        fixture["claimed"]["validation"], fixture["profile"]
    )
    with pytest.raises(WebfillPrepareError) as caught:
        fixture["prepares"]._assert_live_projection(
            artifact, fixture["claimed"]["validation"], live_plan
        )
    assert caught.value.code == "PREPARE_NONDETERMINISTIC"
