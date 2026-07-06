"""ZhuPeng batch session runner — one browser, multiple items, human gate each.

Reuses the same browser/page/B03 frame across multiple approved_fill_queue
items.  Each item follows:

  preflight → fill → WAITING_FOR_HUMAN_CONFIRM → human types DONE → DONE → next

Safety: never auto-submit, auto-confirm, or auto-advance.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from betguard.webfill.batch_audit import sync_batch_audit
from betguard.webfill.batch_queue import (
    CURRENT,
    PENDING,
    DONE,
    WAITING_FOR_HUMAN_CONFIRM,
    mark_current_done_by_human,
    mark_current_waiting_for_human,
)
from betguard.webfill.batch_mock_queue import load_queue_state, save_queue_state
from betguard.webfill.zhu_peng_pipeline import (
    zhu_peng_fill_execute,
    zhu_peng_preflight,
)
from betguard.webfill.batch_mock_queue import load_queue_state, save_queue_state
RISK_LOCK_MESSAGE = (
    "安全鎖：真站 ZhuPeng session fill 需要明確風險確認。\n"
    "請加上 --i-understand-real-site-fill-risk 旗標並確認你了解風險。"
)

# ── B03 frame cache helpers ────────────────────────────────────────────


def _validate_b03_frame(page: Any) -> dict[str, Any]:
    """Quick-validate that the B03 frame is still accessible and on the right page.

    Returns a dict with 'valid' (bool) and 'reason' (str|None).
    """
    try:
        raw = page.evaluate(
            """
            (function(){
                try {
                    var f = window.frames[2];
                    if (!f || !f.document) return 'NO_FRAME';
                    var d = f.document;
                    // Check ZhuPeng manager exists
                    if (!f.Mo || !f.Mo.ZhuPengMgr) return 'NO_MO';
                    // Quick smoke: visible PengBet inputs
                    var inps = d.querySelectorAll('input');
                    var vis = 0;
                    for(var i=0;i<inps.length;i++){
                        var b = inps[i].getAttribute('data-bind')||'';
                        if(b.indexOf('PengBet.Value')>=0 && (inps[i].offsetWidth||inps[i].offsetHeight))
                            vis++;
                    }
                    return vis >= 1 ? 'OK' : 'NO_PENGBET_INPUTS';
                } catch(e) { return 'ERROR:' + e.message; }
            })()
            """
        )
    except Exception as exc:
        return {"valid": False, "reason": f"eval failed: {exc}"}

    if raw == "OK":
        return {"valid": True, "reason": None}
    return {"valid": False, "reason": raw}


# ── Session fill ───────────────────────────────────────────────────────


def run_zhu_peng_session_fill(
    *,
    queue_path: str,
    url: str,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    """Run ZhuPeng batch session fill.

    Opens browser once, processes CURRENT + PENDING items one at a time.
    After each fill, waits for the human to type DONE before advancing.
    Never auto-submits, auto-confirms, or auto-advances.
    """
    if not risk_acknowledged:
        return {"error": RISK_LOCK_MESSAGE}

    qp = Path(queue_path)
    queue = load_queue_state(str(qp))

    items_to_fill: list[dict[str, Any]] = []
    for item in queue.get("items", []):
        if item.get("status") in (CURRENT, PENDING):
            items_to_fill.append(item)

    if not items_to_fill:
        return {"result": "no_items", "items_processed": 0}

    print(f"找到 {len(items_to_fill)} 筆待填（含 CURRENT + PENDING）。")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"error": f"Playwright not installed: {exc}"}

    results: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            browser.close()
            return {"error": f"Failed to navigate to {url}: {exc}"}

        input("手動登入 → 柱碰頁面 → 按 Enter 開始。")

        # Initial frame validation
        frame_status = _validate_b03_frame(page)
        if not frame_status["valid"]:
            print(f"BLOCKED: B03 frame invalid — {frame_status['reason']}")
            browser.close()
            return {"error": f"B03 frame invalid: {frame_status['reason']}"}

        processed_count = 0

        for i, item in enumerate(items_to_fill):
            # ── Re-read queue to pick up state changes ──
            queue = load_queue_state(str(qp))
            idx = int(item.get("index", 0))

            # Re-fetch item from fresh queue for correct status
            fresh_item = None
            for qi in queue.get("items", []):
                if qi.get("index") == idx:
                    fresh_item = qi
                    break
            if fresh_item is None:
                results.append({"idx": idx, "status": "SKIPPED", "reason": "item not in queue"})
                continue
            item = fresh_item

            # Only process CURRENT items (PENDING items that aren't current yet are skipped)
            if item.get("status") != CURRENT:
                results.append({"idx": idx, "status": "SKIPPED", "reason": f"status is {item.get('status')}"})
                continue

            print(f"\n{'='*50}")
            print(f"[{i+1}/{len(items_to_fill)}] #{idx} {item.get('summary', item.get('parsed_summary', ''))}")
            print(f"{'='*50}")

            # ── Frame revalidation ──
            frame_status = _validate_b03_frame(page)
            if not frame_status["valid"]:
                print(f"  BLOCKED: frame invalid — {frame_status['reason']}")
                blocked_items.append({"idx": idx, "reason": frame_status["reason"]})
                results.append({"idx": idx, "status": "BLOCKED", "reason": frame_status["reason"]})
                break

            # ── Preflight ──
            pre = zhu_peng_preflight(item)
            if pre["status"] != "READY_FOR_HUMAN_REVIEW":
                print(f"  SKIP: preflight not ready → {pre.get('errors', [])}")
                results.append({"idx": idx, "status": "SKIPPED", "reason": pre.get("errors")})
                continue

            # ── Fill ──
            t0 = time.time()
            try:
                fill_report = zhu_peng_fill_execute(page, item)
            except Exception as exc:
                print(f"  ERROR during fill: {exc}")
                results.append({"idx": idx, "status": "ERROR", "reason": str(exc)})
                continue
            t1 = time.time()

            if fill_report.get("blocked"):
                print(f"  BLOCKED: fill readback mismatch — {fill_report.get('steps')}")
                blocked_items.append({"idx": idx, "fill_report": fill_report})
                results.append({"idx": idx, "status": "BLOCKED", "reason": "readback mismatch"})
                continue

            # ── Mark WAITING_FOR_HUMAN_CONFIRM ──
            item["fill_completed_at"] = datetime.now(timezone.utc).isoformat()
            item["fill_report"] = fill_report
            item["fill_actions_executed"] = fill_report.get("steps", [])
            queue = mark_current_waiting_for_human(queue)
            save_queue_state(queue, str(qp))
            sync_batch_audit(queue)

            print(f"  號碼: {fill_report.get('numbers')}")
            print(f"  金額: {fill_report.get('amounts')}")
            print(f"  耗時: {t1-t0:.1f}s")
            print()
            print("  已填入目前這筆。請人工檢查網站畫面，人工送出/確認後，輸入 DONE 才會解鎖下一筆。")

            # ── Wait for human DONE ──
            user_input = input("  > ").strip()
            if user_input.upper() != "DONE":
                print("  未確認（需輸入 DONE）。保持 WAITING_FOR_HUMAN_CONFIRM，session 結束。")
                results.append({"idx": idx, "status": "WAITING", "reason": "human did not type DONE"})
                break

            # ── Mark DONE, advance to next ──
            queue = load_queue_state(str(qp))
            queue = mark_current_done_by_human(queue)
            save_queue_state(queue, str(qp))
            sync_batch_audit(queue)
            processed_count += 1
            results.append({"idx": idx, "status": "DONE", "time_s": round(t1 - t0, 1)})

        browser.close()

    return {
        "result": "complete",
        "processed": processed_count,
        "total": len(items_to_fill),
        "blocked": len(blocked_items),
        "items": results,
    }
