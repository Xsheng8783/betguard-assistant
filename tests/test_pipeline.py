"""Unified pipeline tests: decision function, state machine, human edits, idempotency."""
from __future__ import annotations

from betguard.vision.pipeline import (
    can_transition,
    decide,
    idempotency_key,
    merge_column_slices,
    process_row,
    reapply_after_human_edit,
)


def test_decide_clean_requires_human_approval() -> None:
    d = decide(
        parse_status="success",
        supported=True,
        executable_hint=True,
        block_reasons=[],
        check_issues=[],
        warnings=[],
    )
    assert d.executable is True
    assert d.review_status == "needs_review"


def test_decide_approved_exportable() -> None:
    d = decide(
        parse_status="success",
        supported=True,
        executable_hint=True,
        block_reasons=[],
        check_issues=[],
        warnings=[],
        human_approved=True,
    )
    assert d.review_status == "exportable"


def test_decide_car_never_exportable() -> None:
    d = decide(
        parse_status="success",
        supported=False,  # car bet
        executable_hint=False,
        block_reasons=["UNSUPPORTED_CAR_BET"],
        check_issues=[],
        warnings=[],
        human_approved=True,
    )
    assert d.executable is False
    assert d.review_status == "needs_review"


def test_decide_blocking_issue_not_exportable_after_approval() -> None:
    d = decide(
        parse_status="success",
        supported=True,
        executable_hint=True,
        block_reasons=["DUPLICATE_IN_COMBINATION"],
        check_issues=["DUPLICATE_IN_COMBINATION"],
        warnings=[],
        human_approved=True,
    )
    assert d.executable is False
    assert d.review_status == "needs_review"


def test_can_transition_forbidden() -> None:
    assert can_transition("raw", "human_approved")[0] is False
    assert can_transition("human_approved", "exportable", supported=False)[0] is False
    assert can_transition("human_approved", "exportable", executable=False)[0] is False
    assert can_transition("human_approved", "exportable", unresolved_scope=True)[0] is False
    assert can_transition("needs_review", "exportable")[0] is False
    assert can_transition("human_approved", "exportable")[0] is True


def test_process_row_decision_fields() -> None:
    rec = process_row(
        {"raw_text": "05 09 23 26 二三四X1", "numbers": [], "multiplier": None, "layout_hint": "normal_row"},
        region_bound=True,
        provenance={"model": "qwen3-vl-plus", "parser_version": "m2-v1"},
    )
    assert rec["decision"]["parse_status"] == "success"
    assert rec["decision"]["executable"] is True
    assert rec["decision"]["review_status"] == "needs_review"
    assert rec["provenance"]["parser_version"] == "m2-v1"
    assert rec["normalized_text"]


def test_process_row_car_blocked() -> None:
    rec = process_row(
        {"raw_text": "全車 15 25 各1.5車", "numbers": [], "multiplier": None, "layout_hint": "normal_row"},
        region_bound=True,
    )
    assert rec["decision"]["supported"] is False
    assert rec["decision"]["executable"] is False
    assert "UNSUPPORTED_CAR_BET" in rec["decision"]["block_reasons"]


def test_reapply_human_edit_recomputes() -> None:
    original = process_row(
        {"raw_text": "05 09 23 26 二三四X1", "numbers": [], "multiplier": None, "layout_hint": "normal_row"},
        region_bound=True,
    )
    edited = reapply_after_human_edit(original, raw_text="05 09 23 26 二三四X0.5")
    assert edited["money"] == 50
    assert edited["unit"] == 0.5
    assert edited["decision"]["executable"] is True


def test_reapply_human_edit_blocks_on_invalid() -> None:
    original = process_row(
        {"raw_text": "05 09 23 26 二三四X1", "numbers": [], "multiplier": None, "layout_hint": "normal_row"},
        region_bound=True,
    )
    edited = reapply_after_human_edit(original, raw_text="05 09 23 50 二三四X1")
    assert edited["decision"]["executable"] is False
    assert (
        edited["decision"]["parse_status"] == "error"
        or "INVALID_NUMBER_RANGE" in edited["check_issues"]
    )


def test_idempotency_key() -> None:
    kw = dict(sample_id="sample-002", model="qwen3-vl-plus", run=1, prompt_version="v2", parser_version="m2-v1")
    assert idempotency_key(**kw) == idempotency_key(**kw)
    assert idempotency_key(**kw) != idempotency_key(**{**kw, "parser_version": "m2-v2"})


def test_merge_column_slices_two_rows() -> None:
    merged = merge_column_slices([
        {"raw_text": "04×20×39"},
        {"raw_text": "30×49"},
    ])
    assert len(merged) == 1
    assert merged[0]["numbers"] == [["04"], ["20", "30"], ["39", "49"]]
    assert merged[0]["layout_hint"] == "column_bet"
    assert merged[0]["merged_row_count"] == 2


def test_merge_column_slices_three_rows() -> None:
    merged = merge_column_slices([
        {"raw_text": "05×08×10"},
        {"raw_text": "09 20"},
        {"raw_text": "23 29"},
    ])
    assert len(merged) == 1
    assert merged[0]["numbers"] == [["05"], ["08", "09", "23"], ["10", "20", "29"]]


def test_merge_does_not_swallow_unrelated_row() -> None:
    merged = merge_column_slices([
        {"raw_text": "04×20×39"},
        {"raw_text": "30×49"},
        {"raw_text": "11 12 13 14 二三X1"},  # different bet, not a continuation
    ])
    assert len(merged) == 2


def test_merge_skips_car_row() -> None:
    merged = merge_column_slices([
        {"raw_text": "全車 15 25"},
        {"raw_text": "04×20×39"},
    ])
    assert len(merged) == 2
