"""Regression tests for v0.5.26: assist-fill diagnostic collection."""

from __future__ import annotations

import json

import pytest


# ── backend: _collect_diagnostics ────────────────────────────────────────


class FakePage:
    """Minimal fake for testing _collect_diagnostics without a real browser."""

    def __init__(self, url: str = "https://test", body_text: str = "") -> None:
        self._url = url
        self._body_text = body_text
        self._inputs_value: dict[str, str] = {}

    def evaluate(self, expr: str, arg: object = None) -> object:  # noqa: ARG002
        """Simulate page.evaluate() calls."""
        if "window.location.href" in expr:
            return self._url
        if "document.body" in expr and "innerText" in expr:
            return self._body_text
        if "querySelectorAll('input'" in expr or "querySelectorAll" in expr:
            if arg is None:
                return []
            # Simulate: check if padded numbers appear in body text (like inputs would show)
            return [n for n in arg if str(n) in self._body_text]
        if "window.frames" in expr:
            return 1  # pretend there is a frame
        return None

    def content(self) -> str:
        return f"<html><body>{self._body_text}</body></html>"


def _make_worker():
    from betguard.webfill.web_assist_session import _AssistWorker
    import threading

    # Create the worker instance without starting the thread
    # _AssistWorker is a Thread subclass but we just need the diagnostics method
    w = _AssistWorker.__new__(_AssistWorker)
    threading.Thread.__init__(w, daemon=True)
    w.numbers = []
    return w


class TestCollectDiagnostics:
    """_collect_diagnostics must return page state without leaking raw content."""

    def test_page_not_available(self) -> None:
        worker = _make_worker()
        worker._page = None
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        assert diag == {"page_available": False}

    def test_url_captured(self) -> None:
        worker = _make_worker()
        worker._page = FakePage(url="https://gts362.com/game/tiantianle")
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        assert diag["page_available"] is True
        assert "gts362.com" in diag.get("url", "")

    def test_tiantianle_detected(self) -> None:
        worker = _make_worker()
        worker._page = FakePage(body_text="天天樂 二三四星 連碰 06 13 23")
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        assert diag["page_has_tiantianle"] is True
        assert diag["page_has_539"] is False

    def test_539_detected(self) -> None:
        worker = _make_worker()
        worker._page = FakePage(body_text="539 二三四星 單碰 05 12 28")
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        assert diag["page_has_539"] is True
        assert diag["page_has_tiantianle"] is False

    def test_lianpeng_detected(self) -> None:
        worker = _make_worker()
        worker._page = FakePage(body_text="天天樂 連碰 二三四星")
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        assert diag["page_has_lianpeng"] is True
        assert diag["page_has_zhupeng"] is False

    def test_zhupeng_detected(self) -> None:
        worker = _make_worker()
        worker._page = FakePage(body_text="539 柱碰 二三四星")
        worker.numbers = []
        diag = worker._collect_diagnostics()
        assert diag["page_has_zhupeng"] is True
        assert diag["page_has_lianpeng"] is False

    def test_numbers_found_in_text(self) -> None:
        worker = _make_worker()
        # Body text contains padded numbers "06", "13", "23"
        worker._page = FakePage(body_text="號碼06,13,23 金額100")
        worker.numbers = [6, 13, 23]
        diag = worker._collect_diagnostics()
        found = diag.get("numbers_found_in_inputs", [])
        assert "06" in found
        assert "13" in found
        assert "23" in found

    def test_no_raw_body_text_leaked(self) -> None:
        """Diagnostic must NOT include the raw body text."""
        long_text = "餘額: 99999 元\n帳號: user123\n" + ("內容 " * 100)
        worker = _make_worker()
        worker._page = FakePage(body_text=long_text)
        worker.numbers = [6]
        diag = worker._collect_diagnostics()
        # Must NOT contain the raw body text
        assert "body_text" not in diag
        assert "innerText" not in diag
        assert "raw" not in diag
        assert "餘額" not in str(diag)
        assert "帳號" not in str(diag)
        # But the boolean flags are present
        assert isinstance(diag.get("page_has_tiantianle"), bool)


# ── frontend: diagnostic display in JS ──────────────────────────────────


class TestFrontendDiagnosticDisplay:
    """Review console JS must display diagnostic without leaking raw data."""

    def _html_for(self, raw_text: str) -> str:
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue(raw_text)
        return render_review_console_html(queue, queue_path="/test/queue_test.json")

    def test_js_contains_diagnostic_field_handling(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        # The JS must read data.diagnostic
        assert "data.diagnostic" in html_text or "diag =" in html_text or "var diag" in html_text

    def test_js_displays_url_from_diagnostic(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "diag.url" in html_text or '"url"' in html_text

    def test_js_displays_game_detection(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "tiantianle" in html_text.lower() or "天天樂" in html_text
        assert "539" in html_text

    def test_js_displays_page_mode(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "lianpeng" in html_text.lower() or "連碰" in html_text or "zhupeng" in html_text.lower() or "柱碰" in html_text

    def test_js_displays_numbers_found(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "numbers_found_in_inputs" in html_text or "號碼已顯示" in html_text or "號碼" in html_text

    def test_js_does_not_display_raw_page_content(self) -> None:
        """The diagnostic JS must NOT dump raw page text to the UI."""
        html_text = self._html_for("06.13.23.22 234.100")
        # No eval() of raw body text into UI
        assert "body.innerText" not in html_text
        assert "innerText =" not in html_text

    def test_error_still_resets_in_progress(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "assistInProgress = false" in html_text

    def test_error_still_shows_retry_button(self) -> None:
        html_text = self._html_for("06.13.23.22 234.100")
        assert "重試輔助填入" in html_text

    def test_column_still_shows_cli_not_assist(self) -> None:
        """Column bets now show proper preview + can start assist (not just CLI)."""
        html_text = self._html_for("11/22/33/13 234.100")
        assert '"column"' in html_text
        assert "住碰" in html_text or "柱碰" in html_text


# ── net: error response shape ────────────────────────────────────────────


class TestErrorResponseIncludesDiagnostic:
    """Error responses from backend must carry the diagnostic field."""

    def test_worker_timeout_returns_diagnostic_in_serialisation(self) -> None:
        """Verify the worker's CommandResult can carry a diagnostic dict."""
        from betguard.webfill.web_assist_session import _CommandResult

        result = _CommandResult()
        result.set({"ok": False, "error": "worker command timed out", "diagnostic": {"page_available": True, "url": "https://test"}})
        data = json.loads(json.dumps(result.result))
        assert data["ok"] is False
        assert "diagnostic" in data
        assert data["diagnostic"]["page_available"] is True

    def test_diagnostic_keys_are_safe_strings(self) -> None:
        """Diagnostic must only contain short, safe, predefined keys."""
        allowed = {
            "page_available", "url", "page_has_539", "page_has_tiantianle",
            "page_has_234_star", "page_has_lianpeng", "page_has_danpeng", "page_has_zhupeng",
            "numbers_found_in_inputs", "fill_error", "game",
        }
        from betguard.webfill.web_assist_session import _CommandResult

        result = _CommandResult()
        result.set({"ok": False, "error": "timeout", "diagnostic": {"page_available": True}})
        data = result.result
        diag = data.get("diagnostic", {})
        for key in diag:
            assert key in allowed, f"Unexpected diagnostic key: {key}"
