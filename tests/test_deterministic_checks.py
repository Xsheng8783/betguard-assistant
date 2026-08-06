"""Deterministic check tests (M2)."""
from __future__ import annotations

from betguard.models import ParsedBet
from betguard.vision.deterministic_checks import (
    DECLARED_COMBINATION_COUNT_MISMATCH,
    DUPLICATE_IN_COLUMN,
    DUPLICATE_IN_COMBINATION,
    INVALID_NUMBER_RANGE,
    MULTIPLIER_CONFLICT,
    SCOPE_CONFLICT,
    check_bet,
    check_region_conflicts,
    combination_count,
)


def bet(**kw) -> ParsedBet:
    base = dict(
        game="539", type="normal", numbers=[], stars=[], unit=None, money=None,
    )
    base.update(kw)
    return ParsedBet(**base)


def test_out_of_range_blocked() -> None:
    r = check_bet(bet(numbers=[45, 46]), game="539")
    assert r["blocked"] is True
    assert r["block_reason"] == INVALID_NUMBER_RANGE


def test_duplicate_in_normal_combination_blocked() -> None:
    r = check_bet(bet(numbers=[8, 8, 9]))
    assert r["blocked"] is True
    assert r["block_reason"] == DUPLICATE_IN_COMBINATION


def test_duplicate_inside_column_warning_only() -> None:
    r = check_bet(bet(type="column", numbers=[8, 9], columns=[[8, 8], [9]]))
    assert DUPLICATE_IN_COLUMN in r["warnings"]
    assert r["block_reason"] != DUPLICATE_IN_COLUMN


def test_cross_column_duplicate_blocked() -> None:
    r = check_bet(bet(type="column", numbers=[8, 8], columns=[[8], [8, 9]]))
    assert r["blocked"] is True
    assert r["block_reason"] == DUPLICATE_IN_COMBINATION


def test_combination_count_product() -> None:
    assert combination_count([[5], [8, 9, 23], [10, 20, 29]]) == 9
    assert combination_count([[1], [2]]) == 1


def test_declared_count_mismatch_issue() -> None:
    r = check_bet(
        bet(type="column", numbers=[1, 2, 3], columns=[[1], [2, 3]]),
        declared_count=4,
    )
    assert DECLARED_COMBINATION_COUNT_MISMATCH in r["issues"]
    assert r["expected_combination_count"] == 2  # computed value wins


def test_money_unit_mismatch_blocked() -> None:
    r = check_bet(bet(numbers=[1, 2], unit=0.5, money=500))
    assert r["blocked"] is True
    assert r["block_reason"] == "MONEY_UNIT_MISMATCH"


def test_money_unit_consistent_ok() -> None:
    r = check_bet(bet(numbers=[1, 2], unit=0.5, money=50))
    assert r["blocked"] is False


def test_region_multiplier_conflict() -> None:
    issues = check_region_conflicts(
        [
            {"categories": [2, 3], "value_text": "0.5"},
            {"categories": [2, 3], "value_text": "1"},
        ],
        scope="all_groups_in_region",
    )
    assert any(i.startswith(MULTIPLIER_CONFLICT) for i in issues)


def test_region_unresolved_scope_conflict() -> None:
    issues = check_region_conflicts([], scope="unresolved_region")
    assert any(i.startswith(SCOPE_CONFLICT) for i in issues)


def test_parse_error_stays_blocked() -> None:
    r = check_bet(bet(type="error", block_reason="PARSE_ERROR"))
    assert r["blocked"] is True
    assert r["block_reason"] == "PARSE_ERROR"
