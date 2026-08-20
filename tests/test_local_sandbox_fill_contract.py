from __future__ import annotations

import pytest

from betguard.webfill.local_sandbox_contracts import (
    LOCAL_SANDBOX_PROFILE_ID,
    LOCAL_SANDBOX_SITE_ID,
    LocalSandboxContractError,
    decode_public_execute_request,
    local_sandbox_profile,
    require_loopback_url,
    validate_authority_snapshot,
)
from betguard.webfill.local_sandbox_page import render_sandbox_page
from tests.fixtures.local_sandbox_profiles import sample_case


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


def test_public_contract_accepts_only_server_tokens():
    request = {"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": "lsfa-" + "a" * 32, "idempotency_key": "lsfi-contract-0001"}
    assert decode_public_execute_request(request) == request
    forbidden = ["bets", "numbers", "number_groups", "multiplier", "continuation", "special_play", "selector", "candidate_payload", "queue_payload", "claim_payload", "prepare_payload", "mapping_payload"]
    for key in forbidden:
        with pytest.raises(LocalSandboxContractError) as raised:
            decode_public_execute_request({**request, key: "malicious"})
        assert raised.value.code == "SANDBOX_REQUEST_INVALID"


@pytest.mark.parametrize("url", [
    "http://localhost:8000/sandbox-fill",
    "http://[::1]:8000/sandbox-fill",
    "https://127.0.0.1:8000/sandbox-fill",
    "http://127.0.0.2:8000/sandbox-fill",
    "http://127.0.0.1:8000/other",
    "http://127.0.0.1:8000/sandbox-fill?redirect=https://example.com",
    "https://example.com/sandbox-fill",
])
def test_external_alias_redirect_and_url_rejected(url):
    with pytest.raises(LocalSandboxContractError) as raised:
        require_loopback_url(url)
    assert raised.value.code == "SANDBOX_EXTERNAL_URL_REJECTED"


def test_exact_loopback_url_and_profile_have_no_submit_authority():
    url = "http://127.0.0.1:8765/sandbox-fill"
    assert require_loopback_url(url, expected_port=8765) == url
    profile = local_sandbox_profile()
    assert profile["site_id"] == LOCAL_SANDBOX_SITE_ID
    assert profile["profile_id"] == LOCAL_SANDBOX_PROFILE_ID
    assert profile["submit_action"] is None
    assert profile["safety"]["submit"] is False
    page = render_sandbox_page()
    assert "type=\"submit\"" not in page
    assert "window.open" not in page
    assert "localStorage" not in page
    assert "sessionStorage" not in page
    assert "document.cookie" not in page


@pytest.mark.parametrize("sample", ["sample-007", "sample-008", "sample-010", "sample-011", "sample-014"])
def test_samples_preserve_lossless_structures(sample):
    original = _snapshot(sample)
    validated = validate_authority_snapshot(original)
    assert validated == original


def test_column_groups_cannot_be_flattened():
    snapshot = _snapshot("sample-014")
    snapshot["operations"][0]["number_groups"] = [["01", "02", "11", "21", "31"]]
    with pytest.raises(LocalSandboxContractError) as raised:
        validate_authority_snapshot(snapshot)
    assert raised.value.code == "SANDBOX_MAPPING_INVALID"


def test_cancelled_bet_cannot_appear_in_operations():
    snapshot = _snapshot()
    snapshot["operations"][0]["human_bet_id"] = "sample-014-C1"
    with pytest.raises(LocalSandboxContractError) as raised:
        validate_authority_snapshot(snapshot)
    assert raised.value.code == "SANDBOX_CANCELLED_INVALID"
