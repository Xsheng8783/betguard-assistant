from __future__ import annotations

import hashlib
import http.client
import json
import threading
from copy import deepcopy
from contextlib import contextmanager

import pytest

from betguard.vision.candidate_authority import VisionCandidateAuthorityStore
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from betguard.webfill.local_sandbox_provider import InMemorySandboxBrowser
from betguard.webui.mvp_workflow import (
    LocalSandboxBrowserRuntime,
    MvpLocalSandboxOrchestrator,
)
from betguard.webui import app as webui_app


def _bet(human_id, *, groups, bet_type="normal", cancelled=False, continuation=False):
    return {
        "human_bet_id": human_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups),
        "multiplier": {
            "ordered_rules": ["2X3", "3X1"],
            "scope": "bet",
            "resolved": True,
        },
        "special_play": {
            "kind": "structured_human_play" if continuation else "none",
            "raw_text": '{"half_car":true}' if continuation else None,
            "scope": "bet" if continuation else None,
            "resolved": True,
        },
        "continuation": {"present": continuation, "resolved": True},
        "cancelled": cancelled,
        "active": not cancelled,
        "human_confirmed": False,
    }


def _confirmed_review(store):
    review = store.create_human_review(
        review_session_id="mvp-review-1",
        source_image_id="image-1",
        source_image_hash="a" * 64,
        game="539",
        bets=[
            _bet("H-001", groups=[["01", "02"]]),
            _bet(
                "H-002",
                groups=[["12"], ["15"], ["06", "16"]],
                bet_type="column",
                continuation=True,
            ),
            _bet("H-003", groups=[["39"]], cancelled=True),
        ],
        machine_evidence_refs=[],
        blocking_unresolved_count=0,
        actor="test",
    )
    return store.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=["H-001", "H-002", "H-003"],
        actor="human",
    )


def test_explicit_single_window_action_runs_full_authority_chain(tmp_path):
    authority = VisionCandidateAuthorityStore(tmp_path / "authority")
    review = _confirmed_review(authority)
    queue = ValidatedCandidateQueueStore.from_authority_store(
        tmp_path / "queue", authority
    )
    browser = InMemorySandboxBrowser()
    orchestrator = MvpLocalSandboxOrchestrator(
        tmp_path / "mvp", authority, queue, browser=browser
    )

    action = orchestrator.create_fill_action(review["review_session_id"])
    assert set(action) == {
        "schema_version", "action_id", "idempotency_key", "sandbox_url",
        "ready", "human_confirmation_required", "auto_submit",
    }
    assert browser.calls == 0
    result = orchestrator.execute_fill(
        {
            "schema_version": action["schema_version"],
            "action_id": action["action_id"],
            "idempotency_key": action["idempotency_key"],
        }
    )

    assert result["status"] == "FILLED_VERIFIED"
    assert browser.rows[1]["number_groups"] == [["12"], ["15"], ["06", "16"]]
    assert browser.rows[1]["multiplier"]["ordered_rules"] == ["2X3", "3X1"]
    assert browser.rows[1]["continuation"]["present"] is True
    assert browser.rows[1]["special_play"]["kind"] == "structured_human_play"
    assert all(row["human_bet_id"] != "H-003" for row in browser.rows)
    assert result["cancelled_excluded_count"] == 1
    assert result["external_site_calls"] == 0
    assert result["submit_performed"] is False
    assert browser.submit_event_count == 0

    before = browser.mutation_count
    assert orchestrator.execute_fill(
        {
            "schema_version": action["schema_version"],
            "action_id": action["action_id"],
            "idempotency_key": action["idempotency_key"],
        }
    ) == result
    assert browser.calls == 1
    assert browser.mutation_count == before


def test_review_is_not_ready_until_human_confirms(tmp_path):
    authority = VisionCandidateAuthorityStore(tmp_path / "authority")
    review = authority.create_human_review(
        review_session_id="mvp-review-pending",
        source_image_id="image-1",
        source_image_hash="b" * 64,
        game="539",
        bets=[_bet("H-001", groups=[["08"]])],
        machine_evidence_refs=[],
        blocking_unresolved_count=0,
        actor="test",
    )
    queue = ValidatedCandidateQueueStore.from_authority_store(tmp_path / "queue", authority)
    orchestrator = MvpLocalSandboxOrchestrator(
        tmp_path / "mvp", authority, queue, browser=InMemorySandboxBrowser()
    )
    try:
        orchestrator.create_fill_action(review["review_session_id"])
    except Exception as exc:
        assert getattr(exc, "code", None) == "CANDIDATE_NOT_READY"
    else:
        raise AssertionError("unconfirmed review created a fill action")


def test_public_execute_request_rejects_client_values(tmp_path):
    authority = VisionCandidateAuthorityStore(tmp_path / "authority")
    review = _confirmed_review(authority)
    queue = ValidatedCandidateQueueStore.from_authority_store(tmp_path / "queue", authority)
    browser = InMemorySandboxBrowser()
    orchestrator = MvpLocalSandboxOrchestrator(
        tmp_path / "mvp", authority, queue, browser=browser
    )
    action = orchestrator.create_fill_action(review["review_session_id"])
    try:
        orchestrator.execute_fill({**action, "number_groups": [["39"]]})
    except Exception as exc:
        assert getattr(exc, "code", None) == "SANDBOX_REQUEST_INVALID"
    else:
        raise AssertionError("client values were accepted")
    assert browser.calls == 0


@pytest.mark.parametrize("sample", ["sample-007", "sample-008", "sample-011"])
def test_required_sample_vertical_slice_preserves_values(tmp_path, sample):
    from tests.fixtures.local_sandbox_profiles import sample_case

    operations, cancelled = sample_case(sample)
    bets = []
    for operation_index, operation in enumerate(operations, 1):
        bets.append({
            "human_bet_id": f"H-{operation_index:03d}",
            "bet_type": operation["bet_type"],
            "number_groups": operation["number_groups"],
            "multiplier": operation["multiplier"],
            "special_play": operation["special_play"],
            "continuation": {
                "present": operation["continuation"]["present"],
                "resolved": operation["continuation"]["resolved"],
            },
            "cancelled": False,
            "active": True,
            "human_confirmed": False,
        })
    for cancelled_index, _item in enumerate(cancelled, len(operations) + 1):
        bets.append(_bet(f"H-{cancelled_index:03d}", groups=[["39"]], cancelled=True))
    authority = VisionCandidateAuthorityStore(tmp_path / sample / "authority")
    review = authority.create_human_review(
        review_session_id=f"review-{sample}",
        source_image_id=f"image-{sample}",
        source_image_hash=hashlib.sha256(sample.encode()).hexdigest(),
        game="539",
        bets=bets,
        machine_evidence_refs=[],
        blocking_unresolved_count=0,
        actor="test",
    )
    review = authority.confirm_human_review_bets(
        review_session_id=review["review_session_id"],
        expected_human_answer_revision=review["human_answer_revision"],
        expected_human_answer_hash=review["human_answer_hash"],
        human_bet_ids=[bet["human_bet_id"] for bet in review["bets"]],
        actor="human",
    )
    queue = ValidatedCandidateQueueStore.from_authority_store(
        tmp_path / sample / "queue", authority
    )
    browser = InMemorySandboxBrowser()
    orchestrator = MvpLocalSandboxOrchestrator(
        tmp_path / sample / "mvp", authority, queue, browser=browser
    )
    action = orchestrator.create_fill_action(review["review_session_id"])
    result = orchestrator.execute_fill({
        "schema_version": action["schema_version"],
        "action_id": action["action_id"],
        "idempotency_key": action["idempotency_key"],
    })
    assert result["status"] == "FILLED_VERIFIED"
    assert [row["number_groups"] for row in browser.rows] == [
        operation["number_groups"] for operation in operations
    ]
    assert [row["multiplier"] for row in browser.rows] == [
        operation["multiplier"] for operation in operations
    ]
    assert [row["continuation"]["present"] for row in browser.rows] == [
        operation["continuation"]["present"] for operation in operations
    ]
    assert [row["special_play"] for row in browser.rows] == [
        operation["special_play"] for operation in operations
    ]
    assert result["cancelled_excluded_count"] == len(cancelled)
    assert result["external_site_calls"] == 0
    assert result["submit_performed"] is False


class _FakeMvpOrchestrator:
    sandbox_url = "http://127.0.0.1:19001/sandbox-fill"

    def __init__(self):
        self.actions = []
        self.executions = []

    def create_fill_action(self, review_session_id):
        self.actions.append(review_session_id)
        return {
            "schema_version": "betguard-local-sandbox-execute-request-v1",
            "action_id": "lsfa-" + "a" * 32,
            "idempotency_key": "lsfi-mvp-click-0001",
            "sandbox_url": self.sandbox_url,
            "ready": True,
            "human_confirmation_required": True,
            "auto_submit": False,
        }

    def execute_fill(self, payload):
        self.executions.append(deepcopy(payload))
        return {
            "status": "FILLED_VERIFIED",
            "submit_performed": False,
            "external_site_calls": 0,
            "auto_submit": False,
        }


@contextmanager
def _server_with_fake_mvp(monkeypatch):
    fake = _FakeMvpOrchestrator()
    monkeypatch.setattr(webui_app, "_MVP_LOCAL_SANDBOX_ORCHESTRATOR", fake)
    handler = webui_app.build_workbench_handler(project_version="mvp", git_commit="test")
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _http_json(port, path, payload):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST", path, body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode())
    finally:
        connection.close()


def test_http_mvp_boundary_accepts_identity_then_exact_three_field_fill(monkeypatch):
    with _server_with_fake_mvp(monkeypatch) as (port, fake):
        status, action = _http_json(
            port, "/api/vision/v1/mvp/sandbox/actions", {"review_session_id": "review-1"}
        )
        assert status == 201 and action["ok"] is True
        request = {
            "schema_version": action["schema_version"],
            "action_id": action["action_id"],
            "idempotency_key": action["idempotency_key"],
        }
        status, result = _http_json(port, "/api/vision/v1/mvp/sandbox/execute", request)
        assert status == 200 and result["status"] == "FILLED_VERIFIED"
        assert fake.actions == ["review-1"]
        assert fake.executions == [request]


def test_http_mvp_action_rejects_client_bet_values(monkeypatch):
    with _server_with_fake_mvp(monkeypatch) as (port, fake):
        status, result = _http_json(
            port,
            "/api/vision/v1/mvp/sandbox/actions",
            {"review_session_id": "review-1", "number_groups": [["39"]]},
        )
        assert status == 400
        assert result["code"] == "REQUEST_FIELDS_INVALID"
        assert fake.actions == []


@pytest.mark.e2e_local
def test_real_loopback_browser_runtime_is_thread_safe_and_never_submits():
    pytest.importorskip("playwright.sync_api")
    runtime = LocalSandboxBrowserRuntime(headless=True)
    try:
        browser = runtime.start(
            action_id="lsfa-" + "a" * 32,
            idempotency_key="lsfi-runtime-smoke-0001",
            execute_callback=lambda _payload: {"status": "NOT_USED"},
        )
        operations, _cancelled = __import__(
            "tests.fixtures.local_sandbox_profiles", fromlist=["sample_case"]
        ).sample_case("sample-011")
        result = browser.fill(operations)
        assert result["readback"][0]["number_groups"] == [
            ["30"], ["35", "36", "38"]
        ]
        assert result["submit_event_count"] == 0
        assert result["external_request_count"] == 0
        assert runtime.url.startswith("http://127.0.0.1:")
    finally:
        runtime.close()
