from __future__ import annotations

import json
import hashlib
from copy import deepcopy

import pytest

from betguard.webfill.local_sandbox_contracts import LocalSandboxContractError
from betguard.webfill.local_sandbox_provider import InMemorySandboxBrowser
from betguard.webfill.local_sandbox_workflow import LocalSandboxWorkflow
from betguard.webfill.local_sandbox_workflow import MappingPreviewAuthorityLoader
from tests.fixtures.local_sandbox_profiles import sample_case


ACTOR = "human-reviewer"
SESSION = "interactive-session-1"
IDEMPOTENCY = "lsfi-human-click-0001"


def _snapshot(sample="sample-014"):
    operations, cancelled = sample_case(sample)
    return {
        "schema_version": "betguard-local-sandbox-authority-v1",
        "review": {"review_session_id": "review-1", "status": "CONFIRMED", "human_answer_revision": 3, "human_answer_hash": "1" * 64},
        "candidate": {"candidate_id": "vc-1", "candidate_revision": 2, "canonical_content_hash": "2" * 64, "validation_status": "VALID_CURRENT", "lifecycle_state": "CURRENT"},
        "queue": {"queue_entry_id": "vqe-1", "enqueue_sequence": 1, "queue_revision": 1, "entry_integrity_hash": "3" * 64, "state": "QUEUED", "fifo_head": True},
        "claim": {"claim_id": "vqc-1", "claim_generation": 1, "state": "ACTIVE", "fencing_token_hash": "4" * 64, "lease_expires_at": "2026-08-20T12:00:00+00:00"},
        "prepare": {"prepare_id": "vwp-1", "state": "PREPARED", "record_integrity_hash": "5" * 64, "deterministic_plan_hash": "6" * 64, "authority_binding_hash": "7" * 64},
        "mapping": {"mapping_preview_id": "vwmp-1", "state": "PREVIEWED", "status": "VALID_MAPPING_PREVIEW", "record_integrity_hash": "8" * 64},
        "operations": operations,
        "cancelled_exclusions": cancelled,
        "safety": {"server_reloaded": True, "client_values_used": False, "browser_receipt_value_authority": False, "submit_authorized": False, "external_site_authorized": False},
    }


class MutableLoader:
    def __init__(self, snapshot):
        self.snapshot = deepcopy(snapshot)
        self.returned = []
        self.change_after_calls = None

    def __call__(self, review_session_id, actor, session_id):
        assert review_session_id == "review-1"
        assert actor == ACTOR
        assert session_id == SESSION
        value = deepcopy(self.snapshot)
        if self.change_after_calls is not None and len(self.returned) >= self.change_after_calls:
            value["mapping"]["record_integrity_hash"] = "f" * 64
        self.returned.append(deepcopy(value))
        return value


def _workflow(tmp_path, sample="sample-014", browser=None):
    loader = MutableLoader(_snapshot(sample))
    browser = browser or InMemorySandboxBrowser()
    workflow = LocalSandboxWorkflow(tmp_path / "workflow", loader, browser)
    return workflow, loader, browser


def _bind(workflow):
    return workflow.bind_human_fill_action(
        "review-1", authenticated_actor=ACTOR, interactive_session_id=SESSION
    )


def _execute(workflow, action, key=IDEMPOTENCY):
    return workflow.execute_public(
        {"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": action["action_id"], "idempotency_key": key},
        authenticated_actor=ACTOR,
        interactive_session_id=SESSION,
    )


def test_no_explicit_human_action_has_zero_dom_mutation(tmp_path):
    workflow, _loader, browser = _workflow(tmp_path)
    action = _bind(workflow)
    assert action["status"] == "READY_FOR_EXPLICIT_HUMAN_ACTION"
    assert browser.calls == 0
    assert browser.mutation_count == 0


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s["review"].update(status="UNCONFIRMED"), "SANDBOX_REVIEW_UNCONFIRMED"),
    (lambda s: s["candidate"].update(validation_status="STALE"), "SANDBOX_CANDIDATE_INVALID"),
    (lambda s: s["queue"].update(state="BLOCKED"), "SANDBOX_QUEUE_INVALID"),
    (lambda s: s["queue"].update(state="REMOVED"), "SANDBOX_QUEUE_INVALID"),
    (lambda s: s["queue"].update(fifo_head=False), "SANDBOX_QUEUE_INVALID"),
    (lambda s: s["claim"].update(state="EXPIRED"), "SANDBOX_CLAIM_INVALID"),
    (lambda s: s["prepare"].update(state="INVALIDATED"), "SANDBOX_PREPARE_INVALID"),
    (lambda s: s["mapping"].update(status="BLOCKED_MAPPING_PREVIEW"), "SANDBOX_MAPPING_INVALID"),
])
def test_invalid_authority_before_bind_has_zero_dom_mutation(tmp_path, mutation, code):
    workflow, loader, browser = _workflow(tmp_path)
    mutation(loader.snapshot)
    with pytest.raises(LocalSandboxContractError) as raised:
        _bind(workflow)
    assert raised.value.code == code
    assert browser.calls == 0
    assert browser.mutation_count == 0


@pytest.mark.parametrize("mutation", [
    lambda s: s["candidate"].update(validation_status="STALE"),
    lambda s: s["queue"].update(state="BLOCKED"),
    lambda s: s["claim"].update(state="EXPIRED"),
    lambda s: s["prepare"].update(state="INVALIDATED"),
    lambda s: s["mapping"].update(state="REVOKED"),
])
def test_authority_changed_after_binding_has_zero_dom_mutation(tmp_path, mutation):
    workflow, loader, browser = _workflow(tmp_path)
    action = _bind(workflow)
    mutation(loader.snapshot)
    with pytest.raises(LocalSandboxContractError):
        _execute(workflow, action)
    assert browser.calls == 0
    assert browser.mutation_count == 0


@pytest.mark.parametrize("sample", ["sample-007", "sample-008", "sample-010", "sample-011", "sample-014"])
def test_exact_fill_readback_for_required_samples(tmp_path, sample):
    workflow, loader, browser = _workflow(tmp_path, sample)
    original = deepcopy(loader.snapshot)
    action = _bind(workflow)
    result = _execute(workflow, action)
    assert result["status"] == "FILLED_VERIFIED"
    assert result["verification_status"] == "EXACT_READBACK"
    assert browser.rows[0]["number_groups"] == original["operations"][0]["number_groups"]
    assert browser.rows[0]["multiplier"] == original["operations"][0]["multiplier"]
    assert browser.rows[0]["continuation"] == original["operations"][0]["continuation"]
    assert browser.rows[0]["special_play"] == original["operations"][0]["special_play"]
    assert result["cancelled_excluded_count"] == len(original["cancelled_exclusions"])
    assert result["submit_performed"] is False
    assert result["external_site_calls"] == 0
    assert result["auto_submit"] is False
    assert browser.submit_event_count == 0
    assert browser.external_request_count == 0
    assert all(returned == original for returned in loader.returned)


def test_duplicate_click_returns_identical_result_without_mutation(tmp_path):
    workflow, _loader, browser = _workflow(tmp_path)
    action = _bind(workflow)
    first = _execute(workflow, action)
    mutations = browser.mutation_count
    second = _execute(workflow, action)
    assert second == first
    assert browser.calls == 1
    assert browser.mutation_count == mutations
    with pytest.raises(LocalSandboxContractError) as raised:
        _execute(workflow, action, "lsfi-other-click-0002")
    assert raised.value.code == "SANDBOX_ACTION_ALREADY_USED"


def test_partial_fill_requires_manual_review_and_never_retries(tmp_path):
    browser = InMemorySandboxBrowser(fail_after_fields=3)
    workflow, _loader, browser = _workflow(tmp_path, browser=browser)
    action = _bind(workflow)
    first = _execute(workflow, action)
    assert first["status"] == "MANUAL_REVIEW_REQUIRED"
    assert len(first["filled_field_pointers"]) == 3
    mutations = browser.mutation_count
    second = _execute(workflow, action)
    assert second == first
    assert browser.calls == 1
    assert browser.mutation_count == mutations


def test_browser_crash_requires_manual_review_and_never_retries(tmp_path):
    browser = InMemorySandboxBrowser(crash=True)
    workflow, _loader, browser = _workflow(tmp_path, browser=browser)
    action = _bind(workflow)
    result = _execute(workflow, action)
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert _execute(workflow, action) == result
    assert browser.calls == 1


def test_restart_with_started_transaction_never_retries_browser(tmp_path):
    workflow, loader, browser = _workflow(tmp_path)
    action = _bind(workflow)
    idem_hash = hashlib.sha256(IDEMPOTENCY.encode()).hexdigest()
    transaction = {
        "schema_version": "betguard-local-sandbox-transaction-v1",
        "action_id": action["action_id"],
        "idempotency_key_hash": idem_hash,
        "state": "STARTED",
        "authority_snapshot_hash": "9" * 64,
        "filled_field_pointers": [],
    }
    path = workflow.base_dir / "transactions" / f"{idem_hash}.json"
    path.write_text(json.dumps(transaction), encoding="utf-8")
    restarted = LocalSandboxWorkflow(workflow.base_dir, loader, browser)
    result = _execute(restarted, action)
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["manual_review_reason"] == "incomplete_previous_attempt"
    assert browser.calls == 0
    assert _execute(restarted, action) == result


def test_authority_changes_during_fill_requires_manual_review(tmp_path):
    workflow, loader, browser = _workflow(tmp_path)
    loader.change_after_calls = 2  # bind and immediate pre-fill reload remain equal
    action = _bind(workflow)
    result = _execute(workflow, action)
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert browser.calls == 1
    assert result["submit_performed"] is False


def test_client_values_selectors_and_owner_spoof_are_rejected(tmp_path):
    workflow, _loader, browser = _workflow(tmp_path)
    action = _bind(workflow)
    with pytest.raises(LocalSandboxContractError):
        workflow.execute_public(
            {"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": action["action_id"], "idempotency_key": IDEMPOTENCY, "selector": "#evil", "number_groups": [["39"]]},
            authenticated_actor=ACTOR, interactive_session_id=SESSION,
        )
    with pytest.raises(LocalSandboxContractError) as raised:
        workflow.execute_public(
            {"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": action["action_id"], "idempotency_key": IDEMPOTENCY},
            authenticated_actor="attacker", interactive_session_id=SESSION,
        )
    assert raised.value.code == "SANDBOX_ACTION_OWNER_INVALID"
    assert browser.mutation_count == 0


def test_action_record_contains_only_identity_not_candidate_values(tmp_path):
    workflow, loader, _browser = _workflow(tmp_path)
    action = _bind(workflow)
    path = workflow.base_dir / "actions" / f"{action['action_id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    encoded = path.read_text(encoding="utf-8")
    assert record["contains_values"] is False
    assert "operations" not in record
    assert loader.snapshot["operations"][0]["human_bet_id"] not in encoded
    assert "number_groups" not in encoded


def test_concrete_loader_revalidates_existing_gate3b_gate3c_chain(tmp_path):
    from tests.test_vision_webfill_mapping_preview_gate3c2 import (
        CONSUMER, PRINCIPAL, SESSION as GATE_SESSION, _create, _mapping_fixture,
    )

    fixture = _mapping_fixture(tmp_path / "gate-chain")
    preview = _create(fixture)["artifact"]
    claim = fixture["claim"]
    loader = MappingPreviewAuthorityLoader(
        fixture["previews"], fixture["prepares"], fixture["queue"], fixture["claims"]
    )
    loader.register_reference(
        review_session_id="review-1",
        mapping_preview_id=preview["mapping_preview_id"],
        claim_generation=claim["claim_generation"],
        fencing_token=claim["fencing_token"],
        authenticated_principal=PRINCIPAL,
        consumer_id=CONSUMER,
        server_session_id=GATE_SESSION,
    )
    browser = InMemorySandboxBrowser()
    workflow = LocalSandboxWorkflow(tmp_path / "integrated-workflow", loader, browser)
    action = workflow.bind_human_fill_action(
        "review-1", authenticated_actor=PRINCIPAL, interactive_session_id=GATE_SESSION
    )
    result = workflow.execute_public(
        {"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": action["action_id"], "idempotency_key": "lsfi-integrated-chain-0001"},
        authenticated_actor=PRINCIPAL,
        interactive_session_id=GATE_SESSION,
    )
    assert result["status"] == "FILLED_VERIFIED"
    assert browser.rows[1]["number_groups"] == [["12"], ["15"], ["06", "16"]]
    assert browser.rows[1]["multiplier"]["ordered_rules"] == ["2X3", "3X1"]
    assert browser.rows[1]["continuation"]["present"] is True
    assert browser.rows[1]["special_play"]["kind"] == "half_car"
    assert result["cancelled_excluded_count"] == 1
