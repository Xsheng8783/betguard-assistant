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
    """Fill per-star amounts via Playwright on the B03 frame.

    Uses ``window.frames[2]`` to find PengBet.Value inputs and fills them
    by position (0→二星, 1→三星, 2→四星).  Each fill triggers input/change
    events to notify knockout.
    """
    executed: list[dict[str, Any]] = []
    amount_css = f'input[data-bind*="{AMOUNT_DATA_BIND_MARKER}"]'

    for star, position in sorted(STAR_TO_POSITION.items()):
        amt = amounts.get(str(star), amounts.get(star, 0))
        if amt <= 0:
            continue
        try:
            # Access B03 frame via window.frames[2] and find PengBet inputs
            frame = page.frame(url="**" + B03_URL_MARKER + "**")
            if frame is None:
                # Fallback: use page-level locator — Playwright may route to B03
                loc = page.locator(amount_css).nth(position)
            else:
                loc = frame.locator(amount_css).nth(position)
            star_name = STAR_NAMES.get(star, str(star))
            loc.fill(str(amt))
            # Readback: re-locate and read actual input value
            actual = ""
            verified = False
            try:
                loc.dispatch_event("input")
                loc.dispatch_event("change")
                loc.blur()
                # Wait for knockout re-render (sync Playwright API)
                page.wait_for_timeout(50)
                if frame is None:
                    rloc = page.locator(amount_css).nth(position)
                else:
                    rloc = frame.locator(amount_css).nth(position)
                actual = str(rloc.input_value() or "")
                verified = (actual.strip().lstrip("0") or "0") == (str(amt).lstrip("0") or "0")
            except Exception:
                try:
                    # Last resort: evaluate JS to read value
                    actual = str(loc.evaluate("el => el.value") or "")
                    verified = (actual.strip().lstrip("0") or "0") == (str(amt).lstrip("0") or "0")
                except Exception:
                    actual = ""
                    verified = False
            executed.append({
                "type": "SET_AMOUNT",
                "star": star,
                "star_name": star_name,
                "amount": amt,
                "expected_amount": amt,
                "actual_amount": actual,
                "position": position,
                "executed": True,
                "verified": verified,
            })
        except Exception as exc:
            executed.append({
                "type": "SET_AMOUNT",
                "star": star,
                "star_name": STAR_NAMES.get(star, str(star)),
                "amount": amt,
                "executed": False,
                "error": str(exc)[:200],
            })
    return executed


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
