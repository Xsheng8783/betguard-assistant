"""Manual Amount Mapping Override v1 (mapping/report layer only).

Reporting-only. This module never clicks, fills, submits, or confirms. It only
lets a human pre-declare a *context-verified* amount selector for a star when
automatic mapping cannot prove a unique per-star field (e.g. Tiantianle, where
二星/三星 both collapse onto the shared ``#GroupSet_Value``).

Safety properties enforced here:

- An override is never a naked "trust this selector". It only applies when the
  requested selector exists among the discovered candidates AND the candidate's
  diagnostic context matches the required patterns.
- Overrides only apply to amount ``input``/``select``/``textarea`` candidates.
  Buttons, anchors and any other tag are refused (danger buttons can never be
  overridden).
- An override can never point at a selector that automatic mapping already proved
  is shared across stars (so ``#GroupSet_Value`` can never be forced safe).
- This module mutates only the *action* dicts passed in. It never mutates the
  ``selector_report`` / ``site_profile`` candidate dicts.
- ``_apply_selector_guards`` still runs afterwards, so if two stars still resolve
  to the same selector the result stays BLOCKED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_AMOUNT_TAGS = {"input", "select", "textarea"}

# overrides/manual_amount_mapping_tiantianle.json at the repo root.
DEFAULT_OVERRIDE_PATH = (
    Path(__file__).resolve().parents[3] / "overrides" / "manual_amount_mapping_tiantianle.json"
)

# require-key -> diagnostic-context field it must be a substring of.
_REQUIRE_CONTEXT_FIELDS = {
    "parentText_contains": "parentText",
    "grandparentText_contains": "grandparentText",
    "outerHTML_contains": "outerHTML",
    "id_contains": "id",
    "name_contains": "name",
    "className_contains": "className",
    "text_contains": "text",
}


def load_manual_amount_overrides(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load override rules. A missing file returns ``[]`` (current behavior)."""

    override_path = Path(path) if path is not None else DEFAULT_OVERRIDE_PATH
    if not override_path.exists():
        return []
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        return []
    return [rule for rule in rules if isinstance(rule, dict)]


def apply_manual_amount_overrides(
    actions: list[dict[str, Any]],
    *,
    overrides: list[dict[str, Any]] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply context-verified overrides to set_amount actions in place.

    ``overrides`` takes precedence; when ``None`` the default override file is
    loaded. Returns provenance: ``applied`` stars, ``rejected`` reasons, and the
    resulting ``source`` (``manual_override`` if anything applied).
    """

    rules = overrides if overrides is not None else load_manual_amount_overrides(path)
    result: dict[str, Any] = {"applied": [], "rejected": [], "source": "automatic_mapping"}
    if not rules:
        return result

    rules_by_star: dict[str, dict[str, Any]] = {}
    for rule in rules:
        star = str(rule.get("star") or "")
        if star and star not in rules_by_star:
            rules_by_star[star] = rule

    auto_shared = _auto_shared_amount_selectors(actions)

    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") != "set_amount":
            continue
        star = str(step.get("star") or "")
        rule = rules_by_star.get(star)
        if rule is None:
            continue
        ok, reason, candidate = _verify_override(rule, action, auto_shared)
        if not ok:
            result["rejected"].append({"star": star, "reason": reason})
            continue
        _apply_override_to_action(action, rule)
        result["applied"].append(star)

    if result["applied"]:
        result["source"] = "manual_override"
    return result


def _auto_shared_amount_selectors(actions: list[dict[str, Any]]) -> set[str]:
    """Selectors that automatic mapping already resolves for more than one star."""

    by_selector: dict[str, set[str]] = {}
    for action in actions:
        step = action.get("plan_step", {})
        if step.get("type") != "set_amount" or not action.get("selector_found"):
            continue
        selector = str(action.get("selector") or "")
        if selector:
            by_selector.setdefault(selector, set()).add(str(step.get("star") or ""))
    return {selector for selector, stars in by_selector.items() if len(stars) > 1}


def _verify_override(
    rule: dict[str, Any],
    action: dict[str, Any],
    auto_shared: set[str],
) -> tuple[bool, str, dict[str, Any] | None]:
    selector = str(rule.get("selector_candidate") or "")
    if not selector:
        return False, "override missing selector_candidate", None
    if selector in auto_shared:
        return False, f"selector {selector} is shared across stars; cannot override", None

    candidate = _find_candidate_for_selector(action, selector)
    if candidate is None:
        return False, f"selector {selector} not found among discovered candidates", None

    tag = str(candidate.get("tag") or "").lower()
    if tag not in ALLOWED_AMOUNT_TAGS:
        return False, f"override target tag '{tag}' is not an amount input/select", None

    context = _candidate_context(candidate)
    ok, reason = _context_matches(rule.get("require") or {}, context)
    if not ok:
        return False, reason, None

    expected_index = rule.get("expected_index")
    if expected_index is not None:
        if not rule.get("index_is_manual_confirmed"):
            return False, "expected_index requires index_is_manual_confirmed=true", None
        if context.get("source_index") != expected_index:
            return (
                False,
                f"expected_index {expected_index} does not match source_index "
                f"{context.get('source_index')}",
                None,
            )

    return True, "", candidate


def _find_candidate_for_selector(
    action: dict[str, Any], selector: str
) -> dict[str, Any] | None:
    for candidate in action.get("selector_candidates") or []:
        selectors = candidate.get("candidate_selectors")
        if isinstance(selectors, list) and selector in [str(item) for item in selectors]:
            return candidate
    return None


def _candidate_context(candidate: dict[str, Any]) -> dict[str, Any]:
    diagnostic = candidate.get("amount_diagnostic")
    context: dict[str, Any] = dict(diagnostic) if isinstance(diagnostic, dict) else {}
    for key in (
        "id",
        "name",
        "className",
        "parentText",
        "grandparentText",
        "text",
        "value",
        "outerHTML",
        "source_index",
    ):
        if context.get(key) in (None, "") and candidate.get(key) not in (None, ""):
            context[key] = candidate.get(key)
    return context


def _context_matches(require: dict[str, Any], context: dict[str, Any]) -> tuple[bool, str]:
    if not require:
        return False, "override require block is empty; naked selector trust not allowed"
    matched_any = False
    for require_key, context_field in _REQUIRE_CONTEXT_FIELDS.items():
        needle = require.get(require_key)
        if needle in (None, ""):
            continue
        haystack = str(context.get(context_field) or "")
        if str(needle) not in haystack:
            return False, f"context {context_field} does not contain required pattern"
        matched_any = True
    if not matched_any:
        return False, "override require block has no recognized context pattern"
    return True, ""


def _apply_override_to_action(action: dict[str, Any], rule: dict[str, Any]) -> None:
    """Mutate only the action dict; never the underlying candidate/report."""

    selector = str(rule.get("selector_candidate") or "")
    action["selector"] = selector
    action["selector_found"] = True
    action["unique_selector"] = True
    action["selector_unsafe"] = False
    action["confidence"] = "manual_verified"
    action["source"] = "manual_override"
    action["manual_override"] = {
        "star": str(rule.get("star") or ""),
        "selector": selector,
        "require": dict(rule.get("require") or {}),
        "expected_index": rule.get("expected_index"),
        "index_is_manual_confirmed": bool(rule.get("index_is_manual_confirmed")),
        "confirmed_by": rule.get("confirmed_by"),
        "confirmed_at": rule.get("confirmed_at"),
    }
