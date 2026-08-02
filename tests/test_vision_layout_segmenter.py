"""Test layout row logic (pure functions, no cv2 import)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# layout_rows.py is a plain module under tools/vision/ (no package __init__)
_TOOLS_VISION = str(Path(__file__).resolve().parents[1] / "tools" / "vision")
if _TOOLS_VISION not in sys.path:
    sys.path.insert(0, _TOOLS_VISION)

from layout_rows import (  # noqa: E402
    assess_crop_usability,
    group_by_rows,
    row_crop_ranges,
)


def _item(text: str, y1: float, y2: float, x1: float = 0.0, x2: float = 50.0) -> dict:
    return {
        "text": text,
        "box": [x1, y1, x2, y2],
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }


class TestRowGrouping:
    def test_side_by_same_row(self):
        """Two boxes on the same physical line must stay in one row."""
        items = [
            _item("05", 10, 30, 0, 50),
            _item("15", 10, 30, 60, 110),
        ]
        rows = group_by_rows(items)
        assert len(rows) == 1
        assert len(rows[0]) == 2

    def test_vertical_lines_not_merged(self):
        """Boxes on different lines (distant centers) must NOT merge."""
        items = [
            _item("05", 10, 30),
            _item("15", 80, 100),
            _item("25", 150, 170),
        ]
        rows = group_by_rows(items)
        assert len(rows) == 3

    def test_close_lines_not_merged(self):
        """Lines only ~40px apart with 20px text must stay separate."""
        items = [
            _item("05", 10, 30),
            _item("15", 50, 70),
        ]
        rows = group_by_rows(items)
        assert len(rows) == 2

    def test_empty(self):
        assert group_by_rows([]) == []


class TestRowCropRanges:
    def test_non_overlapping(self):
        """Adjacent crop y-ranges must never overlap."""
        items = [
            _item("05", 10, 30),
            _item("15", 80, 100),
            _item("25", 150, 170),
        ]
        rows = group_by_rows(items)
        ranges = row_crop_ranges(rows, region_y1=0, region_y2=200)
        assert len(ranges) == 3
        for i in range(1, len(ranges)):
            assert ranges[i][0] >= ranges[i - 1][1], f"overlap: {ranges}"

    def test_midpoint_boundary(self):
        """Boundary between rows = midpoint of their centers."""
        items = [_item("05", 10, 30), _item("15", 80, 100)]
        rows = group_by_rows(items)
        ranges = row_crop_ranges(rows, region_y1=0, region_y2=120)
        # centers 20 and 90 → midpoint 55
        assert ranges[0][1] <= 55
        assert ranges[1][0] >= 55

    def test_first_row_starts_at_region_top(self):
        items = [_item("05", 40, 60)]
        rows = group_by_rows(items)
        ranges = row_crop_ranges(rows, region_y1=0, region_y2=200)
        assert ranges[0][0] == 0

    def test_last_row_ends_at_region_bottom(self):
        items = [_item("05", 40, 60)]
        rows = group_by_rows(items)
        ranges = row_crop_ranges(rows, region_y1=0, region_y2=200)
        assert ranges[0][1] == 200

    def test_full_width_is_caller_side(self):
        """row_crop_ranges only returns vertical ranges (x handled by caller)."""
        items = [_item("05", 10, 30)]
        rows = group_by_rows(items)
        ranges = row_crop_ranges(rows, 0, 100)
        assert len(ranges[0]) == 2  # (y1, y2)


class TestCropUsability:
    def test_clean_line_trainable(self):
        items = [_item("05", 10, 30)]
        u = assess_crop_usability(items, crop_w=300, crop_h=40, ocr_text="05")
        assert u["trainable"] is True
        assert u["unusable_reason"] == ""

    def test_blank_ocr_not_trainable(self):
        u = assess_crop_usability([_item("", 10, 30)], crop_w=300, crop_h=40, ocr_text="")
        assert u["trainable"] is False
        assert "blank_ocr" in u["unusable_reason"]

    def test_tiny_crop_not_trainable(self):
        u = assess_crop_usability([_item("05", 10, 30)], crop_w=20, crop_h=10, ocr_text="05")
        assert u["trainable"] is False
        assert "crop_too_small" in u["unusable_reason"]

    def test_single_tiny_detection_not_trainable(self):
        items = [_item("·", 10, 15, 0, 5)]  # 5x5 stray dot
        u = assess_crop_usability(items, crop_w=300, crop_h=40, ocr_text="·")
        assert u["trainable"] is False
        assert "single_tiny_detection" in u["unusable_reason"]

    def test_multi_line_suspected_not_trainable(self):
        """Two boxes whose centers are far apart → suspected multi-line."""
        items = [_item("05", 10, 30), _item("15", 100, 120)]
        u = assess_crop_usability(items, crop_w=300, crop_h=120, ocr_text="05 15")
        assert u["trainable"] is False
        assert "suspected_multi_line" in u["unusable_reason"]

    def test_low_res_not_trainable(self):
        u = assess_crop_usability([_item("05", 10, 30)], crop_w=300, crop_h=40, ocr_text="05", low_res=True)
        assert u["trainable"] is False
        assert "low_resolution_original" in u["unusable_reason"]
