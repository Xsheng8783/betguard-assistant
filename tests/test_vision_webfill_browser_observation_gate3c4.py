from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from betguard.vision.candidate_authority import canonical_sha256
from betguard.vision.webfill_browser_observation import (
    WebfillBrowserObservationAdapter,
    WebfillBrowserObservationError,
)
from betguard.vision.webfill_mapping_preview import (
    WebfillAdapterTargetProfileStore,
    WebfillDomObservationStore,
    _field_identity_projection,
    _form_fingerprint_projection,
    _form_identity_projection,
    _page_fingerprint_projection,
)
from betguard.vision.webfill_target_profiles import WebfillTargetProfileStore


PRINCIPAL = "human-reviewer"
SESSION = "interactive-session"
SENTINEL = "RAW-VALUE-MUST-NEVER-ESCAPE-8f73b7"


class SyntheticReadOnlyPort:
    def __init__(self, state, structure, *, raw_values=None):
        self.state = deepcopy(state)
        self.structure = deepcopy(structure)
        self.raw_values = deepcopy(raw_values or {})
        self.page_states = []
        self.structures = []
        self.page_reads = 0
        self.structure_reads = 0
        self.raw_value_reads = 0
        self.mutations = 0
        self.navigations = 0
        self.clicks = 0
        self.typing = 0

    def get_page_state(self, **_kwargs):
        self.page_reads += 1
        if self.page_states:
            return deepcopy(self.page_states.pop(0))
        return deepcopy(self.state)

    def read_allowlisted_structure(self, **_kwargs):
        self.structure_reads += 1
        if self.structures:
            value = deepcopy(self.structures.pop(0))
        else:
            value = deepcopy(self.structure)
        if not value.get("sensitive_field_detected", False):
            for form in value.get("forms", []):
                for field in form.get("fields", []):
                    selector = field["selector_candidates"][0]["selector_text"]
                    if selector in self.raw_values:
                        self.raw_value_reads += 1
                        field["occupancy"] = "EMPTY" if self.raw_values[selector] == "" else "NONEMPTY"
        return value


def _logical_profile():
    profile = {
        "schema_version": "vision-webfill-target-profile-v1",
        "target_profile_id": "wtp-betguard-539-logical",
        "target_profile_version": 1,
        "game": "539",
        "capabilities": {
            "supported_bet_types": ["normal", "column"],
            "supported_special_play_kinds": ["none", "tail", "each", "half_car"],
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
        "created_at": "2026-08-15T08:00:01+00:00",
        "profile_integrity_hash": "",
    }
    profile["profile_integrity_hash"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profile_integrity_hash"}
    )
    return profile


def _structure():
    selectors = [
        "bet-1-type",
        "bet-1-g1-n1",
        "bet-1-m1-c1",
        "bet-1-special-c1",
        "bet-1-continuation-c1",
    ]
    fields = []
    for index, selector in enumerate(selectors, 1):
        fields.append(
            {
                "document_order": index,
                "tag_name": "input",
                "input_type": "text",
                "label_text": None,
                "accessible_name": None,
                "selector_candidates": [
                    {"selector_kind": "DATA_TESTID", "selector_text": selector, "structural_only": True}
                ],
                "structural_marker_hashes": [],
                "visible": True,
                "disabled": False,
                "readonly": False,
                "occupancy": "EMPTY",
            }
        )
    return {
        "schema_version": "vision-webfill-read-only-structure-v1",
        "page_marker_hashes": ["d" * 64],
        "forms": [
            {
                "document_order": 1,
                "method_category": "POST",
                "structural_marker_hashes": ["a" * 64],
                "fields": fields,
            }
        ],
        "sensitive_field_detected": False,
        "unsupported_control_detected": False,
    }


def _reference_observation(structure):
    forms = []
    for source_form in structure["forms"]:
        form = {
            "form_id": "",
            "document_order": source_form["document_order"],
            "method_category": source_form["method_category"],
            "structural_marker_hashes": deepcopy(source_form["structural_marker_hashes"]),
            "fields": [],
            "form_fingerprint": "",
        }
        for source_field in source_form["fields"]:
            field = deepcopy(source_field)
            field["field_id"] = ""
            field["field_identity_hash"] = ""
            field["field_identity_hash"] = canonical_sha256(_field_identity_projection(form, field))
            field["field_id"] = "vwf-" + field["field_identity_hash"][:32]
            form["fields"].append(field)
        form_hash = canonical_sha256(_form_identity_projection(form))
        form["form_id"] = "vwform-" + form_hash[:32]
        form["form_fingerprint"] = canonical_sha256(_form_fingerprint_projection(form))
        forms.append(form)
    observation = {
        "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1",
        "page_identity": {
            "site_id": "site-example-539",
            "game": "539",
            "origin_identity_hash": "b" * 64,
            "normalized_path_identity_hash": "c" * 64,
            "structural_marker_hashes": ["d" * 64],
        },
        "forms": forms,
    }
    observation["page_fingerprint"] = canonical_sha256(_page_fingerprint_projection(observation))
    return observation


def _adapter_profile(logical, structure):
    reference = _reference_observation(structure)
    templates = {
        "SET_BET_TYPE": ("BET", "bet-{operation_index}-type", ["operation_index"]),
        "SET_NUMBER": (
            "NUMBER",
            "bet-{operation_index}-g{group_index}-n{number_index}",
            ["operation_index", "group_index", "number_index"],
        ),
        "SET_MULTIPLIER_RULE": (
            "MULTIPLIER_RULE",
            "bet-{operation_index}-m{multiplier_rule_index}-c{component_index}",
            ["operation_index", "multiplier_rule_index", "component_index"],
        ),
        "SET_SPECIAL_PLAY": (
            "SPECIAL_PLAY",
            "bet-{operation_index}-special-c{component_index}",
            ["operation_index", "component_index"],
        ),
        "SET_CONTINUATION": (
            "CONTINUATION",
            "bet-{operation_index}-continuation-c{component_index}",
            ["operation_index", "component_index"],
        ),
    }
    rules = []
    for action, (scope, template, placeholders) in templates.items():
        rules.append(
            {
                "rule_id": "rule-" + action.lower().replace("_", "-"),
                "logical_action": action,
                "applicable_bet_types": ["normal", "column"],
                "source_scope": scope,
                "selector_kind": "DATA_TESTID",
                "selector_template": template,
                "allowed_placeholders": placeholders,
                "component_policy": "SINGLE_FIELD",
                "required_component_count": 1,
                "cardinality": "EXACTLY_ONE_PER_COMPONENT",
                "expected_field": {
                    "tag_name": "input",
                    "input_types": ["text"],
                    "label_text": None,
                    "required_structural_marker_hashes": [],
                    "must_be_visible": True,
                    "must_be_enabled": True,
                    "must_be_writable": True,
                    "must_be_empty": True,
                },
                "lossless_required": True,
            }
        )
    form = reference["forms"][0]
    profile = {
        "schema_version": "vision-webfill-adapter-target-profile-v1",
        "adapter_target_profile_id": "watp-example-539",
        "adapter_target_profile_version": 1,
        "logical_target_profile_identity": {
            "target_profile_id": logical["target_profile_id"],
            "target_profile_version": logical["target_profile_version"],
            "profile_integrity_hash": logical["profile_integrity_hash"],
        },
        "site_contract": {
            "site_id": "site-example-539",
            "game": "539",
            "origin_identity_hash": "b" * 64,
            "normalized_path_identity_hash": "c" * 64,
            "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1",
            "expected_page_fingerprint": reference["page_fingerprint"],
            "required_page_marker_hashes": ["d" * 64],
        },
        "form_contract": {
            "expected_form_id": form["form_id"],
            "expected_form_fingerprint": form["form_fingerprint"],
            "required_form_marker_hashes": ["a" * 64],
            "exact_form_match": True,
        },
        "logical_field_rules": rules,
        "created_at": "2026-08-15T08:10:01+00:00",
        "profile_integrity_hash": "",
        "safety": {
            "contains_bet_values": False,
            "logical_profile_extension_only": True,
            "read_only_mapping_only": True,
            "selector_values_are_structural_only": True,
            "browser_execution_authorized": False,
            "dom_mutation_authorized": False,
            "fill_authorized": False,
            "submit_authorized": False,
        },
    }
    profile["profile_integrity_hash"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profile_integrity_hash"}
    )
    return profile


def _state():
    return {
        "schema_version": "vision-webfill-read-only-page-state-v1",
        "browser_runtime_id": "vwbr-" + "1" * 32,
        "browser_session_id": "vwbs-" + "2" * 32,
        "browser_connection_id": "vwbc-" + "3" * 32,
        "connection_generation": 1,
        "target_tab_handle": "vwth-" + "4" * 32,
        "target_frame_handle": "vwfh-" + "5" * 32,
        "tab_instance_id": "vwti-" + "6" * 32,
        "frame_instance_id": "vwfi-" + "7" * 32,
        "document_instance_id": "vwdi-" + "8" * 32,
        "page_instance_id": "vwpi-" + "9" * 32,
        "navigation_epoch": 1,
        "dom_mutation_generation": 0,
        "site_id": "site-example-539",
        "game": "539",
        "origin_identity_hash": "b" * 64,
        "normalized_path_identity_hash": "c" * 64,
        "frame_scope": "MAIN_SAME_ORIGIN_LIGHT_DOM",
        "connected": True,
        "runtime_continuity_proven": True,
    }


def _fixture(tmp_path, *, raw_values=None):
    logical = _logical_profile()
    logical_store = WebfillTargetProfileStore(tmp_path / "logical")
    logical_store.register_profile(logical, activate=True)
    structure = _structure()
    for form in structure["forms"]:
        for field in form["fields"]:
            selector = field["selector_candidates"][0]["selector_text"]
            if raw_values and selector in raw_values:
                field["occupancy"] = "EMPTY" if raw_values[selector] == "" else "NONEMPTY"
    profile = _adapter_profile(logical, structure)
    mapping_root = tmp_path / "mapping"
    observation_store = WebfillDomObservationStore(mapping_root)
    profile_store = WebfillAdapterTargetProfileStore(mapping_root)
    profile_store.register_profile(profile, activate=True)
    port = SyntheticReadOnlyPort(_state(), structure, raw_values=raw_values)
    adapter = WebfillBrowserObservationAdapter(
        tmp_path / "capture",
        observation_store,
        profile_store,
        logical_store,
        port,
        clock=lambda: datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )
    bind_kwargs = dict(
        authenticated_principal=PRINCIPAL,
        interactive_session_id=SESSION,
        browser_connection_id=port.state["browser_connection_id"],
        target_tab_handle=port.state["target_tab_handle"],
        target_frame_handle=port.state["target_frame_handle"],
        expected_site_id="site-example-539",
        expected_game="539",
        adapter_target_profile_id=profile["adapter_target_profile_id"],
        adapter_target_profile_version=1,
        expected_adapter_target_profile_integrity_hash=profile["profile_integrity_hash"],
        logical_target_profile_id=logical["target_profile_id"],
        logical_target_profile_version=1,
        expected_logical_target_profile_integrity_hash=logical["profile_integrity_hash"],
        expected_navigation_epoch=1,
        capture_idempotency_key="vwoci-" + "a" * 32,
    )
    request = adapter.bind_capture_action(**bind_kwargs)
    return {
        "adapter": adapter,
        "port": port,
        "request": request,
        "capture_root": tmp_path / "capture",
        "mapping_root": mapping_root,
        "profile": profile,
        "profile_store": profile_store,
        "logical": logical,
        "bind_kwargs": bind_kwargs,
    }


def _capture(fx):
    return fx["adapter"].capture(
        fx["request"], authenticated_principal=PRINCIPAL, interactive_session_id=SESSION
    )


def test_stable_two_pass_capture_is_committed_read_only_and_replayable(tmp_path):
    fx = _fixture(tmp_path)
    first = _capture(fx)
    assert first["status"] == "COMMITTED"
    assert first["replayed"] is False
    assert fx["port"].structure_reads == 2
    assert fx["port"].mutations == fx["port"].navigations == fx["port"].clicks == fx["port"].typing == 0
    second = _capture(fx)
    assert second["capture_id"] == first["capture_id"]
    assert second["replayed"] is True
    assert all(value is False for key, value in first["safety"].items() if key not in {"read_only_capture", "identity_reference_only"})


def test_raw_value_sentinel_is_reduced_to_occupancy_and_never_persisted_or_hashed(tmp_path):
    fx = _fixture(tmp_path, raw_values={"bet-1-type": SENTINEL})
    _capture(fx)
    assert fx["port"].raw_value_reads == 2
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json"))
    assert SENTINEL.encode() not in persisted
    assert canonical_sha256(SENTINEL) .encode() not in persisted
    assert hashlib.sha256(SENTINEL.encode()).hexdigest().encode() not in persisted
    assert b'"occupancy":"NONEMPTY"' in persisted


def test_unstable_structure_fails_without_observation_commit(tmp_path):
    fx = _fixture(tmp_path)
    changed = deepcopy(fx["port"].structure)
    changed["forms"][0]["fields"][0]["occupancy"] = "NONEMPTY"
    fx["port"].structures = [fx["port"].structure, changed, fx["port"].structure, changed]
    with pytest.raises(WebfillBrowserObservationError) as raised:
        _capture(fx)
    assert raised.value.code == "DOM_UNSTABLE"
    assert not list((fx["mapping_root"] / "dom-observations").glob("*.json"))


def test_navigation_toctou_fails_closed(tmp_path):
    fx = _fixture(tmp_path)
    changed = deepcopy(fx["port"].state)
    changed["document_instance_id"] = "vwdi-" + "f" * 32
    fx["port"].page_states = [fx["port"].state, changed, fx["port"].state, changed]
    with pytest.raises(WebfillBrowserObservationError) as raised:
        _capture(fx)
    assert raised.value.code == "NAVIGATION_RACE"


@pytest.mark.parametrize(
    "field",
    [
        "bets",
        "numbers",
        "number_groups",
        "multiplier",
        "special_play",
        "continuation",
        "logical_plan",
        "selector_override",
        "raw_value",
        "dom_html",
        "script",
        "url",
    ],
)
def test_capture_request_is_exact_identity_only(tmp_path, field):
    fx = _fixture(tmp_path)
    fx["request"][field] = "malicious"
    with pytest.raises(WebfillBrowserObservationError) as raised:
        _capture(fx)
    assert raised.value.code == "CAPTURE_REQUEST_INVALID"
    assert fx["port"].page_reads == 0


def test_sensitive_and_unsupported_controls_fail_closed(tmp_path):
    for flag, code in (("sensitive_field_detected", "SENSITIVE_FIELD"), ("unsupported_control_detected", "UNSUPPORTED_CONTROL")):
        fx = _fixture(tmp_path / flag)
        fx["port"].structure[flag] = True
        with pytest.raises(WebfillBrowserObservationError) as raised:
            _capture(fx)
        assert raised.value.code == code


def test_authoritative_context_detects_post_yield_toctou(tmp_path):
    fx = _fixture(tmp_path)
    result = _capture(fx)
    changed = deepcopy(fx["port"].state)
    changed["dom_mutation_generation"] += 1
    fx["port"].page_states = [fx["port"].state, changed]
    with pytest.raises(WebfillBrowserObservationError) as raised:
        with fx["adapter"].authoritative_observation(result["capture_id"]) as observation:
            assert observation["safety"]["dom_mutation_performed"] is False
    assert raised.value.code == "OBSERVATION_STALE"
    assert fx["adapter"].get_capture_state(result["capture_id"])["state"] == "STALE"


def test_transaction_recovery_requires_fresh_stable_capture(tmp_path, monkeypatch):
    fx = _fixture(tmp_path)
    original = fx["adapter"]._resume_transaction
    monkeypatch.setattr(fx["adapter"], "_resume_transaction", lambda _transaction: None)
    first = _capture(fx)
    assert first["status"] == "COMMITTED"
    capture_id = first["capture_id"]
    assert not (fx["capture_root"] / "capture-commits" / f"{capture_id}.json").exists()
    monkeypatch.setattr(fx["adapter"], "_resume_transaction", original)
    restarted = WebfillBrowserObservationAdapter(
        fx["capture_root"],
        WebfillDomObservationStore(fx["mapping_root"]),
        WebfillAdapterTargetProfileStore(fx["mapping_root"]),
        WebfillTargetProfileStore(tmp_path / "logical"),
        fx["port"],
        clock=lambda: datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )
    recovered = restarted.capture(
        fx["request"], authenticated_principal=PRINCIPAL, interactive_session_id=SESSION
    )
    assert recovered["capture_id"] == capture_id
    assert (fx["capture_root"] / "capture-commits" / f"{capture_id}.json").exists()


def test_read_path_never_publishes_pending_transaction(tmp_path, monkeypatch):
    fx = _fixture(tmp_path)
    monkeypatch.setattr(fx["adapter"], "_resume_transaction", lambda _transaction: None)
    result = _capture(fx)
    with pytest.raises(WebfillBrowserObservationError) as raised:
        fx["adapter"].get_capture_state(result["capture_id"])
    assert raised.value.code == "CAPTURE_NOT_FOUND"
    assert not (fx["capture_root"] / "capture-commits" / f"{result['capture_id']}.json").exists()


def test_final_precommit_toctou_marks_capture_stale_and_never_activates_current(tmp_path):
    fx = _fixture(tmp_path)
    changed = deepcopy(fx["port"].state)
    changed["dom_mutation_generation"] = 1
    fx["port"].page_states = [fx["port"].state] * 4 + [changed]
    with pytest.raises(WebfillBrowserObservationError) as raised:
        _capture(fx)
    assert raised.value.code == "OBSERVATION_STALE"
    capture_id = next((fx["capture_root"] / "capture-results").glob("*.json")).stem
    assert fx["adapter"].get_capture_state(capture_id)["state"] == "STALE"
    assert not list((fx["capture_root"] / "current-captures").glob("*.json"))


def test_normative_gate3c3_schemas_validate_real_committed_records(tmp_path):
    fx = _fixture(tmp_path)
    response = _capture(fx)
    capture_id = response["capture_id"]
    result = json.loads(
        (fx["capture_root"] / "capture-results" / f"{capture_id}.json").read_text(encoding="utf-8")
    )
    observation = json.loads(
        next((fx["mapping_root"] / "dom-observations").glob("*.json")).read_text(encoding="utf-8")
    )
    event = json.loads(
        (fx["capture_root"] / "capture-audit-events" / capture_id / "000003.json").read_text(encoding="utf-8")
    )
    cases = [
        ("vision_webfill_browser_observation_capture_request_v1.schema.json", fx["request"]),
        ("vision_webfill_browser_page_identity_v1.schema.json", result["browser_page_identity"]),
        ("vision_webfill_browser_observation_capture_result_v1.schema.json", result),
        ("vision_webfill_browser_observation_audit_event_v1.schema.json", event),
        ("vision_webfill_dom_observation_v1.schema.json", observation),
    ]
    for filename, instance in cases:
        schema = json.loads((Path("docs/schemas") / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)


def test_production_module_has_no_real_browser_or_execution_imports():
    source = Path("src/betguard/vision/webfill_browser_observation.py").read_text(encoding="utf-8")
    forbidden = ["playwright", "selenium", "pyppeteer", "requests", "httpx", "subprocess", "webbrowser"]
    assert all(token not in source.lower() for token in forbidden)
    assert "def fill" not in source and "def submit" not in source and "def click" not in source


def test_synthetic_driver_itself_never_exposes_mutation_methods():
    methods = set(dir(SyntheticReadOnlyPort))
    assert {"click", "type", "navigate", "execute_script", "dispatch_event"}.isdisjoint(methods)


def _expect_capture_code(fx, code):
    with pytest.raises(WebfillBrowserObservationError) as raised:
        _capture(fx)
    assert raised.value.code == code


def _persisted_bytes(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in root.rglob("*.json"))


def _restart_adapter(fx, tmp_path):
    return WebfillBrowserObservationAdapter(
        fx["capture_root"],
        WebfillDomObservationStore(fx["mapping_root"]),
        WebfillAdapterTargetProfileStore(fx["mapping_root"]),
        WebfillTargetProfileStore(tmp_path / "logical"),
        fx["port"],
        clock=lambda: datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("case_id", [f"B{index:02d}" for index in range(1, 37)])
def test_locked_b01_b36_executable_matrix(tmp_path, monkeypatch, case_id):
    """Every locked Gate 3C-3 adversarial row executes against Gate 3C-4."""

    fx = _fixture(tmp_path)
    port = fx["port"]
    if case_id in {"B01", "B02", "B03"}:
        injected = {"B01": "selector_override", "B02": "raw_value", "B03": "dom"}[case_id]
        fx["request"][injected] = SENTINEL
        _expect_capture_code(fx, "CAPTURE_REQUEST_INVALID")
        assert port.page_reads == 0 and SENTINEL.encode() not in _persisted_bytes(tmp_path)
    elif case_id == "B04":
        port.state["target_tab_handle"] = "vwth-" + "f" * 32
        _expect_capture_code(fx, "WRONG_TARGET")
    elif case_id == "B05":
        port.state["origin_identity_hash"] = "e" * 64
        _expect_capture_code(fx, "WRONG_PAGE")
    elif case_id == "B06":
        port.state["game"] = "649"
        _expect_capture_code(fx, "WRONG_GAME")
    elif case_id in {"B07", "B09", "B10"}:
        if case_id == "B07":
            duplicate = deepcopy(port.structure["forms"][0])
            duplicate["document_order"] = 2
            port.structure["forms"].append(duplicate)
        else:
            duplicate = deepcopy(port.structure["forms"][0]["fields"][0])
            duplicate["document_order"] = len(port.structure["forms"][0]["fields"]) + 1
            if case_id == "B10":
                duplicate["visible"] = False
            port.structure["forms"][0]["fields"].append(duplicate)
        _expect_capture_code(fx, "SELECTOR_AMBIGUOUS")
    elif case_id == "B08":
        port.structure["forms"] = []
        _expect_capture_code(fx, "SELECTOR_MISSING")
    elif case_id == "B11":
        port.structure["forms"][0]["fields"][0]["disabled"] = True
        _expect_capture_code(fx, "WRONG_PAGE")
        assert port.mutations == 0
    elif case_id == "B12":
        changed = deepcopy(port.state)
        changed["dom_mutation_generation"] = 1
        port.page_states = [port.state, changed, port.state, changed]
        _expect_capture_code(fx, "DOM_UNSTABLE")
    elif case_id == "B13":
        changed = deepcopy(port.state)
        changed["document_instance_id"] = "vwdi-" + "f" * 32
        port.page_states = [port.state, changed, port.state, changed]
        _expect_capture_code(fx, "NAVIGATION_RACE")
    elif case_id in {"B14", "B16"}:
        changed = deepcopy(port.state)
        changed["page_instance_id"] = "vwpi-" + "e" * 32
        changed["navigation_epoch"] = 2
        port.page_states = [port.state, changed, port.state, changed]
        _expect_capture_code(fx, "NAVIGATION_RACE")
    elif case_id == "B15":
        port.state["navigation_epoch"] = 2
        _expect_capture_code(fx, "OBSERVATION_STALE")
    elif case_id == "B17":
        port.state["connection_generation"] = 2
        port.state["browser_connection_id"] = "vwbc-" + "e" * 32
        _expect_capture_code(fx, "WRONG_TARGET")
    elif case_id == "B18":
        port.state["tab_instance_id"] = "vwti-" + "e" * 32
        port.state["target_tab_handle"] = "vwth-" + "e" * 32
        _expect_capture_code(fx, "WRONG_TARGET")
    elif case_id in {"B19", "B20", "B21"}:
        port.state["frame_scope"] = "CROSS_ORIGIN_IFRAME" if case_id == "B20" else "SHADOW_OR_REPLACED_FRAME"
        _expect_capture_code(fx, "UNSUPPORTED_FRAME")
    elif case_id == "B22":
        changed = deepcopy(port.structure)
        changed["forms"][0]["fields"][0]["selector_candidates"][0]["selector_text"] = "changed-selector"
        port.structures = [port.structure, changed, port.structure, changed]
        _expect_capture_code(fx, "DOM_UNSTABLE")
    elif case_id == "B23":
        malicious = "<script>" + SENTINEL + "</script>"
        port.structure["forms"][0]["fields"][0]["label_text"] = malicious
        _expect_capture_code(fx, "SELECTOR_MISSING")
        assert malicious.encode() not in _persisted_bytes(tmp_path)
    elif case_id == "B24":
        def raw_exception(**_kwargs):
            raise RuntimeError(SENTINEL)

        monkeypatch.setattr(port, "read_allowlisted_structure", raw_exception)
        _expect_capture_code(fx, "DOM_UNSTABLE")
        assert SENTINEL.encode() not in _persisted_bytes(tmp_path)
    elif case_id == "B25":
        original = port.read_allowlisted_structure
        calls = 0

        def activate_profile(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                profile = deepcopy(fx["profile"])
                profile["adapter_target_profile_version"] = 2
                profile["created_at"] = "2026-08-15T08:10:02+00:00"
                profile["profile_integrity_hash"] = canonical_sha256(
                    {key: value for key, value in profile.items() if key != "profile_integrity_hash"}
                )
                fx["profile_store"].register_profile(profile, activate=True)
            return original(**kwargs)

        monkeypatch.setattr(port, "read_allowlisted_structure", activate_profile)
        _expect_capture_code(fx, "PROFILE_MISMATCH")
    elif case_id in {"B26", "B27"}:
        result = _capture(fx)
        assert result["safety"]["fill_authorized"] is False
        source = Path("src/betguard/vision/webfill_browser_observation.py").read_text(encoding="utf-8")
        assert "candidate_consumption" not in source and "validated_candidate_queue" not in source
    elif case_id in {"B28", "B29"}:
        monkeypatch.setattr(fx["adapter"], "_resume_transaction", lambda _transaction: None)
        result = _capture(fx)
        with pytest.raises(WebfillBrowserObservationError) as raised:
            fx["adapter"].get_capture_state(result["capture_id"])
        assert raised.value.code == "CAPTURE_NOT_FOUND"
    elif case_id == "B30":
        original = fx["adapter"]._resume_transaction
        monkeypatch.setattr(fx["adapter"], "_resume_transaction", lambda _transaction: None)
        first = _capture(fx)
        monkeypatch.setattr(fx["adapter"], "_resume_transaction", original)
        restarted = _restart_adapter(fx, tmp_path)
        recovered = restarted.capture(
            fx["request"], authenticated_principal=PRINCIPAL, interactive_session_id=SESSION
        )
        assert recovered["capture_id"] == first["capture_id"]
    elif case_id == "B31":
        changed = deepcopy(fx["bind_kwargs"])
        changed["expected_navigation_epoch"] = 2
        with pytest.raises(WebfillBrowserObservationError) as raised:
            fx["adapter"].bind_capture_action(**changed)
        assert raised.value.code == "CAPTURE_IDEMPOTENCY_CONFLICT"
    elif case_id == "B32":
        changed = deepcopy(port.structure)
        changed["forms"][0]["fields"][0]["occupancy"] = "NONEMPTY"
        port.structures = [port.structure, changed, port.structure, changed]
        _expect_capture_code(fx, "DOM_UNSTABLE")
        assert port.structure_reads == 4
    elif case_id == "B33":
        fx = _fixture(tmp_path / "prefilled", raw_values={"bet-1-type": SENTINEL})
        _capture(fx)
        payload = _persisted_bytes(tmp_path / "prefilled")
        assert SENTINEL.encode() not in payload and canonical_sha256(SENTINEL).encode() not in payload
        assert b'"occupancy":"NONEMPTY"' in payload
    elif case_id == "B34":
        fx = _fixture(tmp_path / "sensitive", raw_values={"bet-1-type": SENTINEL})
        fx["port"].structure["sensitive_field_detected"] = True
        _expect_capture_code(fx, "SENSITIVE_FIELD")
        assert fx["port"].raw_value_reads == 0 and SENTINEL.encode() not in _persisted_bytes(tmp_path / "sensitive")
    elif case_id == "B35":
        result = _capture(fx)
        path = fx["capture_root"] / "capture-results" / f"{result['capture_id']}.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["stable_capture"]["attempt"] = 2
        path.write_text(json.dumps(stored), encoding="utf-8")
        with pytest.raises(WebfillBrowserObservationError) as raised:
            fx["adapter"].get_capture_state(result["capture_id"])
        assert raised.value.code in {"HASH_MISMATCH", "TRANSACTION_RECOVERY_REQUIRED"}
    elif case_id == "B36":
        result = _capture(fx)
        safety = result["safety"]
        assert safety["read_only_capture"] is True and safety["identity_reference_only"] is True
        assert safety["fill_authorized"] is safety["submit_authorized"] is False
        assert safety["queue_or_claim_completed"] is False
    else:  # pragma: no cover - the parameter list is intentionally exhaustive.
        raise AssertionError(case_id)
