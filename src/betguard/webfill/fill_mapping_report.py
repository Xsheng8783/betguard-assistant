from __future__ import annotations

from typing import Any

from betguard.webfill.fill_mapping import (
    AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT,
    AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
    build_dry_run_mapping,
    resolve_final_selector,
)
from betguard.webfill.fill_plan import FORBIDDEN_STEPS
from betguard.webfill.manual_amount_mapping import apply_manual_amount_overrides


def build_mapping_report(
    fill_plan: dict[str, Any],
    selector_report: dict[str, Any],
    mapping_result: dict[str, Any] | None = None,
    *,
    manual_amount_overrides: list[dict[str, Any]] | None = None,
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
    # Context-verified manual overrides (if any) run before the guards so the
    # cross-action uniqueness check still validates the final selectors.
    override_result = apply_manual_amount_overrides(actions, overrides=manual_amount_overrides)
    grouped = _apply_selector_guards(actions, missing, warnings)
    amount_diagnostics = _build_amount_field_diagnostics(actions, grouped, override_result)
    unfound = any(not action["selector_found"] for action in actions)
    blocked = (
        bool(errors)
        or bool(missing)
        or unfound
        or not mapping.get("can_map_all_required_fields")
    )
    status = "BLOCKED" if blocked else "SAFE"

    # Numbers-only plans (see fill_plan.to_numbers_only_plan) never place a
    # set_amount step into planned_steps, so no amount action is ever mapped
    # or executed here. amount_manual_required is an echo of the plan's own
    # flag, never inferred from an empty amount_diagnostics list — a plan
    # that still contains a real (ambiguous) amount action must keep showing
    # its actual SAFE/BLOCKED amount_field_status, never a "skipped" label.
    amount_manual_required = bool(fill_plan.get("amount_manual_required"))
    amount_steps_removed = list(fill_plan.get("amount_steps_removed") or [])
    amount_actions_present = any(_is_amount_action(action) for action in actions)
    if amount_manual_required and not amount_actions_present:
        amount_field_status = "SKIPPED_BY_DESIGN"
    else:
        amount_field_status = amount_diagnostics["status"]

    return {
        "mode": "assisted_fill_dry_run_mapping_report",
        "status": status,
        "market": _market_summary(selector_report),
        "actions": actions,
        "actions_summary": [_action_summary(action) for action in actions],
        "amount_field_status": amount_field_status,
        "amount_field_source": amount_diagnostics["source"],
        "amount_field_overrides": {
            "applied": override_result.get("applied", []),
            "rejected": override_result.get("rejected", []),
        },
        "amount_manual_required": amount_manual_required,
        "amount_steps_removed": amount_steps_removed,
        "ambiguous_amount_fields": amount_diagnostics["ambiguous"],
        "shared_amount_selectors": amount_diagnostics["shared_selectors"],
        "amount_field_diagnostics": amount_diagnostics["diagnostics"],
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
        if action.get("source"):
            lines.append(f"    source: {action.get('source')}")

    lines.append("")
    lines.append("Danger Check:")
    danger_check = report.get("danger_check", {})
    lines.append(f"- danger_candidates_found: {str(danger_check.get('danger_candidates_found')).lower()}")
    lines.append("- dangerous buttons detected:")
    for label in danger_check.get("dangerous_buttons_detected", []):
        lines.append(f"  - {label}")

    if report.get("amount_manual_required"):
        lines.append("")
        lines.append("Amount Fields (manual entry required):")
        lines.append("- amount_manual_required: true")
        lines.append("- amount fields skipped by design; human must enter these manually")
        lines.append("- no amount fill action will be executed")
        for step in report.get("amount_steps_removed", []):
            star = step.get("star")
            amount = step.get("amount")
            lines.append(f"  - {star}: {amount} (enter manually)")

    amount_diagnostics = report.get("amount_field_diagnostics") or []
    if amount_diagnostics:
        lines.append("")
        lines.append("Amount Field Diagnostics:")
        lines.append(f"- Status: {report.get('amount_field_status', 'BLOCKED')}")
        for entry in amount_diagnostics:
            lines.append(f"- {entry.get('star')}: {entry.get('blocked_reason')}")
            if entry.get("source_index") is not None:
                lines.append(f"    source_index: {entry.get('source_index')}")
            if entry.get("parentText"):
                lines.append(f"    parentText: {entry.get('parentText')}")
            if entry.get("grandparentText"):
                lines.append(f"    grandparentText: {entry.get('grandparentText')}")
            attributes = _format_amount_attributes(entry)
            if attributes:
                lines.append(f"    attributes: {attributes}")
        for selector in report.get("shared_amount_selectors", []):
            lines.append(f"- Shared selector: {selector}")
        lines.append("- Result: amount fields are not safe for assisted fill")

    overrides = report.get("amount_field_overrides") or {}
    if overrides.get("applied") or overrides.get("rejected"):
        lines.append("")
        lines.append("Amount Field Mapping:")
        lines.append(f"- status: {report.get('amount_field_status', 'BLOCKED')}")
        lines.append(f"- source: {report.get('amount_field_source', 'automatic_mapping')}")
        for star in overrides.get("applied", []):
            lines.append(f"- manual_override applied: {star}")
        for item in overrides.get("rejected", []):
            lines.append(f"- manual_override rejected: {item.get('star')} ({item.get('reason')})")

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


AMOUNT_STAR_DISPLAY_ORDER = {"二星": 0, "三星": 1, "四星": 2}


def format_concise_mapping_report(report: dict[str, Any]) -> str:
    """A short, human-scannable summary of a mapping report.

    Never prints raw page text, outerHTML, dialog HTML, or full candidate
    dumps -- only the already-computed selector/confidence/uniqueness facts
    that decided SAFE/BLOCKED. Read-only formatting; does not recompute or
    alter any safety decision made by build_mapping_report.
    """
    lines = [
        "Assisted Fill Dry-run Mapping (concise)",
        f"Status: {report.get('status', 'BLOCKED')}",
        "",
        "Market:",
    ]
    market = report.get("market", {})
    lines.append(f"- current_game: {market.get('current_game') or ''}")
    lines.append(f"- can_probe_bet_page: {str(market.get('can_probe_bet_page')).lower()}")

    actions = report.get("actions", [])
    number_actions = [a for a in actions if a.get("plan_step", {}).get("type") == "select_number"]
    if number_actions:
        lines.append("")
        lines.append("Numbers:")
        for action in number_actions:
            label = action.get("plan_step", {}).get("label")
            lines.append(f"- {label}: {_concise_action_line(action)}")

    amount_actions = [a for a in actions if a.get("plan_step", {}).get("type") == "set_amount"]
    if amount_actions:
        lines.append("")
        lines.append("Amounts:")
        ordered = sorted(
            amount_actions,
            key=lambda a: AMOUNT_STAR_DISPLAY_ORDER.get(a.get("plan_step", {}).get("star"), 99),
        )
        for action in ordered:
            star = action.get("plan_step", {}).get("star")
            verified = str(_action_position_verified(action)).lower()
            lines.append(f"- {star}: {_concise_action_line(action)}, position_verified: {verified}")

    if report.get("amount_manual_required"):
        lines.append("")
        lines.append("Amounts (manual entry required):")
        lines.append("- amount_manual_required: true")
        for step in report.get("amount_steps_removed", []):
            lines.append(f"  - {step.get('star')}: {step.get('amount')} (enter manually)")

    # "Excluded" means GroupSet_Value was never accepted as a trusted amount
    # selector -- it may still appear as a candidate that got correctly
    # BLOCKED (selector_unsafe=True); only an *unsafe-cleared* GroupSet_Value
    # selector would mean it was wrongly forced SAFE.
    groupset_forced_safe = any(
        "GroupSet_Value" in str(action.get("selector") or "") and not action.get("selector_unsafe")
        for action in amount_actions
    )
    groupset_excluded = not groupset_forced_safe
    danger_check = report.get("danger_check", {})
    lines.append("")
    lines.append("Safety:")
    lines.append(f"- GroupSet_Value_excluded: {'yes' if groupset_excluded else 'no'}")
    lines.append(
        f"- danger_candidates_detected: {'yes' if danger_check.get('danger_candidates_found') else 'no'}"
    )
    lines.append("- danger_buttons_clicked: none")
    lines.append("- submit_clicked: false")
    lines.append("- confirm_clicked: false")

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

    final_decision = report.get("final_decision", {})
    lines.append("")
    lines.append("Final Decision:")
    lines.append(f"- executable: {str(final_decision.get('executable')).lower()}")
    lines.append(f"- reason: {final_decision.get('reason')}")
    return "\n".join(lines)


def _concise_action_line(action: dict[str, Any]) -> str:
    selector = action.get("selector") or "(none)"
    confidence = action.get("confidence") or "low"
    unique = str(action.get("unique_selector")).lower()
    return f"{selector} (confidence: {confidence}, unique: {unique})"


def _action_position_verified(action: dict[str, Any]) -> bool:
    """Peek at the single boolean flag on the underlying candidate.

    Only reads ``position_verified``; never surfaces the rest of the
    candidate (outerHTML, raw text, box) into the concise report.
    """
    candidates = action.get("selector_candidates") or []
    first = candidates[0] if candidates else {}
    return bool(first.get("position_verified"))


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
) -> dict[str, list[dict[str, Any]]]:
    """Block actions whose final selector cannot be proven unique.

    Two independent checks, both read-only:
    1. Per-action specificity: a select_number/set_amount whose final selector is
       only a generic class (e.g. ``td.selectline2``/``input.BDAll``) is unsafe.
    2. Cross-action uniqueness: if two different numbers/stars resolve to the
       exact same selector, none of them can be trusted.

    Returns the ``selector -> actions`` grouping computed for check 2 so the
    diagnostics layer reuses the exact same conflict decision (never a separate
    recomputation that could disagree with BLOCKED/SAFE).
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

    return grouped


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


def _is_amount_action(action: dict[str, Any]) -> bool:
    return action.get("plan_step", {}).get("type") == "set_amount"


def _build_amount_field_diagnostics(
    actions: list[dict[str, Any]],
    grouped: dict[str, list[dict[str, Any]]],
    override_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reporting-only diagnostics for ambiguous amount fields.

    Reuses the ``selector -> actions`` grouping from ``_apply_selector_guards``
    and the ``selector_unsafe`` flags it already set, so the diagnostics can
    never disagree with the actual BLOCKED/SAFE decision. Never mutates actions;
    never resolves selectors independently.

    When manual overrides were applied and every amount field is safe, the status
    becomes ``MANUAL_VERIFIED`` and the source ``manual_override`` — a clearer
    provenance than a bare SAFE.
    """

    amount_actions = [action for action in actions if _is_amount_action(action)]

    # selector -> stars sharing it, taken straight from the guard's grouped dict.
    selector_stars: dict[str, list[str]] = {}
    for selector, group in grouped.items():
        stars = sorted({str(_action_target(a)) for a in group if _is_amount_action(a)})
        if stars:
            selector_stars[selector] = stars

    diagnostics: list[dict[str, Any]] = []
    ambiguous: list[str] = []
    shared_selectors: list[str] = []
    for action in amount_actions:
        star = str(_action_target(action))
        selector = str(action.get("selector") or "")
        found = bool(action.get("selector_found"))
        unsafe = bool(action.get("selector_unsafe"))
        stars_for_selector = selector_stars.get(selector, [])
        shared = unsafe and len(stars_for_selector) > 1
        shared_with = [other for other in stars_for_selector if other != star] if shared else []

        if not (unsafe or not found):
            # Unique, resolvable amount field: no ambiguity diagnostic emitted.
            continue

        if unsafe and star not in ambiguous:
            ambiguous.append(star)
        if shared and selector and selector not in shared_selectors:
            shared_selectors.append(selector)
        diagnostics.append(
            _amount_diagnostic_entry(action, star, selector, found, unsafe, shared, shared_with)
        )

    applied_overrides = list((override_result or {}).get("applied") or [])
    if diagnostics:
        status = "BLOCKED"
        source = "manual_override" if applied_overrides else "automatic_mapping"
    elif applied_overrides:
        status = "MANUAL_VERIFIED"
        source = "manual_override"
    else:
        status = "SAFE"
        source = "automatic_mapping"
    return {
        "status": status,
        "source": source,
        "ambiguous": ambiguous,
        "shared_selectors": shared_selectors,
        "diagnostics": diagnostics,
    }


def _amount_diagnostic_entry(
    action: dict[str, Any],
    star: str,
    selector: str,
    found: bool,
    unsafe: bool,
    shared: bool,
    shared_with: list[str],
) -> dict[str, Any]:
    candidates = action.get("selector_candidates") or []
    candidate = candidates[0] if candidates else {}
    context = _amount_diagnostic_context(candidate)
    return {
        "star": star,
        "matched_label": star,
        "selector": selector,
        "confidence": action.get("confidence") or "low",
        "selector_found": found,
        "shared": shared,
        "shared_with": shared_with,
        "blocked_reason": _amount_blocked_reason(found, unsafe, shared, shared_with, selector),
        "diagnostic_source": context.get("diagnostic_source") or "automatic_mapping",
        "source_index": context.get("source_index"),
        "id": context.get("id") or "",
        "name": context.get("name") or "",
        "className": context.get("className") or "",
        "parentText": context.get("parentText") or "",
        "grandparentText": context.get("grandparentText") or "",
        "text": context.get("text") or "",
        "value": context.get("value") or "",
        "outerHTML": context.get("outerHTML") or "",
    }


def _amount_diagnostic_context(candidate: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic context for a candidate, always bounded.

    Prefers the structured ``amount_diagnostic`` block produced by the mapping
    layer; falls back to bounded excerpts of flat candidate fields so lightweight
    (test) candidates still surface context.
    """

    diagnostic = candidate.get("amount_diagnostic")
    context: dict[str, Any] = dict(diagnostic) if isinstance(diagnostic, dict) else {}
    excerpt_limits = {
        "id": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "name": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "className": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "parentText": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "grandparentText": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "text": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "value": AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        "outerHTML": AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT,
    }
    for key, limit in excerpt_limits.items():
        if not context.get(key) and candidate.get(key):
            context[key] = _bounded_excerpt(candidate.get(key), limit)
    if context.get("source_index") is None and candidate.get("source_index") is not None:
        context["source_index"] = candidate.get("source_index")
    return context


def _bounded_excerpt(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _amount_blocked_reason(
    found: bool,
    unsafe: bool,
    shared: bool,
    shared_with: list[str],
    selector: str,
) -> str:
    if not found:
        return "no amount selector found"
    if shared:
        others = ", ".join(shared_with) if shared_with else "another field"
        return f"selector {selector} is shared with {others}; not unique"
    if unsafe:
        return f"selector {selector} is not proven unique to this star"
    return ""


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


def _format_amount_attributes(entry: dict[str, Any]) -> str:
    parts = []
    if entry.get("id"):
        parts.append(f"id={entry['id']}")
    if entry.get("name"):
        parts.append(f"name={entry['name']}")
    if entry.get("className"):
        parts.append(f"class={entry['className']}")
    return ", ".join(parts)


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
