from __future__ import annotations

from typing import Any

from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_plan import FORBIDDEN_STEPS


def build_mapping_report(
    fill_plan: dict[str, Any],
    selector_report: dict[str, Any],
    mapping_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mapping = mapping_result or build_dry_run_mapping(fill_plan, selector_report)
    danger_candidates = _danger_candidates(selector_report)
    danger_verified = bool(danger_candidates)
    missing = list(mapping.get("missing", []))
    warnings = list(mapping.get("warnings", []))
    errors = list(mapping.get("errors", []))

    if not danger_verified:
        _append_once(warnings, "danger buttons not verified; cannot proceed beyond dry-run")
        if not any(item.get("type") == "danger" for item in missing):
            missing.append({"type": "danger", "reason": "danger buttons not verified"})

    actions = [_action_from_mapped_step(item) for item in mapping.get("mapped_steps", [])]
    blocked = bool(errors) or bool(missing) or not mapping.get("can_map_all_required_fields")
    status = "BLOCKED" if blocked else "SAFE"

    return {
        "mode": "assisted_fill_dry_run_mapping_report",
        "status": status,
        "market": _market_summary(selector_report),
        "actions": actions,
        "missing": missing,
        "danger_check": {
            "danger_candidates_found": danger_verified,
            "dangerous_buttons_detected": [_danger_label(item) for item in danger_candidates],
        },
        "final_decision": {
            "executable": False,
            "reason": "dry-run only; human review required",
        },
        "forbidden_steps": list(FORBIDDEN_STEPS),
        "warnings": warnings,
        "errors": errors,
    }


def format_pretty_mapping_report(report: dict[str, Any]) -> str:
    lines = [
        "Assisted Fill Dry-run Mapping",
        f"Status: {report.get('status', 'BLOCKED')}",
        "",
        "Market:",
    ]
    market = report.get("market", {})
    lines.append(f"- can_probe_bet_page: {str(market.get('can_probe_bet_page')).lower()}")
    lines.append(f"- current_game: {market.get('current_game') or ''}")
    lines.append(f"- route: {market.get('route') or ''}")
    lines.append("")
    lines.append("Actions:")

    for index, action in enumerate(report.get("actions", []), start=1):
        step = action.get("plan_step", {})
        step_type = str(step.get("type", "")).upper()
        if step.get("type") == "set_amount":
            label = f"{step.get('star')} {step.get('amount')}"
        else:
            label = str(step.get("label", ""))
        lines.append(f"[{index}] {step_type} {label}".rstrip())
        lines.append(f"    selector_found: {str(action.get('selector_found')).lower()}")
        lines.append(f"    selector: {action.get('selector') or ''}")
        lines.append(f"    frame: {action.get('frame') or ''}")
        lines.append(f"    confidence: {action.get('confidence') or 'low'}")

    lines.append("")
    lines.append("Danger Check:")
    danger_check = report.get("danger_check", {})
    lines.append(f"- danger_candidates_found: {str(danger_check.get('danger_candidates_found')).lower()}")
    lines.append("- dangerous buttons detected:")
    for label in danger_check.get("dangerous_buttons_detected", []):
        lines.append(f"  - {label}")

    if report.get("missing"):
        lines.append("")
        lines.append("Missing:")
        for item in report["missing"]:
            lines.append(f"- {_format_missing(item)}")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    if report.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")

    final_decision = report.get("final_decision", {})
    lines.append("")
    lines.append("Final Decision:")
    lines.append(f"- executable: {str(final_decision.get('executable')).lower()}")
    lines.append(f"- reason: {final_decision.get('reason')}")
    return "\n".join(lines)


def _action_from_mapped_step(item: dict[str, Any]) -> dict[str, Any]:
    candidates = list(item.get("selector_candidates") or [])
    first = candidates[0] if candidates else {}
    return {
        "plan_step": item.get("plan_step", {}),
        "selector_found": bool(candidates),
        "selector": _best_selector(first),
        "frame": first.get("frame_name") or first.get("frame_url") or "",
        "confidence": _confidence(first),
        "selector_candidates": candidates,
    }


def _best_selector(candidate: dict[str, Any]) -> str:
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    for key in ("text", "value", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    return ""


def _confidence(candidate: dict[str, Any]) -> str:
    if not candidate:
        return "low"
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return "high"
    if any(candidate.get(key) for key in ("id", "name", "text", "value")):
        return "medium"
    return "low"


def _danger_candidates(selector_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = selector_report.get("danger_candidates") or selector_report.get("danger_selectors") or []
    return candidates if isinstance(candidates, list) else []


def _danger_label(candidate: dict[str, Any]) -> str:
    for key in ("text", "value", "label", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    return "unknown danger candidate"


def _market_summary(selector_report: dict[str, Any]) -> dict[str, Any]:
    market_state = selector_report.get("market_state") or {}
    return {
        "can_probe_bet_page": market_state.get("can_probe_bet_page"),
        "current_game": market_state.get("current_game_name") or selector_report.get("current_game_guess"),
        "route": market_state.get("selected_route"),
    }


def _format_missing(item: dict[str, Any]) -> str:
    if item.get("type") == "number":
        return f"number {item.get('label')} selector missing"
    if item.get("type") == "amount":
        return f"amount field {item.get('star')} missing"
    if item.get("type") == "danger":
        return "danger buttons not verified"
    return str(item)


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
