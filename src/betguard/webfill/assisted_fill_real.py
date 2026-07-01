from __future__ import annotations

import re
from typing import Any

from betguard.review import build_report
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_mapping_report import build_mapping_report
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.market_state import market_open_error
from betguard.webfill.page_routes import assisted_fill_route_error


SAFE_STARS = {"二星", "三星", "四星"}
DANGER_TEXTS = (
    "送出",
    "確認",
    "確定",
    "下注",
    "加入注單",
    "送出注單",
    "刪除",
    "清除全部",
)
FINAL_DECISION = {
    "real_site_executable": False,
    "human_required": True,
    "submit_executed": False,
    "submit_clicked": False,
    "confirm_clicked": False,
    "reason": "safe fill only; human must inspect and submit manually",
}
COMPLETION_MESSAGE = "已完成安全帶入，請人工檢查後自行確認 / 送出。"
SAFETY_LOCK_MESSAGE = "本功能只會協助帶入號碼與金額，不會自動送出。最後確認必須由人工執行。"
SAFETY_STATEMENT_LINES = [
    "安全聲明：",
    "- 系統只會選號與填金額",
    "- 系統不會按送出",
    "- 系統不會按確認",
    "- 完成後請人工檢查畫面",
    "- 最後送出由使用者自行決定",
]
STAR_ORDER = ["二星", "三星", "四星"]


def build_assist_fill_execution_plan(
    text: str,
    selector_report: dict[str, Any],
    *,
    page: str | None = None,
) -> dict[str, Any]:
    review_result = build_report(text).to_dict()
    base = _base_report(text, selector_report)
    base["review_result"] = review_result

    if review_result.get("status") != "ok":
        base["status"] = "BLOCKED"
        base["errors"].append(_review_block_reason(review_result))
        return base

    market_error = market_open_error(selector_report, str(review_result.get("game") or "539"))
    if market_error:
        base["status"] = "BLOCKED"
        base["errors"].append(market_error)
        return base

    route_error = assisted_fill_route_error(selector_report, page_name=page)
    if route_error:
        base["status"] = "BLOCKED"
        base["errors"].append(route_error)
        return base

    fill_plan = build_fill_plan(review_result)
    mapping = build_dry_run_mapping(fill_plan, selector_report)
    mapping_report = build_mapping_report(fill_plan, selector_report, mapping_result=mapping)
    base["fill_plan"] = fill_plan
    base["mapping"] = mapping
    base["mapping_report"] = mapping_report
    base["skipped"] = _skipped_stars(fill_plan)
    base["danger_buttons_detected"] = mapping_report.get("danger_check", {}).get(
        "dangerous_buttons_detected",
        [],
    )

    if mapping_report.get("status") != "SAFE":
        base["status"] = "BLOCKED"
        base["errors"].extend(_mapping_block_reasons(mapping_report))
        return base

    actions: list[dict[str, Any]] = []
    for action in mapping_report.get("actions", []):
        converted = _execution_action(action)
        if converted.get("error"):
            base["status"] = "BLOCKED"
            base["errors"].append(converted["error"])
            return base
        actions.append(converted)

    base["status"] = "READY"
    base["execution_plan"] = actions
    return base


def run_real_site_assisted_fill(
    execution_plan: dict[str, Any],
    *,
    confirmed: bool,
    headless: bool = False,
) -> dict[str, Any]:
    if execution_plan.get("status") != "READY":
        return execution_plan
    if not confirmed:
        report = dict(execution_plan)
        report["status"] = "CANCELLED"
        report["warnings"] = list(report.get("warnings", [])) + ["user confirmation was not YES"]
        return report

    target_url = execution_plan.get("target_url")
    if not target_url:
        report = dict(execution_plan)
        report["status"] = "BLOCKED"
        report["errors"] = list(report.get("errors", [])) + ["target url not found in selector report"]
        return report

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        report = dict(execution_plan)
        report["status"] = "BLOCKED"
        report["errors"] = list(report.get("errors", [])) + [f"Playwright is not installed: {exc}"]
        return report

    completed_actions: list[dict[str, Any]] = []
    with sync_playwright() as playwright:  # pragma: no cover - browser dependent
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            input("請在瀏覽器中手動登入/確認目前頁面，完成後回到終端機按 Enter 開始安全帶入。")
            for action in execution_plan.get("execution_plan", []):
                _execute_action(page, action)
                completed_actions.append(action)
        finally:
            # Keep this as a browser close instead of any submit/confirm action.
            browser.close()

    return build_acceptance_report(execution_plan, completed_actions)


def build_acceptance_report(
    execution_plan: dict[str, Any],
    completed_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    completed = list(completed_actions or [])
    selected_numbers = [str(action["label"]) for action in completed if action.get("type") == "select_number"]
    filled_amounts = {
        str(action["star"]): action["amount"]
        for action in completed
        if action.get("type") == "set_amount"
    }
    skipped = list(execution_plan.get("skipped", []))

    report = dict(execution_plan)
    report.update(
        {
            "mode": "real_site_assisted_fill",
            "status": "completed_before_human_confirm",
            "completed_actions": completed,
            "selected_numbers": selected_numbers,
            "filled_amounts": filled_amounts,
            "skipped": skipped,
            "acceptance_checklist": _acceptance_checklist(selected_numbers, filled_amounts, skipped),
            "danger_buttons_not_clicked": True,
            "danger_buttons_clicked": [],
            "submit_clicked": False,
            "confirm_clicked": False,
            "human_required": True,
            "message": COMPLETION_MESSAGE,
            "final_decision": dict(FINAL_DECISION),
        }
    )
    return report


def format_pretty_assist_fill_plan(report: dict[str, Any]) -> str:
    if report.get("status") == "completed_before_human_confirm":
        return format_pretty_acceptance_report(report)

    lines = [
        "Real-site Assisted Fill v0",
        f"Status: {report.get('status', 'BLOCKED')}",
        "",
        "Execution Plan:",
    ]
    for index, action in enumerate(report.get("execution_plan", []), start=1):
        if action.get("type") == "select_number":
            lines.append(f"[{index}] SELECT_NUMBER {action.get('label')} -> {action.get('selector')}")
        elif action.get("type") == "set_amount":
            lines.append(
                f"[{index}] SET_AMOUNT {action.get('star')} {action.get('amount')} -> {action.get('selector')}"
            )

    if report.get("skipped"):
        lines.append("")
        lines.append("Skipped:")
        for item in report["skipped"]:
            lines.append(f"- {item}")

    lines.append("")
    lines.append("Danger Buttons:")
    for label in report.get("danger_buttons_detected", []):
        lines.append(f"- {label} detected, not clicked")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    lines.append("")
    lines.append("Final Decision:")
    decision = report.get("final_decision", FINAL_DECISION)
    lines.append(f"- real_site_executable: {str(decision.get('real_site_executable')).lower()}")
    lines.append(f"- human_required: {str(decision.get('human_required')).lower()}")
    lines.append(f"- submit_executed: {str(decision.get('submit_executed')).lower()}")
    lines.append(f"- reason: {decision.get('reason')}")
    return "\n".join(lines)


def format_execution_safety_statement(report: dict[str, Any]) -> str:
    lines = list(SAFETY_STATEMENT_LINES)
    danger_labels = list(report.get("danger_buttons_detected") or [])
    if danger_labels:
        lines.append("")
        lines.append("偵測到危險操作元素（只列出，不操作）：")
        for label in danger_labels:
            lines.append(f"- {label}")
    return "\n".join(lines)


def format_pretty_acceptance_report(report: dict[str, Any]) -> str:
    lines = [
        "Real-site Assisted Fill Completed",
        "",
        "請人工檢查：",
    ]
    for item in report.get("acceptance_checklist", []):
        lines.append(f"[ ] {item}")
    lines.extend(
        [
            "",
            "Final:",
            f"- human_required: {str(report.get('human_required')).lower()}",
            f"- submit_clicked: {str(report.get('submit_clicked')).lower()}",
            f"- confirm_clicked: {str(report.get('confirm_clicked')).lower()}",
            f"- message: {report.get('message')}",
        ]
    )
    return "\n".join(lines)


def is_safe_execution_action(action: dict[str, Any]) -> bool:
    action_type = action.get("type")
    if action_type == "select_number":
        return _is_safe_number_action(action)
    if action_type == "set_amount":
        return _is_safe_amount_action(action)
    return False


def _base_report(text: str, selector_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill_v0",
        "status": "BLOCKED",
        "original": text,
        "target_url": _target_url(selector_report),
        "execution_plan": [],
        "skipped": [],
        "danger_buttons_detected": [],
        "danger_buttons_clicked": [],
        "danger_buttons_not_clicked": True,
        "submit_clicked": False,
        "confirm_clicked": False,
        "human_required": True,
        "completed_actions": [],
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [],
    }


def _execution_action(mapping_action: dict[str, Any]) -> dict[str, Any]:
    step = mapping_action.get("plan_step", {})
    candidates = list(mapping_action.get("selector_candidates") or [])
    candidate = candidates[0] if candidates else {}
    selector = _candidate_selector(candidate)
    action: dict[str, Any] = {
        "type": step.get("type"),
        "selector": selector,
        "candidate": candidate,
    }
    if step.get("type") == "select_number":
        action["label"] = step.get("label")
    elif step.get("type") == "set_amount":
        action["star"] = step.get("star")
        action["amount"] = step.get("amount")
    else:
        action["error"] = f"unsupported action type: {step.get('type')}"
        return action

    if not selector:
        action["error"] = "selector missing for execution action"
        return action
    if not is_safe_execution_action(action):
        action["error"] = f"unsafe execution action blocked: {step.get('type')}"
        return action
    return action


def _execute_action(page: Any, action: dict[str, Any]) -> None:
    if not is_safe_execution_action(action):
        raise RuntimeError(f"unsafe execution action blocked: {action.get('type')}")
    locator = page.locator(action["selector"]).first()
    if action["type"] == "select_number":
        locator.click()
    elif action["type"] == "set_amount":
        locator.fill(str(action["amount"]))
    else:
        raise RuntimeError(f"unsupported execution action: {action.get('type')}")


def _is_safe_number_action(action: dict[str, Any]) -> bool:
    label = str(action.get("label", ""))
    if re.fullmatch(r"0[1-9]|[12]\d|3[0-9]", label) is None:
        return False
    return _candidate_safe(action.get("candidate", {})) and not _contains_danger_text(action.get("selector", ""))


def _is_safe_amount_action(action: dict[str, Any]) -> bool:
    if action.get("star") not in SAFE_STARS:
        return False
    try:
        amount = float(action.get("amount"))
    except (TypeError, ValueError):
        return False
    if amount <= 0:
        return False
    return _candidate_safe(action.get("candidate", {})) and not _contains_danger_text(action.get("selector", ""))


def _candidate_safe(candidate: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in (
        "text",
        "value",
        "id",
        "name",
        "className",
        "onclick",
        "title",
        "alt",
        "ariaLabel",
        "outerHTML",
    ):
        value = candidate.get(key)
        if value:
            values.append(str(value))
    selectors = candidate.get("candidate_selectors") or []
    values.extend(str(selector) for selector in selectors)
    return not any(_contains_danger_text(value) for value in values)


def _contains_danger_text(text: Any) -> bool:
    compact = "".join(str(text or "").split())
    return any(word in compact for word in DANGER_TEXTS)


def _candidate_selector(candidate: dict[str, Any]) -> str:
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    for key in ("selector", "text", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    return ""


def _target_url(selector_report: dict[str, Any]) -> str:
    route_probe = selector_report.get("route_probe") or {}
    for key in ("built_url", "actual_url", "url"):
        if route_probe.get(key):
            return str(route_probe[key])
    diagnostics = selector_report.get("diagnostics") or {}
    if diagnostics.get("active_url"):
        return str(diagnostics["active_url"])
    frame_urls = diagnostics.get("frame_urls") or []
    return str(frame_urls[0]) if frame_urls else ""


def _skipped_stars(fill_plan: dict[str, Any]) -> list[str]:
    amounts = fill_plan.get("amounts") or {}
    return [star for star in STAR_ORDER if star not in amounts]


def _acceptance_checklist(
    selected_numbers: list[str],
    filled_amounts: dict[str, Any],
    skipped: list[str],
) -> list[str]:
    checklist: list[str] = []
    checklist.extend(f"號碼 {number} 已選取" for number in selected_numbers)
    checklist.extend(f"{star}金額 {amount} 正確" for star, amount in filled_amounts.items())
    checklist.extend(f"{star}未填" for star in skipped)
    checklist.append("未自動按送出")
    checklist.append("未自動按確認")
    return checklist


def _review_block_reason(result: dict[str, Any]) -> str:
    messages = list(result.get("errors", [])) + list(result.get("warnings", []))
    return "; ".join(str(message) for message in messages) or "review result is not ok"


def _mapping_block_reasons(report: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    reasons.extend(str(error) for error in report.get("errors", []))
    for item in report.get("missing", []):
        if item.get("type") == "number":
            reasons.append(f"number {item.get('label')} selector missing")
        elif item.get("type") == "amount":
            reasons.append(f"amount field {item.get('star')} missing")
        elif item.get("type") == "danger":
            reasons.append("danger buttons not verified")
    reasons.extend(str(warning) for warning in report.get("warnings", []))
    return reasons or ["mapping report is not SAFE"]
