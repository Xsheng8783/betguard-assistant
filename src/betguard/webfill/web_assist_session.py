"""Staged web assisted fill session — dedicated worker thread for Playwright.

Playwright sync API has thread affinity: browser/page objects MUST be used
from the thread that created them.  This module runs a dedicated worker
thread that owns the Playwright instance.  HTTP handler threads dispatch
commands via a thread-safe queue and wait for results.

State machine:  IDLE → BROWSER_OPEN → READY_CHECKED → DONE → IDLE.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from betguard.webfill.web_assisted_fill_executor import (
    _detect_danger_elements,
    _fast_select_numbers_knockout,
    _fill_amounts_on_b03,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IDLE = "idle"
BROWSER_OPEN = "browser_open"
READY_CHECKED = "ready_checked"
DONE = "done"

CMD_START = "start"
CMD_CHECK_READY = "check_ready"
CMD_EXECUTE_FILL = "execute_fill"
CMD_CLOSE = "close"

WORKER_TIMEOUT = 30  # seconds to wait for worker to process a command


# ---------------------------------------------------------------------------
# Per-command result holder
# ---------------------------------------------------------------------------


class _CommandResult:
    """Thread-safe holder for one command's result."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.result: dict[str, Any] = {}

    def set(self, result: dict[str, Any]) -> None:
        self.result = result
        self._event.set()

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        if self._event.wait(timeout=timeout):
            return self.result
        return {"ok": False, "error": "worker command timed out"}


# ---------------------------------------------------------------------------
# Worker thread — owns Playwright
# ---------------------------------------------------------------------------


class _AssistWorker(threading.Thread):
    """Dedicated thread that owns Playwright and processes fill commands."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="betguard-assist-worker")
        self._cmd_queue: queue.Queue[tuple[str, Any, _CommandResult]] = queue.Queue()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        # Session data
        self.numbers: list[int] = []
        self.stars: list[int] = []
        self.amounts: dict[str, int] = {}
        self.state: str = IDLE
        self.danger_detected: list[str] = []
        self.filled_amounts: list[dict[str, Any]] = []
        self._running = True

    def dispatch(self, cmd: str, payload: Any, timeout: float = WORKER_TIMEOUT) -> dict[str, Any]:
        """Send a command to the worker and wait for the result."""
        cq = self._cmd_queue
        if cq is None:
            return {"ok": False, "error": "assisted fill worker is not running"}
        result = _CommandResult()
        try:
            cq.put((cmd, payload, result), timeout=2)
        except queue.Full:
            return {"ok": False, "error": "worker command queue is full"}
        return result.wait(timeout=timeout)

    # ---- Worker loop ----

    def run(self) -> None:
        """Main worker loop: process commands from the queue."""
        while self._running:
            try:
                cmd, payload, result = self._cmd_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._process(cmd, payload, result)
            except Exception as exc:
                result.set({"ok": False, "error": f"worker error: {exc}"})

    def _process(self, cmd: str, payload: Any, result: _CommandResult) -> None:
        if cmd == CMD_START:
            self._handle_start(payload, result)
        elif cmd == CMD_CHECK_READY:
            self._handle_check_ready(result)
        elif cmd == CMD_EXECUTE_FILL:
            self._handle_execute_fill(result)
        elif cmd == CMD_CLOSE:
            self._handle_close(result)
        else:
            result.set({"ok": False, "error": f"unknown command: {cmd}"})

    # ---- Command handlers (run on worker thread) ----

    def _handle_start(self, payload: dict[str, Any], result: _CommandResult) -> None:
        if self.state != IDLE:
            result.set({"ok": False, "error": "another assisted fill session is already active; please cancel it first"})
            return

        try:
            from playwright.sync_api import sync_playwright  # pragma: no cover
        except ImportError as exc:
            result.set({"ok": False, "error": f"Playwright not installed: {exc}"})
            return

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False)
            self._page = self._browser.new_page()
            url = payload.get("url", "https://www.gts362.com")
            self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            self._cleanup()
            self.state = IDLE
            result.set({"ok": False, "error": f"browser start failed: {exc}"})
            return

        self.numbers = list(payload.get("numbers", []))
        self.stars = list(payload.get("stars", []))
        self.amounts = dict(payload.get("amounts", {}))
        self.state = BROWSER_OPEN
        self.danger_detected = []
        self.filled_amounts = []
        result.set({"ok": True, "state": BROWSER_OPEN})

    def _handle_check_ready(self, result: _CommandResult) -> None:
        if self.state != BROWSER_OPEN:
            result.set({"ok": False, "error": f"session is in state '{self.state}', expected '{BROWSER_OPEN}'"})
            return
        try:
            if self._page is None:
                result.set({"ok": False, "error": "page is not available"})
                return
            self.danger_detected = _detect_danger_elements(self._page)
            self.state = READY_CHECKED
            result.set({
                "ok": True,
                "state": READY_CHECKED,
                "danger_detected": self.danger_detected,
            })
        except Exception as exc:
            result.set({"ok": False, "error": f"page check failed: {exc}"})

    def _handle_execute_fill(self, result: _CommandResult) -> None:
        if self.state != READY_CHECKED:
            result.set({"ok": False, "error": f"session is in state '{self.state}', expected '{READY_CHECKED}'"})
            return
        try:
            if self._page is None:
                result.set({"ok": False, "error": "page is not available"})
                return
            _fast_select_numbers_knockout(self._page, [str(n) for n in self.numbers])
            self.filled_amounts = _fill_amounts_on_b03(self._page, self.amounts)
            self.state = DONE
            success = len(self.numbers) > 0 and any(
                a.get("executed") for a in self.filled_amounts
            )
            result.set({
                "ok": success,
                "state": DONE,
                "numbers": self.numbers,
                "stars": self.stars,
                "amounts": self.amounts,
                "filled_amounts": self.filled_amounts,
                "danger_buttons_detected": self.danger_detected,
                "danger_buttons_clicked": [],
                "auto_submit": False,
                "auto_confirm": False,
            })
        except Exception as exc:
            result.set({"ok": False, "error": f"fill execution error: {exc}"})

    def _handle_close(self, result: _CommandResult) -> None:
        self._cleanup()
        self.state = IDLE
        result.set({"ok": True, "state": IDLE})

    def _cleanup(self) -> None:
        """Close browser and stop playwright. Runs on worker thread."""
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._playwright = None

    def shutdown(self) -> None:
        """Signal the worker to stop. Called at module cleanup."""
        self._running = False
        self._cleanup()


# ---------------------------------------------------------------------------
# Module-level singleton — only ONE pending session at a time
# ---------------------------------------------------------------------------

_worker: _AssistWorker | None = None
_worker_lock = threading.Lock()


def get_assist_session() -> _AssistWorker:
    """Return the module-level singleton worker, creating it if needed."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = _AssistWorker()
            _worker.start()
        return _worker
