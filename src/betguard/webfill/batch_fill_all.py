"""Batch fill: process all CURRENT+PENDING items without closing the browser."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from betguard.webfill.real_site_assisted_fill import (
    build_real_site_fill_preflight_report,
    build_execution_actions_from_preflight,
    _fast_select_numbers_knockout,
    _execute_amounts_via_playwright,
    READY_FOR_HUMAN_REVIEW,
)
from betguard.webfill.batch_queue import (
    mark_current_done_by_human,
)

RISK_LOCK_MESSAGE = (
    "安全鎖：真站輔助填入需要明確風險確認。\n"
    "請加上 --i-understand-real-site-fill-risk 旗標並確認你了解風險。"
)


def run_real_site_assisted_fill_all(
    *,
    queue_path: str,
    profile_path: str,
    url: str,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    """Fill ALL CURRENT+PENDING items without closing browser."""
    qp = Path(queue_path)
    pp = Path(profile_path)

    queue = json.loads(qp.read_text(encoding="utf-8"))
    profile = json.loads(pp.read_text(encoding="utf-8"))

    if not risk_acknowledged:
        return {"error": RISK_LOCK_MESSAGE}

    items_to_fill = []
    for item in queue.get("items", []):
        if item.get("status") in ("CURRENT", "PENDING"):
            items_to_fill.append(item)

    if not items_to_fill:
        return {"result": "no_items", "items_processed": 0}

    print(f"找到 {len(items_to_fill)} 筆待填。第一筆 ~8s，後續 ~6s 每筆。")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"error": f"Playwright not installed: {exc}"}

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            input("手動登入 → 二三四星頁面 → 按 Enter 開始。")

            for i, item in enumerate(items_to_fill):
                idx = int(item.get("index", 0))
                stars = item.get("star", "")
                numbers = item.get("numbers", [])
                print(f"\n{'='*50}")
                print(f"[{i+1}/{len(items_to_fill)}] #{idx} {','.join(str(n) for n in numbers)} {stars}")
                print(f"{'='*50}")

                v1 = build_real_site_fill_preflight_report(queue, profile, item_index=idx)
                if v1.get("status") != READY_FOR_HUMAN_REVIEW:
                    print(f"  SKIP: preflight not ready → {v1.get('errors', [])}")
                    results.append({"idx": idx, "status": "SKIPPED"})
                    continue

                try:
                    all_actions = build_execution_actions_from_preflight(v1)
                except ValueError as exc:
                    print(f"  SKIP: {exc}")
                    results.append({"idx": idx, "status": "SKIPPED"})
                    continue

                # Hybrid: knockout for numbers, Playwright for amounts
                t0 = time.time()
                num_actions = [a for a in all_actions if a["type"] == "SELECT_NUMBER"]
                amt_actions = [a for a in all_actions if a["type"] == "SET_AMOUNT"]

                executed = []
                if num_actions:
                    nums = [str(a["number"]) for a in num_actions]
                    _fast_select_numbers_knockout(page, nums)
                    executed += [{"type": "SELECT_NUMBER", "number": a["number"], "executed": True} for a in num_actions]
                    print(f"  號碼 {nums} — knockout 0.05s")

                if amt_actions:
                    executed += _execute_amounts_via_playwright(page, amt_actions)
                    for a in amt_actions:
                        print(f"  金額 {a.get('star')} = {a['amount']}")

                t1 = time.time()
                print(f"  總耗時: {t1-t0:.1f}s")

                print("\n  檢查畫面 → 自行點送出 → 按 Enter 繼續（q=結束）。")
                inp = input("  > ")
                if inp.strip().lower() == 'q':
                    print("  中斷。")
                    break

                # Mark item DONE directly (bypass queue state machine)
                from datetime import datetime, timezone
                item["status"] = "DONE"
                item["fill_completed_at"] = datetime.now(timezone.utc).isoformat()
                # Move queue's CURRENT to next PENDING item
                next_found = False
                for it in queue.get("items", []):
                    if it.get("status") == "PENDING" and not next_found:
                        it["status"] = "CURRENT"
                        next_found = True
                queue["status"] = "READY"
                json.dump(queue, qp.open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
                results.append({"idx": idx, "status": "DONE", "time_s": round(t1 - t0, 1)})

        finally:
            browser.close()

    return {"result": "complete", "processed": len(results), "total": len(items_to_fill), "items": results}
