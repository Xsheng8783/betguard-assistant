"""Deterministic checks (M2): invalid/duplicate numbers, combination count,
scope conflicts. These run AFTER semantic parsing and decide block reasons.

Policies (confirmed with domain rules):
- Out-of-range numbers        -> blocked INVALID_NUMBER_RANGE
- Duplicate in one combination -> blocked DUPLICATE_IN_COMBINATION
- Duplicate inside one column  -> warning only (never silently dedup)
- column-bet combination count = product of column sizes; mismatch with a
  declared count -> DECLARED_COMBINATION_COUNT_MISMATCH (computed value wins,
  still needs_review)
- Same block: conflicting multipliers / overlapping scopes / mixed games
  -> fail-closed SCOPE_CONFLICT / MULTIPLIER_CONFLICT
- A PARSE_ERROR row must never become executable (checked by the replay test)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from betguard.models import ParsedBet
from betguard.semantic_parser import _max_number

INVALID_NUMBER_RANGE = "INVALID_NUMBER_RANGE"
DUPLICATE_IN_COMBINATION = "DUPLICATE_IN_COMBINATION"
DUPLICATE_IN_COLUMN = "DUPLICATE_IN_COLUMN"
DECLARED_COMBINATION_COUNT_MISMATCH = "DECLARED_COMBINATION_COUNT_MISMATCH"
COMBINATION_COUNT_MISMATCH = "COMBINATION_COUNT_MISMATCH"
SCOPE_CONFLICT = "SCOPE_CONFLICT"
MULTIPLIER_CONFLICT = "MULTIPLIER_CONFLICT"


def combination_count(columns: list[list[int]]) -> int:
    """柱碰 expected combination count = product of column candidate counts."""
    count = 1
    for col in columns:
        count *= len(col)
    return count


def check_bet(bet: ParsedBet, *, game: str = "539", declared_count: int | None = None) -> dict[str, Any]:
    """Run deterministic checks on a parsed bet.

    Returns {"issues": [str], "blocked": bool, "block_reason": str|None,
             "warnings": [str], "expected_combination_count": int|None}.
    """
    issues: list[str] = []
    warnings: list[str] = []
    blocked_reason: str | None = None
    expected_count: int | None = None
    max_n = _max_number(game)

    if bet is None or bet.type == "error":
        return {
            "issues": [],
            "warnings": [],
            "blocked": True,
            "block_reason": (bet.block_reason if bet is not None else None) or "PARSE_ERROR",
            "expected_combination_count": None,
        }

    # range + duplicate checks
    if bet.columns:
        for col in bet.columns:
            for n in col:
                if not (1 <= n <= max_n):
                    issues.append(INVALID_NUMBER_RANGE)
                    blocked_reason = blocked_reason or INVALID_NUMBER_RANGE
            if len(set(col)) != len(col):
                warnings.append(DUPLICATE_IN_COLUMN)
        # cross-column duplicate -> a combination may contain the same number twice
        flat = [n for col in bet.columns for n in col]
        if len(set(flat)) != len(flat):
            issues.append(DUPLICATE_IN_COMBINATION)
            blocked_reason = blocked_reason or DUPLICATE_IN_COMBINATION
        expected_count = combination_count(bet.columns)
        if expected_count < 1:
            issues.append(COMBINATION_COUNT_MISMATCH)
            blocked_reason = blocked_reason or COMBINATION_COUNT_MISMATCH
        if declared_count is not None and declared_count != expected_count:
            issues.append(DECLARED_COMBINATION_COUNT_MISMATCH)
    else:
        for n in bet.numbers:
            if not (1 <= n <= max_n):
                issues.append(INVALID_NUMBER_RANGE)
                blocked_reason = blocked_reason or INVALID_NUMBER_RANGE
        if len(set(bet.numbers)) != len(bet.numbers):
            issues.append(DUPLICATE_IN_COMBINATION)
            blocked_reason = blocked_reason or DUPLICATE_IN_COMBINATION

    # amount consistency: money == unit × 100 when both present
    if bet.unit is not None and bet.money is not None:
        expected_money = int((Decimal(str(bet.unit)) * Decimal("100")).quantize(Decimal("1")))
        if expected_money != bet.money:
            issues.append("MONEY_UNIT_MISMATCH")
            blocked_reason = blocked_reason or "MONEY_UNIT_MISMATCH"

    blocked = blocked_reason is not None or bet.executable is False
    if blocked and blocked_reason is None:
        blocked_reason = bet.block_reason
        if blocked_reason is None:
            if bet.type == "shared_multiplier":
                blocked_reason = "SHARED_MULTIPLIER_DECLARATION"
            elif not bet.stars and bet.unit is None:
                blocked_reason = "MISSING_MULTIPLIER"
            else:
                blocked_reason = "BLOCKED_BY_POLICY"
    return {
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "blocked": blocked,
        "block_reason": blocked_reason,
        "expected_combination_count": expected_count,
    }


def check_region_conflicts(
    region_multipliers: list[dict[str, Any]],
    *,
    scope: str,
) -> list[str]:
    """Same-block rule conflicts -> fail-closed reasons."""
    issues: list[str] = []
    by_category: dict[int, list[str]] = {}
    for rule in region_multipliers:
        for cat in rule.get("categories") or []:
            by_category.setdefault(cat, []).append(rule.get("value_text"))
    for cat, values in by_category.items():
        uniq = {v for v in values if v is not None}
        if len(uniq) > 1:
            issues.append(f"{MULTIPLIER_CONFLICT}:{cat}")
    if scope == "unresolved_region":
        issues.append(f"{SCOPE_CONFLICT}:unresolved_region")
    return sorted(set(issues))
