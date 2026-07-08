"""Unit tests for web_assist_session — worker thread, dispatch, constants.

Browser-dependent tests are in test_real_site_assisted_fill.py.
"""

from __future__ import annotations

import pytest


class TestAssistSessionConstants:
    """Verify state and command constants."""

    def test_state_constants(self) -> None:
        from betguard.webfill.web_assist_session import (
            BROWSER_IDLE,
            BROWSER_OPEN,
            DONE,
            IDLE,
            READY_CHECKED,
        )

        assert IDLE == "idle"
        assert BROWSER_IDLE == "browser_idle"
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

    def test_browser_open_blocks_new_start(self) -> None:
        """Non-IDLE, non-BROWSER_IDLE states with alive browser block new start."""
        from betguard.webfill.web_assist_session import (
            BROWSER_OPEN,
            CMD_START,
            _AssistWorker,
        )

        w = _AssistWorker()
        w.state = BROWSER_OPEN
        # Mock: browser is alive so we hit the "already active" path
        w._browser = True  # truthy, not None
        w._page = True
        w._is_browser_alive = lambda: True
        w.start()
        try:
            r = w.dispatch(CMD_START, {"url": "http://localhost"}, timeout=2)
            assert r["ok"] is False
            assert "already active" in r.get("error", "")
        finally:
            w.shutdown()
            w.join(timeout=2)

    def test_stale_browser_auto_recovers_on_start(self) -> None:
        """Dead browser in non-IDLE state → auto-cleanup → try re-launch."""
        from betguard.webfill.web_assist_session import (
            BROWSER_OPEN,
            CMD_START,
            IDLE,
            _AssistWorker,
        )

        w = _AssistWorker()
        w.state = BROWSER_OPEN
        # Browser is dead → _is_browser_alive returns False
        w._browser = None
        w._page = None
        w.start()
        try:
            r = w.dispatch(CMD_START, {"url": "http://localhost"}, timeout=5)
            # Either "already active" or "browser start failed" depending on playwright availability
            # Key assertion: state is now IDLE (cleanup happened)
            assert w.state == IDLE, f"Expected IDLE after stale cleanup, got {w.state}"
        finally:
            w.shutdown()
            w.join(timeout=2)


class TestPadNumber:
    """Zero-pad numbers for site format."""

    def test_pad_single_digit(self) -> None:
        from betguard.webfill.web_assist_session import _pad_number

        assert _pad_number(1) == "01"
        assert _pad_number(5) == "05"
        assert _pad_number(9) == "09"

    def test_pad_double_digit_unchanged(self) -> None:
        from betguard.webfill.web_assist_session import _pad_number

        assert _pad_number(10) == "10"
        assert _pad_number(17) == "17"
        assert _pad_number(39) == "39"

    def test_pad_from_string(self) -> None:
        from betguard.webfill.web_assist_session import _pad_number

        assert _pad_number("3") == "03"
        assert _pad_number("12") == "12"


class TestBrowserAlive:
    """v0.5.15: stale browser detection."""

    def test_is_browser_alive_returns_false_when_no_browser(self) -> None:
        from betguard.webfill.web_assist_session import _AssistWorker

        w = _AssistWorker()
        assert w._is_browser_alive() is False
        assert w._browser is None

    def test_worker_state_machine_has_idle_and_browser_idle(self) -> None:
        from betguard.webfill.web_assist_session import BROWSER_IDLE, IDLE

        assert IDLE == "idle"
        assert BROWSER_IDLE == "browser_idle"


class TestAssistSessionModule:
    """Import and singleton tests."""

    def test_module_imports(self) -> None:
        from betguard.webfill import web_assist_session  # noqa: F401

    def test_get_assist_session_returns_worker(self) -> None:
        from betguard.webfill.web_assist_session import _AssistWorker, get_assist_session

        w = get_assist_session()
        assert isinstance(w, _AssistWorker)
