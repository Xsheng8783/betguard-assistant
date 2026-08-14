"""Independent Gate 3B-2 Candidate consumer-boundary regressions.

These tests deliberately exercise only the read-only consumption validator.
They never create an approved-fill queue or invoke a fill/webfill consumer.
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from betguard.vision.candidate_authority import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateAuthorityError,
    VisionCandidateAuthorityStore,
    canonical_sha256,
)
from betguard.vision.candidate_consumption import (
    CandidateConsumptionAuthorityValidator,
)
from betguard.webui import app as webui_app


IMAGE_HASH = "a" * 64
EVIDENCE_HASH = "b" * 64


def _bet(
    human_bet_id: str = "H-001",
    *,
    bet_type: str = "normal",
    groups: list[list[str]] | None = None,
    rules: list[str] | None = None,
    special_kind: str = "none",
    special_raw: str | None = None,
    special_scope: str | None = None,
    continuation: bool = False,
    cancelled: bool = False,
) -> dict[str, Any]:
    return {
        "human_bet_id": human_bet_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups if groups is not None else [["01", "02"]]),
        "multiplier": {
            "ordered_rules": list(rules if rules is not None else ["2X1"]),
            "scope": "bet",
            "resolved": False,
        },
        "special_play": {
            "kind": special_kind,
            "raw_text": special_raw,
            "scope": special_scope,
            "resolved": False,
        },
        "continuation": {"present": continuation, "resolved": False},
        "cancelled": cancelled,
        "active": not cancelled,
        # This input is untrusted. Explicit server confirmation below is the
        # only operation allowed to make it authoritative.
        "human_confirmed": True,
    }


def _machine_refs() -> list[dict[str, Any]]:
    return [
        {
            "provider_id": "qwen-dashscope",
            "model": "qwen3-vl-plus",
            "request_id": "cached-request",
            "cache_hit": True,
            "evidence_hash": EVIDENCE_HASH,
            "artifact_ref": None,
            "value_authority": False,
        }
    ]


def _create_current_candidate(
    root: Path,
    *,
    bets: list[dict[str, Any]] | None = None,
    review_id: str = "review-gate3b2",
    machine_refs: list[dict[str, Any]] | None = None,
) -> tuple[VisionCandidateAuthorityStore, dict[str, Any], dict[str, Any]]:
    store = VisionCandidateAuthorityStore(root)
    supplied_bets = deepcopy(bets if bets is not None else [_bet()])
    review = store.create_human_review(
        review_session_id=review_id,
        source_image_id=f"image-{review_id}",
        source_image_hash=IMAGE_HASH,
        game="539",
        bets=supplied_bets,
        machine_evidence_refs=deepcopy(
            _machine_refs() if machine_refs is None else machine_refs
        ),
        blocking_unresolved_count=sum(
            1 for bet in supplied_bets if bet["active"] is True
        ),
        actor="gate3b2-reviewer",
    )
    review = store.confirm_human_review_bets(
        review_session_id=review_id,
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[bet["human_bet_id"] for bet in review["bets"]],
        actor="gate3b2-human",
    )
    created = store.create_candidate(
        review_session_id=review_id,
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        idempotency_key=(
            f"{review_id}:{review['human_answer_revision']}:"
            f"{review['human_answer_hash']}"
        ),
        actor="gate3b2-candidate-factory",
    )
    return store, review, created["candidate"]


def _validate(
    validator: CandidateConsumptionAuthorityValidator,
    candidate: dict[str, Any],
    *,
    include_expected: bool = True,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"candidate_id": candidate["candidate_id"]}
    if include_expected:
        kwargs.update(
            expected_candidate_revision=candidate["revision"],
            expected_content_hash=candidate["canonical_content_hash"],
        )
    return validator.validate(**kwargs)


def _assert_error(
    code: str, action: Callable[[], object]
) -> CandidateAuthorityError:
    with pytest.raises(CandidateAuthorityError) as caught:
        action()
    assert caught.value.code == code, (
        f"actual={caught.value.code}: {caught.value.message}"
    )
    return caught.value


def _snapshot_path(root: Path, candidate: dict[str, Any]) -> Path:
    return (
        root
        / "candidates"
        / candidate["candidate_id"]
        / f"revision-{candidate['revision']:06d}.json"
    )


def _commit_path(root: Path, candidate: dict[str, Any]) -> Path:
    return (
        root
        / "candidate-commits"
        / candidate["candidate_id"]
        / f"revision-{candidate['revision']:06d}.json"
    )


def _rewrite_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _rewrite_content_identity(root: Path, candidate: dict[str, Any]) -> None:
    projection = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "game": candidate["game"],
        "source_image_hash": candidate["source"]["source_image_hash"],
        "active_bets": candidate["active_bets"],
        "cancelled_audit": candidate["cancelled_audit"],
        "safety": candidate["safety"],
    }
    candidate["canonical_content_hash"] = canonical_sha256(projection)
    _rewrite_json(_snapshot_path(root, candidate), candidate)

    commit_path = _commit_path(root, candidate)
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    commit["canonical_content_hash"] = candidate["canonical_content_hash"]
    _rewrite_json(commit_path, commit)

    event_dir = root / "candidate-events" / candidate["candidate_id"]
    for path in event_dir.glob("*.json"):
        event = json.loads(path.read_text(encoding="utf-8"))
        if event["candidate_revision"] == candidate["revision"]:
            event["candidate_content_hash"] = candidate["canonical_content_hash"]
            _rewrite_json(path, event)


def _tamper_snapshot(
    root: Path,
    candidate: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
    *,
    rewrite_identity: bool = False,
) -> dict[str, Any]:
    stored = json.loads(_snapshot_path(root, candidate).read_text(encoding="utf-8"))
    mutate(stored)
    if rewrite_identity:
        _rewrite_content_identity(root, stored)
    else:
        _rewrite_json(_snapshot_path(root, candidate), stored)
    return stored


# K01: a complete CURRENT Candidate is the only successful state.
def test_k01_current_candidate_returns_exact_read_only_envelope(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    before = store.get_candidate(candidate["candidate_id"], candidate["revision"])
    disk_before = _tree_bytes(tmp_path)
    manual_before = deepcopy(webui_app._manual_candidates)

    envelope = _validate(
        CandidateConsumptionAuthorityValidator(store), candidate, include_expected=False
    )

    assert envelope["validation_status"] == "VALID_CURRENT"
    assert envelope["candidate_id"] == candidate["candidate_id"]
    assert envelope["candidate_revision"] == candidate["revision"]
    assert envelope["canonical_content_hash"] == candidate["canonical_content_hash"]
    assert envelope["lifecycle_state"] == "CURRENT"
    assert envelope["bets"] == before["active_bets"]
    assert envelope["cancelled_audit"] == before["cancelled_audit"]
    assert envelope["safety"] == {
        "candidate_only": True,
        "approved_for_fill": False,
        "approved_for_queue": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    assert store.get_candidate(candidate["candidate_id"], candidate["revision"]) == before
    assert _tree_bytes(tmp_path) == disk_before
    assert webui_app._manual_candidates == manual_before


# K02-K06: identity, hash, and immutable schema failures.
def test_k02_candidate_not_found(tmp_path: Path) -> None:
    validator = CandidateConsumptionAuthorityValidator(
        VisionCandidateAuthorityStore(tmp_path)
    )
    _assert_error(
        "CANDIDATE_NOT_FOUND",
        lambda: validator.validate(candidate_id="vc-" + "0" * 32),
    )


def test_k03_wrong_expected_revision_is_stale_not_not_found(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    _assert_error(
        "CANDIDATE_STALE",
        lambda: CandidateConsumptionAuthorityValidator(store).validate(
            candidate_id=candidate["candidate_id"],
            expected_candidate_revision=candidate["revision"] + 1,
        ),
    )


def test_k04_wrong_expected_content_hash_is_rejected(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    _assert_error(
        "CANDIDATE_HASH_MISMATCH",
        lambda: CandidateConsumptionAuthorityValidator(store).validate(
            candidate_id=candidate["candidate_id"],
            expected_content_hash="c" * 64,
        ),
    )


def test_k05_stored_candidate_hash_tamper_is_hash_mismatch(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    _tamper_snapshot(
        tmp_path,
        candidate,
        lambda value: value.__setitem__("canonical_content_hash", "c" * 64),
    )
    _assert_error(
        "CANDIDATE_HASH_MISMATCH",
        lambda: _validate(CandidateConsumptionAuthorityValidator(store), candidate),
    )


def test_k06_unknown_candidate_schema_field_is_rejected(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    _tamper_snapshot(
        tmp_path, candidate, lambda value: value.__setitem__("client_bets", [])
    )
    _assert_error(
        "CANDIDATE_SCHEMA_INVALID",
        lambda: _validate(CandidateConsumptionAuthorityValidator(store), candidate),
    )


# K07-K10: each non-CURRENT lifecycle state has its own stable result.
@pytest.mark.parametrize(
    ("event_type", "expected_code"),
    [
        ("STALE", "CANDIDATE_STALE"),
        ("INVALIDATED", "CANDIDATE_INVALIDATED"),
        ("REVOKED", "CANDIDATE_REVOKED"),
    ],
)
def test_k07_k08_k10_noncurrent_lifecycle_states_are_distinct(
    tmp_path: Path, event_type: str, expected_code: str
) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    store.append_lifecycle_event(
        candidate_id=candidate["candidate_id"],
        revision=candidate["revision"],
        event_type=event_type,
        reason_code=f"gate3b2_{event_type.lower()}",
        actor="gate3b2-security-test",
    )
    _assert_error(
        expected_code,
        lambda: _validate(CandidateConsumptionAuthorityValidator(store), candidate),
    )


def test_k09_superseded_revision_is_not_collapsed_to_stale(tmp_path: Path) -> None:
    store, review, candidate = _create_current_candidate(tmp_path)
    replacement_bet = _bet(groups=[["03", "04"]])
    replacement = store.replace_human_review(
        review_session_id=review["review_session_id"],
        expected_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        bets=[replacement_bet],
        machine_evidence_refs=_machine_refs(),
        blocking_unresolved_count=1,
        actor="gate3b2-edit",
    )
    replacement = store.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=replacement["human_answer_revision"],
        expected_human_answer_hash=replacement["human_answer_hash"],
        human_bet_ids=["H-001"],
        actor="gate3b2-human",
    )
    store.create_candidate(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=replacement["human_answer_revision"],
        expected_human_answer_hash=replacement["human_answer_hash"],
        idempotency_key=(
            f"{review['review_session_id']}:{replacement['human_answer_revision']}:"
            f"{replacement['human_answer_hash']}"
        ),
        actor="gate3b2-candidate-factory",
    )
    _assert_error(
        "CANDIDATE_SUPERSEDED",
        lambda: _validate(CandidateConsumptionAuthorityValidator(store), candidate),
    )


# K11-K17: adversarial stored authority/structure mutations all fail closed.
@pytest.mark.parametrize(
    ("name", "mutate", "expected_code", "rewrite_identity"),
    [
        (
            "cancelled_active",
            lambda value: value["cancelled_audit"][0].__setitem__("active", True),
            "CANDIDATE_STRUCTURE_INVALID",
            False,
        ),
        (
            "cancelled_executable",
            lambda value: value["cancelled_audit"][0].__setitem__("executable", True),
            "CANDIDATE_STRUCTURE_INVALID",
            False,
        ),
        (
            "flattened_column",
            lambda value: value["active_bets"][0].__setitem__(
                "number_groups", [["01", "02", "03", "04"]]
            ),
            "CANDIDATE_STRUCTURE_INVALID",
            False,
        ),
        (
            "empty_group",
            lambda value: value["active_bets"][0].__setitem__(
                "number_groups", [["01"], []]
            ),
            "CANDIDATE_STRUCTURE_INVALID",
            False,
        ),
        (
            "duplicate_human_bet_id",
            lambda value: value["active_bets"][1].__setitem__(
                "human_bet_id", value["active_bets"][0]["human_bet_id"]
            ),
            "CANDIDATE_STRUCTURE_INVALID",
            True,
        ),
        (
            "missing_authority_provenance",
            lambda value: value["authority"].pop("confirmed_by"),
            "CANDIDATE_AUTHORITY_INVALID",
            False,
        ),
        (
            "machine_claims_value_authority",
            lambda value: value["authority"]["machine_evidence_refs"][0].__setitem__(
                "value_authority", True
            ),
            "CANDIDATE_AUTHORITY_INVALID",
            False,
        ),
    ],
)
def test_k11_through_k17_stored_tampering_fails_closed(
    tmp_path: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    expected_code: str,
    rewrite_identity: bool,
) -> None:
    del name
    bets = [
        _bet(
            "H-001",
            bet_type="column",
            groups=[["01", "02"], ["03", "04"]],
        ),
        _bet("H-002", groups=[["05", "06"]]),
        _bet("H-003", groups=[["07"]], cancelled=True),
    ]
    store, _review, candidate = _create_current_candidate(tmp_path, bets=bets)
    _tamper_snapshot(
        tmp_path, candidate, mutate, rewrite_identity=rewrite_identity
    )
    _assert_error(
        expected_code,
        lambda: CandidateConsumptionAuthorityValidator(store).validate(
            candidate_id=candidate["candidate_id"]
        ),
    )


# K18-K22: the validator is identity/integrity validation, not a normalizer.
def _rich_candidate_bets() -> list[dict[str, Any]]:
    return [
        _bet("H-001", groups=[["30", "35", "36", "38"]], rules=["3/4X1"]),
        _bet(
            "H-002",
            bet_type="column",
            groups=[["21", "35"], ["23"], ["34"], ["37"]],
            rules=["2X3", "3X1"],
            continuation=True,
            special_kind="half_car",
            special_raw="各半車",
            special_scope="bet",
        ),
    ]


@pytest.mark.parametrize(
    ("label", "projection"),
    [
        ("normal", lambda candidate: candidate["active_bets"][0]["number_groups"]),
        ("column", lambda candidate: candidate["active_bets"][1]["number_groups"]),
        ("multiplier", lambda candidate: candidate["active_bets"][1]["multiplier"]),
        ("continuation", lambda candidate: candidate["active_bets"][1]["continuation"]),
        ("special", lambda candidate: candidate["active_bets"][1]["special_play"]),
    ],
)
def test_k18_through_k22_candidate_semantics_are_not_rewritten(
    tmp_path: Path,
    label: str,
    projection: Callable[[dict[str, Any]], Any],
) -> None:
    del label
    store, _review, candidate = _create_current_candidate(
        tmp_path, bets=_rich_candidate_bets()
    )
    envelope = _validate(CandidateConsumptionAuthorityValidator(store), candidate)
    envelope_projection = {
        "active_bets": envelope["bets"],
        "cancelled_audit": envelope["cancelled_audit"],
    }
    assert projection(envelope_projection) == projection(candidate)


def test_k23_restart_reloads_and_validates_same_snapshot(tmp_path: Path) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    before = _validate(CandidateConsumptionAuthorityValidator(store), candidate)

    restarted = VisionCandidateAuthorityStore(tmp_path)
    after = _validate(CandidateConsumptionAuthorityValidator(restarted), candidate)

    assert after == before


@pytest.mark.parametrize(
    ("forbidden_field", "fake_value"),
    [
        ("bets", [_bet(groups=[["39"]])]),
        ("number_groups", [["39"]]),
        ("multiplier", {"ordered_rules": ["4X9"]}),
        ("special_play", {"kind": "tail"}),
        ("candidate_snapshot", {"active_bets": [_bet(groups=[["39"]])]}),
    ],
)
def test_k24_client_supplied_candidate_values_are_rejected_not_ignored(
    tmp_path: Path, forbidden_field: str, fake_value: object
) -> None:
    store, _review, candidate = _create_current_candidate(tmp_path)
    validator = CandidateConsumptionAuthorityValidator(store)
    payload = {
        "candidate_id": candidate["candidate_id"],
        "expected_candidate_revision": candidate["revision"],
        "expected_content_hash": candidate["canonical_content_hash"],
        forbidden_field: fake_value,
    }
    before = _tree_bytes(tmp_path)
    _assert_error(
        "CANDIDATE_REQUEST_INVALID", lambda: validator.validate_request(payload)
    )
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("sample_id", "bets"),
    [
        (
            "sample-007",
            [
                *[
                    _bet(f"H-{index:03d}", groups=[[f"{index:02d}"]])
                    for index in range(1, 25)
                ],
                _bet("H-025", groups=[["25"]], cancelled=True),
            ],
        ),
        (
            "sample-008",
            [
                _bet("H-003", groups=[["08", "01", "04"]]),
                _bet("H-004", groups=[["08", "16", "26"]]),
                _bet("H-006", groups=[["09"]]),
            ],
        ),
        (
            "sample-010",
            [
                _bet(
                    "H-010",
                    groups=[["32", "34", "35"]],
                    rules=["2X2", "3X5"],
                    special_kind="half_car",
                    special_raw="各半車",
                    special_scope="bet",
                )
            ],
        ),
        (
            "sample-011",
            [
                _bet("H-002", groups=[["30", "35", "36", "38"]], rules=["3/4X1"]),
                _bet(
                    "H-011",
                    bet_type="column",
                    groups=[["21", "35"], ["23"], ["34"], ["37"]],
                ),
                _bet(
                    "H-012",
                    bet_type="column",
                    groups=[["34"], ["23"], ["35"], ["27", "37"]],
                ),
            ],
        ),
        (
            "sample-014",
            [
                _bet("H-005", groups=[["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]], bet_type="column", rules=["2/3/4X0.1"], continuation=True),
                _bet("H-006", groups=[["34"], ["03", "13"], ["16", "36"]], bet_type="column", rules=["2X1"], continuation=True),
                _bet("H-011", groups=[["12"], ["15"], ["34"], ["13", "20"]], bet_type="column", rules=["2X3", "3X1"], continuation=True),
                _bet("H-099", groups=[["12", "15"]], cancelled=True),
            ],
        ),
    ],
)
def test_real_sample_semantics_survive_validator_exactly(
    tmp_path: Path, sample_id: str, bets: list[dict[str, Any]]
) -> None:
    store, _review, candidate = _create_current_candidate(
        tmp_path / sample_id, bets=bets, review_id=f"review-{sample_id}"
    )
    envelope = _validate(CandidateConsumptionAuthorityValidator(store), candidate)
    assert envelope["bets"] == candidate["active_bets"]
    assert envelope["cancelled_audit"] == candidate["cancelled_audit"]
    assert envelope["game"] == candidate["game"]
    assert envelope["source"] == candidate["source"]
    assert envelope["authority"] == candidate["authority"]
    if sample_id == "sample-007":
        assert len(envelope["bets"]) == 24
        assert len(envelope["cancelled_audit"]) == 1
        assert envelope["cancelled_audit"][0]["active"] is False
        assert envelope["cancelled_audit"][0]["executable"] is False
    elif sample_id == "sample-008":
        assert [bet["number_groups"] for bet in envelope["bets"]] == [
            [["08", "01", "04"]],
            [["08", "16", "26"]],
            [["09"]],
        ]
    elif sample_id == "sample-010":
        assert envelope["bets"][0]["multiplier"]["ordered_rules"] == [
            "2X2",
            "3X5",
        ]
        assert envelope["bets"][0]["special_play"]["raw_text"] == "各半車"
    elif sample_id == "sample-011":
        assert envelope["bets"][0]["number_groups"] == [["30", "35", "36", "38"]]
        assert envelope["bets"][0]["multiplier"]["ordered_rules"] == ["3/4X1"]
        assert envelope["bets"][1]["number_groups"] == [
            ["21", "35"], ["23"], ["34"], ["37"]
        ]
        assert envelope["bets"][2]["number_groups"] == [
            ["34"], ["23"], ["35"], ["27", "37"]
        ]
    elif sample_id == "sample-014":
        assert [bet["multiplier"]["ordered_rules"] for bet in envelope["bets"]] == [
            ["2/3/4X0.1"],
            ["2X1"],
            ["2X3", "3X1"],
        ]
        assert all(bet["continuation"]["present"] for bet in envelope["bets"])
        assert envelope["cancelled_audit"][0]["executable"] is False


@contextmanager
def _running_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[int]:
    monkeypatch.setenv("BETGUARD_SKIP_LICENSE", "1")
    monkeypatch.setattr(
        webui_app,
        "_VISION_CANDIDATE_AUTHORITY_STORE",
        VisionCandidateAuthorityStore(tmp_path / "candidate-authority"),
    )
    handler = webui_app.build_workbench_handler(
        project_version="gate3b2-test", git_commit="test"
    )
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _request(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST", path, body=body, headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def test_vc_namespace_cannot_masquerade_as_manual_candidate_or_fill_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual_before = deepcopy(webui_app._manual_candidates)
    candidate_id = "vc-" + "d" * 32
    with _running_server(tmp_path, monkeypatch) as port:
        for path, payload in [
            ("/assist-fill/start", {"manual_candidate_id": candidate_id}),
            ("/assist-fill/manual-done", {"manual_candidate_id": candidate_id}),
            (
                "/assist-fill/start",
                {
                    "candidate_id": candidate_id,
                    "bets": [_bet(groups=[["39"]])],
                    "bet_type": "normal",
                },
            ),
        ]:
            _status, response = _request(port, path, payload)
            assert response["ok"] is False
    assert webui_app._manual_candidates == manual_before


def test_webfill_and_queue_packages_do_not_import_candidate_authority() -> None:
    src_root = Path(__file__).parents[1] / "src" / "betguard"
    forbidden = (
        "candidate_consumption",
        "VisionCandidateAuthorityStore",
        "vision-candidate-authority-v1",
    )
    scanned = [*sorted((src_root / "webfill").glob("*.py"))]
    assert scanned
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in forbidden), path

    # The workbench may create/reload a Candidate at the Gate 3B-1 review
    # boundary, but no legacy UI/fill endpoint may consume it directly. A
    # future consumer must call the validator through a separately reviewed
    # adapter rather than importing the validator into these surfaces.
    for path in (
        src_root / "webui" / "app.py",
        src_root / "webui" / "assist_panel_html.py",
        src_root / "webui" / "assist_panel_vision_html.py",
    ):
        assert "candidate_consumption" not in path.read_text(encoding="utf-8")
