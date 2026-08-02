"""Web-safe assisted fill executor — one item, knockout numbers, Playwright amounts.

Never submits, confirms, or clicks danger buttons.  Always stops for human review.

Called from ``/assist-fill`` after validation by ``_validate_assist_fill_item``.
This executor is the ONLY path through which the web workbench can drive a real
browser — it never calls forbidden CLI flags and never weakens safety guards.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Safety constants — mirrored from real_site_assisted_fill
# ---------------------------------------------------------------------------

B03_URL_MARKER = "/Front/B/B03"
AMOUNT_DATA_BIND_MARKER = "PengBet.Value"
STAR_TO_POSITION: dict[int, int] = {2: 0, 3: 1, 4: 2}
STAR_NAMES: dict[int, str] = {2: "二星", 3: "三星", 4: "四星"}

# ---------------------------------------------------------------------------
# Knockout number selection (reused from real_site_assisted_fill)
# ---------------------------------------------------------------------------


def _fast_select_numbers_knockout(page: Any, numbers: list[str]) -> None:
    """Select numbers via knockout.js OnSwitchSel — instant, no DOM click."""
    nums_json = _json.dumps(numbers)
    js = (
        "(function(){"
        " try{"
        "  var f=null;"
        "  for(var wi=0;wi<window.frames.length;wi++){"
        "   try{if(window.frames[wi].location.href.indexOf('"
        + B03_URL_MARKER
        + "')>=0){f=window.frames[wi];break;}}catch(e){}"
        "  }"
        "  if(!f)f=window.frames[2];"
        "  if(!f||!f.ko||!f.Mo)return;"
        "  var ko=f.ko;var mo=f.Mo;"
        "  var target="
        + nums_json
        + ";"
        "  var tds=f.document.querySelectorAll('td');"
        "  for(var i=0;i<tds.length;i++){"
        "   var txt=(tds[i].textContent||'').trim();"
        "   if(target.indexOf(txt)>=0){"
        "    var ctx=ko.contextFor(tds[i]);"
        "    if(ctx&&ctx.$data&&typeof ctx.$data.HasSeled==='function'&&!ctx.$data.HasSeled()){"
        "     mo.OnSwitchSel(ctx.$data,{});"
        "    }"
        "   }"
        "  }"
        " }catch(e){console.log('betguard knockout error:',e);}"
        "})()"
    )
    page.evaluate(js)


# ---------------------------------------------------------------------------
# Amount fill — direct Playwright on B03 frame PengBet.Value inputs
# ---------------------------------------------------------------------------



def _verify_filled_amounts(
    expected_amounts: dict[int, int],
    filled: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure function: verify filled amounts against expected, no Playwright needed."""
    if not expected_amounts:
        return {"amounts_verified": False, "missing_amount_stars": [], "amount_mismatches": [],
                "error": "no expected amounts"}
    if not filled:
        missing = sorted(expected_amounts.keys())
        return {"amounts_verified": False, "missing_amount_stars": missing, "amount_mismatches": [],
                "error": "no filled results"}

    # Build result map — detect duplicate stars
    seen: set[int] = set()
    duplicates: list[int] = []
    result_map: dict[int, dict[str, Any]] = {}
    for a in filled:
        s = a.get("star")
        if s is None:
            continue
        star_i = int(s)
        if star_i in seen:
            duplicates.append(star_i)
        seen.add(star_i)
        if star_i not in result_map:
            result_map[star_i] = a

    amounts_verified = True
    missing_amount_stars: list[int] = []
    amount_mismatches: list[dict[str, Any]] = []

    for star_i, expected in sorted(expected_amounts.items()):
        ar = result_map.get(star_i)
        if ar is None:
            amounts_verified = False
            missing_amount_stars.append(star_i)
            continue
        executed = bool(ar.get("executed"))
        verified = bool(ar.get("verified", False))  # safe default: False
        actual = str(ar.get("actual_amount", ""))
        if not executed or not verified:
            amounts_verified = False
            amount_mismatches.append({
                "star": star_i, "expected": expected, "actual": actual,
                "executed": executed, "verified": verified,
            })
            continue
        actual_norm = actual.strip().lstrip("0") or "0"
        expected_norm = str(expected).lstrip("0") or "0"
        if actual_norm != expected_norm:
            amounts_verified = False
            amount_mismatches.append({
                "star": star_i, "expected": expected, "actual": actual,
                "executed": True, "verified": True,
                "error": "amount readback mismatch",
            })

    if duplicates:
        amounts_verified = False
        amount_mismatches.append({
            "error": "duplicate star results", "stars": duplicates,
        })

    return {
        "amounts_verified": amounts_verified,
        "missing_amount_stars": missing_amount_stars,
        "amount_mismatches": amount_mismatches,
    }


def _fill_amounts_on_b03(page: Any, amounts: dict[str, int]) -> list[dict[str, Any]]:
    """Fill per-star amounts with retry and readback verification.

    On slow machines the website may re-render after number selection,
    clearing amount fields.  This function retries up to 3 times per star,
    reads back the actual value, and logs each attempt to UTF-8 log.
    """
    import time as _time

    executed: list[dict[str, Any]] = []
    amount_css = f'input[data-bind*="{AMOUNT_DATA_BIND_MARKER}"]'

    for star, position in sorted(STAR_TO_POSITION.items()):
        amt = amounts.get(str(star), amounts.get(star, 0))
        if amt <= 0:
            continue
        star_name = STAR_NAMES.get(star, str(star))
        result = None

        for attempt in range(1, 4):  # up to 3 attempts
            try:
                # Wait for the amount field to be visible and editable
                frame = page.frame(url="**" + B03_URL_MARKER + "**")
                target = frame if frame is not None else page
                try:
                    target.locator(amount_css).nth(position).wait_for(state="visible", timeout=2000)
                except Exception:
                    # Field not yet visible — may still be rendering
                    page.wait_for_timeout(300)
                    if attempt < 3:
                        continue  # retry

                loc = target.locator(amount_css).nth(position)
                loc.fill(str(amt))

                # Trigger knockout events
                try:
                    loc.dispatch_event("input")
                    loc.dispatch_event("change")
                    loc.blur()
                except Exception:
                    pass

                # Wait for knockout to process
                page.wait_for_timeout(200)

                # Readback: re-locate (field may have been re-rendered)
                try:
                    rloc = target.locator(amount_css).nth(position)
                    actual = str(rloc.input_value() or "")
                except Exception:
                    try:
                        actual = str(loc.evaluate("el => el.value") or "")
                    except Exception:
                        actual = ""

                exp_norm = (str(amt).strip().lstrip("0") or "0")
                act_norm = (actual.strip().lstrip("0") or "0")
                verified = exp_norm == act_norm

                _log_amount_attempt(star_name, amt, actual, attempt, verified, "")

                if verified:
                    result = {
                        "type": "SET_AMOUNT",
                        "star": star,
                        "star_name": star_name,
                        "amount": amt,
                        "expected_amount": amt,
                        "actual_amount": actual,
                        "position": position,
                        "executed": True,
                        "verified": True,
                        "attempts": attempt,
                    }
                    break  # success — exit retry loop

            except Exception as exc:
                _log_amount_attempt(star_name, amt, "", attempt, False, str(exc))
                if attempt >= 3:
                    result = {
                        "type": "SET_AMOUNT",
                        "star": star,
                        "star_name": star_name,
                        "amount": amt,
                        "expected_amount": amt,
                        "actual_amount": "",
                        "position": position,
                        "executed": False,
                        "verified": False,
                        "error": str(exc),
                        "attempts": attempt,
                    }

        if result is None:
            result = {
                "type": "SET_AMOUNT",
                "star": star,
                "star_name": star_name,
                "amount": amt,
                "expected_amount": amt,
                "actual_amount": "",
                "position": position,
                "executed": False,
                "verified": False,
                "error": "amount field not visible after 3 attempts",
                "attempts": 3,
            }
        executed.append(result)

    return executed


def _log_amount_attempt(star_name: str, expected: int, actual: str, attempt: int, verified: bool, error: str) -> None:
    """Log amount fill attempt to UTF-8 log (no site-sensitive data)."""
    try:
        import os
        log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Betguard Assistant Data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "amount_fill.log")
        import time as _time
        ts = _time.strftime('%Y-%m-%d %H:%M:%S')
        status = "OK" if verified else "FAIL"
        msg = f"[{ts}] {star_name} exp={expected} act={actual!r} attempt={attempt} {status}"
        if error:
            msg += f" err={error}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# Keep old function signature for compatibility
_FillVerifier = _verify_filled_amounts

def _precheck_numbers_on_page(page: Any, numbers: list[str]) -> dict[str, Any]:
    """Verify all target numbers exist on the current page before clicking.

    Checks each number has a unique, visible <td> element on the page.
    Returns {"ok": True} or {"ok": False, "error": ..., "missing": [...]}.
    Does NOT click anything.
    """
    import json as _json
    nums_json = _json.dumps([str(n) for n in numbers])
    js = (
        "(function() {"
        " try {"
        "  var f = null;"
        "  for (var wi = 0; wi < window.frames.length; wi++) {"
        "   try { if (window.frames[wi].location.href.indexOf('/Front/B/B03') >= 0) { f = window.frames[wi]; break; } } catch(e) {}"
        "  }"
        "  if (!f) f = window.frames[2];"
        "  if (!f || !f.document) return JSON.stringify({ok: false, error: 'frame_not_found'});"
        "  var target = " + nums_json + ";"
        "  var found = {};"
        "  var missing = [];"
        "  var ambiguous = [];"
        "  var tds = f.document.querySelectorAll('td');"
        "  for (var i = 0; i < tds.length; i++) {"
        "   var txt = (tds[i].textContent || '').trim();"
        "   if (target.indexOf(txt) >= 0) {"
        "    if (found[txt]) {"
        "     ambiguous.push(txt);"
        "    } else {"
        "     found[txt] = true;"
        "    }"
        "   }"
        "  }"
        "  for (var j = 0; j < target.length; j++) {"
        "   if (!found[target[j]]) missing.push(target[j]);"
        "  }"
        "  if (missing.length > 0) return JSON.stringify({ok: false, error: 'number_not_found', missing: missing});"
        "  if (ambiguous.length > 0) return JSON.stringify({ok: false, error: 'ambiguous_selector', ambiguous: ambiguous});"
        "  return JSON.stringify({ok: true});"
        " } catch(e) { return JSON.stringify({ok: false, error: 'precheck_error: ' + String(e)}); }"
        "})()"
    )
    try:
        raw = page.evaluate(js)
        result = _json.loads(raw)
        if not result.get("ok"):
            missing = result.get("missing", [])
            if missing:
                result["error"] = f"目前頁面找不到號碼{','.join(missing)}，請確認遊戲頁面"
            return result
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"precheck failed: {exc}"}


# Import pure verifier from separate module
try:
    from betguard.webfill.amount_verify import _verify_filled_amounts
except ImportError:
    pass  # amount_verify not available; use local definition



# ---------------------------------------------------------------------------
# Safety — detect danger elements on page
# ---------------------------------------------------------------------------

DANGER_WORDS = frozenset({
    "送出", "確認", "下注", "投注", "完成",
    "submit", "confirm", "bet", "done",
})


def _detect_danger_elements(page: Any) -> list[str]:
    """Scan the B03 frame for danger buttons.  NEVER clicks them."""
    found: list[str] = []
    try:
        js = (
            "(function(){"
            " var f=null;"
            " for(var wi=0;wi<window.frames.length;wi++){"
            "  try{if(window.frames[wi].location.href.indexOf('"
            + B03_URL_MARKER
            + "')>=0){f=window.frames[wi];break;}}catch(e){}"
            " }"
            " if(!f)f=window.frames[2];"
            " if(!f||!f.document)return JSON.stringify([]);"
            " var danger="
            + _json.dumps(sorted(DANGER_WORDS))
            + ";"
            " var els=f.document.querySelectorAll('button,input[type=submit],input[type=button],a');"
            " var result=[];"
            " for(var i=0;i<els.length;i++){"
            "  var txt=(els[i].textContent||els[i].value||'').toLowerCase();"
            "  for(var j=0;j<danger.length;j++){"
            "   if(txt.indexOf(danger[j].toLowerCase())>=0){"
            "    result.push(els[i].tagName+': '+danger[j]);break;"
            "   }"
            "  }"
            " }"
            " return JSON.stringify(result);"
            "})()"
        )
        raw = page.evaluate(js)
        found = _json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        pass
    return found


# ---------------------------------------------------------------------------
# Main executor — single public entry point
# ---------------------------------------------------------------------------


def execute_web_assisted_fill_one_item(
    *,
    numbers: list[int],
    stars: list[int],
    amounts: dict[str, int],
    url: str = "https://www.gts362.com",
    game: str = "539",
) -> dict[str, Any]:
    """Open browser, fill one item, stop.  Never submits or confirms.

    Called synchronously from ``/assist-fill``.  The HTTP request blocks
    while the user logs in, navigates, and reviews — this is intentional:
    the web UI only shows "已輔助填入" after the browser session completes.

    Returns:
        ``{"ok": True, "numbers": [...], "stars": [...], "amounts": {...},
           "filled_amounts": [...], "danger_buttons_clicked": [],
           "auto_submit": False, "auto_confirm": False}``

        or ``{"ok": False, "error": "reason"}``
    """
    # --- Pre-flight safety checks ---
    if not numbers:
        return {"ok": False, "error": "no numbers to fill; BLOCKED"}
    if not stars:
        return {"ok": False, "error": "no stars specified; BLOCKED"}
    if not amounts:
        return {"ok": False, "error": "no amounts specified; BLOCKED"}

    # --- Import Playwright (lazy, only when actually executing) ---
    try:
        from playwright.sync_api import sync_playwright  # pragma: no cover
    except ImportError as exc:
        return {"ok": False, "error": f"Playwright not installed: {exc}"}

    executed_numbers: list[int] = []
    executed_amounts: list[dict[str, Any]] = []
    danger_detected: list[str] = []
    danger_clicked: list[str] = []

    try:
        with sync_playwright() as playwright:  # pragma: no cover
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)

                # --- Wait for human to log in and navigate ---
                input(
                    "\n[Betguard Web Assist Fill]\n"
                    "請在瀏覽器中手動登入並進入正確頁面（539 或天天樂 二三四星連碰），\n"
                    "完成後回到此終端機按 Enter 繼續。\n"
                )

                # --- Safety: detect danger elements BEFORE any fill ---
                danger_detected = _detect_danger_elements(page)
                if danger_detected:
                    input(
                        f"\n⚠️  偵測到可能的 danger 元素: {danger_detected}\n"
                        "這些元素不會被點擊。按 Enter 繼續填入（或 Ctrl-C 取消）。\n"
                    )

                # --- Step 1: Select numbers via knockout ---
                _fast_select_numbers_knockout(page, [str(n) for n in numbers])
                executed_numbers = list(numbers)

                # --- Step 2: Fill amounts via Playwright ---
                executed_amounts = _fill_amounts_on_b03(page, amounts)

                # --- Final stop ---
                input(
                    "\n已完成安全帶入，請人工檢查畫面。\n"
                    "⚠️  系統不會自動送出/確認。請手動檢查後手動操作。\n"
                    "按 Enter 關閉瀏覽器。\n"
                )

            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"fill execution error: {exc}",
                    "auto_submit": False,
                    "auto_confirm": False,
                    "danger_buttons_clicked": danger_clicked,
                }
            finally:
                browser.close()

    except Exception as exc:
        return {
            "ok": False,
            "error": f"browser error: {exc}",
            "auto_submit": False,
            "auto_confirm": False,
            "danger_buttons_clicked": danger_clicked,
        }

    # --- Build safety report ---
    success = len(executed_numbers) > 0 and any(
        a.get("executed") for a in executed_amounts
    )
    return {
        "ok": success,
        "numbers": executed_numbers,
        "stars": stars,
        "amounts": amounts,
        "filled_amounts": executed_amounts,
        "danger_buttons_detected": danger_detected,
        "danger_buttons_clicked": danger_clicked,
        "auto_submit": False,
        "auto_confirm": False,
        "executed_numbers_count": len(executed_numbers),
        "executed_amounts_count": sum(1 for a in executed_amounts if a.get("executed")),
    }
