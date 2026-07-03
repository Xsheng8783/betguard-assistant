from __future__ import annotations

from typing import Any

from betguard.webfill.fill_mapping import build_dry_run_mapping, resolve_final_selector
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
    _apply_selector_guards(actions, missing, warnings)
    unfound = any(not action["selector_found"] for action in actions)
    blocked = (
        bool(errors)
        or bool(missing)
        or unfound
        or not mapping.get("can_map_all_required_fields")
    )
    status = "BLOCKED" if blocked else "SAFE"

    return {
        "mode": "assisted_fill_dry_run_mapping_report",
        "status": status,
        "market": _market_summary(selector_report),
        "actions": actions,
        "actions_summary": [_action_summary(action) for action in actions],
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
    step = item.get("plan_step", {})
    if candidates:
        selector, unique = resolve_final_selector(first, step)
    else:
        selector, unique = "", False
    return {
        "plan_step": step,
        "selector_found": bool(candidates),
        "selector": selector,
        "frame": first.get("frame_name") or first.get("frame_url") or "",
        "confidence": _confidence_for(first, step, selector, unique) if candidates else "blocked",
        "unique_selector": unique,
        "selector_unsafe": False,
        "rejected_candidate_count": int(item.get("rejected_candidate_count", 0) or 0),
        "selector_candidates": candidates,
    }


def _apply_selector_guards(
    actions: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    """Block actions whose final selector cannot be proven unique.

    Two independent checks, both read-only:
    1. Per-action specificity: a select_number/set_amount whose final selector is
       only a generic class (e.g. ``td.selectline2``/``input.BDAll``) is unsafe.
    2. Cross-action uniqueness: if two different numbers/stars resolve to the
       exact same selector, none of them can be trusted.
    """

    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") not in {"select_number", "set_amount"}:
            continue
        if not action.get("selector_found"):
            continue
        if action.get("unique_selector"):
            continue
        _mark_action_unsafe(action, missing, warnings, reason="not-specific")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") not in {"select_number", "set_amount"}:
            continue
        if not action.get("selector_found"):
            continue
        selector = str(action.get("selector") or "")
        if not selector:
            continue
        grouped.setdefault(selector, []).append(action)

    for selector, group in grouped.items():
        targets = sorted({str(_action_target(action)) for action in group})
        if len(targets) <= 1:
            continue
        for action in group:
            _mark_action_unsafe(action, missing, warnings, reason="shared")
        _append_once(
            warnings,
            f"selector '{selector}' maps to multiple targets ({', '.join(targets)}); not unique",
        )


def _mark_action_unsafe(
    action: dict[str, Any],
    missing: list[dict[str, Any]],
    warnings: list[str],
    *,
    reason: str,
) -> None:
    action["confidence"] = "low"
    action["selector_unsafe"] = True
    step = action.get("plan_step", {})
    selector = action.get("selector") or ""
    if step.get("type") == "select_number":
        label = step.get("label")
        if reason == "shared":
            detail = "selector shared by multiple numbers"
            warning = f"number {label} selector '{selector}' is shared by another number; not unique"
        else:
            detail = "selector not label-specific"
            warning = (
                f"number {label} selector '{selector}' is not unique to {label}; "
                "prefer exact text such as text=" + str(label)
            )
        _append_missing(missing, {"type": "number", "label": label, "reason": detail})
    else:
        star = step.get("star")
        if reason == "shared":
            detail = "selector shared by multiple amount fields"
            warning = f"amount {star} selector '{selector}' is shared by another field; not unique"
        else:
            detail = "selector not star-specific"
            warning = f"amount {star} selector '{selector}' is not proven unique; prefer id/name"
        _append_missing(missing, {"type": "amount", "star": star, "reason": detail})
    _append_once(warnings, warning)


def _action_target(action: dict[str, Any]) -> Any:
    step = action.get("plan_step", {})
    if step.get("type") == "set_amount":
        return step.get("star")
    return step.get("label")


def _append_missing(missing: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if item not in missing:
        missing.append(item)


def _confidence_for(
    candidate: dict[str, Any],
    step: dict[str, Any],
    selector: str,
    unique: bool,
) -> str:
    if step.get("type") in {"select_number", "set_amount"}:
        if not selector:
            return "low"
        return "high" if unique else "low"
    return _confidence(candidate)


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    step = action.get("plan_step", {})
    if step.get("type") == "set_amount":
        label = f"{step.get('star')} {step.get('amount')}".strip()
    else:
        label = str(step.get("label", ""))
    return {
        "type": step.get("type"),
        "label": label,
        "selector_found": action.get("selector_found"),
        "selector": action.get("selector") or "",
        "frame": action.get("frame") or "",
        "confidence": action.get("confidence") or "blocked",
        "rejected_candidate_count": action.get("rejected_candidate_count", 0),
    }


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
    reason = item.get("reason")
    if item.get("type") == "number":
        if reason:
            return f"number {item.get('label')}: {reason}"
        return f"number {item.get('label')} selector missing"
    if item.get("type") == "amount":
        if reason:
            return f"amount field {item.get('star')}: {reason}"
        return f"amount field {item.get('star')} missing"
    if item.get("type") == "danger":
        return "danger buttons not verified"
    return str(item)


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
