"""HTTP boundary tests for the Gate 3B-1 Candidate authority workflow."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from betguard.vision.candidate_authority import VisionCandidateAuthorityStore
from betguard.webui import app as webui_app


IMAGE_HASH = "a" * 64


def _review_bet(*, number: str = "01", confirmed: bool = True) -> dict:
    return {
        "human_bet_id": "H-001",
        "bet_type": "normal",
        "number_groups": [[number, "02"]],
        "multiplier": {
            "ordered_rules": ["2X1"],
            "scope": "bet",
            "resolved": False,
        },
        "special_play": {
            "kind": "none",
            "raw_text": None,
            "scope": None,
            "resolved": False,
        },
        "continuation": {"present": False, "resolved": False},
        "cancelled": False,
        "active": True,
        "human_confirmed": confirmed,
    }


@contextmanager
def _running_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[int, VisionCandidateAuthorityStore]]:
    store = VisionCandidateAuthorityStore(tmp_path / "candidate-authority")
    monkeypatch.setattr(webui_app, "_VISION_CANDIDATE_AUTHORITY_STORE", store)
    handler = webui_app.build_workbench_handler(
        project_version="gate3b1-test", git_commit="test"
    )
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _request(
    port: int, method: str, path: str, payload: dict | None = None
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _create_review(port: int) -> dict:
    status, response = _request(
        port,
        "POST",
        "/api/vision/v1/review-sessions",
        {
            "review_session_id": "review-api-1",
            "source_image_id": "image-api-1",
            "source_image_hash": IMAGE_HASH,
            "game": "539",
            "bets": [_review_bet()],
            "machine_evidence_refs": [],
            "blocking_unresolved_count": 1,
        },
    )
    assert status == 201
    assert response["ok"] is True
    return response["review"]


def _confirm_review(port: int, review: dict) -> dict:
    status, response = _request(
        port,
        "POST",
        "/api/vision/v1/review-sessions/review-api-1/confirmations",
        {
            "expected_human_answer_revision": review["human_answer_revision"],
            "expected_human_answer_hash": review["human_answer_hash"],
            "human_bet_ids": ["H-001"],
        },
    )
    assert status == 200
    assert response["ok"] is True
    return response["review"]


def _candidate_identity(review: dict) -> dict:
    return {
        "review_session_id": review["review_session_id"],
        "expected_human_answer_revision": review["human_answer_revision"],
        "expected_human_answer_hash": review["human_answer_hash"],
        "idempotency_key": (
            f"{review['review_session_id']}:{review['human_answer_revision']}:"
            f"{review['human_answer_hash']}"
        ),
    }


def test_http_confirmation_candidate_and_reload_use_server_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual_before = dict(webui_app._manual_candidates)
    assist_before = dict(webui_app._ASSIST_PANEL_STATE)
    with _running_server(tmp_path, monkeypatch) as (port, _store):
        created = _create_review(port)
        assert created["bets"][0]["human_confirmed"] is False
        assert created["blocking_unresolved_count"] == 1

        confirmed = _confirm_review(port, created)
        assert confirmed["bets"][0]["human_confirmed"] is True
        assert confirmed["bets"][0]["multiplier"]["resolved"] is True
        assert confirmed["bets"][0]["executable"] is True
        assert confirmed["blocking_unresolved_count"] == 0

        status, response = _request(
            port, "POST", "/api/vision/v1/candidates", _candidate_identity(confirmed)
        )
        assert status == 201
        candidate = response["candidate"]
        assert candidate["source"]["human_answer_hash"] == confirmed["human_answer_hash"]
        assert candidate["active_bets"][0]["number_groups"] == [["01", "02"]]
        assert candidate["safety"]["candidate_only"] is True
        assert candidate["safety"]["approved_for_fill"] is False
        assert candidate["safety"]["queue_written"] is False
        assert candidate["safety"]["webfill_called"] is False

        status, reloaded = _request(
            port, "GET", "/api/vision/v1/review-sessions/review-api-1"
        )
        assert status == 200
        assert reloaded["review"] == confirmed
        assert reloaded["candidate"]["candidate"] == candidate
        assert reloaded["candidate"]["state"] == "CURRENT"

    assert webui_app._manual_candidates == manual_before
    assert webui_app._ASSIST_PANEL_STATE == assist_before


def test_candidate_http_rejects_client_values_without_creating_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _running_server(tmp_path, monkeypatch) as (port, store):
        confirmed = _confirm_review(port, _create_review(port))
        malicious = {**_candidate_identity(confirmed), "bets": [_review_bet()]}
        status, response = _request(
            port, "POST", "/api/vision/v1/candidates", malicious
        )
        assert status == 400
        assert response["code"] == "REQUEST_FIELDS_INVALID"
        assert response["safety"]["queue_written"] is False
        assert response["safety"]["webfill_called"] is False
        assert store.get_candidate_for_review("review-api-1") is None


def test_http_edit_stales_candidate_and_old_cas_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _running_server(tmp_path, monkeypatch) as (port, _store):
        confirmed = _confirm_review(port, _create_review(port))
        status, created = _request(
            port, "POST", "/api/vision/v1/candidates", _candidate_identity(confirmed)
        )
        assert status == 201

        edited_bet = _review_bet(number="03", confirmed=True)
        edited_bet["multiplier"]["resolved"] = True
        edited_bet["special_play"]["resolved"] = True
        edited_bet["continuation"]["resolved"] = True
        status, replaced = _request(
            port,
            "PATCH",
            "/api/vision/v1/review-sessions/review-api-1",
            {
                "expected_human_answer_revision": confirmed["human_answer_revision"],
                "expected_human_answer_hash": confirmed["human_answer_hash"],
                "bets": [edited_bet],
                "machine_evidence_refs": [],
                "blocking_unresolved_count": 0,
            },
        )
        assert status == 200
        assert replaced["review"]["bets"][0]["human_confirmed"] is False
        assert replaced["candidate"]["candidate"] == created["candidate"]
        assert replaced["candidate"]["state"] == "STALE"

        status, stale = _request(
            port, "POST", "/api/vision/v1/candidates", _candidate_identity(confirmed)
        )
        assert status == 409
        assert stale["code"] == "REVIEW_STALE"
        assert stale["safety"]["auto_confirm"] is False
        assert stale["safety"]["auto_submit"] is False


def test_unexpected_authority_error_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingStore:
        def get_human_review(self, review_session_id: str) -> dict:
            raise RuntimeError(r"private path C:\\secret\\authority.json")

    with _running_server(tmp_path, monkeypatch) as (port, _store):
        monkeypatch.setattr(
            webui_app, "_VISION_CANDIDATE_AUTHORITY_STORE", ExplodingStore()
        )
        status, response = _request(
            port, "GET", "/api/vision/v1/review-sessions/review-api-1"
        )
        assert status == 500
        assert response["code"] == "CANDIDATE_AUTHORITY_INTERNAL"
        serialized = json.dumps(response)
        assert "secret" not in serialized
        assert "authority.json" not in serialized
        assert response["safety"]["queue_written"] is False
        assert response["safety"]["webfill_called"] is False
