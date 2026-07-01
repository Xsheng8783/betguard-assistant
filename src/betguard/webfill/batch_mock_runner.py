from __future__ import annotations

from pathlib import Path
from typing import Any

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    COMPLETED,
    DONE,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
    get_current_item,
    mark_item_done_by_human,
    mark_item_waiting_for_human,
)
from betguard.webfill.mock_page import DANGER_BUTTONS, STAR_FIELDS, mock_page_url


FINAL_DECISION = {
    "real_site_auto_submit": False,
    "human_required_each_item": True,
    "reason": "mock test only; no real website operation",
}


def build_batch_mock_runner_report(
    text: str,
    *,
    auto_confirm_mock: bool = False,
) -> dict[str, Any]:
    review_result = attach_summaries(review_text(text).to_dict())
    queue = build_batch_queue(review_result)
    report = _base_report(queue)
    report["review_result"] = review_result
    report["queue"] = queue

    if queue.get("status") == BATCH_BLOCKED:
        report["status"] = BATCH_BLOCKED
        report["mock_page_opened"] = False
        report["items"] = _blocked_items(queue)
        return report

    while True:
        current = get_current_item(queue)
        if current is None:
            break

        item_report = _mock_fill_item(current)
        queue = mark_item_waiting_for_human(queue, int(current["index"]))
        item_report["status"] = WAITING_FOR_HUMAN_CONFIRM
        item_report["status_history"].append(WAITING_FOR_HUMAN_CONFIRM)
        report["items"].append(item_report)

        if not auto_confirm_mock:
            report["status"] = WAITING_FOR_HUMAN_CONFIRM
            report["queue"] = queue
            return _refresh_report_summary(report, queue)

        queue = mark_item_done_by_human(queue, int(current["index"]))
        item_report["status"] = DONE
        item_report["status_history"].append(DONE)

    report["queue"] = queue
    report["status"] = "COMPLETED_MOCK_ONLY" if queue.get("status") == COMPLETED else queue.get("status")
    return _refresh_report_summary(report, queue)


def run_batch_mock_runner(
    text: str,
    *,
    auto_confirm_mock: bool = False,
    headless: bool = False,
    page_path: str | Path | None = None,
    execute_browser: bool = True,
) -> dict[str, Any]:
    report = build_batch_mock_runner_report(text, auto_confirm_mock=auto_confirm_mock)
    if report.get("status") == BATCH_BLOCKED or not execute_browser:
        return report

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        blocked = dict(report)
        blocked["status"] = "BLOCKED"
        blocked["errors"] = [f"Playwright is not installed: {exc}"]
        return blocked

    page_url = mock_page_url(page_path)
    if not page_url.startswith("file://"):
        blocked = dict(report)
        blocked["status"] = "BLOCKED"
        blocked["errors"] = ["mock page must be local file URL"]
        return blocked

    with sync_playwright() as playwright:  # pragma: no cover - browser dependent
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            for item in report.get("items", []):
                page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                for number in item.get("selected_numbers", []):
                    _click_mock_number(page, number)
                for star, amount in item.get("filled_amounts", {}).items():
                    _fill_mock_amount(page, star, amount)
                item["danger_buttons_detected"] = _read_mock_danger_labels(page)
                item["danger_buttons_clicked"] = _read_danger_clicked_state(page)
        finally:
            browser.close()

    report["danger_buttons_detected"] = list(DANGER_BUTTONS)
    report["danger_buttons_clicked"] = [
        click
        for item in report.get("items", [])
        for click in item.get("danger_buttons_clicked", [])
    ]
    return report


def run_batch_mock_runner_from_file(
    file_path: str | Path,
    *,
    auto_confirm_mock: bool = False,
    execute_browser: bool = True,
) -> dict[str, Any]:
    text = Path(file_path).read_text(encoding="utf-8")
    return run_batch_mock_runner(
        text,
        auto_confirm_mock=auto_confirm_mock,
        execute_browser=execute_browser,
    )


def format_pretty_batch_mock_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "Batch Mock Runner Report",
        "",
        f"Status: {report.get('status')}",
        "",
        "Summary:",
        f"- total: {summary.get('total', 0)}",
        f"- completed: {summary.get('completed', 0)}",
        f"- blocked: {summary.get('blocked', 0)}",
        f"- real_site_auto_submit: {str(summary.get('real_site_auto_submit')).lower()}",
        "",
        "Items:",
    ]
    for item in report.get("items", []):
        lines.append(f"[{item.get('index')}] {item.get('status')}")
        lines.append(f"    original: {item.get('original', '')}")
        if item.get("selected_numbers"):
            lines.append(f"    selected: {', '.join(item.get('selected_numbers', []))}")
        if item.get("filled_amounts"):
            filled = ", ".join(f"{star}={amount}" for star, amount in item["filled_amounts"].items())
            lines.append(f"    filled: {filled}")
        lines.append(f"    stopped_before_confirm: {str(item.get('stopped_before_confirm')).lower()}")
        for error in item.get("errors", []):
            lines.append(f"    - {error}")
        for warning in item.get("warnings", []):
            lines.append(f"    - {warning}")

    lines.append("")
    lines.append("Danger Buttons:")
    for label in report.get("danger_buttons_detected", []):
        lines.append(f"- {label} detected, not clicked")

    decision = report.get("final_decision", FINAL_DECISION)
    lines.extend(
        [
            "",
            "Final Decision:",
            f"- real_site_auto_submit: {str(decision.get('real_site_auto_submit')).lower()}",
            f"- human_required_each_item: {str(decision.get('human_required_each_item')).lower()}",
            f"- reason: {decision.get('reason')}",
        ]
    )
    return "\n".join(lines)


def _base_report(queue: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "batch_mock_runner",
        "status": queue.get("status"),
        "summary": {
            "total": queue.get("summary", {}).get("total", 0),
            "completed": 0,
            "blocked": queue.get("summary", {}).get("blocked", 0),
            "real_site_auto_submit": False,
        },
        "items": [],
        "mock_page_opened": queue.get("status") != BATCH_BLOCKED,
        "danger_buttons_detected": list(DANGER_BUTTONS) if queue.get("status") != BATCH_BLOCKED else [],
        "danger_buttons_clicked": [],
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [],
    }


def _blocked_items(queue: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in queue.get("items", []):
        items.append(
            {
                "index": item.get("index"),
                "original": item.get("original", ""),
                "status": item.get("status"),
                "selected_numbers": [],
                "filled_amounts": {},
                "stopped_before_confirm": False,
                "warnings": list(item.get("warnings", [])),
                "errors": list(item.get("errors", [])),
            }
        )
    return items


def _mock_fill_item(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("review_result", {})
    selected_numbers = _numbers_for_result(result)
    filled_amounts = _filled_amounts_for_result(result)
    return {
        "index": item.get("index"),
        "original": item.get("original", ""),
        "parsed_summary": item.get("parsed_summary", ""),
        "status": "READY_TO_FILL",
        "status_history": ["READY_TO_FILL"],
        "selected_numbers": selected_numbers,
        "filled_amounts": filled_amounts,
        "skipped": [star for star in STAR_FIELDS if star not in filled_amounts],
        "danger_buttons_detected": list(DANGER_BUTTONS),
        "danger_buttons_clicked": [],
        "stopped_before_confirm": True,
        "warnings": [],
        "errors": [],
    }


def _numbers_for_result(result: dict[str, Any]) -> list[str]:
    if result.get("type") == "column":
        numbers = [number for column in result.get("columns", []) for number in column]
    elif result.get("type") == "car":
        numbers = [result.get("number")]
    else:
        numbers = list(result.get("numbers", []))
    return [f"{int(number):02d}" for number in numbers if number is not None]


def _filled_amounts_for_result(result: dict[str, Any]) -> dict[str, int]:
    if result.get("type") == "car":
        return {}

    bets = result.get("bets") or {}
    filled: dict[str, int] = {}
    for star in result.get("stars", []):
        label = _star_label(star)
        per_star = bets.get(str(star), {}) if isinstance(bets, dict) else {}
        money = per_star.get("money") if isinstance(per_star, dict) else None
        if money is None:
            money = result.get("money")
        if money is not None:
            filled[label] = int(money)
    return filled


def _star_label(star: Any) -> str:
    index = int(star) - 2
    return STAR_FIELDS[index] if 0 <= index < len(STAR_FIELDS) else f"{star}星"


def _refresh_report_summary(report: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    report["summary"] = {
        "total": queue.get("summary", {}).get("total", 0),
        "completed": sum(1 for item in report.get("items", []) if item.get("status") == DONE),
        "blocked": queue.get("summary", {}).get("blocked", 0),
        "real_site_auto_submit": False,
    }
    report["final_decision"] = dict(FINAL_DECISION)
    return report


def _click_mock_number(page: Any, number: str) -> None:
    page.locator(f'button[data-number="{number}"]').click()


def _fill_mock_amount(page: Any, star: str, amount: Any) -> None:
    page.locator(f'input[data-amount-field="{star}"]').fill(str(amount))


def _read_mock_danger_labels(page: Any) -> list[str]:
    return [text.strip() for text in page.locator('[data-danger="true"]').all_inner_texts()]


def _read_danger_clicked_state(page: Any) -> list[str]:
    clicked = page.evaluate("document.body.dataset.dangerClicked || ''")
    return ["unexpected danger button click detected"] if clicked else []

