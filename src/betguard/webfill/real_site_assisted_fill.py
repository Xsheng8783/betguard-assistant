from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from betguard.webfill.batch_queue import WAITING_FOR_HUMAN_CONFIRM, mark_item_waiting_for_human
from betguard.webfill.real_site_fill_plan import build_real_site_assisted_fill_plan
from betguard.webfill.safety import is_dangerous_action


RISK_LOCK_MESSAGE = (
    "本功能會在真實網站協助帶入號碼與金額。必須加上 "
    "--i-understand-real-site-fill-risk 才能執行；系統不會自動送出或確認。"
)
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
READY_TO_FILL = "READY_TO_FILL"
BLOCKED = "BLOCKED"

ENGLISH_DANGER_WORDS = (
    "submit",
    "confirm",
    "sendbet",
    "send_bet",
    "data-danger",
    "delete",
    "clearall",
)
FINAL_DECISION = {
    "real_site_auto_submit": False,
    "human_required": True,
    "next_step": "Human must inspect and manually confirm/submit. After that, user may run next item.",
}


def build_real_site_assisted_fill_preflight(
    queue: dict[str, Any],
    selector_report: dict[str, Any],
    *,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    report = _base_preflight_report()
    if not risk_acknowledged:
        report["errors"].append(RISK_LOCK_MESSAGE)
        return report

    fill_plan_report = build_real_site_assisted_fill_plan(queue, selector_report)
    report["fill_plan_report"] = fill_plan_report
    report["item"] = fill_plan_report.get("item")
    report["danger_buttons_detected"] = _danger_buttons(fill_plan_report)

    if fill_plan_report.get("status") != READY_FOR_HUMAN_REVIEW:
        report["errors"].append("real_site_fill_plan is not READY_FOR_HUMAN_REVIEW")
        report["errors"].extend(str(error) for error in fill_plan_report.get("errors", []))
        report["missing"].extend(str(item) for item in fill_plan_report.get("missing", []))
        return report

    if not report["danger_buttons_detected"]:
        report["errors"].append("danger candidates not found")
        report["missing"].append("danger candidates missing")
        return report

    for planned_action in fill_plan_report.get("planned_actions", []):
        action = _execution_action(planned_action, selector_report)
        error = validate_real_site_action(action)
        if error:
            report["errors"].append(error)
            return report
        report["execution_actions"].append(action)

    report["status"] = READY_TO_FILL
    return report


def run_real_site_assisted_fill_with_page(
    queue: dict[str, Any],
    selector_report: dict[str, Any],
    page: Any,
    *,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    preflight = build_real_site_assisted_fill_preflight(
        queue,
        selector_report,
        risk_acknowledged=risk_acknowledged,
    )
    if preflight.get("status") != READY_TO_FILL:
        return _blocked_runtime_report(preflight)

    try:
        executed_actions = execute_actions_on_page(page, preflight["execution_actions"])
    except Exception as exc:
        blocked = _blocked_runtime_report(preflight)
        blocked["errors"].append(str(exc))
        return blocked

    return build_real_site_assisted_fill_report(queue, preflight, executed_actions)


def run_real_site_assisted_fill(
    queue: dict[str, Any],
    selector_report: dict[str, Any],
    *,
    url: str,
    risk_acknowledged: bool,
    headless: bool = False,
    input_func: Any = input,
) -> dict[str, Any]:
    preflight = build_real_site_assisted_fill_preflight(
        queue,
        selector_report,
        risk_acknowledged=risk_acknowledged,
    )
    if preflight.get("status") != READY_TO_FILL:
        return _blocked_runtime_report(preflight)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        blocked = _blocked_runtime_report(preflight)
        blocked["errors"].append(f"Playwright is not installed: {exc}")
        return blocked

    with sync_playwright() as playwright:  # pragma: no cover - browser dependent
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            input_func("請在瀏覽器中手動登入並進入正確頁面，完成後回到終端機按 Enter 繼續。")
            executed_actions = execute_actions_on_page(page, preflight["execution_actions"])
            report = build_real_site_assisted_fill_report(queue, preflight, executed_actions)
            input_func("已完成安全帶入，請人工檢查畫面。按 Enter 結束此工具。")
            return report
        except Exception as exc:
            blocked = _blocked_runtime_report(preflight)
            blocked["errors"].append(str(exc))
            return blocked
        finally:
            browser.close()


def execute_actions_on_page(page: Any, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for action in actions:
        error = validate_real_site_action(action)
        if error:
            raise RuntimeError(error)

        try:
            locator = _locator_for_action(page, action)
        except Exception as exc:
            raise RuntimeError(f"locator lookup failed for {action_label(action)}: {exc}") from exc

        element_info = _read_element_safety_info(locator)
        if not _metadata_is_safe(element_info):
            raise RuntimeError(f"danger text detected on target element for {action_label(action)}")

        if action["type"] == "SELECT_NUMBER":
            try:
                locator.click()
            except Exception as exc:
                raise RuntimeError(f"click failed for {action_label(action)}: {exc}") from exc
            executed.append(
                {
                    "type": "SELECT_NUMBER",
                    "number": action["number"],
                    "executed": True,
                }
            )
        elif action["type"] == "SET_AMOUNT":
            try:
                locator.fill(str(action["amount"]))
            except Exception as exc:
                raise RuntimeError(f"fill failed for {action_label(action)}: {exc}") from exc
            executed.append(
                {
                    "type": "SET_AMOUNT",
                    "star": action["star"],
                    "amount": action["amount"],
                    "executed": True,
                }
            )
        else:
            raise RuntimeError(f"unsupported action type: {action.get('type')}")
    return executed


def build_real_site_assisted_fill_report(
    queue: dict[str, Any],
    preflight: dict[str, Any],
    executed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    item = preflight.get("item") or {}
    updated_queue = mark_item_waiting_for_human(deepcopy(queue), int(item.get("index")))
    return {
        "mode": "real_site_assisted_fill",
        "status": WAITING_FOR_HUMAN_CONFIRM,
        "item": item,
        "actions_executed": executed_actions,
        "danger_buttons_detected": list(preflight.get("danger_buttons_detected", [])),
        "danger_buttons_clicked": [],
        "queue_status": updated_queue.get("status"),
        "queue": updated_queue,
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [],
    }


def validate_real_site_action(action: dict[str, Any]) -> str | None:
    action_type = action.get("type")
    selector = str(action.get("selector") or "")
    if action_type not in {"SELECT_NUMBER", "SET_AMOUNT"}:
        return f"unsupported action type: {action_type}"
    if not selector:
        return f"selector missing for {action_label(action)}"
    if _contains_danger_text(selector):
        return f"unsafe selector blocked for {action_label(action)}"
    if _targets_groupset_value(selector, action.get("candidate") or {}):
        return (
            f"unsafe selector blocked for {action_label(action)}: "
            "GroupSet_Value must never be used as an amount target"
        )
    if not _metadata_is_safe(action.get("candidate") or {}):
        return f"unsafe selector candidate blocked for {action_label(action)}"

    if action_type == "SELECT_NUMBER":
        number = str(action.get("number") or "")
        if re.fullmatch(r"0[1-9]|[12]\d|3[0-9]", number) is None:
            return f"invalid number action: {number}"
        return None

    try:
        amount = float(action.get("amount"))
    except (TypeError, ValueError):
        return f"invalid amount action: {action.get('amount')}"
    if amount <= 0:
        return f"invalid amount action: {action.get('amount')}"
    if not action.get("star"):
        return "missing amount star"
    return None


def format_pretty_real_site_assisted_fill(report: dict[str, Any]) -> str:
    item = report.get("item") or {}
    lines = [
        "Real-site Assisted Fill Report",
        "",
        f"Status: {report.get('status')}",
        "",
        "Current Item:",
        f"[{item.get('index', '')}] {item.get('original', '')}".rstrip(),
    ]
    if item.get("parsed_summary"):
        lines.append(str(item["parsed_summary"]))

    lines.extend(["", "Executed:"])
    for action in report.get("actions_executed", []):
        if action.get("type") == "SELECT_NUMBER":
            lines.append(f"- SELECT_NUMBER {action.get('number')}")
        elif action.get("type") == "SET_AMOUNT":
            lines.append(f"- SET_AMOUNT {action.get('star')} {action.get('amount')}")

    lines.extend(["", "Danger:"])
    for label in report.get("danger_buttons_detected", []):
        lines.append(f"- {label} detected, not clicked")
    if not report.get("danger_buttons_detected"):
        lines.append("- danger candidates not verified")

    queue = report.get("queue") or {}
    items = list(queue.get("items", []))
    next_locked = any(item.get("status") == WAITING_FOR_HUMAN_CONFIRM for item in items)
    lines.extend(
        [
            "",
            "Queue:",
            f"- current item: {report.get('queue_status') or queue.get('status')}",
            f"- next item: {'locked until human confirms current item' if next_locked else 'not locked'}",
        ]
    )

    if report.get("missing"):
        lines.append("")
        lines.append("Missing:")
        for item_missing in report["missing"]:
            lines.append(f"- {item_missing}")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    decision = report.get("final_decision", FINAL_DECISION)
    lines.extend(
        [
            "",
            "Final:",
            f"- real_site_auto_submit: {str(decision.get('real_site_auto_submit')).lower()}",
            f"- human_required: {str(decision.get('human_required')).lower()}",
        ]
    )
    return "\n".join(lines)


def action_label(action: dict[str, Any]) -> str:
    if action.get("type") == "SELECT_NUMBER":
        return f"SELECT_NUMBER {action.get('number')}"
    if action.get("type") == "SET_AMOUNT":
        return f"SET_AMOUNT {action.get('star')} {action.get('amount')}"
    return str(action.get("type") or "UNKNOWN")


def _base_preflight_report() -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill_preflight",
        "status": BLOCKED,
        "item": None,
        "execution_actions": [],
        "danger_buttons_detected": [],
        "danger_buttons_clicked": [],
        "missing": [],
        "warnings": [],
        "errors": [],
        "final_decision": dict(FINAL_DECISION),
    }


def _blocked_runtime_report(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill",
        "status": BLOCKED,
        "item": preflight.get("item"),
        "actions_executed": [],
        "danger_buttons_detected": list(preflight.get("danger_buttons_detected", [])),
        "danger_buttons_clicked": [],
        "queue_status": None,
        "final_decision": dict(FINAL_DECISION),
        "missing": list(preflight.get("missing", [])),
        "warnings": list(preflight.get("warnings", [])),
        "errors": list(preflight.get("errors", [])),
    }


def _execution_action(planned_action: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    if planned_action.get("type") == "SELECT_NUMBER":
        number = str(planned_action.get("number") or "")
        candidate = _first_candidate(_number_candidates(selector_report).get(number))
        return {
            "type": "SELECT_NUMBER",
            "number": number,
            "selector": planned_action.get("selector") or _best_selector(candidate),
            "frame": planned_action.get("frame") or _frame(candidate),
            "candidate": candidate,
        }

    if planned_action.get("type") == "SET_AMOUNT":
        star = str(planned_action.get("star") or "")
        candidate = _first_candidate(_amount_candidates(selector_report).get(star))
        return {
            "type": "SET_AMOUNT",
            "star": star,
            "amount": planned_action.get("amount"),
            "selector": planned_action.get("selector") or _best_selector(candidate),
            "frame": planned_action.get("frame") or _frame(candidate),
            "candidate": candidate,
        }

    return {"type": planned_action.get("type"), "candidate": {}}


def _number_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("number_candidates") or selector_report.get("number_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _amount_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("amount_field_candidates") or selector_report.get("amount_field_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _first_candidate(candidates: Any) -> dict[str, Any]:
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        return first if isinstance(first, dict) else {}
    return {}


def _danger_buttons(plan_report: dict[str, Any]) -> list[str]:
    danger_check = plan_report.get("danger_check") or {}
    buttons = danger_check.get("danger_buttons") or []
    return [str(button) for button in buttons]


def _best_selector(candidate: dict[str, Any]) -> str:
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    for key in ("selector", "text", "value", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    return ""


def _frame(candidate: dict[str, Any]) -> str:
    return str(candidate.get("frame_name") or candidate.get("frame_url") or "")


def _metadata_is_safe(metadata: dict[str, Any]) -> bool:
    fields = (
        "text",
        "innerText",
        "textContent",
        "value",
        "id",
        "name",
        "className",
        "onclick",
        "title",
        "alt",
        "aria-label",
        "ariaLabel",
        "outerHTML",
    )
    values = [str(metadata.get(field) or "") for field in fields]
    selectors = metadata.get("candidate_selectors") or []
    values.extend(str(selector) for selector in selectors)
    return not any(_contains_danger_text(value) for value in values)


def _targets_groupset_value(selector: str, candidate: dict[str, Any]) -> bool:
    """Explicit, independent reject for GroupSet_Value (a 分組序號 field, not an
    amount field). Defense-in-depth: does not rely on upstream mapping having
    already excluded it -- checks the literal selector text, candidate id, and
    any candidate_selectors regardless of danger-word content.
    """
    if "GroupSet_Value" in str(selector or ""):
        return True
    if str(candidate.get("id") or "") == "GroupSet_Value":
        return True
    selectors = candidate.get("candidate_selectors") or []
    return any("GroupSet_Value" in str(item) for item in selectors)


def _contains_danger_text(text: Any) -> bool:
    value = str(text or "")
    compact = "".join(value.split())
    lowered = compact.lower()
    return is_dangerous_action(value) or any(word in lowered for word in ENGLISH_DANGER_WORDS)


def _locator_for_action(page: Any, action: dict[str, Any]) -> Any:
    context = _frame_context(page, str(action.get("frame") or ""))
    return _resolve_first_locator(context.locator(str(action["selector"])))


def _resolve_first_locator(locator: Any) -> Any:
    """Return the first-match locator across Playwright versions and fakes.

    Real Playwright exposes ``Locator.first`` as a property, while some fakes
    implement it as a callable method. Handle both without crashing.
    """
    first = getattr(locator, "first", None)
    if first is None:
        return locator
    if callable(first):
        return first()
    return first


def _frame_context(page: Any, frame_ref: str) -> Any:
    if not frame_ref:
        return page
    for frame in list(getattr(page, "frames", []) or []):
        name = _frame_attr(frame, "name")
        url = _frame_attr(frame, "url")
        if frame_ref in {name, url} or (url and frame_ref in url):
            return frame
    raise RuntimeError(f"frame not found: {frame_ref}")


def _frame_attr(frame: Any, attr: str) -> str:
    value = getattr(frame, attr, "")
    if callable(value):
        value = value()
    return str(value or "")


def _read_element_safety_info(locator: Any) -> dict[str, Any]:
    try:
        data = locator.evaluate(
            """el => ({
                text: el.textContent || "",
                innerText: el.innerText || "",
                value: el.value || "",
                id: el.id || "",
                name: el.name || "",
                className: el.className || "",
                title: el.title || "",
                alt: el.alt || "",
                ariaLabel: el.getAttribute("aria-label") || "",
                onclick: el.getAttribute("onclick") || "",
                outerHTML: (el.outerHTML || "").slice(0, 500)
            })"""
        )
    except Exception as exc:  # pragma: no cover - browser dependent
        raise RuntimeError(f"unable to verify element safety before operation: {exc}") from exc
    return data if isinstance(data, dict) else {}
