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
import json
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
CMD_ZHU_PENG_EXECUTE = "execute_zhu_peng"
CMD_CLOSE = "close"

WORKER_TIMEOUT = 30  # seconds to wait for worker to process a command


def _pad_number(n: int) -> str:
    """Zero-pad 1-9 to '01'-'09'; 10+ stays as-is."""
    i = int(n)
    if 1 <= i <= 9:
        return f"0{i}"
    return str(i)


def _assist_panel_popup_script(url: str) -> str:
    encoded_url = json.dumps(url)
    return (
        f"() => {{ window.open({encoded_url}, 'betguard_panel', "
        "'width=420,height=1040,left=1480,top=0'); }"
    )


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


def _get_selected_numbers(page: Any) -> set[str]:
    """Return the set of padded numbers currently selected (HasSeled) in B03."""
    try:
        js = (
            "(function(){"
            " var f=null;"
            " for(var wi=0;wi<window.frames.length;wi++){"
            "  try{if(window.frames[wi].location.href.indexOf('/Front/B/B03')>=0){f=window.frames[wi];break;}}catch(e){}"
            " }"
            " if(!f)f=window.frames[2];"
            " if(!f||!f.ko)return [];"
            " var tds=f.document.querySelectorAll('td');"
            " var out=[];"
            " for(var i=0;i<tds.length;i++){"
            "  var txt=(tds[i].textContent||'').trim();"
            "  var ctx=f.ko.contextFor(tds[i]);"
            "  if(ctx&&ctx.$data&&typeof ctx.$data.HasSeled==='function'&&ctx.$data.HasSeled()){"
            "   out.push(txt);"
            "  }"
            " }"
            " return out;"
            "})()"
        )
        raw = page.evaluate(js)
        return set(str(x).strip() for x in (raw or []))
    except Exception:
        return set()


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
        self._context: Any = None
        self._page: Any = None
        # Session data
        self.numbers: list[int] = []
        self.stars: list[int] = []
        self.amounts: dict[str, int] = {}
        self.state: str = IDLE
        self.danger_detected: list[str] = []
        self.filled_amounts: list[dict[str, Any]] = []
        self._running = True
        self._dispatch_lock = threading.Lock()
        self._pending_result = None

    def dispatch(self, cmd: str, payload: Any, timeout: float = WORKER_TIMEOUT) -> dict[str, Any]:
        """Send a command to the worker and wait for the result."""
        cq = self._cmd_queue
        if cq is None:
            return {"ok": False, "error": "assisted fill worker is not running"}
        result = _CommandResult()
        if not self._dispatch_lock.acquire(blocking=False):
            return {"ok": False, "error": "another worker command is pending"}
        try:
            if self._pending_result is not None and not self._pending_result._event.is_set():
                return {"ok": False, "error": "previous command is still running; no automatic retry"}
            self._pending_result = result
            cq.put((cmd, payload, result), timeout=2)
            return result.wait(timeout=timeout)
        except queue.Full:
            return {"ok": False, "error": "worker command queue is full"}
        finally:
            self._dispatch_lock.release()

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
        elif cmd == CMD_ZHU_PENG_EXECUTE:
            self._handle_zhu_peng_execute(payload, result)
        elif cmd == CMD_CLOSE:
            self._handle_close(result)
        else:
            result.set({"ok": False, "error": f"unknown command: {cmd}"})

    # ---- Command handlers (run on worker thread) ----

    def _is_browser_alive(self) -> bool:
        """Check if the controlled browser is still connected and responsive."""
        if self._browser is None or self._page is None:
            return False
        try:
            self._page.evaluate("() => 1")
            return True
        except Exception:
            return False

    def _handle_start(self, payload: dict[str, Any], result: _CommandResult) -> None:
        from betguard.webfill.local_text_form import local_fill_url
        from urllib.parse import urlencode
        import os
        local_url = local_fill_url()
        if local_url:
            mode = "column" if payload.get("numbers") and isinstance(payload["numbers"][0], list) else "normal"
            payload = {**payload, "url": local_url + "/__text_fill_test?" + urlencode(
                {"game": payload.get("game", "539"), "mode": mode})}
        # BROWSER_IDLE → reuse existing browser, skip launch (if alive)
        if self.state == BROWSER_IDLE:
            if not self._is_browser_alive():
                self._cleanup()
                self.state = IDLE
                # Fall through to IDLE → launch new
            else:
                if local_url and self._page.url != payload["url"]:
                    self._page.goto(payload["url"], wait_until="domcontentloaded")
                if payload.get("open_site_only"):
                    result.set({"ok": True, "state": BROWSER_IDLE, "reused": True})
                    return
                self.numbers = list(payload.get("numbers", []))
                self.stars = list(payload.get("stars", []))
                self.amounts = dict(payload.get("amounts", {}))
                self.game = payload.get("game", "539")
                self.state = BROWSER_OPEN
                self.danger_detected = []
                self.filled_amounts = []
                result.set({"ok": True, "state": BROWSER_OPEN, "reused": True})
                return

        if self.state != IDLE:
            # Stale state (BROWSER_OPEN/READY_CHECKED with dead browser) → auto-recover
            if not self._is_browser_alive():
                self._cleanup()
                self.state = IDLE
                # Fall through to IDLE → launch new browser
            else:
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
            self._browser = self._playwright.chromium.launch(
                headless=bool(local_url and os.environ.get("BETGUARD_LOCAL_FILL_HEADLESS") == "1"),
                args=["--start-maximized", "--disable-popup-blocking"],
            )
            self._context = self._browser.new_context(no_viewport=True)
            if local_url:
                from urllib.parse import urlparse
                self._context.route("**/*", lambda route: route.continue_()
                    if urlparse(route.request.url).hostname in {"127.0.0.1", "::1", "localhost"}
                    else route.abort())
            self._page = self._context.new_page()
            url = payload.get("url", "https://www.gts362.com")
            self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            self._cleanup()
            self.state = IDLE
            result.set({"ok": False, "error": f"browser start failed: {exc}"})
            return

        # Open assist panel popup after any successful browser start (new or reused)
        try:
            assist_panel_url = str(
                payload.get("assist_panel_url")
                or "http://127.0.0.1:8765/assist-panel"
            )
            if not local_url:
                self._page.evaluate(_assist_panel_popup_script(assist_panel_url))
        except Exception:
            pass

        if payload.get("open_site_only"):
            self.state = BROWSER_IDLE
            result.set({"ok": True, "state": BROWSER_IDLE, "reused": False})
        else:
            self.numbers = list(payload.get("numbers", []))
            self.stars = list(payload.get("stars", []))
            self.amounts = dict(payload.get("amounts", {}))
            self.game = payload.get("game", "539")
            self.state = BROWSER_OPEN
            self.danger_detected = []
            self.filled_amounts = []
            result.set({"ok": True, "state": BROWSER_OPEN, "reused": False})

    def _collect_diagnostics(self) -> dict[str, Any]:
        """Collect page state for error diagnostics (no side effects)."""
        info: dict[str, Any] = {}
        if self._page is None:
            info["page_available"] = False
            return info
        info["page_available"] = True
        info["game"] = getattr(self, "game", "539")
        try:
            url = self._page.evaluate("() => window.location.href")
            info["url"] = str(url)[:200]
        except Exception:
            info["url"] = "unable to read"
        try:
            body = self._page.evaluate("() => document.body ? document.body.innerText : ''")
            body_text = str(body)[:500] if body else ""
            info["page_has_539"] = "539" in body_text
            info["page_has_tiantianle"] = "天天樂" in body_text
            info["page_has_234_star"] = "二三四星" in body_text or "2 3 4 星" in body_text
            info["page_has_lianpeng"] = "連碰" in body_text
            info["page_has_danpeng"] = "單碰" in body_text
            info["page_has_zhupeng"] = "柱碰" in body_text or "住碰" in body_text
        except Exception:
            pass
        # Check if our target numbers are clickable
        try:
            padded = [_pad_number(n) for n in self.numbers]
            found = self._page.evaluate(
                """(nums) => {
                    var inputs = document.querySelectorAll('input[type=text], input:not([type])');
                    var found = [];
                    nums.forEach(function(n) {
                        for (var i = 0; i < inputs.length; i++) {
                            if (inputs[i].value === n) { found.push(n); break; }
                        }
                    });
                    return found;
                }""",
                padded,
            )
            info["numbers_found_in_inputs"] = list(found) if found else []
        except Exception:
            pass
        return info

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
                "diagnostic": self._collect_diagnostics(),
            })
        except Exception as exc:
            self.state = BROWSER_IDLE
            result.set({"ok": False, "error": f"page check failed: {exc}"})

    def _handle_execute_fill(self, result: _CommandResult) -> None:
        if self.state != READY_CHECKED:
            result.set({"ok": False, "error": f"session is in state '{self.state}', expected '{READY_CHECKED}'"})
            return
        try:
            if self._page is None:
                result.set({"ok": False, "error": "page is not available"})
                return
            from betguard.webfill.fill_readback import verify_game
            if not verify_game(self._page, getattr(self, "game", None)):
                result.set({"ok": False, "game_verified": False, "error": "無法確認表單彩種，未填入。請先選擇正確彩種。"})
                self.state = BROWSER_IDLE
                return
            # Pre-fill diagnostic: check if numbers/amounts are likely findable
            pre_diag = self._collect_diagnostics()
            # Zero-pad numbers 1-9 to "01"-"09" (site uses two-digit format)
            padded = [_pad_number(n) for n in self.numbers]
            try:
                _fast_select_numbers_knockout(self._page, padded)
                self.filled_amounts = _fill_amounts_on_b03(self._page, self.amounts)
            except Exception as exc:
                pre_diag["fill_error"] = str(exc)
                # This command has stopped. A fresh explicit operation may check
                # the page again; the failed operation itself is never retried.
                self.state = BROWSER_IDLE
                result.set({"ok": False, "error": f"fill execution error: {exc}", "diagnostic": pre_diag})
                return
            self.state = BROWSER_IDLE  # browser stays open for reuse

            # Verify selection — BLOCKED only if targets are actually missing
            selected = _count_selected_numbers(self._page, padded)
            # Strict amount verification via pure function (no Playwright needed)
            from betguard.webfill.web_assisted_fill_executor import _verify_filled_amounts
            expected_amounts: dict[int, int] = {}
            for star_str, amt in self.amounts.items():
                star_i = int(star_str)
                amt_i = int(amt)
                if amt_i > 0:
                    expected_amounts[star_i] = amt_i
            vf = _verify_filled_amounts(expected_amounts, self.filled_amounts)
            amounts_verified = vf["amounts_verified"]
            missing_amount_stars = vf["missing_amount_stars"]
            amount_mismatches = vf["amount_mismatches"]
            amounts_ok = amounts_verified

            # Use actual target presence, not raw count (site may double-count)
            selected_set = _get_selected_numbers(self._page)
            missing: list[str] = []
            for n in padded:
                if n not in selected_set:
                    missing.append(n)
            unexpected = sorted(selected_set - set(padded))
            all_targets_selected = (len(missing) == 0 and not unexpected)
            game_verified = verify_game(self._page, self.game)
            success = amounts_ok and all_targets_selected and game_verified and len(self.numbers) > 0
            warnings: list[str] = []
            if selected != len(padded):
                if selected > len(padded):
                    warnings.append(
                        f"post-fill verify: {selected}/{len(padded)} confirmed "
                        f"(over-count from multi-area readback — all targets verified)"
                    )
                else:
                    warnings.append(
                        f"post-fill verify: {selected}/{len(padded)} numbers confirmed selected. "
                        f"Missing: {', '.join(missing)}"
                    )
            result.set({
                "ok": success,
                "state": DONE,
                "numbers": self.numbers,
                "numbers_for_fill": padded,
                "stars": self.stars,
                "amounts": self.amounts,
                "filled_amounts": self.filled_amounts,
                "amounts_expected": expected_amounts,
                "amounts_verified": amounts_verified,
                "missing_amount_stars": missing_amount_stars,
                "amount_mismatches": amount_mismatches,
                "numbers_selected": selected,
                "numbers_expected": len(padded),
                "filled_targets": [n for n in padded if n not in missing],
                "missing_targets": missing,
                "unexpected_targets": unexpected,
                "numbers_verified": all_targets_selected,
                "game_verified": game_verified,
                "possible_site_limit": selected < len(padded) and selected == 7,
                "warnings": warnings,
                "danger_buttons_detected": self.danger_detected,
                "danger_buttons_clicked": [],
                "auto_submit": False,
                "auto_confirm": False,
            })
        except Exception as exc:
            self.state = BROWSER_IDLE
            result.set({"ok": False, "error": f"fill execution error: {exc}"})

    def _handle_zhu_peng_execute(self, payload: dict[str, Any], result: _CommandResult) -> None:
        """Execute ZhuPeng column fill via knockout JS (same as CLI path)."""
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_fill_execute

        if self.state != READY_CHECKED:
            result.set({"ok": False, "error": f"session is in state '{self.state}', expected '{READY_CHECKED}'"})
            return
        try:
            if self._page is None:
                result.set({"ok": False, "error": "page is not available"})
                return
            item = payload.get("item", {})
            from betguard.webfill.fill_readback import verify_game
            if not verify_game(self._page, getattr(self, "game", None)):
                result.set({"ok": False, "game_verified": False, "error": "無法確認表單彩種，未填入。"})
                self.state = BROWSER_IDLE
                return
            fill_report = zhu_peng_fill_execute(self._page, item)
            fill_report.setdefault("auto_submit", False)
            fill_report.setdefault("auto_confirm", False)
            fill_report.setdefault("danger_buttons_clicked", [])
            fill_report.setdefault("danger_buttons_detected", self.danger_detected)
            fill_report["game_verified"] = verify_game(self._page, self.game)
            fill_report["ok"] = (fill_report.get("ok") is True and
                fill_report.get("numbers_verified") is True and
                fill_report.get("amounts_verified") is True and fill_report["game_verified"])
            self.state = BROWSER_IDLE
            result.set(fill_report)
        except Exception as exc:
            self.state = BROWSER_IDLE
            result.set({"ok": False, "error": f"zhu peng execute error: {exc}"})

    def _handle_close(self, result: _CommandResult) -> None:
        self._cleanup()
        self.state = IDLE
        result.set({"ok": True, "state": IDLE})

    def _cleanup(self) -> None:
        """Close browser and stop playwright. Runs on worker thread."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
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
        self._context = None
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
