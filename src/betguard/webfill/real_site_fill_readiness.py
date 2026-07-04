"""Live Assisted Fill Readiness Checklist v1.

A local-only, read-only report that answers one question: is this one
approved_fill_queue item ready for a human to begin assisted fill? It is not
a replacement for ``real_site_fill_preflight`` -- it is a go/no-go summary
that flattens the preflight gates, the execution-action guard, and the
queue's one-item-at-a-time/no-auto-next state into a single PASS/BLOCKED
checklist, so an operator can see readiness at a glance before ever
acknowledging the real-site fill risk.

This module never opens a browser, never imports Playwright, and never calls
``execute_actions_on_page`` / ``run_real_site_assisted_fill``. It only reads
already-computed data from ``real_site_fill_preflight`` and the queue's own
item statuses.
"""

from __future__ import annotations

from typing import Any

from betguard.webfill.batch_queue import CURRENT, DONE, WAITING_FOR_HUMAN_CONFIRM
from betguard.webfill.real_site_fill_preflight import (
    READY_FOR_HUMAN_REVIEW,
    build_execution_actions_from_preflight,
    build_real_site_fill_preflight_report,
)


MODE = "real_site_assisted_fill_readiness_checklist_v1"
PASS = "PASS"
BLOCKED = "BLOCKED"

ALLOWED_ACTION_TYPES = {"SELECT_NUMBER", "SET_AMOUNT"}

CHECKLIST_KEYS = (
    "approved_fill_queue_present",
    "item_found",
    "human_accepted",
    "review_state_excluded",
    "profile_supplied",
    "profile_validation_ok",
    "preflight_status_ready",
    "execution_actions_buildable",
    "action_types_allowed_only",
    "groupset_value_excluded",
    "amounts_position_verified",
    "no_forbidden_steps",
    "final_flags_safe",
    "one_item_at_a_time",
    "no_auto_next_evidence",
)

FINAL_DECISION = {
    "real_site_execute": False,
    "auto_submit": False,
    "executable": False,
    "human_required_each_item": True,
    "reason": "readiness checklist only; no real website operation",
}


def build_readiness_checklist(
    queue: dict[str, Any],
    profile: dict[str, Any] | None,
    *,
    item_index: int,
) -> dict[str, Any]:
    """Pure, read-only readiness checklist for one approved_fill_queue item.

    Reuses ``build_real_site_fill_preflight_report`` and
    ``build_execution_actions_from_preflight`` as the single source of truth
    for every check that those functions already compute -- this never
    recomputes selector/mapping/GroupSet_Value/position_verified logic on its
    own. It only adds the two checks neither of those functions answers:
    one-item-at-a-time and no-auto-next-evidence, read straight from the
    queue's own item statuses. Never mutates ``queue``/``profile``.
    """
    report = _base_report(item_index)
    checklist = report["checklist"]

    checklist["profile_supplied"] = isinstance(profile, dict) and bool(profile)
    effective_profile = profile if isinstance(profile, dict) else {}

    v1_report = build_real_site_fill_preflight_report(queue, effective_profile, item_index=item_index)
    gates = v1_report.get("gates") or {}
    report["item"] = v1_report.get("item") or report["item"]

    checklist["approved_fill_queue_present"] = bool(gates.get("source_is_approved_fill_queue"))
    checklist["item_found"] = "original_text" in (v1_report.get("item") or {})
    checklist["human_accepted"] = bool(gates.get("human_accepted"))
    checklist["review_state_excluded"] = bool(gates.get("review_state_excluded"))
    checklist["profile_validation_ok"] = bool(gates.get("profile_validation_ok"))
    checklist["preflight_status_ready"] = v1_report.get("status") == READY_FOR_HUMAN_REVIEW

    converter_error: str | None = None
    actions: list[dict[str, Any]] = []
    if checklist["profile_supplied"]:
        try:
            actions = build_execution_actions_from_preflight(v1_report)
            checklist["execution_actions_buildable"] = True
        except ValueError as exc:
            converter_error = str(exc)
            checklist["execution_actions_buildable"] = False
    else:
        checklist["execution_actions_buildable"] = False

    checklist["action_types_allowed_only"] = checklist["execution_actions_buildable"] and all(
        action.get("type") in ALLOWED_ACTION_TYPES for action in actions
    )
    checklist["groupset_value_excluded"] = _refine_with_converter_error(
        bool(gates.get("groupset_value_excluded")), converter_error, "GroupSet_Value"
    )
    checklist["amounts_position_verified"] = _refine_with_converter_error(
        bool(gates.get("amounts_position_verified")), converter_error, "position_verified"
    )
    checklist["no_forbidden_steps"] = _refine_with_converter_error(
        bool(gates.get("no_forbidden_steps_in_plan")), converter_error, "forbidden step"
    )

    v1_final_decision = v1_report.get("final_decision") or {}
    checklist["final_flags_safe"] = (
        v1_final_decision.get("real_site_execute") is False
        and v1_final_decision.get("auto_submit") is False
        and v1_final_decision.get("executable") is False
        and v1_final_decision.get("human_required_each_item") is True
    )

    items = list(queue.get("items") or [])
    checklist["one_item_at_a_time"] = _one_item_active(items)
    checklist["no_auto_next_evidence"] = _no_auto_next_evidence(items)

    report["execution_action_summary"] = [
        {"type": action.get("type"), "number_or_star": action.get("number") or action.get("star")}
        for action in actions
    ]

    report["missing"] = list(v1_report.get("missing", []))
    report["errors"].extend(str(error) for error in v1_report.get("errors", []))
    if not checklist["profile_supplied"]:
        report["errors"].append("profile not supplied; cannot run preflight")
    if converter_error:
        report["errors"].append(converter_error)
    if not checklist["one_item_at_a_time"]:
        report["errors"].append("more than one item is CURRENT/WAITING_FOR_HUMAN_CONFIRM at the same time")
    if not checklist["no_auto_next_evidence"]:
        report["errors"].append("queue shows evidence of a later item completing before this one (auto-next)")

    report["status"] = PASS if all(checklist.values()) else BLOCKED
    return report


def format_pretty_readiness_checklist(report: dict[str, Any]) -> str:
    item = report.get("item") or {}
    lines = [
        "Live Assisted Fill Readiness Checklist",
        f"Status: {report.get('status', BLOCKED)}",
        "",
        "Item:",
        f"[{item.get('index', '')}] {item.get('original_text', '')}".rstrip(),
        str(item.get("parsed_summary", "")),
        "",
        "Checklist:",
    ]
    checklist = report.get("checklist") or {}
    for key in CHECKLIST_KEYS:
        lines.append(f"- {key}: {str(checklist.get(key)).lower()}")

    if report.get("execution_action_summary"):
        lines.append("")
        lines.append("Execution Actions:")
        for action in report["execution_action_summary"]:
            lines.append(f"- {action.get('type')} {action.get('number_or_star')}")

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


def format_concise_readiness_checklist(report: dict[str, Any]) -> str:
    """Short summary: status, 15-item checklist, errors, final decision.

    Never prints raw selectors, outerHTML, candidate lists, or page text --
    ``execution_action_summary`` only carries action type + number/star.
    """
    item = report.get("item") or {}
    lines = [
        "Live Assisted Fill Readiness Checklist (concise)",
        f"Status: {report.get('status', BLOCKED)}",
        "",
        f"Item: [{item.get('index', '')}] {item.get('parsed_summary', '')}".rstrip(),
        "",
        "Checklist:",
    ]
    checklist = report.get("checklist") or {}
    for key in CHECKLIST_KEYS:
        lines.append(f"- {key}: {str(checklist.get(key)).lower()}")

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


def _refine_with_converter_error(gate_value: bool, converter_error: str | None, keyword: str) -> bool:
    """AND a preflight gate with an independent re-check from the converter.

    If the execution-action converter independently failed for this exact
    reason, the checklist item is False even if the upstream preflight gate
    said True -- matching the defense-in-depth pattern used everywhere else
    in this codebase (never trust a single layer for GroupSet_Value/
    position_verified/forbidden-step safety).
    """
    if converter_error and keyword in converter_error:
        return False
    return gate_value


def _one_item_active(items: list[dict[str, Any]]) -> bool:
    active = [item for item in items if item.get("status") in {CURRENT, WAITING_FOR_HUMAN_CONFIRM}]
    return len(active) <= 1


def _no_auto_next_evidence(items: list[dict[str, Any]]) -> bool:
    """True unless a later item completed (DONE) before an earlier one did.

    Items are expected in ascending index order (as build_batch_queue /
    build_batch_mock_queue produce them). Once a non-DONE item is seen, no
    subsequent item may already be DONE -- that would mean the queue advanced
    past an unconfirmed item, i.e. auto-next.
    """
    seen_incomplete = False
    for item in items:
        if item.get("status") == DONE:
            if seen_incomplete:
                return False
        else:
            seen_incomplete = True
    return True


def _base_report(item_index: int) -> dict[str, Any]:
    return {
        "mode": MODE,
        "status": BLOCKED,
        "item": {"index": item_index},
        "checklist": {key: False for key in CHECKLIST_KEYS},
        "execution_action_summary": [],
        "missing": [],
        "errors": [],
        "final_decision": dict(FINAL_DECISION),
    }
