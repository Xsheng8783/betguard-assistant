from __future__ import annotations

from typing import Any

from betguard.webfill.batch_queue import CURRENT, PENDING, READY_FOR_QUEUE, READY_TO_FILL, get_current_item


FINAL_DECISION = {
    "real_site_execute": False,
    "real_site_auto_submit": False,
    "human_review_required": True,
    "reason": "planning only; no real website operation",
}


def build_real_site_assisted_fill_plan(
    queue: dict[str, Any],
    selector_report: dict[str, Any],
) -> dict[str, Any]:
    report = _base_report()

    if queue.get("status") != READY_FOR_QUEUE:
        report["errors"].append(f"queue status must be READY, got {queue.get('status')}")
        return report

    item = get_current_item(queue)
    if item is None:
        report["errors"].append("current queue item not found")
        return report
    if item.get("status") not in {CURRENT, PENDING, READY_TO_FILL}:
        report["item"] = _item_summary(item)
        report["errors"].append(f"current item is not ready: {item.get('status')}")
        return report

    report["item"] = _item_summary(item)
    fill_plan = item.get("fill_plan") or {}
    if fill_plan.get("errors"):
        report["errors"].extend(str(error) for error in fill_plan["errors"])
        return report

    danger_candidates = _danger_candidates(selector_report)
    report["danger_check"] = {
        "danger_candidates_found": bool(danger_candidates),
        "danger_buttons": [_danger_label(candidate) for candidate in danger_candidates],
        "will_click_danger": False,
    }
    if not danger_candidates:
        report["missing"].append("danger candidates missing")
        report["errors"].append("danger candidates not found")

    for step in fill_plan.get("planned_steps", []):
        action = _planned_action(step, selector_report)
        report["planned_actions"].append(action)
        if not action["selector_found"]:
            report["missing"].append(_missing_message(step))

    if report["missing"] or report["errors"]:
        return report

    report["status"] = "READY_FOR_HUMAN_REVIEW"
    return report


def format_pretty_real_site_fill_plan(report: dict[str, Any]) -> str:
    item = report.get("item") or {}
    lines = [
        "Real-site Assisted Fill Plan",
        "",
        f"Status: {report.get('status', 'BLOCKED')}",
        "",
        "Current Item:",
        f"[{item.get('index', '')}] {item.get('original', '')}".rstrip(),
        str(item.get("parsed_summary", "")),
        "",
        "Planned Actions:",
    ]
    for index, action in enumerate(report.get("planned_actions", []), start=1):
        if action.get("type") == "SELECT_NUMBER":
            lines.append(f"[{index}] SELECT_NUMBER {action.get('number')}")
        else:
            lines.append(f"[{index}] SET_AMOUNT {action.get('star')} {action.get('amount')}")
        lines.append(f"    selector_found: {str(action.get('selector_found')).lower()}")
        lines.append(f"    execute: {str(action.get('execute')).lower()}")
        if action.get("selector"):
            lines.append(f"    selector: {action.get('selector')}")
        if action.get("frame"):
            lines.append(f"    frame: {action.get('frame')}")

    lines.append("")
    lines.append("Danger Check:")
    danger_check = report.get("danger_check") or {}
    for label in danger_check.get("danger_buttons", []):
        lines.append(f"- {label} detected")
    lines.append(f"- will_click_danger: {str(danger_check.get('will_click_danger')).lower()}")

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

    final_decision = report.get("final_decision", FINAL_DECISION)
    lines.extend(
        [
            "",
            "Final Decision:",
            f"- real_site_execute: {str(final_decision.get('real_site_execute')).lower()}",
            f"- real_site_auto_submit: {str(final_decision.get('real_site_auto_submit')).lower()}",
            f"- human_review_required: {str(final_decision.get('human_review_required')).lower()}",
            f"- reason: {final_decision.get('reason')}",
        ]
    )
    return "\n".join(lines)


def _base_report() -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill_plan",
        "status": "BLOCKED",
        "item": None,
        "planned_actions": [],
        "danger_check": {
            "danger_candidates_found": False,
            "danger_buttons": [],
            "will_click_danger": False,
        },
        "missing": [],
        "errors": [],
        "warnings": [],
        "final_decision": dict(FINAL_DECISION),
    }


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": item.get("index"),
        "original": item.get("original", ""),
        "parsed_summary": item.get("parsed_summary", ""),
    }


def _planned_action(step: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    step_type = step.get("type")
    if step_type == "select_number":
        candidates = _number_candidates(selector_report).get(str(step.get("label")), [])
        candidate = candidates[0] if candidates else {}
        return {
            "type": "SELECT_NUMBER",
            "number": str(step.get("label")),
            "selector_found": bool(candidates),
            "selector": _best_selector(candidate),
            "frame": _frame(candidate),
            "execute": False,
        }
    if step_type == "set_amount":
        candidates = _amount_candidates(selector_report).get(str(step.get("star")), [])
        candidate = candidates[0] if candidates else {}
        return {
            "type": "SET_AMOUNT",
            "star": str(step.get("star")),
            "amount": step.get("amount"),
            "selector_found": bool(candidates),
            "selector": _best_selector(candidate),
            "frame": _frame(candidate),
            "execute": False,
        }
    return {
        "type": str(step_type or "UNKNOWN").upper(),
        "selector_found": False,
        "selector": "",
        "frame": "",
        "execute": False,
    }


def _missing_message(step: dict[str, Any]) -> str:
    if step.get("type") == "select_number":
        return f"number {step.get('label')} selector missing"
    if step.get("type") == "set_amount":
        return f"amount field {step.get('star')} missing"
    return f"unsupported action {step.get('type')}"


def _number_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("number_candidates") or selector_report.get("number_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _amount_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("amount_field_candidates") or selector_report.get("amount_field_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _danger_candidates(selector_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = selector_report.get("danger_candidates") or selector_report.get("danger_selectors") or []
    return candidates if isinstance(candidates, list) else []


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


def _danger_label(candidate: dict[str, Any]) -> str:
    for key in ("text", "value", "label", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    return "unknown danger candidate"
