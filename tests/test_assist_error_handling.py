"""Regression tests for v0.5.24: assist-fill error handling.

Verifies that:
  - _send_json / _send_html catch ConnectionAbortedError / BrokenPipeError
  - startAssist error paths reset assistInProgress and re-enable the button
"""

from __future__ import annotations

import pytest


# ── backend: _send_json resilience ──────────────────────────────────────


class TestSendJsonConnectionErrors:
    """_send_json must not raise when the client disconnects."""

    @pytest.fixture
    def handler_class(self):
        from betguard.webui.app import build_workbench_handler

        return build_workbench_handler(
            project_version="test",
            git_commit="test",
        )

    def _make_handler(self, handler_class):
        import io
        from http.server import BaseHTTPRequestHandler

        handler = BaseHTTPRequestHandler.__new__(handler_class)
        handler.wfile = io.BytesIO()
        handler.rfile = io.BytesIO()
        handler.requestline = "POST /test HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.log_message = lambda fmt, *args: None
        return handler

    def test_send_json_survives_broken_pipe(self, handler_class) -> None:
        """_send_json must not raise BrokenPipeError — it must be caught."""
        handler = self._make_handler(handler_class)
        def broken_write(data):
            raise BrokenPipeError()
        handler.wfile.write = broken_write
        handler._send_json({"ok": True})

    def test_send_json_survives_connection_aborted(self, handler_class) -> None:
        """_send_json must not raise ConnectionAbortedError."""
        handler = self._make_handler(handler_class)
        def aborted_write(data):
            raise ConnectionAbortedError()
        handler.wfile.write = aborted_write
        handler._send_json({"ok": True})

    def test_send_json_still_sends_on_normal_write(self, handler_class) -> None:
        """Normal writes must still produce the correct JSON."""
        handler = self._make_handler(handler_class)
        handler._send_json({"ok": True, "msg": "test"})
        data = handler.wfile.getvalue()
        assert b'"ok": true' in data

    def test_send_html_and_text_also_survive(self, handler_class) -> None:
        """_send_html and _send_text must also catch connection errors."""
        handler = self._make_handler(handler_class)
        def broken_write(data):
            raise ConnectionAbortedError()
        handler.wfile.write = broken_write
        handler._send_html("<html></html>")
        handler._send_text("hello")


# ── frontend: startAssist error paths ───────────────────────────────────


class TestStartAssistErrorHandling:
    """startAssist must reset state and re-enable button on error."""

    def test_html_contains_assist_in_progress_false_in_error_path(self) -> None:
        """The catch/error branches must set assistInProgress = false."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("06.13.23.22 234.100")
        html_text = render_review_console_html(queue)

        # The catch block must contain assistInProgress = false
        assert "assistInProgress = false" in html_text
        # The error branch must contain assistInProgress = false
        assert html_text.count("assistInProgress = false") >= 2, (
            "assistInProgress=false must appear in both .catch() and error response paths"
        )

    def test_html_contains_retry_button_in_error_paths(self) -> None:
        """Error paths must show '重試輔助填入' button text."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("06.13.23.22 234.100")
        html_text = render_review_console_html(queue)

        assert "重試輔助填入" in html_text

    def test_column_item_still_shows_cli_not_hang(self) -> None:
        """Column bets must still show CLI message, not the regular flow."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("11/22/33/13 234.100")
        html_text = render_review_console_html(queue)

        assert "\"column\"" in html_text
        assert "柱碰" in html_text or "CLI" in html_text


class TestFetchTimeoutHandling:
    """startAssist fetch must have a timeout to prevent infinite loading."""

    def test_html_contains_abort_controller(self) -> None:
        """The JS must use AbortController with a 20-second timeout."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("06.13.23.22 234.100")
        html_text = render_review_console_html(queue)
        assert "AbortController" in html_text
        assert "controller.abort()" in html_text
        assert "20000" in html_text  # 20-second timeout

    def test_html_contains_timeout_error_message(self) -> None:
        """The catch block must handle AbortError with a user-friendly message."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("06.13.23.22 234.100")
        html_text = render_review_console_html(queue)
        assert "AbortError" in html_text
        assert "逾時" in html_text or "timeout" in html_text.lower()

    def test_standard_assist_still_works_via_html(self) -> None:
        """Standard bet still has working assist button."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.review_console import render_review_console_html

        queue = build_batch_mock_queue("06.13.23.22 234.100")
        html_text = render_review_console_html(queue)
        assert "輔助填入" in html_text
