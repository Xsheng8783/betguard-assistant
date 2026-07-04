"""Local-only preflight report for a future real-site assisted fill.

This module never opens a browser, never imports Playwright, and never calls
into ``real_site_assisted_fill`` / ``execute_actions_on_page``. It only reads
already-approved local JSON (a batch queue's ``approved_fill_queue`` and a
site profile) and reports whether one specific approved item is well-formed
enough that a *future* real-site fill would be safe to even attempt.

Selector truth always comes from ``fill_mapping_report.build_mapping_report``
(the position-verified mapping used by ``--map-dry-run``) -- never from the
older first-candidate selection in ``real_site_fill_plan.py``. That older path
has no concept of "exactly three same-row PengBet.Value inputs" and no
``#GroupSet_Value`` exclusion, so reusing it here would reopen the exact
ambiguity this whole mapping effort has been closing.
"""

from __future__ import annotations

from typing import Any

from betguard.formatter import format_bet_summary
from betguard.webfill.fill_mapping_report import build_mapping_report
from betguard.webfill.fill_plan import FORBIDDEN_STEPS, build_fill_plan
from betguard.webfill.site_profile import STATUS_OK, profile_as_selector_report, validate_site_profile


MODE = "real_site_assisted_fill_preflight_v1"
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
BLOCKED = "BLOCKED"

DISALLOWED_REVIEW_STATES = {"NEEDS_REVIEW", "INVALID", "WATCHLIST"}

FINAL_DECISION = {
    "real_site_execute": False,
    "auto_submit": False,
    "executable": False,
    "human_required_each_item": True,
    "reason": "preflight only; no real website operation",
}


def contains_forbidden_steps(fill_plan: dict[str, Any]) -> list[str]:
    """Return any planned-step types that are forbidden or unrecognized.

    ``build_fill_plan`` never emits anything other than ``select_number`` /
    ``set_amount`` today; this is a defense-in-depth static scan so a future
    change to the plan builder cannot silently smuggle a submit/confirm/
    danger step past the preflight gate.
    """
    found: list[str] = []
    for step in fill_plan.get("planned_steps", []) or []:
        step_type = str(step.get("type") or "")
        if step_type in FORBIDDEN_STEPS or step_type not in {"select_number", "set_amount"}:
            found.append(step_type)
    return found


def build_real_site_fill_preflight_report(
    queue: dict[str, Any],
    profile: dict[str, Any],
    *,
    item_index: int,
) -> dict[str, Any]:
    """Pure, read-only preflight check for one approved_fill_queue item.

    Never mutates ``queue`` or ``profile``. Never calls Playwright or any
    real-site execution path. Returns a report; does not act on it.
    """
    report = _base_report(item_index)
    gates = report["gates"]

    approved = queue.get("approved_fill_queue")
    gates["source_is_approved_fill_queue"] = isinstance(approved, list) and bool(approved)
    if not gates["source_is_approved_fill_queue"]:
        report["errors"].append(
            "approved_fill_queue missing or empty; source must be approved_fill_queue only"
        )
        return report

    entry = _find_entry(approved, item_index)
    if entry is None:
        report["errors"].append(f"item index {item_index} not found in approved_fill_queue")
        return report

    review_result = entry.get("review_result") if isinstance(entry.get("review_result"), dict) else {}
    report["item"] = _item_summary(item_index, entry, review_result)

    gates["human_accepted"] = entry.get("accepted_by_human") is True
    if not gates["human_accepted"]:
        report["errors"].append("item is not accepted_by_human")
        return report

    disallowed_state = _disallowed_review_state(entry, review_result)
    gates["review_state_excluded"] = disallowed_state is None
    if disallowed_state is not None:
        report["errors"].append(f"item review state is {disallowed_state}; must not enter fill flow")
        return report

    profile_validation = validate_site_profile(profile)
    gates["profile_validation_ok"] = profile_validation.get("status") == STATUS_OK
    report["profile_validation_status"] = profile_validation.get("status")
    if not gates["profile_validation_ok"]:
        report["errors"].append("site profile validation is not OK")
        report["errors"].extend(str(error) for error in profile_validation.get("errors", []))
        return report

    if not review_result:
        report["errors"].append("approved_fill_queue entry missing review_result; cannot build fill plan")
        return report

    fill_plan = build_fill_plan(review_result)
    if fill_plan.get("errors"):
        report["errors"].extend(str(error) for error in fill_plan["errors"])
        return report

    forbidden = contains_forbidden_steps(fill_plan)
    gates["no_forbidden_steps_in_plan"] = not forbidden
    if forbidden:
        report["errors"].append(f"forbidden step(s) present in plan: {forbidden}")
        return report

    selector_report = profile_as_selector_report(profile)
    mapping_report = build_mapping_report(fill_plan, selector_report)

    gates["map_dry_run_status_safe"] = mapping_report.get("status") == "SAFE"

    actions = mapping_report.get("actions", [])
    numbers = _extract_numbers(actions)
    amounts = _extract_amounts(actions)
    report["numbers"] = numbers
    report["amounts"] = amounts

    gates["numbers_resolved_unique"] = bool(numbers) and all(
        number["confidence"] == "high" and number["unique"] for number in numbers
    )
    gates["amounts_position_verified"] = bool(amounts) and all(
        amount["position_verified"] and amount["unique"] and amount["confidence"] == "high"
        for amount in amounts
    )
    gates["groupset_value_excluded"] = _groupset_value_excluded(actions)

    danger_check = mapping_report.get("danger_check", {})
    report["danger_check"] = {
        "danger_candidates_found": bool(danger_check.get("danger_candidates_found")),
        "dangerous_buttons_detected": list(danger_check.get("dangerous_buttons_detected", [])),
        "danger_buttons_clicked": [],
    }
    gates["danger_detected_none_clicked"] = (
        report["danger_check"]["danger_candidates_found"]
        and report["danger_check"]["danger_buttons_clicked"] == []
    )

    report["missing"] = list(mapping_report.get("missing", []))
    report["errors"].extend(str(error) for error in mapping_report.get("errors", []))
    if not gates["groupset_value_excluded"]:
        report["errors"].append(
            "#GroupSet_Value must never be accepted as a safe amount selector"
        )

    if all(gates.values()):
        report["status"] = READY_FOR_HUMAN_REVIEW
    else:
        report["status"] = BLOCKED
    return report


ALLOWED_EXECUTION_ACTION_TYPES = {"SELECT_NUMBER", "SET_AMOUNT"}


def build_execution_actions_from_preflight(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure, read-only converter: preflight report -> execution action objects.

    This is the only sanctioned way to turn an already-verified
    ``real_site_assisted_fill_preflight_v1`` report into concrete
    SELECT_NUMBER/SET_AMOUNT actions for a future real-site execution layer.
    It never clicks, fills, or touches a browser -- it only reshapes data the
    preflight already decided. There is no partial/best-effort mode: any
    report that is not unambiguously ready raises ``ValueError`` instead of
    returning a degraded action list.

    Each action carries the ``frame`` the selector was discovered in (e.g.
    ``"mainFrame"``), taken verbatim from the mapping candidate. The site's
    bet controls live inside a child frame, not the top-level page, so a
    frame-unaware locator lookup at execution time would silently search the
    wrong document and time out even though the selector itself is correct.
    """
    if report.get("status") != READY_FOR_HUMAN_REVIEW:
        raise ValueError(
            f"preflight report is not READY_FOR_HUMAN_REVIEW: {report.get('status')!r}"
        )

    final_decision = report.get("final_decision") or {}
    if final_decision.get("real_site_execute") is not False:
        raise ValueError("final_decision.real_site_execute must be false")
    if final_decision.get("auto_submit") is not False:
        raise ValueError("final_decision.auto_submit must be false")
    if final_decision.get("executable") is not False:
        raise ValueError("final_decision.executable must be false")
    if final_decision.get("human_required_each_item") is not True:
        raise ValueError("final_decision.human_required_each_item must be true")

    actions: list[dict[str, Any]] = []

    for number in report.get("numbers", []):
        _reject_forbidden_step_type(number)
        selector = str(number.get("selector") or "")
        if not selector:
            raise ValueError(f"number {number.get('number')} has no selector")
        _reject_groupset_value(selector)
        actions.append(
            {
                "type": "SELECT_NUMBER",
                "number": number.get("number"),
                "selector": selector,
                "frame": str(number.get("frame") or ""),
            }
        )

    for amount in report.get("amounts", []):
        _reject_forbidden_step_type(amount)
        selector = str(amount.get("selector") or "")
        if not selector:
            raise ValueError(f"amount {amount.get('star')} has no selector")
        _reject_groupset_value(selector)
        if amount.get("position_verified") is not True:
            raise ValueError(f"amount {amount.get('star')} is not position_verified")
        actions.append(
            {
                "type": "SET_AMOUNT",
                "star": amount.get("star"),
                "amount": amount.get("amount"),
                "selector": selector,
                "frame": str(amount.get("frame") or ""),
            }
        )

    for action in actions:
        if action["type"] not in ALLOWED_EXECUTION_ACTION_TYPES:
            raise ValueError(f"unsupported execution action type: {action['type']}")

    return actions


def _reject_groupset_value(selector: str) -> None:
    if "GroupSet_Value" in selector:
        raise ValueError(
            f"selector '{selector}' targets GroupSet_Value; refusing to build execution action"
        )


def _reject_forbidden_step_type(item: dict[str, Any]) -> None:
    step_type = str(item.get("type") or "")
    if step_type in FORBIDDEN_STEPS:
        raise ValueError(f"forbidden step type present: {step_type}")


def format_pretty_real_site_fill_preflight(report: dict[str, Any]) -> str:
    item = report.get("item") or {}
    lines = [
        "Real-site Assisted Fill Preflight",
        f"Status: {report.get('status', BLOCKED)}",
        "",
        "Item:",
        f"[{item.get('index', '')}] {item.get('original_text', '')}".rstrip(),
        str(item.get("parsed_summary", "")),
        "",
        "Gates:",
    ]
    for name, passed in report.get("gates", {}).items():
        lines.append(f"- {name}: {str(passed).lower()}")

    if report.get("numbers"):
        lines.append("")
        lines.append("Numbers:")
        for number in report["numbers"]:
            lines.append(
                f"- {number.get('number')}: {number.get('selector')} "
                f"(confidence: {number.get('confidence')}, unique: {str(number.get('unique')).lower()})"
            )

    if report.get("amounts"):
        lines.append("")
        lines.append("Amounts:")
        for amount in report["amounts"]:
            lines.append(
                f"- {amount.get('star')}: {amount.get('selector')} "
                f"(confidence: {amount.get('confidence')}, unique: {str(amount.get('unique')).lower()}), "
                f"position_verified: {str(amount.get('position_verified')).lower()}"
            )

    danger_check = report.get("danger_check") or {}
    lines.append("")
    lines.append("Danger Check:")
    lines.append(f"- danger_candidates_found: {str(danger_check.get('danger_candidates_found')).lower()}")
    lines.append(f"- danger_buttons_clicked: {danger_check.get('danger_buttons_clicked', [])}")

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
            f"- auto_submit: {str(final_decision.get('auto_submit')).lower()}",
            f"- executable: {str(final_decision.get('executable')).lower()}",
            f"- human_required_each_item: {str(final_decision.get('human_required_each_item')).lower()}",
        ]
    )
    return "\n".join(lines)


def format_concise_real_site_fill_preflight(report: dict[str, Any]) -> str:
    """Short summary: status, gates, numbers/amounts selectors, safety, decision.

    Never prints raw page text, outerHTML, or full candidate lists -- only the
    already-decided selector/confidence/uniqueness facts, matching the
    ``--map-dry-run --concise`` convention.
    """
    item = report.get("item") or {}
    lines = [
        "Real-site Assisted Fill Preflight (concise)",
        f"Status: {report.get('status', BLOCKED)}",
        "",
        f"Item: [{item.get('index', '')}] {item.get('parsed_summary', '')}".rstrip(),
        "",
        "Gates:",
    ]
    for name, passed in report.get("gates", {}).items():
        lines.append(f"- {name}: {str(passed).lower()}")

    if report.get("numbers"):
        lines.append("")
        lines.append("Numbers:")
        for number in report["numbers"]:
            lines.append(
                f"- {number.get('number')}: confidence={number.get('confidence')}, "
                f"unique={str(number.get('unique')).lower()}"
            )

    if report.get("amounts"):
        lines.append("")
        lines.append("Amounts:")
        for amount in report["amounts"]:
            lines.append(
                f"- {amount.get('star')}: confidence={amount.get('confidence')}, "
                f"unique={str(amount.get('unique')).lower()}, "
                f"position_verified={str(amount.get('position_verified')).lower()}"
            )

    danger_check = report.get("danger_check") or {}
    lines.append("")
    lines.append("Safety:")
    lines.append(
        f"- danger_candidates_detected: {'yes' if danger_check.get('danger_candidates_found') else 'no'}"
    )
    lines.append("- danger_buttons_clicked: none")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    final_decision = report.get("final_decision", FINAL_DECISION)
    lines.append("")
    lines.append("Final Decision:")
    lines.append(f"- executable: {str(final_decision.get('executable')).lower()}")
    lines.append(f"- real_site_execute: {str(final_decision.get('real_site_execute')).lower()}")
    lines.append(f"- human_required_each_item: {str(final_decision.get('human_required_each_item')).lower()}")
    return "\n".join(lines)


def _base_report(item_index: int) -> dict[str, Any]:
    return {
        "mode": MODE,
        "status": BLOCKED,
        "item": {"index": item_index},
        "gates": {
            "source_is_approved_fill_queue": False,
            "human_accepted": False,
            "review_state_excluded": False,
            "profile_validation_ok": False,
            "no_forbidden_steps_in_plan": False,
            "map_dry_run_status_safe": False,
            "numbers_resolved_unique": False,
            "amounts_position_verified": False,
            "groupset_value_excluded": False,
            "danger_detected_none_clicked": False,
        },
        "numbers": [],
        "amounts": [],
        "danger_check": {
            "danger_candidates_found": False,
            "dangerous_buttons_detected": [],
            "danger_buttons_clicked": [],
        },
        "missing": [],
        "errors": [],
        "final_decision": dict(FINAL_DECISION),
    }


def _find_entry(approved: list[Any], item_index: int) -> dict[str, Any] | None:
    for position, entry in enumerate(approved):
        if not isinstance(entry, dict):
            continue
        entry_index = entry.get("index")
        if entry_index == item_index or (entry_index is None and position == item_index):
            return entry
    return None


def _item_summary(item_index: int, entry: dict[str, Any], review_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": item_index,
        "original_text": entry.get("original_fragment") or "",
        "parsed_summary": format_bet_summary(review_result) if review_result else "",
        "bet_type": entry.get("bet_type"),
    }


def _disallowed_review_state(entry: dict[str, Any], review_result: dict[str, Any]) -> str | None:
    candidates = {
        str(entry.get("status") or "").upper(),
        str(entry.get("review_state") or "").upper(),
        str(review_result.get("status") or "").upper(),
    }
    hit = candidates & DISALLOWED_REVIEW_STATES
    if hit:
        return sorted(hit)[0]
    status = str(review_result.get("status") or "").lower()
    if status and status != "ok":
        return "INVALID"
    return None


def _extract_numbers(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbers = []
    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") != "select_number":
            continue
        numbers.append(
            {
                "number": step.get("label"),
                "selector": action.get("selector") or "",
                "frame": action.get("frame") or "",
                "confidence": action.get("confidence") or "low",
                "unique": bool(action.get("unique_selector")),
            }
        )
    return numbers


def _extract_amounts(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    amounts = []
    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") != "set_amount":
            continue
        candidates = action.get("selector_candidates") or []
        first = candidates[0] if candidates else {}
        amounts.append(
            {
                "star": step.get("star"),
                "amount": step.get("amount"),
                "selector": action.get("selector") or "",
                "frame": action.get("frame") or "",
                "confidence": action.get("confidence") or "low",
                "unique": bool(action.get("unique_selector")),
                "position_verified": bool(first.get("position_verified")),
            }
        )
    return amounts


def _groupset_value_excluded(actions: list[dict[str, Any]]) -> bool:
    """True unless GroupSet_Value was accepted as a trusted amount selector.

    Mirrors the check already validated in
    ``fill_mapping_report.format_concise_mapping_report``: GroupSet_Value may
    still appear as a candidate that was correctly marked ``selector_unsafe``
    (BLOCKED); only an *unsafe-cleared* GroupSet_Value selector means it was
    wrongly forced SAFE.
    """
    amount_actions = [action for action in actions if action.get("plan_step", {}).get("type") == "set_amount"]
    forced_safe = any(
        "GroupSet_Value" in str(action.get("selector") or "") and not action.get("selector_unsafe")
        for action in amount_actions
    )
    return not forced_safe
