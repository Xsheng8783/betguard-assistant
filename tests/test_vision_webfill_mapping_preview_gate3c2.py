from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from betguard.vision.candidate_authority import canonical_json_bytes, canonical_sha256
from betguard.vision.candidate_authority import CandidateAuthorityError
from betguard.vision.validated_candidate_claims import ValidatedCandidateClaimStore
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from betguard.vision.webfill_mapping_preview import (
    WebfillAdapterTargetProfileStore,
    WebfillDomObservationStore,
    WebfillMappingError,
    WebfillMappingPreviewStore,
    _field_identity_projection,
    _form_fingerprint_projection,
    _form_identity_projection,
    _page_fingerprint_projection,
)
from betguard.vision.webfill_prepare import WebfillPrepareStore
from betguard.vision.webfill_target_profiles import WebfillTargetProfileStore


PRINCIPAL = "worker"
CONSUMER = "vqcns-" + "3" * 32
SESSION = "session"


class _Validator:
    def __init__(self, bets=None, cancelled=None):
        self.bets = bets or [
            _bet("H-001", "normal", [["01", "02"]]),
            _bet(
                "H-002",
                "column",
                [["12"], ["15"], ["06", "16"]],
                rules=["2X3", "3X1"],
                special={"kind": "half_car", "raw_text": "half car", "scope": "bet", "resolved": True},
                continuation=True,
            ),
        ]
        self.cancelled = cancelled or [{"human_bet_id": "H-003", "cancelled": True, "active": False, "executable": False}]
        self.failure_code = None

    def validate_request(self, payload):
        if self.failure_code:
            raise CandidateAuthorityError(self.failure_code, "candidate authority changed", 409)
        request = dict(payload)
        return {
            "schema_version": "vision-candidate-consumption-validation-v1",
            "validation_status": "VALID_CURRENT",
            "candidate_id": request["candidate_id"],
            "candidate_revision": request["expected_candidate_revision"],
            "canonical_content_hash": request["expected_content_hash"],
            "lifecycle_state": "CURRENT",
            "game": "539",
            "bets": deepcopy(self.bets),
            "cancelled_audit": deepcopy(self.cancelled),
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


def _bet(human_id, bet_type, groups, *, rules=None, special=None, continuation=False):
    return {
        "human_bet_id": human_id,
        "bet_type": bet_type,
        "number_groups": groups,
        "multiplier": {"ordered_rules": rules or ["2X1"], "scope": "bet", "resolved": True},
        "special_play": special or {"kind": "none", "raw_text": None, "scope": None, "resolved": True},
        "continuation": {"present": continuation, "resolved": True},
        "cancelled": False,
        "active": True,
        "executable": True,
        "value_authority": "human_answer",
    }


def _logical_profile(version=1):
    profile = {
        "schema_version": "vision-webfill-target-profile-v1",
        "target_profile_id": "wtp-betguard-539-logical",
        "target_profile_version": version,
        "game": "539",
        "capabilities": {"supported_bet_types": ["normal", "column"], "supported_special_play_kinds": ["none", "half_car", "tail", "each"], "supports_continuation": True, "supports_multiple_multiplier_rules": True, "maximum_active_bets": 100, "maximum_number_groups_per_bet": 12, "maximum_numbers_per_group": 39},
        "ordering_contract": {"bet_order": "preserve_candidate_active_bets", "group_order": "preserve", "number_order": "preserve", "multiplier_rule_order": "preserve"},
        "adapter_boundary": {"logical_contract_only": True, "dom_mapping_present": False, "browser_execution_authorized": False, "submit_authorized": False},
        "created_at": f"2026-08-15T08:00:0{version}+00:00",
        "profile_integrity_hash": "",
    }
    profile["profile_integrity_hash"] = canonical_sha256({key: value for key, value in profile.items() if key != "profile_integrity_hash"})
    return profile


def _upstream(tmp_path: Path, *, bets=None, cancelled=None):
    validator = _Validator(bets, cancelled)
    queue = ValidatedCandidateQueueStore(tmp_path / "queue", validator)
    identity = {"candidate_id": "vc-" + "1" * 32, "candidate_revision": 1, "canonical_content_hash": "2" * 64}
    key = "qik-" + "1" * 32
    action = queue.bind_human_enqueue_action(authenticated_actor="human", interactive_session_id="ui", candidate_id=identity["candidate_id"], candidate_revision=1, canonical_content_hash=identity["canonical_content_hash"], idempotency_key=key)
    entry = queue.enqueue({"candidate_id": identity["candidate_id"], "expected_candidate_revision": 1, "expected_content_hash": identity["canonical_content_hash"], "human_enqueue_action_id": action["action_id"], "idempotency_key": key}, authenticated_actor="human", interactive_session_id="ui")["queue_entry"]
    clock = _Clock()
    claims = ValidatedCandidateClaimStore(queue.claim_store_root, queue, validator, clock=clock, owner_session_validator=lambda *_: True)
    claim_key = "qik-" + "2" * 32
    claim_action = claims.bind_claim_action(authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION, idempotency_key=claim_key)
    claim = claims.claim_next({"action_id": claim_action["action_id"], "idempotency_key": claim_key}, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    logical_profiles = WebfillTargetProfileStore(tmp_path / "prepare")
    logical_profiles.register_profile(_logical_profile(), activate=True)
    prepares = WebfillPrepareStore(tmp_path / "prepare", queue, claims, logical_profiles)
    prepare_request = {"schema_version": "vision-webfill-prepare-request-v1", "queue_entry_id": entry["queue_entry_id"], "claim_id": claim["claim_id"], "claim_session_id": SESSION, "claim_generation": claim["claim_generation"], "fencing_token": claim["fencing_token"], "expected_candidate_id": identity["candidate_id"], "expected_canonical_content_hash": identity["canonical_content_hash"], "expected_queue_revision": 1, "target_profile_id": "wtp-betguard-539-logical", "target_profile_version": 1, "idempotency_key": "wpi-" + "4" * 32}
    prepared = prepares.create_prepare(prepare_request, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)["artifact"]
    return queue, claims, prepares, prepared, claim, clock, validator


def _selector_for(action, operation_index, *, group_index=0, number_index=0, multiplier_rule_index=0, component_index=1):
    names = {
        "SET_BET_TYPE": f"bet-{operation_index}-type",
        "SET_NUMBER": f"bet-{operation_index}-g{group_index}-n{number_index}",
        "SET_MULTIPLIER_RULE": f"bet-{operation_index}-m{multiplier_rule_index}-c{component_index}",
        "SET_SPECIAL_PLAY": f"bet-{operation_index}-special-c{component_index}",
        "SET_CONTINUATION": f"bet-{operation_index}-continuation-c{component_index}",
    }
    return names[action]


def _observation_for(prepared, *, epoch=1, duplicate_selector=None, blocked_selector=None):
    selectors = []
    for operation in prepared["logical_plan"]["operations"]:
        op = operation["operation_index"]
        selectors.append(_selector_for("SET_BET_TYPE", op))
        for gi, group in enumerate(operation["number_groups"], 1):
            for ni, _number in enumerate(group, 1):
                selectors.append(_selector_for("SET_NUMBER", op, group_index=gi, number_index=ni))
        for mi, _rule in enumerate(operation["multiplier"]["ordered_rules"], 1):
            selectors.append(_selector_for("SET_MULTIPLIER_RULE", op, multiplier_rule_index=mi))
        if operation["special_play"]["kind"] != "none":
            selectors.append(_selector_for("SET_SPECIAL_PLAY", op))
        if operation["continuation"]["present"]:
            selectors.append(_selector_for("SET_CONTINUATION", op))
    if duplicate_selector:
        selectors.append(duplicate_selector)
    form = {"document_order": 1, "method_category": "POST", "structural_marker_hashes": ["a" * 64], "fields": []}
    seen_selectors = set()
    for index, selector in enumerate(selectors, 1):
        duplicate_marker = ["f" * 64] if selector in seen_selectors else []
        seen_selectors.add(selector)
        field = {
            "document_order": index,
            "tag_name": "input",
            "input_type": "text",
            "label_text": None,
            "accessible_name": None,
            "selector_candidates": [{"selector_kind": "DATA_TESTID", "selector_text": selector, "structural_only": True}],
            "structural_marker_hashes": duplicate_marker,
            "visible": True,
            "disabled": False,
            "readonly": False,
            "occupancy": "NONEMPTY" if selector == blocked_selector else "EMPTY",
            "field_id": "",
            "field_identity_hash": "",
        }
        field["field_identity_hash"] = canonical_sha256(_field_identity_projection(form, field))
        field["field_id"] = "vwf-" + field["field_identity_hash"][:32]
        form["fields"].append(field)
    form_hash = canonical_sha256(_form_identity_projection(form))
    form["form_id"] = "vwform-" + form_hash[:32]
    form["form_fingerprint"] = canonical_sha256(_form_fingerprint_projection(form))
    observation = {
        "schema_version": "vision-webfill-dom-observation-v1",
        "dom_observation_id": "vwdo-" + f"{epoch:032x}",
        "observed_at": f"2026-08-15T08:0{epoch}:00+00:00",
        "page_instance_id": "vwpi-" + f"{epoch:032x}",
        "navigation_epoch": epoch,
        "page_identity": {"site_id": "site-example-539", "game": "539", "origin_identity_hash": "b" * 64, "normalized_path_identity_hash": "c" * 64, "structural_marker_hashes": ["d" * 64]},
        "forms": [form],
        "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1",
        "page_fingerprint": "",
        "observation_hash": "",
        "safety": {"read_only_observation": True, "occupancy_class_derived": True, "raw_input_value_content_exposed": False, "raw_input_value_content_persisted": False, "query_or_fragment_persisted": False, "cookies_or_storage_read": False, "html_or_script_persisted": False, "dom_mutation_performed": False, "click_performed": False, "typing_performed": False, "events_dispatched": False, "navigation_performed": False, "submit_performed": False},
    }
    observation["page_fingerprint"] = canonical_sha256(_page_fingerprint_projection(observation))
    observation["observation_hash"] = canonical_sha256({key: value for key, value in observation.items() if key != "observation_hash"})
    return observation


def _adapter_profile(prepared, observation, *, version=1, omit_action=None):
    templates = {
        "SET_BET_TYPE": ("BET", "bet-{operation_index}-type", ["operation_index"]),
        "SET_NUMBER": ("NUMBER", "bet-{operation_index}-g{group_index}-n{number_index}", ["operation_index", "group_index", "number_index"]),
        "SET_MULTIPLIER_RULE": ("MULTIPLIER_RULE", "bet-{operation_index}-m{multiplier_rule_index}-c{component_index}", ["operation_index", "multiplier_rule_index", "component_index"]),
        "SET_SPECIAL_PLAY": ("SPECIAL_PLAY", "bet-{operation_index}-special-c{component_index}", ["operation_index", "component_index"]),
        "SET_CONTINUATION": ("CONTINUATION", "bet-{operation_index}-continuation-c{component_index}", ["operation_index", "component_index"]),
    }
    rules = []
    for action, (scope, template, placeholders) in templates.items():
        if action == omit_action:
            continue
        rules.append({"rule_id": "rule-" + action.lower().replace("_", "-"), "logical_action": action, "applicable_bet_types": ["normal", "column"], "source_scope": scope, "selector_kind": "DATA_TESTID", "selector_template": template, "allowed_placeholders": placeholders, "component_policy": "SINGLE_FIELD", "required_component_count": 1, "cardinality": "EXACTLY_ONE_PER_COMPONENT", "expected_field": {"tag_name": "input", "input_types": ["text"], "label_text": None, "required_structural_marker_hashes": [], "must_be_visible": True, "must_be_enabled": True, "must_be_writable": True, "must_be_empty": True}, "lossless_required": True})
    form = observation["forms"][0]
    profile = {
        "schema_version": "vision-webfill-adapter-target-profile-v1",
        "adapter_target_profile_id": "watp-example-539",
        "adapter_target_profile_version": version,
        "logical_target_profile_identity": deepcopy(prepared["authority"]["target_profile"]),
        "site_contract": {"site_id": "site-example-539", "game": "539", "origin_identity_hash": "b" * 64, "normalized_path_identity_hash": "c" * 64, "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1", "expected_page_fingerprint": observation["page_fingerprint"], "required_page_marker_hashes": ["d" * 64]},
        "form_contract": {"expected_form_id": form["form_id"], "expected_form_fingerprint": form["form_fingerprint"], "required_form_marker_hashes": ["a" * 64], "exact_form_match": True},
        "logical_field_rules": rules,
        "created_at": f"2026-08-15T08:10:0{version}+00:00",
        "profile_integrity_hash": "",
        "safety": {"contains_bet_values": False, "logical_profile_extension_only": True, "read_only_mapping_only": True, "selector_values_are_structural_only": True, "browser_execution_authorized": False, "dom_mutation_authorized": False, "fill_authorized": False, "submit_authorized": False},
    }
    profile["profile_integrity_hash"] = canonical_sha256({key: value for key, value in profile.items() if key != "profile_integrity_hash"})
    return profile


def _mapping_fixture(tmp_path: Path, *, bets=None, cancelled=None, duplicate_selector=None, blocked_selector=None):
    queue, claims, prepares, prepared, claim, clock, validator = _upstream(tmp_path, bets=bets, cancelled=cancelled)
    root = tmp_path / "mapping"
    observations = WebfillDomObservationStore(root)
    observation = _observation_for(prepared, duplicate_selector=duplicate_selector, blocked_selector=blocked_selector)
    observations.register_observation(observation)
    profiles = WebfillAdapterTargetProfileStore(root)
    profile = _adapter_profile(prepared, observation)
    profiles.register_profile(profile, activate=True)
    previews = WebfillMappingPreviewStore(root, prepares, observations, profiles, clock=clock)
    request = {
        "schema_version": "vision-webfill-mapping-preview-request-v1",
        "prepare_id": prepared["prepare_id"],
        "expected_prepare_record_integrity_hash": prepared["record_integrity_hash"],
        "expected_deterministic_plan_hash": prepared["deterministic_plan_hash"],
        "expected_authority_binding_hash": prepared["authority_binding_hash"],
        "claim_id": claim["claim_id"],
        "claim_generation": claim["claim_generation"],
        "claim_session_id": SESSION,
        "fencing_token": claim["fencing_token"],
        "adapter_target_profile_id": profile["adapter_target_profile_id"],
        "adapter_target_profile_version": profile["adapter_target_profile_version"],
        "expected_adapter_target_profile_integrity_hash": profile["profile_integrity_hash"],
        "dom_observation_id": observation["dom_observation_id"],
        "expected_observation_hash": observation["observation_hash"],
        "expected_page_fingerprint": observation["page_fingerprint"],
        "expected_page_instance_id": observation["page_instance_id"],
        "expected_navigation_epoch": observation["navigation_epoch"],
        "idempotency_key": "vwmi-" + "5" * 32,
    }
    return {"queue": queue, "claims": claims, "prepares": prepares, "prepared": prepared, "claim": claim, "clock": clock, "validator": validator, "observations": observations, "observation": observation, "profiles": profiles, "profile": profile, "previews": previews, "request": request, "root": root}


def _create(fixture, request=None):
    return fixture["previews"].create_mapping_preview(request or fixture["request"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)


def test_valid_mapping_preserves_nested_structure_and_excludes_cancelled(tmp_path):
    fx = _mapping_fixture(tmp_path)
    result = _create(fx)
    artifact = result["artifact"]
    assert result["status"] == "VALID_MAPPING_PREVIEW"
    assert all(item["mapping_state"] == "MAPPED_UNIQUE" for item in artifact["mapping_items"])
    pointers = [item["source"]["logical_pointer"] for item in artifact["mapping_items"]]
    assert "/logical_plan/operations/1/number_groups/2/1" in pointers
    assert "/logical_plan/operations/1/multiplier/ordered_rules/1" in pointers
    assert "/logical_plan/operations/1/continuation" in pointers
    assert artifact["cancelled_exclusions"] == [{"human_bet_id": "H-003", "source_section": "cancelled_audit_refs", "excluded_reason": "cancelled_non_executable", "mapping_item_count": 0}]
    raw = (fx["root"] / "mapping-previews" / f"{artifact['mapping_preview_id']}.json").read_text(encoding="utf-8")
    assert '"01"' not in raw and '"number_groups"' not in raw and fx["claim"]["fencing_token"] not in raw
    assert all(value is False for key, value in artifact["safety"].items() if key not in {"mapping_preview_only", "read_only_dom_observation", "contains_raw_bet_values", "contains_current_dom_values"})


def test_human_preview_fresh_joins_values_without_persisting_them(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    dto = fx["previews"].get_human_preview(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert dto["display_status"] == "READY"
    assert dto["bets"][1]["human_confirmed_value"]["number_groups"] == [["12"], ["15"], ["06", "16"]]
    assert dto["bets"][1]["human_confirmed_value"]["multiplier"]["ordered_rules"] == ["2X3", "3X1"]
    assert dto["safety"]["persisted"] is False


@pytest.mark.parametrize("extra", ["bets", "numbers", "number_groups", "multiplier", "special_play", "continuation", "logical_plan", "dom_values", "field_values", "selector_override", "confidence_override", "preview_state", "executable_state"])
def test_identity_only_request_rejects_client_values(tmp_path, extra):
    fx = _mapping_fixture(tmp_path)
    request = deepcopy(fx["request"])
    request[extra] = []
    with pytest.raises(WebfillMappingError) as raised:
        _create(fx, request)
    assert raised.value.code == "MAPPING_REQUEST_INVALID"
    assert not list((fx["root"] / "mapping-previews").glob("*.json"))


def test_duplicate_and_tuple_replay_return_same_immutable_preview(tmp_path):
    fx = _mapping_fixture(tmp_path)
    first = _create(fx)
    second = _create(fx)
    other = deepcopy(fx["request"])
    other["idempotency_key"] = "vwmi-" + "6" * 32
    third = _create(fx, other)
    assert second["replayed"] is True
    assert first["artifact"] == second["artifact"] == third["artifact"]


def test_ambiguous_missing_and_blocked_are_diagnostic_only(tmp_path):
    missing = _mapping_fixture(tmp_path / "missing")
    missing_observation = deepcopy(missing["observation"])
    missing_observation["forms"][0]["fields"] = missing_observation["forms"][0]["fields"][:-1]
    # A new consistent fixture is simpler for missing: profile rule selector has no field.
    missing_profile = deepcopy(missing["profile"])
    missing_profile["adapter_target_profile_version"] = 2
    missing_profile["created_at"] = "2026-08-15T08:10:02+00:00"
    missing_profile["logical_field_rules"][-1]["selector_template"] = "never-present-{operation_index}-{component_index}"
    missing_profile["profile_integrity_hash"] = canonical_sha256({key: value for key, value in missing_profile.items() if key != "profile_integrity_hash"})
    missing["profiles"].register_profile(missing_profile, activate=True)
    missing["request"]["adapter_target_profile_version"] = 2
    missing["request"]["expected_adapter_target_profile_integrity_hash"] = missing_profile["profile_integrity_hash"]
    result = _create(missing)
    assert result["status"] == "BLOCKED_MAPPING_PREVIEW"
    assert any(item["mapping_state"] == "MISSING" for item in result["artifact"]["mapping_items"])

    selector = "bet-1-type"
    ambiguous = _mapping_fixture(tmp_path / "ambiguous", duplicate_selector=selector)
    result = _create(ambiguous)
    assert any(item["mapping_state"] == "AMBIGUOUS" for item in result["artifact"]["mapping_items"])

    blocked = _mapping_fixture(tmp_path / "blocked", blocked_selector=selector)
    result = _create(blocked)
    assert any(item["mapping_state"] == "BLOCKED" for item in result["artifact"]["mapping_items"])
    assert result["artifact"]["safety"]["approved_for_fill"] is False


def test_new_observation_makes_old_preview_observation_stale(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    newer = _observation_for(fx["prepared"], epoch=2)
    fx["observations"].register_observation(newer)
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "OBSERVATION_STALE"
    assert state["artifact"] == artifact


def test_profile_advance_supersedes_old_preview(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    profile2 = _adapter_profile(fx["prepared"], fx["observation"], version=2)
    fx["profiles"].register_profile(profile2, activate=True)
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "SUPERSEDED"
    events = fx["profiles"].get_lifecycle_events(fx["profile"]["adapter_target_profile_id"])
    assert events[-1]["event_type"] == "ACTIVATED"
    assert events[-1]["adapter_target_profile_version"] == 2
    assert events[-1]["superseded_version"] == 1
    with pytest.raises(WebfillMappingError) as raised:
        fx["profiles"].activate_version(fx["profile"]["adapter_target_profile_id"], 1)
    assert raised.value.code == "MAPPING_TARGET_PROFILE_SUPERSEDED"


def test_release_invalidates_preview_and_old_fence_cannot_read(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    key = "qik-" + "7" * 32
    action = fx["claims"].bind_release_action(claim_id=fx["claim"]["claim_id"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION, idempotency_key=key)
    fx["claims"].release({"action_id": action["action_id"], "idempotency_key": key}, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "AUTHORITY_BLOCKED"


def test_wrong_fence_is_rejected_without_terminalizing_preview(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    with pytest.raises(WebfillMappingError) as raised:
        fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token="vqf-" + "9" * 64, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert raised.value.code == "MAPPING_CLAIM_INVALID"
    assert [event["event_type"] for event in fx["previews"].get_lifecycle_events(artifact["mapping_preview_id"])] == ["PREVIEWED"]


def test_claim_expiry_terminalizes_preview_and_never_mutates_snapshot(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    before = deepcopy(artifact)
    fx["clock"].advance(3600)
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "EXPIRED"
    assert state["artifact"] == before


def test_claim_renew_same_generation_keeps_preview_current(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    fx["clock"].advance(1)
    key = "qik-" + "a" * 32
    action = fx["claims"].bind_renew_action(claim_id=fx["claim"]["claim_id"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION, idempotency_key=key)
    renewed = fx["claims"].renew({"action_id": action["action_id"], "idempotency_key": key}, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert renewed["claim_generation"] == fx["claim"]["claim_generation"]
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "PREVIEWED"
    assert state["artifact"] == artifact


def test_candidate_stale_blocks_claim_queue_prepare_and_preview(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    fx["validator"].failure_code = "CANDIDATE_STALE"
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "AUTHORITY_BLOCKED"
    queue_state = fx["queue"].get_entry(fx["prepared"]["authority"]["queue"]["queue_entry_id"])
    assert queue_state["state"] == "BLOCKED"


@pytest.mark.parametrize("mismatch", ["fingerprint", "game", "path"])
def test_page_identity_mismatch_creates_zero_preview(tmp_path, mismatch):
    fx = _mapping_fixture(tmp_path)
    profile = deepcopy(fx["profile"])
    profile["adapter_target_profile_version"] = 2
    profile["created_at"] = "2026-08-15T08:10:02+00:00"
    if mismatch == "fingerprint":
        profile["site_contract"]["expected_page_fingerprint"] = "f" * 64
    elif mismatch == "game":
        profile["site_contract"]["game"] = "649"
    else:
        profile["site_contract"]["normalized_path_identity_hash"] = "f" * 64
    profile["profile_integrity_hash"] = canonical_sha256({key: value for key, value in profile.items() if key != "profile_integrity_hash"})
    fx["profiles"].register_profile(profile, activate=True)
    request = deepcopy(fx["request"])
    request["adapter_target_profile_version"] = 2
    request["expected_adapter_target_profile_integrity_hash"] = profile["profile_integrity_hash"]
    with pytest.raises(WebfillMappingError) as raised:
        _create(fx, request)
    assert raised.value.code == "MAPPING_PAGE_IDENTITY_MISMATCH"
    assert not list((fx["root"] / "mapping-previews").glob("*.json"))


def test_unsupported_continuation_has_no_normal_fallback(tmp_path):
    fx = _mapping_fixture(tmp_path)
    profile = _adapter_profile(fx["prepared"], fx["observation"], version=2, omit_action="SET_CONTINUATION")
    fx["profiles"].register_profile(profile, activate=True)
    request = deepcopy(fx["request"])
    request["adapter_target_profile_version"] = 2
    request["expected_adapter_target_profile_integrity_hash"] = profile["profile_integrity_hash"]
    result = _create(fx, request)
    continuation = [item for item in result["artifact"]["mapping_items"] if item["source"]["logical_action"] == "SET_CONTINUATION"]
    assert continuation and continuation[0]["mapping_state"] == "UNSUPPORTED"
    assert result["status"] == "BLOCKED_MAPPING_PREVIEW"
    assert result["artifact"]["bet_summaries"][1]["bet_type"] == "column"


def test_rehashed_pointer_tamper_is_rejected_against_live_recompile(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    preview_id = artifact["mapping_preview_id"]
    transaction_path = next((fx["root"] / "preview-transactions").glob("*.json"))
    transaction = __import__("json").loads(transaction_path.read_text(encoding="utf-8"))
    tampered = transaction["artifact"]
    tampered["mapping_items"][0]["source"]["logical_pointer"] = "/logical_plan/operations/0/number_groups/0/0"
    tampered["mapping_items"][0]["source"]["intended_value_hash"] = "f" * 64
    tampered["mapping_content_hash"] = canonical_sha256(fx["previews"]._mapping_content_projection(tampered))
    tampered["record_integrity_hash"] = canonical_sha256({key: value for key, value in tampered.items() if key != "record_integrity_hash"})
    transaction["artifact"] = tampered
    event = transaction["previewed_event"]
    event["mapping_content_hash"] = tampered["mapping_content_hash"]
    event["preview_record_integrity_hash"] = tampered["record_integrity_hash"]
    event["event_integrity_hash"] = canonical_sha256({key: value for key, value in event.items() if key != "event_integrity_hash"})
    transaction["previewed_event"] = event
    idem_path = next((fx["root"] / "idempotency").glob("*.json"))
    tuple_path = next((fx["root"] / "authority-tuple-index").glob("*.json"))
    commit_path = fx["root"] / "mapping-preview-commits" / f"{preview_id}.json"
    event_path = fx["root"] / "lifecycle-events" / preview_id / "000001.json"
    preview_path = fx["root"] / "mapping-previews" / f"{preview_id}.json"
    idem = __import__("json").loads(idem_path.read_text(encoding="utf-8")); idem["record_integrity_hash"] = tampered["record_integrity_hash"]
    tuple_record = __import__("json").loads(tuple_path.read_text(encoding="utf-8")); tuple_record["record_integrity_hash"] = tampered["record_integrity_hash"]; tuple_record["mapping_content_hash"] = tampered["mapping_content_hash"]
    commit = {"mapping_preview_id": preview_id, "mapping_content_hash": tampered["mapping_content_hash"], "record_integrity_hash": tampered["record_integrity_hash"]}
    for path, value in ((transaction_path, transaction), (preview_path, tampered), (event_path, event), (idem_path, idem), (tuple_path, tuple_record), (commit_path, commit)):
        path.write_bytes(__import__("betguard.vision.candidate_authority", fromlist=["canonical_json_bytes"]).canonical_json_bytes(value))
    with pytest.raises(WebfillMappingError) as raised:
        fx["previews"].get_mapping_preview_state(preview_id, claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert raised.value.code == "MAPPING_NONDETERMINISTIC"


def test_corrupt_pending_mapping_transaction_fails_before_claim_clock_write(tmp_path):
    fx = _mapping_fixture(tmp_path)
    _create(fx)
    transaction_path = next((fx["root"] / "preview-transactions").glob("*.json"))
    transaction = __import__("json").loads(transaction_path.read_text(encoding="utf-8"))
    transaction["candidate_snapshot"] = {"bets": []}
    transaction_path.write_bytes(canonical_json_bytes(transaction))
    clock_path = fx["queue"].claim_store_root / "clock" / "state.json"
    clock_before = clock_path.read_bytes()
    with pytest.raises(WebfillMappingError) as raised:
        _create(fx)
    assert raised.value.code in {"MAPPING_STORE_CORRUPT", "MAPPING_SCHEMA_INVALID"}
    assert clock_path.read_bytes() == clock_before


def test_old_claim_generation_and_fence_never_authorize_after_reclaim(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    release_key = "qik-" + "8" * 32
    release_action = fx["claims"].bind_release_action(claim_id=fx["claim"]["claim_id"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION, idempotency_key=release_key)
    fx["claims"].release({"action_id": release_action["action_id"], "idempotency_key": release_key}, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    claim_key = "qik-" + "9" * 32
    action = fx["claims"].bind_claim_action(authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id="session-new", idempotency_key=claim_key)
    newer = fx["claims"].claim_next({"action_id": action["action_id"], "idempotency_key": claim_key}, authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id="session-new")
    assert newer["claim_generation"] == fx["claim"]["claim_generation"] + 1
    state = fx["previews"].get_mapping_preview_state(artifact["mapping_preview_id"], claim_generation=fx["claim"]["claim_generation"], fencing_token=fx["claim"]["fencing_token"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert state["state"] == "AUTHORITY_BLOCKED"
    assert newer["fencing_token"] != fx["claim"]["fencing_token"]


def test_restart_recovers_committed_preview_and_partial_is_invisible(tmp_path, monkeypatch):
    fx = _mapping_fixture(tmp_path)
    original = fx["previews"]._resume_transaction

    def crash(transaction):
        artifact = transaction["artifact"]
        from betguard.vision.webfill_mapping_preview import _write_immutable

        _write_immutable(fx["previews"]._previews / f"{artifact['mapping_preview_id']}.json", artifact)
        raise RuntimeError("crash")

    monkeypatch.setattr(fx["previews"], "_resume_transaction", crash)
    with pytest.raises(RuntimeError):
        _create(fx)
    assert not list((fx["root"] / "mapping-preview-commits").glob("*.json"))
    monkeypatch.setattr(fx["previews"], "_resume_transaction", original)
    restarted = WebfillMappingPreviewStore(fx["root"], fx["prepares"], fx["observations"], fx["profiles"], clock=fx["clock"])
    result = restarted.create_mapping_preview(fx["request"], authenticated_principal=PRINCIPAL, consumer_id=CONSUMER, server_session_id=SESSION)
    assert result["replayed"] is True
    assert list((fx["root"] / "mapping-preview-commits").glob("*.json"))


@pytest.mark.parametrize("sample_id", ["sample-007", "sample-008", "sample-010", "sample-011", "sample-014"])
def test_five_sample_shapes_preserve_value_semantics(sample_id, tmp_path):
    shapes = {
        "sample-007": [_bet("H-001", "column", [["03"], ["16"]], rules=["2/3X1"], special={"kind": "tail", "raw_text": "9tail", "scope": "bet", "resolved": True})],
        "sample-008": [_bet("H-001", "normal", [["08", "04", "26", "09"]])],
        "sample-010": [_bet("H-001", "normal", [["01", "02"]], rules=["2X2", "3X5"], special={"kind": "each", "raw_text": "each", "scope": "bet", "resolved": True})],
        "sample-011": [_bet("H-001", "column", [["30"], ["35", "36", "38"]], rules=["3/4X1"], continuation=True)],
        "sample-014": [_bet("H-001", "column", [["03"], ["08", "09"]], rules=["2X1", "3X1"], continuation=True)],
    }
    fx = _mapping_fixture(tmp_path, bets=shapes[sample_id], cancelled=[{"human_bet_id": "H-099", "cancelled": True, "active": False, "executable": False}])
    before = deepcopy(fx["prepared"]["logical_plan"])
    artifact = _create(fx)["artifact"]
    assert artifact["mapping_preview_status"] == "VALID_MAPPING_PREVIEW"
    assert fx["prepared"]["logical_plan"] == before
    assert artifact["cancelled_exclusions"][0]["mapping_item_count"] == 0
    assert len({item["source"]["logical_pointer"] for item in artifact["mapping_items"] if item["source"]["logical_action"] == "SET_NUMBER"}) == sum(len(group) for group in before["operations"][0]["number_groups"])


def test_public_surface_has_no_browser_fill_submit_or_completion_api():
    forbidden = {"click", "type", "navigate", "fill", "submit", "complete", "mark_completed", "execute"}
    public = {name.lower() for name in dir(WebfillMappingPreviewStore) if not name.startswith("_")}
    assert not (public & forbidden)
    source = Path("src/betguard/vision/webfill_mapping_preview.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {alias.name.lower() for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not any(any(token in name for token in ("playwright", "selenium", "browser", "qwen", "dashscope")) for name in imports)


def test_normative_five_schemas_validate_created_records(tmp_path):
    fx = _mapping_fixture(tmp_path)
    artifact = _create(fx)["artifact"]
    event = fx["previews"].get_lifecycle_events(artifact["mapping_preview_id"])[0]
    root = Path("docs/schemas")
    records = {
        "vision_webfill_dom_observation_v1.schema.json": fx["observation"],
        "vision_webfill_adapter_target_profile_v1.schema.json": fx["profile"],
        "vision_webfill_mapping_preview_request_v1.schema.json": fx["request"],
        "vision_webfill_mapping_preview_artifact_v1.schema.json": artifact,
        "vision_webfill_mapping_preview_lifecycle_event_v1.schema.json": event,
    }
    for filename, record in records.items():
        schema = __import__("json").loads((root / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)


def test_observation_and_profile_partial_publish_recover_exactly(tmp_path):
    fx = _mapping_fixture(tmp_path)
    observation_commit = fx["root"] / "dom-observation-commits" / f"{fx['observation']['dom_observation_id']}.json"
    observation_commit.unlink()
    assert fx["observations"].register_observation(fx["observation"])["observation_hash"] == fx["observation"]["observation_hash"]
    profile_commit = fx["root"] / "adapter-target-profile-commits" / fx["profile"]["adapter_target_profile_id"] / "00000001.json"
    profile_commit.unlink()
    assert fx["profiles"].register_profile(fx["profile"])["profile_integrity_hash"] == fx["profile"]["profile_integrity_hash"]


def test_old_observation_cannot_be_reactivated_after_navigation(tmp_path):
    fx = _mapping_fixture(tmp_path)
    newer = _observation_for(fx["prepared"], epoch=2)
    fx["observations"].register_observation(newer)
    with pytest.raises(WebfillMappingError) as raised:
        fx["observations"].register_observation(fx["observation"], activate=True)
    assert raised.value.code == "MAPPING_OBSERVATION_STALE"


def test_concurrent_same_observation_and_profile_registration_is_idempotent(tmp_path):
    fx = _mapping_fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        observations = list(pool.map(lambda _: fx["observations"].register_observation(fx["observation"]), range(4)))
        profiles = list(pool.map(lambda _: fx["profiles"].register_profile(fx["profile"]), range(4)))
    assert all(item == observations[0] for item in observations)
    assert all(item == profiles[0] for item in profiles)


def test_observation_rejects_raw_value_cookie_html_and_script_fields(tmp_path):
    fx = _mapping_fixture(tmp_path)
    for key in ("raw_value", "cookie", "raw_html", "script", "screenshot"):
        bad = deepcopy(fx["observation"])
        bad[key] = "forbidden"
        with pytest.raises(WebfillMappingError):
            fx["observations"].register_observation(bad)


def test_locked_m01_m24_and_five_sample_inventory_is_complete():
    text = Path("docs/test-specs/gate3c1_webfill_adapter_mapping_preview_test_matrix.md").read_text(encoding="utf-8")
    assert all(f"M{index:02d}" in text for index in range(1, 25))
    assert all(sample in text for sample in ("sample-007", "sample-008", "sample-010", "sample-011", "sample-014"))
    coverage = {
        "M01": "test_valid_mapping_preserves_nested_structure_and_excludes_cancelled",
        "M02": "test_ambiguous_missing_and_blocked_are_diagnostic_only",
        "M03": "test_ambiguous_missing_and_blocked_are_diagnostic_only",
        "M04": "test_page_identity_mismatch_creates_zero_preview",
        "M05": "test_page_identity_mismatch_creates_zero_preview",
        "M06": "test_ambiguous_missing_and_blocked_are_diagnostic_only",
        "M07": "test_five_sample_shapes_preserve_value_semantics",
        "M08": "test_valid_mapping_preserves_nested_structure_and_excludes_cancelled",
        "M09": "test_valid_mapping_preserves_nested_structure_and_excludes_cancelled",
        "M10": "test_five_sample_shapes_preserve_value_semantics",
        "M11": "test_unsupported_continuation_has_no_normal_fallback",
        "M12": "test_valid_mapping_preserves_nested_structure_and_excludes_cancelled",
        "M13": "test_release_invalidates_preview_and_old_fence_cannot_read",
        "M14": "test_claim_expiry_terminalizes_preview_and_never_mutates_snapshot",
        "M15": "test_wrong_fence_is_rejected_without_terminalizing_preview",
        "M16": "test_candidate_stale_blocks_claim_queue_prepare_and_preview",
        "M17": "test_candidate_stale_blocks_claim_queue_prepare_and_preview",
        "M18": "test_profile_advance_supersedes_old_preview",
        "M19": "test_new_observation_makes_old_preview_observation_stale",
        "M20": "test_identity_only_request_rejects_client_values",
        "M21": "test_rehashed_pointer_tamper_is_rejected_against_live_recompile",
        "M22": "test_restart_recovers_committed_preview_and_partial_is_invisible",
        "M23": "test_five_sample_shapes_preserve_value_semantics",
        "M24": "test_unsupported_continuation_has_no_normal_fallback",
    }
    assert set(coverage) == {f"M{index:02d}" for index in range(1, 25)}
    assert all(callable(globals()[name]) for name in coverage.values())
