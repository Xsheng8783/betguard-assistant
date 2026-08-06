"""整區套用柱碰 tool tests: patch-only edits through the official pipeline."""
from __future__ import annotations

import json
from pathlib import Path

from betguard.vision.region_edit import (
    apply_region_edit,
    build_edit_patch,
    columns_to_text,
    validate_edit_patch,
)
from betguard.vision.region_edit import apply_region_edit

GT = Path(r"C:\BetguardOCRDataset\ground-truth-draft")


def _rows(raw_text: str, multiplier_text: str | None = "二三X1.5") -> list[dict]:
    return [{"raw_text": raw_text, "multiplier_text": multiplier_text, "play_type": None}]


def _patch(columns, region_id="R01", rows=None):
    return build_edit_patch(
        region_id=region_id,
        source_row_ids=[r.get("line_id", "row-1") for r in (rows or _rows("x"))],
        columns=columns,
    )


def test_apply_produces_column_bet_and_product() -> None:
    patch = _patch([["05"], ["08", "09", "23"], ["10", "20", "29"]])
    res = apply_region_edit(patch, region_rows=_rows("05 08 10 / 09 20 / 23 29 二三X1.5"))

    assert res["blocked"] is False
    assert res["combination_count"] == 9
    assert res["new_row"]["bet_type"] == "column"
    assert res["new_row"]["columns"] == [[5], [8, 9, 23], [10, 20, 29]]
    assert res["decision"]["executable"] is True
    assert res["decision"]["review_status"] == "needs_review"  # never auto-approved


def test_idempotent_same_patch_same_result() -> None:
    patch = _patch([["05"], ["08", "09", "23"], ["10", "20", "29"]])
    rows = _rows("05 08 10 / 09 20 / 23 29 二三X1.5")
    r1 = apply_region_edit(patch, region_rows=rows)
    r2 = apply_region_edit(patch, region_rows=rows)

    assert r1["new_row"] == r2["new_row"]
    assert r1["combination_count"] == r2["combination_count"]


def test_edit_column_recomputes() -> None:
    patch = _patch([["05"], ["08", "09", "23"], ["10", "20", "29"]])
    rows = _rows("05 08 10 / 09 20 / 23 29 二三X1.5")
    base = apply_region_edit(patch, region_rows=rows)
    patch2 = _patch([["05"], ["08", "09"], ["10", "20", "29"]])
    edited = apply_region_edit(patch2, region_rows=rows)

    assert base["combination_count"] == 9
    assert edited["combination_count"] == 6


def test_car_bet_mixed_region_rejected() -> None:
    patch = _patch([["05"], ["08", "09", "23"], ["10", "20", "29"]])
    rows = [
        {"raw_text": "全車 15 25 各1.5車", "multiplier_text": None, "play_type": "car_bet"},
    ]
    res = apply_region_edit(patch, region_rows=rows)

    assert res["blocked"] is True
    assert "MIXED_REGION_CAR_BET" in res["block_reason"]


def test_empty_column_fail_closed() -> None:
    patch = _patch([["05"], [], ["10", "20", "29"]])
    res = apply_region_edit(patch, region_rows=_rows("x"))

    assert res["blocked"] is True
    assert "EMPTY_COLUMN" in res["block_reason"]


def test_invalid_number_fail_closed() -> None:
    patch = _patch([["05"], ["50"], ["10", "20", "29"]])
    res = apply_region_edit(patch, region_rows=_rows("x"))

    assert res["blocked"] is True
    assert "INVALID_NUMBER_IN_COLUMN" in res["block_reason"]


def test_undo_preserved() -> None:
    rows = _rows("05 08 10 / 09 20 / 23 29 二三X1.5")
    patch = _patch([["05"], ["08", "09", "23"], ["10", "20", "29"]], rows=rows)
    res = apply_region_edit(patch, region_rows=rows)

    assert res["undo"] == rows
    assert res["patch"]["edit_type"] == "APPLY_COLUMN_COMBO_TO_REGION"


def _z2(n: int) -> str:
    return f"{n:02d}"


def test_gt_column_bets_apply_cleanly() -> None:
    """Integration: every GT column bet in 002/005/006 applies through the tool."""
    for sid in ("sample-002", "sample-005", "sample-006"):
        d = json.loads((GT / f"{sid}.json").read_text(encoding="utf-8"))
        col_bets = [l for l in d["lines"] if l.get("layout_hint") == "column_bet"]
        assert col_bets, sid
        for line in col_bets:
            columns = [[_z2(int(x)) for x in col] for col in line["number_groups"]]
            game = "六合彩" if any(int(x) > 39 for col in columns for x in col) else "539"
            mult = line.get("multiplier_text")
            rows = [{"raw_text": line.get("human_raw_text") or line.get("raw_text"),
                     "multiplier_text": mult, "play_type": None}]
            patch = _patch(columns, region_id=line.get("region_id", "R01"), rows=rows)
            res = apply_region_edit(patch, region_rows=rows, game=game)
            assert res["blocked"] is False, f"{sid} {line['line_id']}: {res['block_reason']}"
            assert res["new_row"]["bet_type"] == "column"
            expected = 1
            for col in columns:
                expected *= len(col)
            assert res["combination_count"] == expected
            assert res["decision"]["review_status"] == "needs_review"


def test_sample005_eight_bets_no_over_expansion() -> None:
    d = json.loads((GT / "sample-005.json").read_text(encoding="utf-8"))
    col_bets = [l for l in d["lines"] if l.get("layout_hint") == "column_bet"]
    assert len(col_bets) == 8
    applied = 0
    for line in col_bets:
        columns = [[_z2(int(x)) for x in col] for col in line["number_groups"]]
        game = "六合彩" if any(int(x) > 39 for col in columns for x in col) else "539"
        rows = [{"raw_text": line.get("human_raw_text") or line.get("raw_text"),
                 "multiplier_text": line.get("multiplier_text"), "play_type": None}]
        res = apply_region_edit(_patch(columns, rows=rows), region_rows=rows, game=game)
        assert res["blocked"] is False
        assert res["new_row"]["bet_type"] == "column"
        assert len(res["new_row"]["columns"]) == len(columns)  # one bet, not expanded
        applied += 1
    assert applied == 8
