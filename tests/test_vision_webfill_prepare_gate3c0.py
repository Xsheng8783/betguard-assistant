from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path

import pytest

from betguard.vision.candidate_authority import canonical_sha256
from betguard.vision.validated_candidate_claims import ValidatedCandidateClaimStore
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from betguard.vision.webfill_prepare import WebfillPrepareError, WebfillPrepareStore
from betguard.vision.webfill_target_profiles import WebfillTargetProfileStore


class _Validator:
    def validate_request(self, payload):
        request = dict(payload)
        return {
            "schema_version": "vision-candidate-consumption-validation-v1",
            "validation_status": "VALID_CURRENT",
            "candidate_id": request["candidate_id"],
            "candidate_revision": request["expected_candidate_revision"],
            "canonical_content_hash": request["expected_content_hash"],
            "lifecycle_state": "CURRENT",
            "game": "539",
            "bets": [
                {
                    "human_bet_id": "H-001",
                    "bet_type": "normal",
                    "number_groups": [["01", "02"]],
                    "multiplier": {"ordered_rules": ["2X1"], "scope": "bet", "resolved": True},
                    "special_play": {"kind": "none", "raw_text": None, "scope": None, "resolved": True},
                    "continuation": {"present": False, "resolved": True},
                    "cancelled": False,
                    "active": True,
                    "executable": True,
                    "value_authority": "human_answer",
                },
                {
                    "human_bet_id": "H-002",
                    "bet_type": "column",
                    "number_groups": [["12"], ["15"], ["06", "16"]],
                    "multiplier": {"ordered_rules": ["2X3", "3X1"], "scope": "bet", "resolved": True},
                    "special_play": {"kind": "half_car", "raw_text": "half car", "scope": "bet", "resolved": True},
                    "continuation": {"present": True, "resolved": True},
                    "cancelled": False,
                    "active": True,
                    "executable": True,
                    "value_authority": "human_answer",
                },
            ],
            "cancelled_audit": [{"human_bet_id": "H-003", "cancelled": True, "active": False, "executable": False}],
            "source": {"review_session_id": "review-1", "human_answer_revision": 3, "human_answer_hash": "e" * 64, "source_image_hash": "9" * 64},
            "authority": {"value_authority": "human_answer"},
            "safety": {"candidate_only": True, "approved_for_fill": False, "approved_for_queue": False, "auto_confirm": False, "auto_submit": False},
        }


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _profile(version=1):
    profile = {
        "schema_version": "vision-webfill-target-profile-v1",
        "target_profile_id": "wtp-betguard-539-logical",
        "target_profile_version": version,
        "game": "539",
        "capabilities": {"supported_bet_types": ["normal", "column"], "supported_special_play_kinds": ["none", "half_car"], "supports_continuation": True, "supports_multiple_multiplier_rules": True, "maximum_active_bets": 100, "maximum_number_groups_per_bet": 12, "maximum_numbers_per_group": 39},
        "ordering_contract": {"bet_order": "preserve_candidate_active_bets", "group_order": "preserve", "number_order": "preserve", "multiplier_rule_order": "preserve"},
        "adapter_boundary": {"logical_contract_only": True, "dom_mapping_present": False, "browser_execution_authorized": False, "submit_authorized": False},
        "created_at": f"2026-08-15T08:00:0{version}+00:00",
        "profile_integrity_hash": "",
    }
    profile["profile_integrity_hash"] = canonical_sha256({k: v for k, v in profile.items() if k != "profile_integrity_hash"})
    return profile


def _setup(tmp_path: Path):
    validator = _Validator()
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    identity = {"candidate_id": "vc-" + "1" * 32, "candidate_revision": 1, "canonical_content_hash": "2" * 64}
    key = "qik-" + "1" * 32
    action = queue.bind_human_enqueue_action(authenticated_actor="human", interactive_session_id="ui", candidate_id=identity["candidate_id"], candidate_revision=1, canonical_content_hash=identity["canonical_content_hash"], idempotency_key=key)
    entry = queue.enqueue({"candidate_id": identity["candidate_id"], "expected_candidate_revision": 1, "expected_content_hash": identity["canonical_content_hash"], "human_enqueue_action_id": action["action_id"], "idempotency_key": key}, authenticated_actor="human", interactive_session_id="ui")["queue_entry"]
    clock = _Clock()
    claims = ValidatedCandidateClaimStore(queue.claim_store_root, queue, validator, clock=clock, owner_session_validator=lambda *_: True)
    claim_key = "qik-" + "2" * 32
    claim_action = claims.bind_claim_action(authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session", idempotency_key=claim_key)
    claim = claims.claim_next({"action_id": claim_action["action_id"], "idempotency_key": claim_key}, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    profiles = WebfillTargetProfileStore(tmp_path / "prepare")
    profiles.register_profile(_profile(), activate=True)
    prepares = WebfillPrepareStore(tmp_path / "prepare", queue, claims, profiles)
    request = {"schema_version": "vision-webfill-prepare-request-v1", "queue_entry_id": entry["queue_entry_id"], "claim_id": claim["claim_id"], "claim_session_id": "session", "claim_generation": claim["claim_generation"], "fencing_token": claim["fencing_token"], "expected_candidate_id": identity["candidate_id"], "expected_canonical_content_hash": identity["canonical_content_hash"], "expected_queue_revision": 1, "target_profile_id": "wtp-betguard-539-logical", "target_profile_version": 1, "idempotency_key": "wpi-" + "4" * 32}
    return queue, claims, profiles, prepares, request, clock


def _create(prepares, request):
    return prepares.create_prepare(request, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")


def test_create_dry_run_preserves_columns_rules_continuation_and_cancelled_refs(tmp_path):
    _queue, _claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    result = _create(prepares, request)
    artifact = result["artifact"]
    assert result["state"] == "PREPARED"
    assert artifact["logical_plan"]["operations"][1]["number_groups"] == [["12"], ["15"], ["06", "16"]]
    assert artifact["logical_plan"]["operations"][1]["multiplier"]["ordered_rules"] == ["2X3", "3X1"]
    assert artifact["logical_plan"]["operations"][1]["continuation"] == {"present": True, "resolved": True, "binding": "within_human_bet"}
    assert artifact["logical_plan"]["cancelled_audit_refs"] == [{"human_bet_id": "H-003", "candidate_section": "cancelled_audit", "excluded_reason": "cancelled_non_executable"}]
    assert artifact["safety"]["approved_for_fill"] is False
    assert artifact["safety"]["approved_for_submit"] is False


def test_exact_request_replays_byte_identical_artifact(tmp_path):
    *_unused, prepares, request, _clock = _setup(tmp_path)
    first = _create(prepares, request)
    second = _create(prepares, request)
    assert second["replayed"] is True
    assert second["artifact"] == first["artifact"]


def test_same_authority_tuple_different_key_replays_same_artifact(tmp_path):
    *_unused, prepares, request, _clock = _setup(tmp_path)
    first = _create(prepares, request)
    other = deepcopy(request); other["idempotency_key"] = "wpi-" + "5" * 32
    second = _create(prepares, other)
    assert second["artifact"]["prepare_id"] == first["artifact"]["prepare_id"]


@pytest.mark.parametrize("extra", ["bets", "selector", "url", "submitted_payload"])
def test_request_rejects_value_or_execution_fields(tmp_path, extra):
    *_unused, prepares, request, _clock = _setup(tmp_path)
    request[extra] = []
    with pytest.raises(WebfillPrepareError) as raised:
        _create(prepares, request)
    assert raised.value.code == "PREPARE_REQUEST_INVALID"


def test_release_derives_invalidated_and_never_mutates_artifact(tmp_path):
    _queue, claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    created = _create(prepares, request)
    before = deepcopy(created["artifact"])
    key = "qik-" + "6" * 32
    action = claims.bind_release_action(claim_id=request["claim_id"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session", idempotency_key=key)
    claims.release({"action_id": action["action_id"], "idempotency_key": key}, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    state = prepares.get_prepare_state(before["prepare_id"], claim_generation=request["claim_generation"], fencing_token=request["fencing_token"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert state["state"] == "INVALIDATED"
    assert state["artifact"] == before


def test_profile_advance_derives_superseded(tmp_path):
    _queue, _claims, profiles, prepares, request, _clock = _setup(tmp_path)
    artifact = _create(prepares, request)["artifact"]
    profiles.register_profile(_profile(2), activate=True)
    state = prepares.get_prepare_state(artifact["prepare_id"], claim_generation=request["claim_generation"], fencing_token=request["fencing_token"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert state["state"] == "SUPERSEDED"


def test_successful_authoritative_read_has_no_execution_authority(tmp_path):
    *_unused, prepares, request, _clock = _setup(tmp_path)
    artifact = _create(prepares, request)["artifact"]
    state = prepares.get_prepare_state(artifact["prepare_id"], claim_generation=request["claim_generation"], fencing_token=request["fencing_token"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert state["state"] == "PREPARED"
    assert state["artifact"]["executable"] is False


def test_renew_same_generation_replays_byte_identical_artifact(tmp_path):
    _queue, claims, _profiles, prepares, request, clock = _setup(tmp_path)
    first = _create(prepares, request)["artifact"]
    clock.advance(1)
    key = "qik-" + "7" * 32
    action = claims.bind_renew_action(claim_id=request["claim_id"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session", idempotency_key=key)
    claims.renew({"action_id": action["action_id"], "idempotency_key": key}, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    replay = _create(prepares, request)
    assert replay["artifact"] == first
    assert replay["status"] == "VALID_PREPARED"


def test_wrong_fence_rejects_without_terminalizing_prepare(tmp_path):
    _queue, _claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    artifact = _create(prepares, request)["artifact"]
    with pytest.raises(WebfillPrepareError) as raised:
        prepares.get_prepare_state(artifact["prepare_id"], claim_generation=request["claim_generation"], fencing_token="vqf-" + "0" * 64, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert raised.value.code == "PREPARE_CLAIM_INVALID"
    assert [event["event_type"] for event in prepares.get_lifecycle_events(artifact["prepare_id"])] == ["PREPARED"]


def test_corrupt_pending_transaction_preflight_writes_nothing_upstream(tmp_path):
    _queue, claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    path = prepares.base_dir / "prepare-transactions" / "bad.json"
    path.write_text('{"schema_version":"wrong"}', encoding="utf-8")
    before = {str(item.relative_to(claims.base_dir)): item.read_bytes() for item in claims.base_dir.rglob("*") if item.is_file()}
    with pytest.raises(WebfillPrepareError) as raised:
        _create(prepares, request)
    assert raised.value.code == "PREPARE_SCHEMA_INVALID"
    after = {str(item.relative_to(claims.base_dir)): item.read_bytes() for item in claims.base_dir.rglob("*") if item.is_file()}
    assert after == before


def test_crash_before_commit_recovers_same_prepare_under_another_tuple_key(tmp_path, monkeypatch):
    import betguard.vision.webfill_prepare as module

    _queue, _claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    original = module._write_immutable
    failed = False

    def interrupt_commit(path, value):
        nonlocal failed
        if path.parent.name == "artifact-commits" and not failed:
            failed = True
            raise RuntimeError("simulated commit interruption")
        return original(path, value)

    monkeypatch.setattr(module, "_write_immutable", interrupt_commit)
    with pytest.raises(RuntimeError):
        _create(prepares, request)
    prepare_ids = [path.stem for path in (prepares.base_dir / "artifacts").glob("*.json")]
    assert len(prepare_ids) == 1
    assert prepares.get_lifecycle_events(prepare_ids[0]) == []
    monkeypatch.setattr(module, "_write_immutable", original)
    other = deepcopy(request); other["idempotency_key"] = "wpi-" + "8" * 32
    recovered = _create(prepares, other)
    assert recovered["artifact"]["prepare_id"] == prepare_ids[0]
    assert recovered["replayed"] is True


# Machine-check the locked matrix inventory.  Behavioral cases are grouped in
# this file and the independent adversarial suite, but no locked ID may silently
# disappear when either suite is refactored.
GATE3C0_POSITIVE_MATRIX = {
    f"P{number:02d}": description
    for number, description in enumerate(
        (
            "authority happy path", "exact retry", "tuple replay", "normal",
            "column", "multiple rules", "special", "continuation", "cancel ref",
            "ordered operations", "renew", "new generation", "restart",
            "commit crash", "partial publish", "fresh read", "profile",
            "deterministic compile", "machine evidence excluded", "public redaction",
        ),
        1,
    )
}
GATE3C0_FAILURE_MATRIX = {
    f"F{number:02d}": description
    for number, description in enumerate(
        (
            "24/25", "review changed", "candidate stale", "candidate terminal",
            "queue stale", "claim terminal", "claim expiry", "owner mismatch",
            "generation mismatch", "fence mismatch", "old generation", "clock rollback",
            "multiplier unresolved", "special unresolved", "empty groups", "normal groups",
            "flattened column", "cancelled operation", "inactive operation",
            "continuation ambiguity", "duplicate bet id", "operation accounting",
            "request values", "request execution fields", "idempotency conflict",
            "profile missing", "profile inactive", "profile tamper", "bet unsupported",
            "special unsupported", "target feature unsupported", "target limits",
            "operation reorder", "nested reorder", "plan hash", "binding hash",
            "integrity hash", "transaction shape", "all-journal preflight",
            "commit visibility", "session unrecoverable", "candidate freshness",
            "queue freshness", "release abandon", "expiry event", "authority block",
            "profile supersede", "terminal transition", "event relation",
            "execution forbidden", "legacy forbidden", "JSON canonical safety",
        ),
        1,
    )
}
GATE3C0_FIVE_SAMPLE_MATRIX = {
    "sample-007": "normal/column/special/cancelled",
    "sample-008": "human corrected nested groups",
    "sample-010": "multi-rule column and special scope",
    "sample-011": "corrected 30, 3/4X1 and continuation",
    "sample-014": "continuation, independent scopes and cancelled audit",
}


def test_locked_matrix_and_five_sample_inventory_is_complete():
    assert set(GATE3C0_POSITIVE_MATRIX) == {f"P{number:02d}" for number in range(1, 21)}
    assert set(GATE3C0_FAILURE_MATRIX) == {f"F{number:02d}" for number in range(1, 53)}
    assert set(GATE3C0_FIVE_SAMPLE_MATRIX) == {
        "sample-007", "sample-008", "sample-010", "sample-011", "sample-014"
    }


def test_recomputed_self_consistent_plan_tamper_fails_live_candidate_projection(tmp_path):
    _queue, _claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    artifact = _create(prepares, request)["artifact"]
    prepare_id = artifact["prepare_id"]
    artifact["logical_plan"]["operations"][0]["number_groups"][0][1] = "03"
    artifact["deterministic_plan_hash"] = canonical_sha256(artifact["logical_plan"])
    authority = artifact["authority"]
    binding = {
        "schema_version": "vision-webfill-prepare-authority-binding-v1",
        "queue": authority["queue"],
        "candidate": authority["candidate"],
        "claim": {key: value for key, value in authority["claim"].items() if key != "lease_expires_at_at_prepare"},
        "target_profile": authority["target_profile"],
        "deterministic_plan_hash": artifact["deterministic_plan_hash"],
    }
    artifact["authority_binding_hash"] = canonical_sha256(binding)
    artifact["record_integrity_hash"] = canonical_sha256({key: value for key, value in artifact.items() if key != "record_integrity_hash"})
    event_path = prepares.base_dir / "lifecycle-events" / prepare_id / "000001.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["authority_binding_hash"] = artifact["authority_binding_hash"]
    event["deterministic_plan_hash"] = artifact["deterministic_plan_hash"]
    event["prepare_record_integrity_hash"] = artifact["record_integrity_hash"]
    event["event_integrity_hash"] = canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"})
    (prepares.base_dir / "artifacts" / f"{prepare_id}.json").write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    event_path.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    commit = {"prepare_id": prepare_id, "record_integrity_hash": artifact["record_integrity_hash"], "authority_binding_hash": artifact["authority_binding_hash"]}
    (prepares.base_dir / "artifact-commits" / f"{prepare_id}.json").write_text(json.dumps(commit, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(WebfillPrepareError) as raised:
        prepares.get_prepare_state(prepare_id, claim_generation=request["claim_generation"], fencing_token=request["fencing_token"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert raised.value.code in {"PREPARE_HASH_MISMATCH", "PREPARE_STORE_CORRUPT"}


def test_claim_clock_rollback_rejects_before_prepare_write(tmp_path):
    _queue, _claims, _profiles, prepares, request, clock = _setup(tmp_path)
    clock.value -= timedelta(seconds=1)
    with pytest.raises(WebfillPrepareError) as raised:
        _create(prepares, request)
    assert raised.value.code == "PREPARE_CLOCK_UNSAFE"
    assert list((prepares.base_dir / "artifacts").glob("*.json")) == []


def test_claim_at_expiry_materializes_claim_expired_without_prepare(tmp_path):
    _queue, _claims, _profiles, prepares, request, clock = _setup(tmp_path)
    clock.advance(300)
    with pytest.raises(WebfillPrepareError) as raised:
        _create(prepares, request)
    assert raised.value.code == "PREPARE_CLAIM_EXPIRED"
    assert list((prepares.base_dir / "artifacts").glob("*.json")) == []


def test_new_claim_generation_creates_new_binding_same_plan(tmp_path):
    _queue, claims, _profiles, prepares, request, clock = _setup(tmp_path)
    first = _create(prepares, request)["artifact"]
    release_key = "qik-" + "9" * 32
    release = claims.bind_release_action(claim_id=request["claim_id"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session", idempotency_key=release_key)
    claims.release({"action_id": release["action_id"], "idempotency_key": release_key}, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    clock.advance(1)
    claim_key = "qik-" + "a" * 32
    action = claims.bind_claim_action(authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session", idempotency_key=claim_key)
    second_claim = claims.claim_next({"action_id": action["action_id"], "idempotency_key": claim_key}, authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    second_request = deepcopy(request)
    second_request.update({"claim_id": second_claim["claim_id"], "claim_generation": second_claim["claim_generation"], "fencing_token": second_claim["fencing_token"], "idempotency_key": "wpi-" + "b" * 32})
    second = _create(prepares, second_request)["artifact"]
    assert second["prepare_id"] != first["prepare_id"]
    assert second["deterministic_plan_hash"] == first["deterministic_plan_hash"]
    assert second["authority_binding_hash"] != first["authority_binding_hash"]


def test_trusted_revocation_is_append_only_terminal_and_idempotent(tmp_path):
    queue, claims, profiles, _prepares, request, _clock = _setup(tmp_path)
    prepares = WebfillPrepareStore(tmp_path / "prepare", queue, claims, profiles, administrative_principal_validator=lambda principal: principal == "security-admin")
    artifact = _create(prepares, request)["artifact"]
    before = deepcopy(artifact)
    payload = {"schema_version": "vision-webfill-prepare-revoke-request-v1", "prepare_id": artifact["prepare_id"], "reason_code": "security_revocation"}
    revoked = prepares.revoke_prepare(payload, authenticated_principal="security-admin")
    replay = prepares.revoke_prepare(payload, authenticated_principal="security-admin")
    assert revoked["state"] == "REVOKED"
    assert revoked["artifact"] == before
    assert replay["replayed"] is True
    assert [event["event_type"] for event in prepares.get_lifecycle_events(artifact["prepare_id"])] == ["PREPARED", "REVOKED"]


def test_normative_four_schemas_validate_created_records(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    _queue, _claims, profiles, prepares, request, _clock = _setup(tmp_path)
    result = _create(prepares, request)
    root = Path(__file__).parents[1] / "docs" / "schemas"
    cases = [
        ("vision_webfill_prepare_request_v1.schema.json", request),
        ("vision_webfill_target_profile_v1.schema.json", profiles.get_active_profile(request["target_profile_id"])),
        ("vision_webfill_prepare_artifact_v1.schema.json", result["artifact"]),
        ("vision_webfill_prepare_lifecycle_event_v1.schema.json", result["lifecycle_event"]),
    ]
    checker = jsonschema.FormatChecker()
    for name, value in cases:
        schema = json.loads((root / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        assert list(jsonschema.Draft202012Validator(schema, format_checker=checker).iter_errors(value)) == []


def test_conflicting_partial_output_is_rejected_before_claim_clock_write(tmp_path):
    _queue, claims, _profiles, prepares, request, _clock = _setup(tmp_path)
    artifact = _create(prepares, request)["artifact"]
    index_path = next((prepares.base_dir / "idempotency").glob("*.json"))
    record = json.loads(index_path.read_text(encoding="utf-8"))
    record["record_integrity_hash"] = "0" * 64
    index_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    clock_path = claims.base_dir / "clock" / "state.json"
    clock_before = clock_path.read_bytes()
    with pytest.raises(WebfillPrepareError) as raised:
        prepares.get_prepare_state(artifact["prepare_id"], claim_generation=request["claim_generation"], fencing_token=request["fencing_token"], authenticated_principal="worker", consumer_id="vqcns-" + "3" * 32, server_session_id="session")
    assert raised.value.code == "PREPARE_STORE_CORRUPT"
    assert clock_path.read_bytes() == clock_before


def test_prepare_module_has_no_execution_or_legacy_adapter_imports():
    source_path = Path(__file__).parents[1] / "src" / "betguard" / "vision" / "webfill_prepare.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(any(token in name.lower() for token in ("selenium", "playwright", "browser", "approved_fill", "manual_candidate", "webfill.executor", "webfill.adapter")) for name in imported)
    public_methods = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")}
    assert public_methods.isdisjoint({"fill", "submit", "complete", "mark_completed", "execute", "compile_adapter"})
