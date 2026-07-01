from __future__ import annotations

from pathlib import Path
from typing import Any

from betguard.review import build_report
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.mock_page import DANGER_BUTTONS, STAR_FIELDS, mock_page_url


FINAL_DECISION = {
    "real_site_executable": False,
    "human_required": True,
    "reason": "mock test only; no real website operation",
}


def build_mock_fill_report(text: str) -> dict[str, Any]:
    review_result = build_report(text).to_dict()
    if review_result.get("status") != "ok":
        return _blocked_report(text, _review_block_reason(review_result), review_result=review_result)

    fill_plan = build_fill_plan(review_result)
    if fill_plan.get("errors"):
        return _blocked_report(text, "; ".join(fill_plan["errors"]), review_result=review_result, fill_plan=fill_plan)

    filled_amounts = {
        star: amount
        for star, amount in fill_plan.get("amounts", {}).items()
        if amount is not None
    }
    selected_numbers = list(fill_plan.get("numbers", []))
    skipped = [star for star in STAR_FIELDS if star not in filled_amounts]
    return {
        "mode": "assisted_fill_mock",
        "status": "COMPLETED_MOCK_ONLY",
        "original": text,
        "selected_numbers": selected_numbers,
        "filled_amounts": filled_amounts,
        "skipped": skipped,
        "danger_buttons_detected": list(DANGER_BUTTONS),
        "danger_buttons_clicked": [],
        "review_result": review_result,
        "fill_plan": fill_plan,
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [],
    }


def run_assisted_fill_mock(
    text: str,
    *,
    headless: bool = False,
    page_path: str | Path | None = None,
) -> dict[str, Any]:
    report = build_mock_fill_report(text)
    if report.get("status") != "COMPLETED_MOCK_ONLY":
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
        return _blocked_report(text, "mock page must be local file URL", fill_plan=report.get("fill_plan"))

    with sync_playwright() as playwright:  # pragma: no cover - browser dependent
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
            for number in report["selected_numbers"]:
                _click_mock_number(page, number)
            for star, amount in report["filled_amounts"].items():
                _fill_mock_amount(page, star, amount)
            report["danger_buttons_detected"] = _read_mock_danger_labels(page)
            report["danger_buttons_clicked"] = _read_danger_clicked_state(page)
        finally:
            browser.close()

    return report


def format_pretty_mock_report(report: dict[str, Any]) -> str:
    lines = [
        "Assisted Fill Mock Report",
        "",
        f"Status: {report.get('status', 'BLOCKED')}",
        "",
        "Original:",
        str(report.get("original", "")),
        "",
        "Selected Numbers:",
    ]
    for number in report.get("selected_numbers", []):
        lines.append(f"- {number} selected")

    lines.append("")
    lines.append("Filled Amounts:")
    for star, amount in report.get("filled_amounts", {}).items():
        lines.append(f"- {star}: {amount}")

    lines.append("")
    lines.append("Skipped:")
    for star in report.get("skipped", []):
        lines.append(f"- {star}")

    lines.append("")
    lines.append("Danger Buttons:")
    for label in report.get("danger_buttons_detected", []):
        lines.append(f"- {label} detected, not clicked")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    final_decision = report.get("final_decision", FINAL_DECISION)
    lines.append("")
    lines.append("Final Decision:")
    lines.append(f"- real_site_executable: {str(final_decision.get('real_site_executable')).lower()}")
    lines.append(f"- human_required: {str(final_decision.get('human_required')).lower()}")
    lines.append(f"- reason: {final_decision.get('reason')}")
    return "\n".join(lines)


def _click_mock_number(page: Any, number: str) -> None:
    page.locator(f'button[data-number="{number}"]').click()


def _fill_mock_amount(page: Any, star: str, amount: Any) -> None:
    page.locator(f'input[data-amount-field="{star}"]').fill(str(amount))


def _read_mock_danger_labels(page: Any) -> list[str]:
    return [text.strip() for text in page.locator('[data-danger="true"]').all_inner_texts()]


def _read_danger_clicked_state(page: Any) -> list[str]:
    clicked = page.evaluate("document.body.dataset.dangerClicked || ''")
    return ["unexpected danger button click detected"] if clicked else []


def _blocked_report(
    text: str,
    reason: str,
    *,
    review_result: dict[str, Any] | None = None,
    fill_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "mode": "assisted_fill_mock",
        "status": "BLOCKED",
        "original": text,
        "selected_numbers": [],
        "filled_amounts": {},
        "skipped": list(STAR_FIELDS),
        "danger_buttons_detected": [],
        "danger_buttons_clicked": [],
        "review_result": review_result,
        "fill_plan": fill_plan,
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [reason],
    }


def _review_block_reason(review_result: dict[str, Any]) -> str:
    messages = list(review_result.get("errors", [])) + list(review_result.get("warnings", []))
    return "; ".join(str(message) for message in messages) or "review result is not ok"
