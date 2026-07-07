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
BROWSER_IDLE = "browser_idle"  # browser open, no active fill job — can reuse
BROWSER_OPEN = "browser_open"
READY_CHECKED = "ready_checked"
DONE = "done"

CMD_START = "start"
CMD_CHECK_READY = "check_ready"
CMD_EXECUTE_FILL = "execute_fill"
CMD_CLOSE = "close"

WORKER_TIMEOUT = 30  # seconds to wait for worker to process a command


def _pad_number(n: int) -> str:
    """Zero-pad 1-9 to '01'-'09'; 10+ stays as-is."""
    i = int(n)
    if 1 <= i <= 9:
        return f"0{i}"
    return str(i)


def _count_selected_numbers(page: Any, expected: list[str]) -> int:
    """Count how many of the expected numbers are selected on the B03 frame."""
    import json as _json

    try:
        js = (
            "(function(){"
            " var f=null;"
            " for(var wi=0;wi<window.frames.length;wi++){"
            "  try{if(window.frames[wi].location.href.indexOf('"
            + "/Front/B/B03"
            + "')>=0){f=window.frames[wi];break;}}catch(e){}"
            " }"
            " if(!f)f=window.frames[2];"
            " if(!f||!f.ko)return 0;"
            " var expected="
            + _json.dumps(expected)
            + ";"
            " var tds=f.document.querySelectorAll('td');"
            " var count=0;"
            " for(var i=0;i<tds.length;i++){"
            "  var txt=(tds[i].textContent||'').trim();"
            "  if(expected.indexOf(txt)>=0){"
            "   var ctx=f.ko.contextFor(tds[i]);"
            "   if(ctx&&ctx.$data&&typeof ctx.$data.HasSeled==='function'&&ctx.$data.HasSeled()){"
            "    count++;"
            "   }"
            "  }"
            " }"
            " return count;"
            "})()"
        )
        raw = page.evaluate(js)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


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
        # BROWSER_IDLE → reuse existing browser, skip launch
        if self.state == BROWSER_IDLE:
            if payload.get("open_site_only"):
                result.set({"ok": True, "state": BROWSER_IDLE, "reused": True})
                return
            self.numbers = list(payload.get("numbers", []))
            self.stars = list(payload.get("stars", []))
            self.amounts = dict(payload.get("amounts", {}))
            self.state = BROWSER_OPEN
            self.danger_detected = []
            self.filled_amounts = []
            result.set({"ok": True, "state": BROWSER_OPEN, "reused": True})
            return

        if self.state != IDLE:
            result.set({"ok": False, "error": "another assisted fill job is already active; please cancel it first"})
            return

        # IDLE → launch new browser
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

        if payload.get("open_site_only"):
            self.state = BROWSER_IDLE
            result.set({"ok": True, "state": BROWSER_IDLE, "reused": False})
        else:
            self.numbers = list(payload.get("numbers", []))
            self.stars = list(payload.get("stars", []))
            self.amounts = dict(payload.get("amounts", {}))
            self.state = BROWSER_OPEN
            self.danger_detected = []
            self.filled_amounts = []
            result.set({"ok": True, "state": BROWSER_OPEN, "reused": False})

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
            # Zero-pad numbers 1-9 to "01"-"09" (site uses two-digit format)
            padded = [_pad_number(n) for n in self.numbers]
            _fast_select_numbers_knockout(self._page, padded)
            self.filled_amounts = _fill_amounts_on_b03(self._page, self.amounts)
            self.state = BROWSER_IDLE  # browser stays open for reuse

            # Verify selection (informational only — does NOT block success)
            selected = _count_selected_numbers(self._page, padded)
            amounts_ok = any(a.get("executed") for a in self.filled_amounts)
            success = amounts_ok and len(self.numbers) > 0
            warnings: list[str] = []
            if selected != len(padded):
                warnings.append(
                    f"post-fill verify: {selected}/{len(padded)} numbers confirmed selected "
                    f"(fill executed, may be a readback timing issue)"
                )
            result.set({
                "ok": success,
                "state": DONE,
                "numbers": self.numbers,
                "numbers_for_fill": padded,
                "stars": self.stars,
                "amounts": self.amounts,
                "filled_amounts": self.filled_amounts,
                "numbers_selected": selected,
                "numbers_expected": len(padded),
                "warnings": warnings,
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
