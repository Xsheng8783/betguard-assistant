"""Regression tests: 柱碰 column grouping by center_x (not OCR order)."""
from __future__ import annotations

from betguard.vision.column_geometry import build_columns_from_bbox


def _tok(text, x1, y1, x2, y2):
    return {"text": text, "bbox": [x1, y1, x2, y2]}


# ── 案例一: 第一柱 06,07 | 第二柱 08,38 | 二碰 | x1 ──────────────────────
CASE1_TOKENS = [
    _tok("06", 40, 100, 80, 130),
    _tok("07", 44, 150, 84, 180),   # 07 與 06 同 X band
    _tok("08", 200, 100, 240, 130),
    _tok("38", 204, 150, 244, 180),  # 38 與 08 同 X band
    _tok("2", 300, 110, 320, 140),   # 碰法 二
    _tok("x", 330, 115, 350, 140),
    _tok("1", 360, 115, 390, 140),   # 倍率 x1
]


def test_case1_two_columns() -> None:
    res = build_columns_from_bbox(CASE1_TOKENS)

    assert res["column_count"] == 2
    assert res["columns"] == {"1": ["06", "07"], "2": ["08", "38"]}
    assert res["collision"] == "二碰"
    assert res["collision_raw"] == "2"
    assert res["multiplier"] == 1.0


def test_case1_wrong_split_rejected() -> None:
    """欄1:06 / 欄2:08,07 / 欄3:38 必須判定失敗。"""
    res = build_columns_from_bbox(CASE1_TOKENS)
    assert res["columns"] != {"1": ["06"], "2": ["08", "07"], "3": ["38"]}
    assert res["column_count"] == 2  # 不得產生第三柱


# ── 案例二: 24,34 | 03,23 | 17,27,37 | 20,30,35 | 四三碰 | x0.1 ───────────
CASE2_TOKENS = [
    _tok("24", 40, 100, 80, 130),
    _tok("34", 40, 140, 80, 170),
    _tok("03", 140, 100, 180, 130),
    _tok("23", 140, 140, 180, 170),
    _tok("17", 240, 100, 280, 130),
    _tok("27", 240, 140, 280, 170),
    _tok("37", 242, 180, 282, 210),  # 37 的 center_x 接近 27（案例三原則）
    _tok("20", 340, 100, 380, 130),
    _tok("30", 340, 140, 380, 170),
    _tok("35", 340, 180, 380, 210),
    _tok("2", 430, 100, 450, 130),   # 碰法雜訊（孤立 2，不新增柱）
    _tok("3", 460, 100, 480, 130),
    _tok("4", 460, 140, 480, 170),   # 3 上 4 下 = 4/3
    _tok("x", 500, 110, 520, 140),
    _tok("0", 530, 110, 550, 140),
    _tok("1", 560, 110, 580, 140),   # x0.1
]


def test_case2_four_columns() -> None:
    res = build_columns_from_bbox(CASE2_TOKENS)

    assert res["column_count"] == 4
    assert res["columns"] == {
        "1": ["24", "34"],
        "2": ["03", "23"],
        "3": ["17", "27", "37"],
        "4": ["20", "30", "35"],
    }
    assert res["collision_raw"] == "4/3"
    assert res["collision"] == "四三碰"
    assert res["multiplier"] == 0.1


def test_case2_wrong_columns_rejected() -> None:
    """欄1:24,34,37 / 欄2:03,23,35 / 欄3:17,27 / 欄4:20,30 / 欄5:2 必須失敗。"""
    res = build_columns_from_bbox(CASE2_TOKENS)
    wrong = {
        "1": ["24", "34", "37"],
        "2": ["03", "23", "35"],
        "3": ["17", "27"],
        "4": ["20", "30"],
        "5": ["2"],
    }
    assert res["columns"] != wrong
    assert "5" not in res["columns"]  # 孤立 2 不得建立第五柱


# ── 案例三: 37 的 center_x 接近 27，不是 28 ────────────────────────────────
def test_case3_37_assigned_to_27_column() -> None:
    tokens = CASE2_TOKENS + [_tok("28", 344, 180, 384, 210)]  # 28 在第四柱下方
    res = build_columns_from_bbox(tokens)

    assert "37" in res["columns"]["3"]  # 37 跟 17,27 同柱
    assert "37" not in res["columns"]["4"]  # 不得放到 28 的柱
