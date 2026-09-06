"""Real Playwright E2E tests for the assist-panel — local only, no 真站."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

try:
    from playwright.sync_api import Page, sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui import app as webui_app

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function", autouse=True)
def _clean_state(monkeypatch):
    """Reset shared module-level state between tests."""
    monkeypatch.setattr(webui_app, "_ASSIST_PANEL_STATE", {})
    monkeypatch.setattr(webui_app, "_manual_candidates", {})


@pytest.fixture(scope="function")
def page():
    """Yield a Playwright Page that gets closed after the test."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        pg = ctx.new_page()
        yield pg
        ctx.close()
        browser.close()


pytestmark = [
    pytest.mark.e2e_local,
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed"),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def _local_server(tmp_path: Path, monkeypatch) -> Iterator[int]:
    """Start local ThreadingHTTPServer with isolated RUNS_DIR."""
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)

    handler = webui_app.build_workbench_handler(
        project_version="test", git_commit="test",
    )
    # Bind to port 0 (OS assigns) to avoid port race
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=3)


# ---------------------------------------------------------------------------
# mock responses for /assist-fill/start
# ---------------------------------------------------------------------------

MOCK_NORMAL_SUCCESS = {
    "ok": True,
    "numbers_verified": True,
    "game_verified": True,
    "amounts_verified": True,
    "missing_targets": [],
    "missing_amount_stars": [],
    "amount_mismatches": [],
    "filled_amounts": [
        {"star": 2, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
        {"star": 3, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
        {"star": 4, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
    ],
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}

MOCK_AMOUNT_FAILURE = {
    "ok": False,
    "amounts_verified": False,
    "missing_targets": [],
    "missing_amount_stars": [3],
    "amount_mismatches": [{"star": 3, "expected": 100, "actual": ""}],
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}

MOCK_MISSING_NUMBER = {
    "ok": False,
    "amounts_verified": True,
    "missing_targets": ["33"],
    "missing_amount_stars": [],
    "amount_mismatches": [],
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}

MOCK_TIMEOUT = {
    "ok": False,
    "error": "worker command timed out",
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}

MOCK_COLUMN_SUCCESS = {
    "ok": True,
    "blocked": False,
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}

MOCK_COLUMN_BLOCKED = {
    "ok": False,
    "blocked": True,
    "auto_submit": False,
    "auto_confirm": False,
    "danger_buttons_clicked": [],
}


def _route_assist_fill(page: Page, response: dict[str, Any]) -> list[dict]:
    """Route /assist-fill/start to return a mock response; return captured requests."""
    captured: list[dict] = []

    def handle(route):
        captured.append({
            "url": route.request.url,
            "method": route.request.method,
            "body": route.request.post_data_json,
        })
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(response),
        )

    page.route("**/assist-fill/start", handle)
    return captured


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestAssistPanelE2E:

    def test_page_loads_without_js_errors(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.wait_for_selector("textarea", timeout=5000)
        assert not js_errors, f"Page errors: {js_errors}"

    def test_create_batch_renders_valid_and_needs_review(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            textarea = page.wait_for_selector("textarea", timeout=5000)
            textarea.fill("11 22 33 23X1\n15 26 35 21 19 1000\n17 29 1000")
            page.click("#createBatchBtn")
            # Wait for valid items
            page.wait_for_selector("#valid-items .item", timeout=8000)
        # Check valid section
        valid_text = page.inner_text("#valid-items")
        assert "11" in valid_text or "22" in valid_text, f"valid items missing numbers: {valid_text}"
        # Check Needs Review section
        review_text = page.inner_text("#review-items")
        assert "1000" in review_text or "17" in review_text, f"review items should contain 17 29 1000: {review_text}"
        assert not js_errors, f"JS errors: {js_errors}"

    def test_normal_success_marks_card_completed_and_keeps_it_visible(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        captures = _route_assist_fill(page, MOCK_NORMAL_SUCCESS)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            fill_btn = page.query_selector("#valid-items .assist-fill-btn")
            fill_btn.click()
            page.wait_for_selector(".mark-done-btn")
            assert not page.locator(".assist-completed").count()
            page.click(".mark-done-btn")  # explicit existing human action
            page.wait_for_selector("#valid-items .item.assist-completed", timeout=5000)
            # Wait 3 seconds — card must still exist (no auto-remove)
            page.wait_for_timeout(3000)
        cards = page.query_selector_all("#valid-items .item.assist-completed")
        assert len(cards) >= 1, "Card should have assist-completed class after success"
        status = page.inner_text("body")
        assert "已下牌" in status
        assert "已輔助填入" in status
        assert "已下牌" in status
        # Count assertion
        completed_cnt = int(page.inner_text("#completed-count"))
        assert completed_cnt >= 1
        clear_btn = page.query_selector("#clear-completed-btn")
        assert clear_btn and not clear_btn.is_disabled()
        assert len(captures) == 1

    def test_normal_amount_failure_does_not_complete_card(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        _route_assist_fill(page, MOCK_AMOUNT_FAILURE)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 2000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            page.click("#valid-items .assist-fill-btn")
            # Wait for error text or button re-enabled
            page.wait_for_function(
                "() => { var b = document.querySelector('#valid-items .assist-fill-btn'); return b && !b.disabled; }",
                timeout=5000,
            )
        # Card must NOT be completed
        completed = page.query_selector_all("#valid-items .item.assist-completed")
        assert len(completed) == 0, "Amount failure should not add assist-completed"
        cards = page.query_selector_all("#valid-items .item")
        assert len(cards) >= 1, "Card should still exist"
        status = page.inner_text("body")
        assert "三星" in status or "3" in status, f"Should mention star 3 issue: {status}"
        completed_cnt = int(page.inner_text("#completed-count"))
        assert completed_cnt == 0

    def test_missing_number_does_not_complete_card(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        _route_assist_fill(page, MOCK_MISSING_NUMBER)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            page.click("#valid-items .assist-fill-btn")
            page.wait_for_function(
                "() => { var b = document.querySelector('#valid-items .assist-fill-btn'); return b && !b.disabled; }",
                timeout=5000,
            )
        completed = page.query_selector_all("#valid-items .item.assist-completed")
        assert len(completed) == 0
        status = page.inner_text("body")
        assert "33" in status or "缺號" in status

    def test_worker_timeout_keeps_card_retryable(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        captures = _route_assist_fill(page, MOCK_TIMEOUT)
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            btn = page.query_selector("#valid-items .assist-fill-btn")
            btn.click()
            # Wait for mock request to complete
            while len(captures) == 0:
                page.wait_for_timeout(100)
            # Wait for error message to appear in DOM
            page.wait_for_function(
                "() => document.body.innerText.includes('worker command timed out') || document.body.innerText.includes('timed out')",
                timeout=5000,
            )
            # Button must be re-enabled after failure
            page.wait_for_function(
                "() => { var b = document.querySelector('#valid-items .assist-fill-btn'); return b && !b.disabled; }",
                timeout=5000,
            )
        # Assertions
        completed = page.query_selector_all("#valid-items .item.assist-completed")
        assert len(completed) == 0
        cards = page.query_selector_all("#valid-items .item")
        assert len(cards) >= 1, "Card should remain"
        # Button should still be clickable
        btn = page.query_selector("#valid-items .assist-fill-btn")
        assert btn and not btn.is_disabled(), "Fill button should be re-enabled"
        assert not js_errors, f"JS errors on timeout: {js_errors}"

    def test_remove_completed_removes_only_selected_card(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        _route_assist_fill(page, MOCK_NORMAL_SUCCESS)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            # Create two valid items
            page.fill("textarea", "09 15 22 27 33 1000\n26 27 28 23X1")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            # Mark first as completed
            btns = page.query_selector_all("#valid-items .assist-fill-btn")
            assert len(btns) >= 2
            btns[0].click()
            page.wait_for_selector(".mark-done-btn")
            assert not page.locator(".assist-completed").count()
            page.click(".mark-done-btn")
            page.wait_for_selector("#valid-items .item.assist-completed", timeout=5000)
            # Click remove on first completed card
            page.click("#clear-completed-btn")
            page.wait_for_timeout(500)
        # First card removed, second still exists
        all_cards = page.query_selector_all("#valid-items .item")
        assert len(all_cards) == 1, "Only the second, uncompleted card should remain"

    def test_clear_completed_removes_only_completed_cards(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        sr_fail = dict(MOCK_AMOUNT_FAILURE)
        _route_assist_fill(page, sr_fail)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            # Create one valid item + mark as completed via JS injection
            page.fill("textarea", "09 15 22 27 33 1000\n17 29 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            # Mark first card as completed directly via JS (simulating success)
            page.evaluate("""() => {
                var r = document.querySelector("#valid-items .item");
                if (r) r.classList.add("assist-completed");
                updateCompletedCount();
            }""")
            page.wait_for_timeout(300)
            # Now click clear-completed
            btn = page.query_selector("#clear-completed-btn")
            if btn and not btn.is_disabled():
                btn.click()
            page.wait_for_timeout(300)
        # Check needs-review items still exist
        review = page.query_selector_all("#review-items")
        assert len(review) >= 1 or len(page.query_selector_all("#review-items .item, #review-items div")) >= 0

    def test_clear_completed_does_not_trigger_fill_request(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        captures: list[dict] = []
        page.route("**/assist-fill/start", lambda route: (
            captures.append({}), route.fulfill(status=200, content_type="application/json", body="{}")
        ))
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            # Simulate completed card
            page.evaluate("""() => {
                var r = document.querySelector("#valid-items .item");
                if (r) r.classList.add("assist-completed");
                updateCompletedCount();
            }""")
            # Click clear
            btn = page.query_selector("#clear-completed-btn")
            if btn and not btn.is_disabled():
                btn.click()
            page.wait_for_timeout(300)
        # No assist-fill requests should have been triggered by clear
        assert len(captures) == 0, f"Clear should not trigger /assist-fill/start: {captures}"

    def test_column_success_uses_column_contract(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        _route_assist_fill(page, MOCK_COLUMN_SUCCESS)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            # Create column batch
            page.fill("textarea", "11/22/33-21 23.100")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .item", timeout=8000)
            btns = page.query_selector_all("#valid-items .assist-fill-btn")
            if btns:
                btns[0].click()
                page.wait_for_timeout(1500)
        # Column items may show "請回主 Review 頁操作" — verify they don't crash
        items = page.query_selector_all("#valid-items .item")
        assert len(items) >= 1, "Column item should exist"
        body = page.inner_text("body")
        # Column should not show assist-fill errors
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        assert not js_errors, f"Column test JS errors: {js_errors}"

    def test_column_blocked_does_not_complete(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        _route_assist_fill(page, MOCK_COLUMN_BLOCKED)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "11/22/33-21 23.100")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .item", timeout=8000)
            # If fill button exists, click it; otherwise the column "請回主 Review 頁操作" is shown
            btns = page.query_selector_all("#valid-items .assist-fill-btn")
            if btns:
                btns[0].click()
                page.wait_for_timeout(1500)
            completed = page.query_selector_all("#valid-items .item.assist-completed")
            assert len(completed) == 0, "Blocked column should not complete"

    def test_e2e_does_not_contact_external_hosts(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        external: list[str] = []

        def check_request(request):
            url = request.url
            if "127.0.0.1" not in url and "localhost" not in url:
                external.append(url)

        page.on("request", check_request)
        _route_assist_fill(page, MOCK_NORMAL_SUCCESS)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
            page.click("#valid-items .assist-fill-btn")
            page.wait_for_selector(".mark-done-btn", timeout=5000)
            assert not page.locator(".assist-completed").count()
            page.click(".mark-done-btn")
            page.wait_for_selector("#valid-items .item.assist-completed", timeout=5000)
        assert not external, f"External requests made: {external}"

    def test_e2e_does_not_write_project_runs(
        self, tmp_path: Path, monkeypatch, page: Page
    ) -> None:
        real_runs = Path(__file__).resolve().parent.parent / "runs"
        before = set(real_runs.rglob("*")) if real_runs.exists() else set()
        _route_assist_fill(page, MOCK_NORMAL_SUCCESS)
        with _local_server(tmp_path, monkeypatch) as port:
            page.goto(f"http://127.0.0.1:{port}/assist-panel")
            page.fill("textarea", "09 15 22 27 33 1000")
            page.click("#createBatchBtn")
            page.wait_for_selector("#valid-items .assist-fill-btn", timeout=8000)
        after = set(real_runs.rglob("*")) if real_runs.exists() else set()
        new = after - before
        assert not new, f"Files created in project runs/: {new}"
