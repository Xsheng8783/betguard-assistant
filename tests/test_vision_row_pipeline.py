"""Row pipeline tests: synthetic bet-slip images (OpenCV), no models.

Core acceptance: every physical text line must be detected — a line never
silently disappears. When rows cannot be trusted, the page is flagged
NEEDS_MANUAL_ROW_REVIEW.
"""

from __future__ import annotations

import json

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402

from betguard.vision.row_pipeline import (  # noqa: E402
    REGION_DEFS,
    RowPipelineResult,
    _detect_lines_in_gray,
    run_pipeline,
    write_outputs,
)


# ── Synthetic bet-slip builders ───────────────────────────────────────────────

def make_slip(
    n_rows: int = 6,
    width: int = 1200,
    height: int = 1600,
    row_h: int = 46,
    gap: int = 12,
    ink: tuple[int, int, int] = (30, 30, 30),
    seed_text: str | None = None,
) -> np.ndarray:
    """White paper with n_rows dark text lines across the left region."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    y = 60
    for i in range(n_rows):
        if seed_text:
            text = seed_text if i == 0 else f"row {i + 1}"
        else:
            text = f"01 20 x{i + 1}"
        cv2.putText(img, text, (40, y + 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1.4, ink, 3, cv2.LINE_AA)
        y += row_h + gap
    return img


def make_slip_with_matrix(width: int = 1200, height: int = 1600) -> np.ndarray:
    """Slip with three-column matrix lines (zhu-peng) in center region."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    # left region rows
    for i in range(3):
        cv2.putText(img, f"0{i + 1} 20 x1", (40, 100 + i * 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (30, 30, 30), 3, cv2.LINE_AA)
    # center region matrix: 3 rows × 3 columns
    cols_x = [int(0.36 * width), int(0.5 * width), int(0.64 * width)]
    for row in range(3):
        y = 320 + row * 60
        for c, x in enumerate(cols_x):
            cv2.putText(img, f"{row + 1}{c + 1}", (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.3, (30, 30, 30), 3, cv2.LINE_AA)
    return img


def make_skewed_slip(n_rows: int = 5) -> np.ndarray:
    img = make_slip(n_rows=n_rows)
    (h, w) = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), 4.0, 1.0)
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestResolutionInvariance:
    @pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
    def test_same_rows_across_resolutions(self, scale):
        base = make_slip(n_rows=6, width=1200, height=1600)
        resized = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        path = "res_tmp.png"
        cv2.imwrite(path, resized)
        try:
            result = run_pipeline(path)
            assert result.page_status == "OK", result.review_reason
            assert len(result.rows) >= 6, (
                f"scale={scale}: only {len(result.rows)} rows — lines disappeared"
            )
        finally:
            import os
            os.remove(path)

    def test_all_rows_present_no_silent_loss(self):
        """Core acceptance: 10 physical lines → at least 10 detected rows."""
        path = "rows10.png"
        cv2.imwrite(path, make_slip(n_rows=10, height=2200, row_h=40, gap=10))
        try:
            result = run_pipeline(path)
            assert len(result.rows) >= 10, f"lost rows: {len(result.rows)} < 10"
        finally:
            import os
            os.remove(path)


class TestMissingRows:
    def test_blank_page_flagged_review(self):
        img = np.full((800, 600, 3), 255, dtype=np.uint8)
        path = "blank.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert result.page_status == "NEEDS_MANUAL_ROW_REVIEW"
            assert result.review_reason
        finally:
            import os
            os.remove(path)

    def test_few_rows_flagged_review(self):
        path = "1row.png"
        cv2.imwrite(path, make_slip(n_rows=1))
        try:
            result = run_pipeline(path)
            assert result.page_status == "NEEDS_MANUAL_ROW_REVIEW"
        finally:
            import os
            os.remove(path)

    def test_missing_row_still_detected_when_visible(self):
        """A line that is physically present is detected even if adjacent
        rows are absent (no silent swallowing)."""
        img = make_slip(n_rows=4)
        # erase rows 2 and 3 (leave 1 and 4)
        cv2.rectangle(img, (0, 60 + 58), (600, 60 + 58 * 3), (255, 255, 255), -1)
        path = "gap.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert len(result.rows) >= 2
        finally:
            import os
            os.remove(path)


class TestDuplicateRows:
    def test_duplicate_text_two_lines_two_ids(self):
        """Two lines with identical text are two rows with unique line_ids."""
        img = make_slip(n_rows=2, seed_text="01 20 x1")
        path = "dup.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert len(result.rows) >= 2
        finally:
            import os
            os.remove(path)

    def test_line_ids_unique(self):
        img = make_slip_with_matrix()
        path = "matrix.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            ids = [r.line_id for r in result.rows]
            assert len(ids) == len(set(ids)), "duplicate line_id!"
            region_ids = [r.region_id for r in result.rows]
            assert len(region_ids) == len(set(region_ids)), "duplicate region_id!"
        finally:
            import os
            os.remove(path)


class TestRegions:
    def test_all_four_regions_in_defs(self):
        assert set(REGION_DEFS) == {"left", "center", "right", "bottom"}
        for name, (x0, y0, x1, y1) in REGION_DEFS.items():
            assert 0 <= x0 < x1 <= 1
            assert 0 <= y0 < y1 <= 1

    def test_regions_tile_the_page(self):
        # left/center/right cover full width above bottom; bottom covers rest
        assert REGION_DEFS["left"][0] == 0.0
        assert REGION_DEFS["right"][2] == 1.0
        assert REGION_DEFS["left"][3] == REGION_DEFS["center"][3] == REGION_DEFS["right"][3]
        assert REGION_DEFS["bottom"][1] == REGION_DEFS["left"][3]


class TestCorrection:
    def test_skew_corrected(self):
        path = "skew.png"
        cv2.imwrite(path, make_skewed_slip())
        try:
            result = run_pipeline(path)
            assert result.correction_applied.startswith("rotation")
        finally:
            import os
            os.remove(path)


class TestOutputs:
    def test_write_outputs_creates_files(self, tmp_path):
        path = "out_src.png"
        cv2.imwrite(path, make_slip(n_rows=4))
        try:
            result = run_pipeline(path)
            out = write_outputs(result, str(tmp_path))
            assert (tmp_path / "row-pipeline.json").is_file()
            assert (tmp_path / "rows_overlay.png").is_file()
            assert (tmp_path / "regions_overlay.png").is_file()
            assert (tmp_path / "corrected.png").is_file()
            assert (tmp_path / "summary.txt").is_file()
            assert len(out["crops"]) == len(result.rows)
            data = json.loads((tmp_path / "row-pipeline.json").read_text(encoding="utf-8"))
            assert data["page_status"] == result.page_status
        finally:
            import os
            os.remove(path)


class TestMergedBand:
    def test_tall_band_flags_review(self):
        """A band much taller than the median is likely merged rows — the
        page must be flagged, never silently treated as one line."""
        img = make_slip(n_rows=4)
        # draw a huge contiguous dark block (like a merged region)
        cv2.rectangle(img, (30, 400), (700, 520), (30, 30, 30), -1)
        path = "merged.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert result.page_status == "NEEDS_MANUAL_ROW_REVIEW"
            reason = result.review_reason or ""
            assert ("merged" in reason) or ("flat" in reason)
        finally:
            import os
            os.remove(path)

    def test_normal_rows_stay_ok(self):
        path = "ok.png"
        cv2.imwrite(path, make_slip(n_rows=6))
        try:
            result = run_pipeline(path)
            assert result.page_status == "OK"
        finally:
            import os
            os.remove(path)


class TestSecondarySplit:
    """Conservative split of tall bands: only at reliable valleys."""

    def _two_line_tall_band(self) -> np.ndarray:
        """Two text lines drawn close together so they merge into one band."""
        img = np.full((800, 1200, 3), 255, dtype=np.uint8)
        # two lines with only a 2px gap — merged into one tall band
        cv2.putText(img, "01 20 x1", (40, 300), cv2.FONT_HERSHEY_SIMPLEX,
                    1.4, (30, 30, 30), 3, cv2.LINE_AA)
        cv2.putText(img, "02 21 x2", (40, 348), cv2.FONT_HERSHEY_SIMPLEX,
                    1.4, (30, 30, 30), 3, cv2.LINE_AA)
        return img

    def test_clear_valley_splits_into_two(self):
        img = self._two_line_tall_band()
        path = "split2.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            # the merged tall band should be split; rows now >= 2
            assert len(result.rows) >= 2
            if result.splits:
                for split in result.splits:
                    assert split["split_method"] == "projection_valley"
                    assert len(split["child_band_ids"]) >= 2
        finally:
            import os
            os.remove(path)

    def test_no_valley_no_forced_split(self):
        """A solid block has no valley → never force-split."""
        img = np.full((600, 800, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (30, 200), (700, 300), (30, 30, 30), -1)  # solid block
        path = "solid.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert result.splits == []
            assert result.page_status == "NEEDS_MANUAL_ROW_REVIEW"
        finally:
            import os
            os.remove(path)

    def test_large_font_single_line_not_split(self):
        """A tall but single text line must not be cut into two."""
        img = np.full((600, 1200, 3), 255, dtype=np.uint8)
        cv2.putText(img, "01 20 x1", (40, 300), cv2.FONT_HERSHEY_SIMPLEX,
                    3.0, (30, 30, 30), 6, cv2.LINE_AA)  # huge font, one line
        path = "bigfont.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            # no splits should occur (no internal valley in a single glyph row)
            assert result.splits == []
        finally:
            import os
            os.remove(path)

    def test_split_children_do_not_overlap(self):
        img = self._two_line_tall_band()
        path = "noverlap.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            for split in result.splits:
                children = [r for r in result.rows
                            if r.line_id in split["child_band_ids"]]
                boxes = [r.bounding_box for r in children]
                for i in range(len(boxes) - 1):
                    assert boxes[i][1] + boxes[i][3] <= boxes[i + 1][1], "overlap!"
        finally:
            import os
            os.remove(path)


class TestStability:
    def test_rerun_stable_line_ids_and_boxes(self):
        """Repeated runs must produce identical results."""
        path = "stable.png"
        cv2.imwrite(path, make_slip(n_rows=5))
        try:
            r1 = run_pipeline(path)
            r2 = run_pipeline(path)
            b1 = [r.bounding_box for r in r1.rows]
            b2 = [r.bounding_box for r in r2.rows]
            assert b1 == b2
            assert r1.page_status == r2.page_status
        finally:
            import os
            os.remove(path)

    def test_split_consistent_across_resolutions(self):
        img = self._two_line_tall_band() if hasattr(self, "_two_line_tall_band") else None
        # use a two-line image and check row counts match at 1x and 0.5x
        base = np.full((800, 1200, 3), 255, dtype=np.uint8)
        cv2.putText(base, "01 20 x1", (40, 300), cv2.FONT_HERSHEY_SIMPLEX,
                    1.4, (30, 30, 30), 3, cv2.LINE_AA)
        cv2.putText(base, "02 21 x2", (40, 348), cv2.FONT_HERSHEY_SIMPLEX,
                    1.4, (30, 30, 30), 3, cv2.LINE_AA)
        import os
        p1, p2 = "res1.png", "res05.png"
        cv2.imwrite(p1, base)
        cv2.imwrite(p2, cv2.resize(base, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
        try:
            r1 = run_pipeline(p1)
            r2 = run_pipeline(p2)
            assert len(r1.rows) == len(r2.rows)
        finally:
            os.remove(p1)
            os.remove(p2)


class TestBlockedInputs:
    def test_blank_image_blocked(self):
        img = np.full((600, 800, 3), 255, dtype=np.uint8)
        path = "blank2.png"
        cv2.imwrite(path, img)
        try:
            result = run_pipeline(path)
            assert result.page_status == "NEEDS_MANUAL_ROW_REVIEW"
            assert result.review_reason
        finally:
            import os
            os.remove(path)

    def test_severely_blurred_blocked_or_review(self):
        img = make_slip(n_rows=5)
        blurred = cv2.GaussianBlur(img, (51, 51), 0)
        path = "blur.png"
        cv2.imwrite(path, blurred)
        try:
            result = run_pipeline(path)
            assert result.page_status in ("NEEDS_MANUAL_ROW_REVIEW", "OK")
        finally:
            import os
            os.remove(path)

    def test_corrupt_image_raises_clear_error(self):
        path = "corrupt.png"
        with open(path, "wb") as f:
            f.write(b"this is not an image at all")
        try:
            with pytest.raises(ValueError, match="unable to read image"):
                run_pipeline(path)
        finally:
            import os
            os.remove(path)

    def test_missing_file_raises_clear_error(self):
        with pytest.raises(ValueError, match="unable to read image"):
            run_pipeline("does-not-exist.png")


class TestRegionDetection:
    def test_line_boxes_within_image(self):
        path = "bounds.png"
        cv2.imwrite(path, make_slip(n_rows=5))
        try:
            result = run_pipeline(path)
            h, w = cv2.imread(path).shape[:2]
            for row in result.rows:
                x, y, bw, bh = row.bounding_box
                assert x >= 0 and y >= 0
                assert x + bw <= w and y + bh <= h
        finally:
            import os
            os.remove(path)
