from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from betguard.vision.candidate_authority import (
    CandidateAuthorityError,
    VisionCandidateAuthorityStore,
    canonical_json_bytes,
    canonical_sha256,
)


IMAGE_HASH = "a" * 64
EVIDENCE_HASH = "b" * 64


def _bet(
    bet_id: str = "H-007",
    *,
    groups: list[list[str]] | None = None,
    bet_type: str = "normal",
    rules: list[str] | None = None,
    resolved: bool = True,
    confirmed: bool = True,
    cancelled: bool = False,
    special_kind: str = "none",
    continuation: bool = False,
) -> dict:
    return {
        "human_bet_id": bet_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups if groups is not None else [["01", "02"]]),
        "multiplier": {
            "ordered_rules": list(rules if rules is not None else ["2X1"]),
            "scope": "bet",
            "resolved": resolved,
        },
        "special_play": {
            "kind": special_kind,
            "raw_text": None if special_kind == "none" else "尾/車/半車/各",
            "scope": None if special_kind == "none" else "bet",
            "resolved": resolved,
        },
        "continuation": {"present": continuation, "resolved": resolved},
        "cancelled": cancelled,
        "active": not cancelled,
        # Deliberately client-controlled input. The store must ignore true on
        # create and on semantically changed replacement values.
        "human_confirmed": confirmed,
    }


def _refs() -> list[dict]:
    return [
        {
            "provider_id": "qwen-dashscope",
            "model": "qwen3-vl-plus",
            "request_id": "req-1",
            "cache_hit": True,
            "evidence_hash": EVIDENCE_HASH,
            "artifact_ref": None,
            "value_authority": False,
        }
    ]


def _create_review(
    store: VisionCandidateAuthorityStore,
    bets: list[dict] | None = None,
    *,
    review_id: str = "review-1",
    image_hash: str = IMAGE_HASH,
    blockers: int = 0,
) -> dict:
    return store.create_human_review(
        review_session_id=review_id,
        source_image_id="image-1",
        source_image_hash=image_hash,
        game="539",
        bets=deepcopy(bets if bets is not None else [_bet()]),
        machine_evidence_refs=_refs(),
        blocking_unresolved_count=blockers,
        actor="review-creator",
    )


def _confirm_all(
    store: VisionCandidateAuthorityStore, review: dict, *, actor: str = "human-confirmer"
) -> dict:
    return store.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[bet["human_bet_id"] for bet in review["bets"]],
        actor=actor,
    )


def _create_candidate(store: VisionCandidateAuthorityStore, review: dict) -> dict:
    key = (
        f"{review['review_session_id']}:{review['human_answer_revision']}:"
        f"{review['human_answer_hash']}"
    )
    return store.create_candidate(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        idempotency_key=key,
        actor="candidate-api-actor",
    )


def _error_code(exc: pytest.ExceptionInfo[CandidateAuthorityError]) -> str:
    return exc.value.code


def test_canonical_serialization_normalizes_nfc_and_preserves_array_order() -> None:
    composed = {"label": "\u00e9", "groups": [["01", "02"], ["03"]]}
    decomposed = {"groups": [["01", "02"], ["03"]], "label": "e\u0301"}
    reordered = {"label": "\u00e9", "groups": [["03"], ["01", "02"]]}

    assert canonical_json_bytes(composed) == canonical_json_bytes(decomposed)
    assert canonical_sha256(composed) == canonical_sha256(decomposed)
    assert canonical_sha256(reordered) != canonical_sha256(composed)


def test_create_forces_confirmation_false_and_explicit_confirm_is_authority(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(store)

    assert review["human_answer_revision"] == 1
    assert review["bets"][0]["human_confirmed"] is False
    assert review["bets"][0]["value_authority"] == "human_answer"
    assert review["bets"][0]["executable"] is True
    assert review["all_bets_confirmed"] is False
    assert review["ready_for_candidate"] is False

    confirmed = _confirm_all(store, review)
    assert confirmed["human_answer_revision"] == 2
    assert confirmed["bets"][0]["human_confirmed"] is True
    assert confirmed["ready_for_candidate"] is True
    assert confirmed["confirmed_by"] == "human-confirmer"
    assert confirmed["confirmed_at"]

    result = _create_candidate(store, confirmed)
    candidate = result["candidate"]
    assert result["replayed"] is False
    assert candidate["authority"]["confirmed_by"] == "human-confirmer"
    assert candidate["authority"]["confirmed_at"] == confirmed["confirmed_at"]
    assert candidate["safety"] == {
        "candidate_only": True,
        "approved_for_fill": False,
        "queue_written": False,
        "auto_confirm": False,
        "auto_submit": False,
        "webfill_called": False,
    }


def test_replace_server_diff_clears_changed_or_ai_adopted_value(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    changed = [_bet(groups=[["08", "01", "04"]], confirmed=True)]

    replaced = store.replace_human_review(
        review_session_id="review-1",
        expected_revision=confirmed["human_answer_revision"],
        expected_human_answer_hash=confirmed["human_answer_hash"],
        bets=changed,
        machine_evidence_refs=_refs(),
        blocking_unresolved_count=0,
        actor="browser-gemma-adoption",
    )

    assert replaced["bets"][0]["number_groups"] == [["08", "01", "04"]]
    assert replaced["bets"][0]["human_confirmed"] is False
    assert replaced["all_bets_confirmed"] is False
    assert replaced["confirmed_by"] is None
    assert replaced["confirmed_at"] is None


def test_replace_preserves_unchanged_server_confirmation_only(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    client_bets = deepcopy(confirmed["bets"])
    for bet in client_bets:
        bet.pop("value_authority")
        bet.pop("executable")
        bet["human_confirmed"] = False

    replaced = store.replace_human_review(
        review_session_id="review-1",
        expected_revision=confirmed["human_answer_revision"],
        expected_human_answer_hash=confirmed["human_answer_hash"],
        bets=client_bets,
        machine_evidence_refs=[],
        blocking_unresolved_count=0,
        actor="metadata-update",
    )
    assert replaced["bets"][0]["human_confirmed"] is True
    assert replaced["confirmed_by"] == confirmed["confirmed_by"]
    assert replaced["confirmed_at"] == confirmed["confirmed_at"]


@pytest.mark.parametrize("mutation", ["remove", "reorder"])
def test_remove_or_reorder_requires_fresh_human_confirmation(
    tmp_path: Path, mutation: str
) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(store, [_bet("H-001"), _bet("H-002")])
    confirmed = _confirm_all(store, review)
    public_bets = []
    for bet in confirmed["bets"]:
        public = deepcopy(bet)
        public.pop("value_authority")
        public.pop("executable")
        public_bets.append(public)
    next_bets = public_bets[:1] if mutation == "remove" else list(reversed(public_bets))

    replaced = store.replace_human_review(
        review_session_id="review-1",
        expected_revision=confirmed["human_answer_revision"],
        expected_human_answer_hash=confirmed["human_answer_hash"],
        bets=next_bets,
        machine_evidence_refs=_refs(),
        blocking_unresolved_count=0,
        actor=f"human-{mutation}",
    )

    assert replaced["human_answer_revision"] == confirmed["human_answer_revision"] + 1
    assert all(bet["human_confirmed"] is False for bet in replaced["bets"])
    assert replaced["ready_for_candidate"] is False
    with pytest.raises(CandidateAuthorityError) as exc:
        _create_candidate(store, replaced)
    assert _error_code(exc) == "CANDIDATE_NOT_READY"


def test_unconfirm_is_explicit_cas_and_stales_candidate(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    created = _create_candidate(store, confirmed)["candidate"]
    snapshot_path = next((tmp_path / "candidates" / created["candidate_id"]).glob("*.json"))
    original_bytes = snapshot_path.read_bytes()

    unconfirmed = store.unconfirm_human_review_bets(
        review_session_id="review-1",
        expected_human_answer_revision=confirmed["human_answer_revision"],
        expected_human_answer_hash=confirmed["human_answer_hash"],
        human_bet_ids=["H-007"],
        actor="human-unconfirm",
    )
    assert unconfirmed["bets"][0]["human_confirmed"] is False
    assert unconfirmed["ready_for_candidate"] is False
    assert store.get_candidate_for_review("review-1")["state"] == "STALE"
    assert snapshot_path.read_bytes() == original_bytes


def test_24_of_25_confirmed_is_not_candidate_ready(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    bets = [
        _bet(f"H-{index:03d}", groups=[[f"{index:02d}"]])
        for index in range(1, 26)
    ]
    review = _create_review(store, bets)
    partial = store.confirm_human_review_bets(
        review_session_id="review-1",
        expected_human_answer_revision=1,
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[f"H-{index:03d}" for index in range(1, 25)],
        actor="human",
    )
    assert sum(bet["human_confirmed"] for bet in partial["bets"]) == 24
    assert partial["ready_for_candidate"] is False
    with pytest.raises(CandidateAuthorityError) as exc:
        _create_candidate(store, partial)
    assert _error_code(exc) == "CANDIDATE_NOT_READY"


def test_unresolved_empty_multiplier_cannot_be_confirmed(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(
        store,
        [_bet(rules=[], resolved=False)],
        blockers=1,
    )
    with pytest.raises(CandidateAuthorityError) as exc:
        _confirm_all(store, review)
    assert _error_code(exc) in {"MULTIPLIER_INVALID", "CANDIDATE_NOT_READY"}
    assert store.get_human_review("review-1") == review


def test_explicit_confirmation_resolves_complete_scope_without_inventing_values(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(store, [_bet(resolved=False)], blockers=1)
    confirmed = _confirm_all(store, review)
    bet = confirmed["bets"][0]
    assert bet["multiplier"]["ordered_rules"] == ["2X1"]
    assert bet["multiplier"]["resolved"] is True
    assert bet["special_play"]["resolved"] is True
    assert bet["continuation"]["resolved"] is True
    assert bet["executable"] is True
    assert confirmed["blocking_unresolved_count"] == 0


def test_normal_column_multiplier_continuation_special_and_cancelled_preserved(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    bets = [
        _bet("H-007", groups=[["01", "02"]], rules=["2X2", "3X5"]),
        _bet(
            "H-008",
            groups=[["21", "35"], ["23"], ["34"], ["37"]],
            bet_type="column",
            rules=["2/3X1"],
            continuation=True,
            special_kind="structured_human_play",
        ),
        _bet("H-009", groups=[], rules=[], resolved=False, cancelled=True),
    ]
    review = _create_review(store, bets)
    confirmed = _confirm_all(store, review)
    candidate = _create_candidate(store, confirmed)["candidate"]

    assert candidate["active_bets"][0]["number_groups"] == [["01", "02"]]
    assert candidate["active_bets"][0]["multiplier"]["ordered_rules"] == ["2X2", "3X5"]
    assert candidate["active_bets"][1]["number_groups"] == [
        ["21", "35"], ["23"], ["34"], ["37"]
    ]
    assert candidate["active_bets"][1]["continuation"]["present"] is True
    assert candidate["active_bets"][1]["special_play"]["kind"] == "structured_human_play"
    assert candidate["cancelled_audit"][0]["human_bet_id"] == "H-009"
    assert candidate["cancelled_audit"][0]["active"] is False
    assert candidate["cancelled_audit"][0]["executable"] is False


def test_stale_revision_hash_is_rejected_without_mutation(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(store)
    before = json.dumps(store.get_human_review("review-1"), sort_keys=True)
    with pytest.raises(CandidateAuthorityError) as exc:
        store.replace_human_review(
            review_session_id="review-1",
            expected_revision=99,
            expected_human_answer_hash=review["human_answer_hash"],
            bets=[_bet()],
            machine_evidence_refs=_refs(),
            blocking_unresolved_count=0,
            actor="stale-browser",
        )
    assert _error_code(exc) == "REVIEW_STALE"
    assert json.dumps(store.get_human_review("review-1"), sort_keys=True) == before


def test_candidate_idempotency_and_reload(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    first = _create_candidate(store, confirmed)
    second = _create_candidate(VisionCandidateAuthorityStore(tmp_path), confirmed)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["candidate"] == first["candidate"]
    assert len(store.get_lifecycle_events(first["candidate"]["candidate_id"])) == 1


def test_candidate_revision_stale_then_superseded_is_append_only(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed1 = _confirm_all(store, _create_review(store))
    candidate1 = _create_candidate(store, confirmed1)["candidate"]
    snapshot1 = next((tmp_path / "candidates" / candidate1["candidate_id"]).glob("*.json"))
    bytes1 = snapshot1.read_bytes()

    changed = store.replace_human_review(
        review_session_id="review-1",
        expected_revision=confirmed1["human_answer_revision"],
        expected_human_answer_hash=confirmed1["human_answer_hash"],
        bets=[_bet(groups=[["03", "04"]])],
        machine_evidence_refs=_refs(),
        blocking_unresolved_count=0,
        actor="human-edit",
    )
    assert store.get_candidate_for_review("review-1")["state"] == "STALE"
    confirmed2 = _confirm_all(store, changed)
    candidate2 = _create_candidate(store, confirmed2)["candidate"]
    assert candidate2["candidate_id"] == candidate1["candidate_id"]
    assert candidate2["revision"] == 2
    assert candidate2["previous_revision_hash"] == candidate1["canonical_content_hash"]
    assert snapshot1.read_bytes() == bytes1
    events = store.get_lifecycle_events(candidate1["candidate_id"])
    assert [event["event_type"] for event in events] == [
        "CREATED", "STALE", "SUPERSEDED", "CREATED"
    ]


def test_old_idempotency_tuple_cannot_replay_after_human_answer_changes(
    tmp_path: Path,
) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    _create_candidate(store, confirmed)
    store.replace_human_review(
        review_session_id="review-1",
        expected_revision=confirmed["human_answer_revision"],
        expected_human_answer_hash=confirmed["human_answer_hash"],
        bets=[_bet(groups=[["03", "04"]])],
        machine_evidence_refs=_refs(),
        blocking_unresolved_count=0,
        actor="human-edit",
    )

    with pytest.raises(CandidateAuthorityError) as exc:
        _create_candidate(store, confirmed)
    assert _error_code(exc) == "REVIEW_STALE"


def test_candidate_content_hash_excludes_volatile_and_machine_refs(tmp_path: Path) -> None:
    first_store = VisionCandidateAuthorityStore(tmp_path / "one")
    second_store = VisionCandidateAuthorityStore(tmp_path / "two")
    first = _create_candidate(first_store, _confirm_all(first_store, _create_review(first_store)))["candidate"]
    second_review = _create_review(second_store, review_id="different-review")
    second = _create_candidate(second_store, _confirm_all(second_store, second_review))["candidate"]
    assert first["candidate_id"] != second["candidate_id"]
    assert first["canonical_content_hash"] == second["canonical_content_hash"]

    third_store = VisionCandidateAuthorityStore(tmp_path / "three")
    changed_image = _create_review(third_store, image_hash="c" * 64)
    third = _create_candidate(third_store, _confirm_all(third_store, changed_image))["candidate"]
    assert third["canonical_content_hash"] != first["canonical_content_hash"]


def test_malicious_machine_authority_and_blocker_claim_fail_closed(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    refs = _refs()
    refs[0]["value_authority"] = True
    with pytest.raises(CandidateAuthorityError) as exc:
        store.create_human_review(
            review_session_id="bad",
            source_image_id="image-1",
            source_image_hash=IMAGE_HASH,
            game="539",
            bets=[_bet()],
            machine_evidence_refs=refs,
            blocking_unresolved_count=0,
            actor="attacker",
        )
    assert _error_code(exc) == "MACHINE_VALUE_AUTHORITY_FORBIDDEN"

    with pytest.raises(CandidateAuthorityError) as exc:
        _create_review(
            store,
            [_bet(rules=[], resolved=False)],
            review_id="wrong-blockers",
            blockers=0,
        )
    assert _error_code(exc) == "BLOCKING_UNRESOLVED_MISMATCH"


def test_incomplete_candidate_transaction_is_invisible_and_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    real_write = store._write_idempotency

    def fail_write(*args, **kwargs):
        raise RuntimeError("simulated disk interruption")

    monkeypatch.setattr(store, "_write_idempotency", fail_write)
    with pytest.raises(RuntimeError, match="interruption"):
        _create_candidate(store, confirmed)
    assert store.get_candidate_for_review("review-1") is None

    monkeypatch.setattr(store, "_write_idempotency", real_write)
    recovered = _create_candidate(store, confirmed)
    assert recovered["candidate"]["revision"] == 1
    assert store.get_candidate_for_review("review-1")["state"] == "CURRENT"


def test_review_identity_mismatch_is_stale_even_if_lifecycle_append_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    candidate = _create_candidate(store, confirmed)["candidate"]

    def fail_stale(*args, **kwargs):
        raise RuntimeError("simulated lifecycle interruption")

    monkeypatch.setattr(store, "_mark_current_candidate_stale", fail_stale)
    with pytest.raises(RuntimeError, match="lifecycle interruption"):
        store.replace_human_review(
            review_session_id="review-1",
            expected_revision=confirmed["human_answer_revision"],
            expected_human_answer_hash=confirmed["human_answer_hash"],
            bets=[_bet(groups=[["03", "04"]])],
            machine_evidence_refs=_refs(),
            blocking_unresolved_count=0,
            actor="human-edit",
        )

    reloaded = VisionCandidateAuthorityStore(tmp_path)
    current_review = reloaded.get_human_review("review-1")
    assert current_review["human_answer_revision"] == confirmed["human_answer_revision"] + 1
    assert reloaded.get_candidate_for_review("review-1") == {
        "candidate": candidate,
        "state": "STALE",
    }


def test_immutable_snapshot_tamper_fails_closed(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    candidate = _create_candidate(store, _confirm_all(store, _create_review(store)))["candidate"]
    path = next((tmp_path / "candidates" / candidate["candidate_id"]).glob("*.json"))
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["active_bets"][0]["number_groups"] = [["39"]]
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CandidateAuthorityError) as exc:
        store.get_candidate(candidate["candidate_id"], 1)
    assert _error_code(exc) == "CANDIDATE_SCHEMA_INVALID"


def test_no_queue_draft_webfill_or_manual_registry_dependency() -> None:
    source = Path("src/betguard/vision/candidate_authority.py").read_text(encoding="utf-8")
    for forbidden in ("approved_fill_queue", "_manual_candidates", "webfill executor"):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("sample_id", "bet"),
    [
        (
            "sample-007",
            _bet(groups=[["03", "16", "27"]], rules=["2X1"]),
        ),
        (
            "sample-008",
            _bet(
                groups=[["08"], ["01", "04"]],
                bet_type="column",
                rules=["2X5"],
                continuation=True,
            ),
        ),
        (
            "sample-010",
            _bet(groups=[["32", "34", "35"]], rules=["2X2", "3X5"]),
        ),
        (
            "sample-011",
            _bet(
                groups=[["21", "35"], ["23"], ["34"], ["37"]],
                bet_type="column",
                rules=["3/4X1"],
                continuation=True,
            ),
        ),
        (
            "sample-014",
            _bet(
                groups=[["34"], ["03", "13"], ["16", "36"]],
                bet_type="column",
                rules=["2X1"],
                special_kind="structured_human_play",
            ),
        ),
    ],
)
def test_representative_sample_structures_round_trip_without_flattening(
    tmp_path: Path, sample_id: str, bet: dict
) -> None:
    store = VisionCandidateAuthorityStore(tmp_path / sample_id)
    review = _create_review(store, [bet], review_id=f"review-{sample_id}")
    candidate = _create_candidate(store, _confirm_all(store, review))["candidate"]
    actual = candidate["active_bets"][0]
    assert actual["number_groups"] == bet["number_groups"]
    assert actual["multiplier"]["ordered_rules"] == bet["multiplier"]["ordered_rules"]
    assert actual["continuation"]["present"] == bet["continuation"]["present"]
    assert actual["special_play"]["kind"] == bet["special_play"]["kind"]


def test_empty_number_group_and_invalid_multiplier_fail_closed(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    with pytest.raises(CandidateAuthorityError) as exc:
        _create_review(store, [_bet(groups=[[]])])
    assert _error_code(exc) == "COLUMN_STRUCTURE_INVALID"

    invalid = _bet(rules=["2/3"], resolved=False)
    with pytest.raises(CandidateAuthorityError) as exc:
        _create_review(store, [invalid], review_id="invalid-multiplier", blockers=1)
    assert _error_code(exc) == "MULTIPLIER_INVALID"


def test_candidate_endpoint_contract_cannot_accept_client_bets(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    confirmed = _confirm_all(store, _create_review(store))
    with pytest.raises(TypeError):
        store.create_candidate(
            review_session_id="review-1",
            expected_human_answer_revision=confirmed["human_answer_revision"],
            expected_human_answer_hash=confirmed["human_answer_hash"],
            idempotency_key=(
                f"review-1:{confirmed['human_answer_revision']}:"
                f"{confirmed['human_answer_hash']}"
            ),
            actor="attacker",
            bets=[_bet(groups=[["39"]])],  # type: ignore[call-arg]
        )


def test_wrong_hash_and_path_traversal_fail_closed(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    review = _create_review(store)
    with pytest.raises(CandidateAuthorityError) as exc:
        store.confirm_human_review_bets(
            review_session_id="review-1",
            expected_human_answer_revision=1,
            expected_human_answer_hash="c" * 64,
            human_bet_ids=["H-007"],
            actor="stale-browser",
        )
    assert _error_code(exc) == "REVIEW_STALE"
    with pytest.raises(CandidateAuthorityError) as exc:
        store.get_candidate("../escape", 1)
    assert _error_code(exc) == "CANDIDATE_SCHEMA_INVALID"


def test_append_only_invalidated_and_revoked_lifecycle(tmp_path: Path) -> None:
    store = VisionCandidateAuthorityStore(tmp_path)
    candidate = _create_candidate(store, _confirm_all(store, _create_review(store)))["candidate"]
    store.append_lifecycle_event(
        candidate_id=candidate["candidate_id"],
        revision=1,
        event_type="INVALIDATED",
        reason_code="authority_integrity_failure",
        actor="auditor",
    )
    assert store.get_candidate_for_review("review-1")["state"] == "INVALIDATED"
    store.append_lifecycle_event(
        candidate_id=candidate["candidate_id"],
        revision=1,
        event_type="REVOKED",
        reason_code="human_revocation",
        actor="human",
    )
    assert store.get_candidate_for_review("review-1")["state"] == "REVOKED"
    with pytest.raises(CandidateAuthorityError) as exc:
        store.append_lifecycle_event(
            candidate_id=candidate["candidate_id"],
            revision=1,
            event_type="INVALIDATED",
            reason_code="repeat",
            actor="auditor",
        )
    assert _error_code(exc) == "LIFECYCLE_CONFLICT"
