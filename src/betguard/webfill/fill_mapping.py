from __future__ import annotations

from typing import Any

from betguard.webfill.fill_plan import FORBIDDEN_STEPS


def build_dry_run_mapping(fill_plan: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    mapping = _base_mapping()

    if fill_plan.get("executable") is not False:
        mapping["errors"].append("fill plan executable must be false in v0")
        mapping["can_map_all_required_fields"] = False
        return mapping

    if fill_plan.get("errors"):
        mapping["errors"].extend(fill_plan.get("errors", []))
        mapping["can_map_all_required_fields"] = False
        return mapping

    market_state = selector_report.get("market_state")
    if isinstance(market_state, dict) and market_state.get("can_probe_bet_page") is False:
        mapping["errors"].append("bet page unavailable; cannot map selectors")
        mapping["can_map_all_required_fields"] = False
        return mapping

    if not selector_report.get("danger_candidates"):
        mapping["warnings"].append("danger candidates not found; assisted fill must remain disabled")

    steps = list(fill_plan.get("planned_steps", []))
    for step in steps:
        mapped = _map_step(step, selector_report)
        mapping["mapped_steps"].append(mapped)
        if not mapped["selector_candidates"]:
            missing = _missing_for_step(step)
            if missing:
                mapping["missing"].append(missing)

    mapping["can_map_all_required_fields"] = not mapping["missing"] and not mapping["errors"]
    return mapping


def _base_mapping() -> dict[str, Any]:
    return {
        "mode": "assisted_fill_dry_run_mapping_v0",
        "executable": False,
        "requires_human_review": True,
        "can_map_all_required_fields": False,
        "mapped_steps": [],
        "missing": [],
        "forbidden_steps": list(FORBIDDEN_STEPS),
        "warnings": [],
        "errors": [],
    }


def _map_step(step: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    step_type = step.get("type")
    if step_type == "select_number":
        candidates = _number_candidates(selector_report).get(str(step.get("label")), [])
    elif step_type == "set_amount":
        candidates = _amount_candidates(selector_report).get(str(step.get("star")), [])
    else:
        candidates = []
    return {
        "plan_step": step,
        "selector_candidates": candidates,
    }


def _missing_for_step(step: dict[str, Any]) -> dict[str, Any] | None:
    step_type = step.get("type")
    if step_type == "select_number":
        return {"type": "number", "label": step.get("label")}
    if step_type == "set_amount":
        return {"type": "amount", "star": step.get("star")}
    return None


def _number_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("number_candidates") or selector_report.get("number_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _amount_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("amount_field_candidates") or selector_report.get("amount_field_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}
