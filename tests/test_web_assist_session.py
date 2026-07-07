"""Unit tests for web_assist_session — worker thread, dispatch, constants.

Browser-dependent tests are in test_real_site_assisted_fill.py.
"""

from __future__ import annotations

import pytest


class TestAssistSessionConstants:
    """Verify state and command constants."""

    def test_state_constants(self) -> None:
        from betguard.webfill.web_assist_session import (
            BROWSER_OPEN,
            DONE,
            IDLE,
            READY_CHECKED,
        )

        assert IDLE == "idle"
        assert BROWSER_OPEN == "browser_open"
        assert READY_CHECKED == "ready_checked"
        assert DONE == "done"

    def test_command_constants(self) -> None:
        from betguard.webfill.web_assist_session import (
            CMD_CHECK_READY,
            CMD_CLOSE,
            CMD_EXECUTE_FILL,
            CMD_START,
        )

        assert CMD_START == "start"
        assert CMD_CHECK_READY == "check_ready"
        assert CMD_EXECUTE_FILL == "execute_fill"
        assert CMD_CLOSE == "close"


class TestAssistSessionWorker:
    """Test worker state machine without a browser (no Playwright)."""

    def test_worker_initial_state(self) -> None:
        from betguard.webfill.web_assist_session import IDLE, _AssistWorker

        w = _AssistWorker()
        assert w.state == IDLE

    def test_worker_dispatch_rejects_when_not_running(self) -> None:
        from betguard.webfill.web_assist_session import CMD_START, _AssistWorker

        w = _AssistWorker()
        # Don't start the thread — dispatch should fail gracefully
        result = w.dispatch(CMD_START, {"url": "http://localhost"}, timeout=1)
        assert result["ok"] is False
        assert "error" in result

    def test_worker_dispatch_bad_state(self) -> None:
        """check_ready / execute_fill should fail when worker is IDLE."""
        from betguard.webfill.web_assist_session import (
            CMD_CHECK_READY,
            CMD_EXECUTE_FILL,
            _AssistWorker,
        )

        w = _AssistWorker()
        w.start()  # start thread so queue works
        try:
            r1 = w.dispatch(CMD_CHECK_READY, None, timeout=2)
            assert r1["ok"] is False
            assert "idle" in str(r1.get("error", "")).lower()

            r2 = w.dispatch(CMD_EXECUTE_FILL, None, timeout=2)
            assert r2["ok"] is False
            assert "idle" in str(r2.get("error", "")).lower()
        finally:
            w.shutdown()
            w.join(timeout=2)

    def test_command_result_timeout(self) -> None:
        from betguard.webfill.web_assist_session import _CommandResult

        cr = _CommandResult()
        result = cr.wait(timeout=0.1)
        assert result["ok"] is False
        assert "timed out" in result["error"]


class TestAssistSessionModule:
    """Import and singleton tests."""

    def test_module_imports(self) -> None:
        from betguard.webfill import web_assist_session  # noqa: F401

    def test_get_assist_session_returns_worker(self) -> None:
        from betguard.webfill.web_assist_session import _AssistWorker, get_assist_session

        w = get_assist_session()
        assert isinstance(w, _AssistWorker)
