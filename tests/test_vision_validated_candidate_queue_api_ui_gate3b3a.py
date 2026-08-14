"""Gate 3B-3A thin API/UI boundary and security regressions."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui import app as webui_app
from betguard.vision.candidate_authority import VisionCandidateAuthorityStore
from betguard.vision.candidate_consumption import CandidateConsumptionAuthorityValidator
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from tests.test_vision_candidate_consumption_boundary_gate3b2 import (
    _bet,
    _create_current_candidate,
    _tree_bytes,
)
from tests.test_vision_review_session_gate3a import (
    _confirm_structure,
    _create_candidate,
    _mount,
    _run_qwen,
)


CANDIDATE_ID = "vc-" + "1" * 32
CANDIDATE_HASH = "c" * 64
ENTRY_ID = "vcq-" + "2" * 32


class _FakeQueueStore:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.bind_enqueue_calls: list[dict[str, Any]] = []
        self.enqueue_calls: list[dict[str, Any]] = []
        self.bind_remove_calls: list[dict[str, Any]] = []
        self.remove_calls: list[dict[str, Any]] = []
        self.prepare_calls: list[str] = []

    def bind_human_enqueue_action(self, **kwargs: Any) -> dict[str, Any]:
        self.bind_enqueue_calls.append(kwargs)
        return {"action_id": "hqe-" + "3" * 32}

    def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.enqueue_calls.append({"payload": payload, **kwargs})
        if self.entries:
            return {**self.entries[0], "replayed": True}
        entry = {
            "schema_version": "vision-validated-candidate-queue-entry-v1",
            "queue_entry_id": ENTRY_ID,
            "enqueue_sequence": 1,
            "candidate_identity": {
                "candidate_id": payload["candidate_id"],
                "candidate_revision": payload["expected_candidate_revision"],
                "canonical_content_hash": payload["expected_content_hash"],
            },
            "state_at_creation": "QUEUED",
        }
        result = {"queue_entry": entry, "state": "QUEUED"}
        self.entries.append(result)
        return {**result, "replayed": False}

    def list_entries(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.entries))

    def bind_human_remove_action(self, **kwargs: Any) -> dict[str, Any]:
        self.bind_remove_calls.append(kwargs)
        return {"action_id": "hqr-" + "4" * 32}

    def remove(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        queue_entry_id = payload["queue_entry_id"]
        self.remove_calls.append({"payload": payload, **kwargs})
        self.entries[0]["state"] = "REMOVED"
        return {"queue_entry_id": queue_entry_id, "state": "REMOVED", "replayed": False}

    def prepare_next(self, *, prepare_action_id: str) -> dict[str, Any] | None:
        self.prepare_calls.append(prepare_action_id)
        if not self.entries or self.entries[0]["state"] != "QUEUED":
            return None
        entry = self.entries[0]["queue_entry"]
        identity = entry["candidate_identity"]
        return {
            "schema_version": "vision-validated-candidate-queue-prepared-v1",
            "prepare_status": "VALID_CURRENT",
            "state": "QUEUED",
            "queue_entry_id": entry["queue_entry_id"],
            "enqueue_sequence": entry["enqueue_sequence"],
            **identity,
            "queue_entry": entry,
            "validation": {
                "validation_status": "VALID_CURRENT",
                "safety": {
                    "candidate_only": True,
                    "approved_for_fill": False,
                    "approved_for_queue": False,
                    "auto_confirm": False,
                    "auto_submit": False,
                },
            },
            "safety": {
                "read_only": True,
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_submit": False,
                "auto_confirm": False,
                "auto_submit": False,
            },
        }


@contextmanager
def _running_server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[int, _FakeQueueStore]]:
    queue_store = _FakeQueueStore()
    monkeypatch.setattr(webui_app, "_VALIDATED_CANDIDATE_QUEUE_STORE", queue_store)
    monkeypatch.setattr(webui_app, "_VALIDATED_QUEUE_WEB_ACTIONS", {})
    handler = webui_app.build_workbench_handler(
        project_version="gate3b3a-test", git_commit="test"
    )
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], queue_store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def _running_server_with_stores(
    monkeypatch: pytest.MonkeyPatch,
    authority_store: VisionCandidateAuthorityStore,
    queue_store: ValidatedCandidateQueueStore,
) -> Iterator[int]:
    monkeypatch.setattr(
        webui_app, "_VISION_CANDIDATE_AUTHORITY_STORE", authority_store
    )
    monkeypatch.setattr(webui_app, "_VALIDATED_CANDIDATE_QUEUE_STORE", queue_store)
    monkeypatch.setattr(webui_app, "_VALIDATED_QUEUE_WEB_ACTIONS", {})
    handler = webui_app.build_workbench_handler(
        project_version="gate3b3a-real-test", git_commit="test"
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


def _request(
    port: int, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            method, path, body=body, headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _identity() -> dict[str, Any]:
    return {
        "candidate_id": CANDIDATE_ID,
        "expected_candidate_revision": 1,
        "expected_content_hash": CANDIDATE_HASH,
    }


def _issue_enqueue(port: int) -> dict[str, Any]:
    status, result = _request(
        port, "POST", "/api/vision/v1/candidate-queue/enqueue-actions", _identity()
    )
    assert status == 201
    return result


def test_candidate_queue_requires_separate_server_issued_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_server(monkeypatch) as (port, store):
        status, direct = _request(
            port,
            "POST",
            "/api/vision/v1/candidate-queue/enqueue",
            {
                **_identity(),
                "human_enqueue_action_id": "hqe-" + "9" * 32,
                "idempotency_key": "qik-" + "9" * 32,
            },
        )
        assert status == 403
        assert direct["code"] == "EXPLICIT_HUMAN_ENQUEUE_REQUIRED"
        assert store.enqueue_calls == []

        action = _issue_enqueue(port)
        assert action["human_enqueue_action_id"].startswith("hqe-")
        assert action["idempotency_key"].startswith("qik-")
        assert store.bind_enqueue_calls[0]["authenticated_actor"] == "assist-panel-human"
        assert "interactive_session_id" in store.bind_enqueue_calls[0]


def test_enqueue_is_identity_only_and_lost_response_retry_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_server(monkeypatch) as (port, store):
        action = _issue_enqueue(port)
        payload = {
            **_identity(),
            "human_enqueue_action_id": action["human_enqueue_action_id"],
            "idempotency_key": action["idempotency_key"],
        }
        first_status, first = _request(
            port, "POST", "/api/vision/v1/candidate-queue/enqueue", payload
        )
        second_status, second = _request(
            port, "POST", "/api/vision/v1/candidate-queue/enqueue", payload
        )
        assert first_status == second_status == 201
        assert first["queue_entry"] == second["queue_entry"]
        assert first["replayed"] is False
        assert second["replayed"] is True
        assert len(store.entries) == 1
        assert store.enqueue_calls[0]["interactive_session_id"] == store.enqueue_calls[1][
            "interactive_session_id"
        ]
        serialized = json.dumps(first["queue_entry"])
        for forbidden in ("bets", "numbers", "multiplier", "layout", "webfill"):
            assert forbidden not in serialized.lower()


@pytest.mark.parametrize("field", ["bets", "numbers", "multiplier", "layout", "actor", "interactive_session_id"])
def test_enqueue_rejects_fake_values_and_client_authority(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    with _running_server(monkeypatch) as (port, store):
        action = _issue_enqueue(port)
        payload = {
            **_identity(),
            "human_enqueue_action_id": action["human_enqueue_action_id"],
            "idempotency_key": action["idempotency_key"],
            field: [] if field in {"bets", "numbers"} else "attacker",
        }
        status, response = _request(
            port, "POST", "/api/vision/v1/candidate-queue/enqueue", payload
        )
        assert status == 400
        assert response["code"] == "QUEUE_REQUEST_INVALID"
        assert store.enqueue_calls == []
        assert store.entries == []


def test_remove_requires_its_own_bound_action_and_allows_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_server(monkeypatch) as (port, store):
        action = _issue_enqueue(port)
        enqueue_payload = {
            **_identity(),
            "human_enqueue_action_id": action["human_enqueue_action_id"],
            "idempotency_key": action["idempotency_key"],
        }
        _request(port, "POST", "/api/vision/v1/candidate-queue/enqueue", enqueue_payload)

        status, issued = _request(
            port,
            "POST",
            f"/api/vision/v1/candidate-queue/entries/{ENTRY_ID}/remove-actions",
            {},
        )
        assert status == 201
        status, removed = _request(
            port,
            "POST",
            f"/api/vision/v1/candidate-queue/entries/{ENTRY_ID}/remove",
            {
                "human_remove_action_id": issued["human_remove_action_id"],
                "idempotency_key": issued["idempotency_key"],
            },
        )
        assert status == 200
        assert removed["state"] == "REMOVED"
        status, listed = _request(port, "GET", "/api/vision/v1/candidate-queue")
        assert status == 200
        assert listed["entries"][0]["state"] == "REMOVED"
        assert store.remove_calls[0]["interactive_session_id"] == store.bind_remove_calls[0][
            "interactive_session_id"
        ]


def test_prepare_route_is_read_only_and_has_no_execution_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_server(monkeypatch) as (port, store):
        action = _issue_enqueue(port)
        _request(
            port,
            "POST",
            "/api/vision/v1/candidate-queue/enqueue",
            {
                **_identity(),
                "human_enqueue_action_id": action["human_enqueue_action_id"],
                "idempotency_key": action["idempotency_key"],
            },
        )
        before = json.loads(json.dumps(store.entries))
        status, prepared = _request(
            port, "POST", "/api/vision/v1/candidate-queue/prepare-next", {}
        )
        assert status == 200
        assert prepared["read_only"] is True
        assert prepared["prepared"]["validation"]["validation_status"] == "VALID_CURRENT"
        assert prepared["prepared"]["candidate_id"] == CANDIDATE_ID
        assert prepared["prepared"]["candidate_revision"] == 1
        assert prepared["prepared"]["canonical_content_hash"] == CANDIDATE_HASH
        assert prepared["prepared"]["safety"] == {
            "read_only": True,
            "candidate_only": True,
            "approved_for_fill": False,
            "approved_for_submit": False,
            "auto_confirm": False,
            "auto_submit": False,
        }
        assert store.entries == before
        assert store.prepare_calls[0].startswith("qpa-")
        safety = prepared["safety"]
        assert safety["approved_for_fill"] is False
        assert safety["approved_for_submit"] is False
        assert safety["submitted"] is False
        assert safety["webfill_authorized"] is False


def test_actual_authority_validator_queue_and_http_handlers_interoperate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority_root = tmp_path / "actual-http-authority"
    authority, _review, candidate = _create_current_candidate(
        authority_root, review_id="review-gate3b3a-actual-http"
    )
    queue = ValidatedCandidateQueueStore.from_authority_store(
        tmp_path / "actual-http-queue", authority
    )
    authority_before = _tree_bytes(authority_root)
    with _running_server_with_stores(monkeypatch, authority, queue) as port:
        identity = {
            "candidate_id": candidate["candidate_id"],
            "expected_candidate_revision": candidate["revision"],
            "expected_content_hash": candidate["canonical_content_hash"],
        }
        status, action = _request(
            port,
            "POST",
            "/api/vision/v1/candidate-queue/enqueue-actions",
            identity,
        )
        assert status == 201
        status, enqueued = _request(
            port,
            "POST",
            "/api/vision/v1/candidate-queue/enqueue",
            {
                **identity,
                "human_enqueue_action_id": action["human_enqueue_action_id"],
                "idempotency_key": action["idempotency_key"],
            },
        )
        assert status == 201
        assert enqueued["state"] == "QUEUED"
        status, prepared = _request(
            port, "POST", "/api/vision/v1/candidate-queue/prepare-next", {}
        )
        assert status == 200
        assert prepared["prepared"]["candidate_id"] == candidate["candidate_id"]
        assert prepared["prepared"]["validation"]["bets"] == candidate["active_bets"]
        assert prepared["prepared"]["safety"]["approved_for_submit"] is False

        entry_id = enqueued["queue_entry"]["queue_entry_id"]
        status, remove_action = _request(
            port,
            "POST",
            f"/api/vision/v1/candidate-queue/entries/{entry_id}/remove-actions",
            {},
        )
        assert status == 201
        status, removed = _request(
            port,
            "POST",
            f"/api/vision/v1/candidate-queue/entries/{entry_id}/remove",
            {
                "human_remove_action_id": remove_action["human_remove_action_id"],
                "idempotency_key": remove_action["idempotency_key"],
            },
        )
        assert status == 200
        assert removed["state"] == "REMOVED"
    assert _tree_bytes(authority_root) == authority_before


@pytest.mark.parametrize(
    "legacy_path",
    ["/assist-fill/start", "/assist-fill/manual-done", "/assist-fill/mark-done"],
)
@pytest.mark.parametrize(
    "foreign_id", [CANDIDATE_ID, ENTRY_ID, "vq-" + "7" * 32]
)
def test_validated_candidate_namespaces_do_not_enter_legacy_manual_registry(
    monkeypatch: pytest.MonkeyPatch, legacy_path: str, foreign_id: str
) -> None:
    monkeypatch.setenv("BETGUARD_SKIP_LICENSE", "1")
    manual_before = dict(webui_app._manual_candidates)
    with _running_server(monkeypatch) as (port, store):
        status, response = _request(
            port,
            "POST",
            legacy_path,
            {"manual_candidate_id": foreign_id, "bet_type": "normal"},
        )
        assert status == 400
        assert response["ok"] is False
        assert response["code"] == "LEGACY_QUEUE_INTEROP_FORBIDDEN"
        assert response["error"]["code"] == "LEGACY_QUEUE_INTEROP_FORBIDDEN"
        assert store.entries == []
        assert store.enqueue_calls == []
        assert store.remove_calls == []
    assert webui_app._manual_candidates == manual_before


@pytest.mark.parametrize(
    "legacy_path",
    ["/assist-fill/start", "/assist-fill/manual-done", "/assist-fill/mark-done"],
)
def test_validated_queue_user_data_path_cannot_enter_legacy_routes(
    monkeypatch: pytest.MonkeyPatch, legacy_path: str
) -> None:
    monkeypatch.setenv("BETGUARD_SKIP_LICENSE", "1")
    with _running_server(monkeypatch) as (port, store):
        status, response = _request(
            port,
            "POST",
            legacy_path,
            {
                "queue_path": (
                    "C:/Users/USER/AppData/Local/Betguard Assistant/vision/"
                    "validated-candidate-queue-v1/entries/example.json"
                ),
                "item_index": 0,
            },
        )
        assert status == 400
        assert response["code"] == "LEGACY_QUEUE_INTEROP_FORBIDDEN"
        assert store.entries == []


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(5000)
        yield browser_page
        context.close()
        browser.close()


def _mount_queue_routes(page: Any, calls: list[dict[str, Any]]) -> None:
    entry: dict[str, Any] | None = None

    def handle(route: Any) -> None:
        nonlocal entry
        request = route.request
        calls.append({"url": request.url, "method": request.method, "body": request.post_data_json})
        if request.url.endswith("/enqueue-actions"):
            route.fulfill(status=201, content_type="application/json", body=json.dumps({
                "ok": True,
                "human_enqueue_action_id": "hqe-" + "3" * 32,
                "idempotency_key": "qik-" + "5" * 32,
            }))
            return
        if request.url.endswith("/enqueue"):
            payload = request.post_data_json
            entry = {
                "queue_entry_id": ENTRY_ID,
                "enqueue_sequence": 7,
                "candidate_identity": {
                    "candidate_id": payload["candidate_id"],
                    "candidate_revision": payload["expected_candidate_revision"],
                    "canonical_content_hash": payload["expected_content_hash"],
                },
                "state_at_creation": "QUEUED",
            }
            route.fulfill(status=201, content_type="application/json", body=json.dumps({
                "ok": True, "queue_entry": entry, "state": "QUEUED", "replayed": False,
            }))
            return
        if request.url.endswith("/remove-actions"):
            route.fulfill(status=201, content_type="application/json", body=json.dumps({
                "ok": True,
                "human_remove_action_id": "hqr-" + "4" * 32,
                "idempotency_key": "qik-" + "6" * 32,
            }))
            return
        if request.url.endswith("/remove"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "queue_entry_id": ENTRY_ID, "state": "REMOVED", "replayed": False,
            }))
            return
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "entries": [] if entry is None else [{"queue_entry": entry, "state": "QUEUED"}],
            }))
            return
        route.abort()

    page.route("**/api/vision/v1/candidate-queue**", handle)


def test_ui_candidate_creation_does_not_auto_enqueue_and_current_only_shows_action(page) -> None:
    calls = _mount(page)
    queue_calls: list[dict[str, Any]] = []
    _mount_queue_routes(page, queue_calls)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    before = len(calls["urls"])
    _create_candidate(page)
    assert calls["urls"][before:] == ["http://gate3a.test/api/vision/v1/candidates"]
    assert queue_calls == []
    assert page.locator("#qwen-candidate-enqueue").count() == 1
    assert page.locator("#qwen-candidate-remove").count() == 0
    button_text = " ".join(
        page.locator("#qwen-review-completion button").all_inner_texts()
    ).lower()
    for forbidden_button in ("fill", "execute", "submit", "prepare-next"):
        assert forbidden_button not in button_text


def test_ui_explicit_enqueue_uses_five_identity_action_fields_and_can_remove(page) -> None:
    _mount(page)
    queue_calls: list[dict[str, Any]] = []
    _mount_queue_routes(page, queue_calls)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    _create_candidate(page)
    page.click("#qwen-candidate-enqueue")
    page.wait_for_selector('#qwen-candidate-queue-status[data-queue-state="QUEUED"]')
    assert queue_calls[0]["url"].endswith("/enqueue-actions")
    assert set(queue_calls[0]["body"]) == {
        "candidate_id", "expected_candidate_revision", "expected_content_hash"
    }
    assert set(queue_calls[1]["body"]) == {
        "candidate_id", "expected_candidate_revision", "expected_content_hash",
        "human_enqueue_action_id", "idempotency_key",
    }
    assert "sequence=7" in page.text_content("#qwen-candidate-queue-status")
    page.click("#qwen-candidate-remove")
    page.wait_for_selector('#qwen-candidate-queue-status[data-queue-state="REMOVED"]')
    assert set(queue_calls[-1]["body"]) == {
        "human_remove_action_id", "idempotency_key"
    }


def test_queue_namespace_is_not_exposed_as_legacy_fill_action() -> None:
    source = Path("src/betguard/webui/assist_panel_vision_html.py").read_text(encoding="utf-8")
    queue_slice = source[source.index("function _qwenCandidateQueueHtml"):source.index("window.qwenCompleteReview")]
    assert "assist-fill" not in queue_slice
    assert "manual_candidate_id" not in queue_slice
    assert "approved_fill_queue" not in queue_slice
    assert "webfill" not in queue_slice.lower()
    assert "prepare-next" not in queue_slice
    assert "qwenEnqueueCandidate" in queue_slice
    assert "qwenRemoveCandidateQueueEntry" in queue_slice


_REAL_SHAPE_BETS = [
    (
        "sample-007",
        [
            *[_bet(f"H-{index:03d}", groups=[[f"{index:02d}"]]) for index in range(1, 25)],
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
            _bet(
                "H-005",
                groups=[["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                bet_type="column",
                rules=["2/3/4X0.1"],
                continuation=True,
            ),
            _bet(
                "H-006",
                groups=[["34"], ["03", "13"], ["16", "36"]],
                bet_type="column",
                rules=["2X1"],
                continuation=True,
            ),
            _bet(
                "H-011",
                groups=[["12"], ["15"], ["34"], ["13", "20"]],
                bet_type="column",
                rules=["2X3", "3X1"],
                continuation=True,
            ),
            _bet("H-099", groups=[["12", "15"]], cancelled=True),
        ],
    ),
]


@pytest.mark.parametrize(("sample_id", "bets"), _REAL_SHAPE_BETS)
def test_real_shape_authority_to_queue_prepare_is_lossless_and_read_only(
    tmp_path: Path, sample_id: str, bets: list[dict[str, Any]]
) -> None:
    authority_root = tmp_path / sample_id / "candidate-authority"
    queue_root = tmp_path / sample_id / "validated-candidate-queue-v1"
    authority, _review, candidate = _create_current_candidate(
        authority_root,
        bets=bets,
        review_id=f"review-{sample_id}-gate3b3a",
    )
    direct_validation = CandidateConsumptionAuthorityValidator(authority).validate(
        candidate_id=candidate["candidate_id"],
        expected_candidate_revision=candidate["revision"],
        expected_content_hash=candidate["canonical_content_hash"],
    )
    authority_bytes_before = _tree_bytes(authority_root)

    queue = ValidatedCandidateQueueStore.from_authority_store(queue_root, authority)
    action = queue.bind_human_enqueue_action(
        authenticated_actor="gate3b3a-human",
        interactive_session_id=f"interactive-{sample_id}",
        candidate_id=candidate["candidate_id"],
        candidate_revision=candidate["revision"],
        canonical_content_hash=candidate["canonical_content_hash"],
        idempotency_key="qik-" + "1" * 32,
    )
    enqueued = queue.enqueue(
        {
            "candidate_id": candidate["candidate_id"],
            "expected_candidate_revision": candidate["revision"],
            "expected_content_hash": candidate["canonical_content_hash"],
            "human_enqueue_action_id": action["action_id"],
            "idempotency_key": action["idempotency_key"],
        },
        authenticated_actor="gate3b3a-human",
        interactive_session_id=f"interactive-{sample_id}",
    )
    entry = enqueued["queue_entry"]
    event_bytes_before = json.dumps(
        queue.get_lifecycle_events(entry["queue_entry_id"]), sort_keys=True
    )
    prepared = queue.prepare_next(prepare_action_id="qpa-" + "2" * 32)

    assert prepared is not None
    assert prepared["queue_entry"] == entry
    assert prepared["validation"] == direct_validation
    assert queue.get_entry(entry["queue_entry_id"])["state"] == "QUEUED"
    assert json.dumps(
        queue.get_lifecycle_events(entry["queue_entry_id"]), sort_keys=True
    ) == event_bytes_before
    assert _tree_bytes(authority_root) == authority_bytes_before

    entry_serialized = json.dumps(entry, ensure_ascii=False).lower()
    for forbidden in (
        "bets", "active_bets", "cancelled_audit", "numbers", "number_groups",
        "multiplier", "layout", "continuation", "special_play", "machine_evidence_refs",
    ):
        assert forbidden not in entry_serialized

    restarted_authority = VisionCandidateAuthorityStore(authority_root)
    restarted_queue = ValidatedCandidateQueueStore.from_authority_store(
        queue_root, restarted_authority
    )
    assert restarted_queue.get_entry(entry["queue_entry_id"]) == queue.get_entry(
        entry["queue_entry_id"]
    )
    assert restarted_queue.prepare_next(
        prepare_action_id="qpa-" + "3" * 32
    ) == prepared
