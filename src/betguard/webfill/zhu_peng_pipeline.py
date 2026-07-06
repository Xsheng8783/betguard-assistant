"""ZhuPeng pipeline — integrate zhu_peng_fill into safety flow.

Adds ZhuPeng-aware preflight, fill execution, and guard checks.
Never auto-submits, confirms, or advances to next item.
"""

from __future__ import annotations

from typing import Any

from betguard.webfill.zhu_peng_fill import (
    build_zhu_peng_plan,
    execute_zhu_peng_plan,
    verify_zhu_peng_result,
)


# ── Item identification ─────────────────────────────────────────────────

def is_zhu_peng_item(item: dict[str, Any]) -> bool:
    """Return True if *item* is a ZhuPeng column bet."""
    bet_type = (item.get("bet_type") or item.get("type") or "").lower()
    if bet_type == "column":
        return True
    if bet_type == "zhu_peng":
        return True
    # Detect from structure: has 'columns' list
    if item.get("columns"):
        return True
    return False


def zhu_peng_columns_from_item(item: dict[str, Any]) -> list[list[int]]:
    """Extract column number lists from a ZhuPeng item.

    Supports multiple formats:
      - item['columns']: list of dict with 'numbers' key
      - item['columns']: list of list of int
      - item['numbers']: list of list of int (plan format)
    """
    columns = item.get("columns") or item.get("numbers")
    if not columns:
        return []
    result: list[list[int]] = []
    for col in columns:
        if isinstance(col, dict):
            nums = col.get("numbers", [])
            result.append([int(n) for n in nums])
        elif isinstance(col, list):
            result.append([int(n) for n in col])
        else:
            result.append([int(col)])
    return result


def zhu_peng_star_map(item: dict[str, Any]) -> dict[str, str]:
    """Map star numbers to labels used in PengBet inputs.

    Default: {2: '二星', 3: '三星', 4: '四星'}.
    Item can override with 'star_map' or 'stars'.
    """
    if item.get("star_map"):
        return {int(k): v for k, v in item["star_map"].items()}
    stars = item.get("stars") or [2, 3, 4]
    default = {2: "二星", 3: "三星", 4: "四星"}
    return {s: default.get(s, f"star_{s}") for s in stars}


# ── Preflight ───────────────────────────────────────────────────────────

def zhu_peng_preflight(item: dict[str, Any]) -> dict[str, Any]:
    """Run ZhuPeng safety preflight on an item.

    Returns a report dict with status and any errors.
    Does NOT open a browser — only validates item structure.
    """
    errors: list[str] = []
    missing: list[str] = []

    # Must be approved
    if not item.get("accepted_by_human"):
        errors.append("Item not accepted by human — must pass review/accepted-valid")
    status = item.get("status", "")
    if status in ("NEEDS_REVIEW", "INVALID", "WATCHLIST"):
        errors.append(f"Item status '{status}' is blocked from fill flow")

    # Must have columns
    columns = zhu_peng_columns_from_item(item)
    if not columns:
        errors.append("No columns found in item")
        missing.append("columns")
    elif any(not col for col in columns):
        errors.append("Empty column in columns list")
        missing.append("columns_non_empty")
    elif any(any(not isinstance(n, int) or n < 1 or n > 99 for n in col) for col in columns):
        errors.append("Column numbers out of valid range (1-99)")
        missing.append("numbers_in_range")

    # Amount check
    amounts = item.get("amounts") or item.get("amount_per_star") or {}
    star_map = zhu_peng_star_map(item)
    for star in star_map:
        if star not in amounts:
            errors.append(f"Missing amount for star {star}")
            missing.append(f"amount_star_{star}")

    return {
        "status": "READY_FOR_HUMAN_REVIEW" if not errors else "BLOCKED",
        "errors": errors,
        "missing": missing,
        "columns": columns,
        "amounts": amounts,
        "star_map": star_map,
        "item": item,
    }


# ── Fill execution ──────────────────────────────────────────────────────

def zhu_peng_fill_execute(
    page: Any,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Execute a ZhuPeng fill on a live page.

    Runs the full flow: identify columns, fill numbers, fill amounts,
    readback and verify. Never submits.
    """
    columns = zhu_peng_columns_from_item(item)
    amounts_raw = item.get("amounts") or item.get("amount_per_star") or {}
    amounts = {str(k): int(v) for k, v in amounts_raw.items()}

    # Convert star numbers to labels if needed
    if any(isinstance(k, int) or k.isdigit() for k in amounts):
        star_map = zhu_peng_star_map(item)
        amounts = {star_map.get(int(k), str(k)): int(v) for k, v in amounts_raw.items()}

    plan = build_zhu_peng_plan({"numbers": columns, "amounts": amounts})
    report = execute_zhu_peng_plan(page, plan)

    # Add human-confirmation prompt
    report["next_step"] = "Human must inspect page, manually submit/confirm, then mark DONE"
    report["auto_submit"] = False
    report["auto_confirm"] = False
    report["auto_next"] = False

    return report


# ── Safety ──────────────────────────────────────────────────────────────

ZHU_PENG_SAFETY_GUARDS = {
    "auto_submit": False,
    "auto_confirm": False,
    "auto_next": False,
    "allow_needs_review": False,
    "allow_invalid": False,
    "allow_watchlist": False,
    "require_accepted_by_human": True,
    "forbidden_selectors": [
        "#GroupSet_Value",
        "input[id^='ta_']",
        "input[id^='tb_']",
        "[data-bind*='OnChkNO']",
        "[data-bind*='OnChkBet']",
    ],
}

SAFETY_GUARD_DOC = """
ZhuPeng safety rules:
  - Never auto-submit or auto-confirm
  - Fill only from approved_fill_queue
  - Needs Review / Invalid / Watchlist items are BLOCKED
  - Numbers: el.click() on visible TD elements only
  - Amounts: visible PengBet.Value inputs only
  - Never use tb_X, ta_X_Y, OnChkNO, OnChkBet, Mo.OnSwitchSel, hidden inputs
  - Every step readback-verified; mismatch → BLOCKED
"""
