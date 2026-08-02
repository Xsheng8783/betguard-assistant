"""Integration tests for layout segmenter — synthetic 4-region image.

Runs the REAL segmenter in the OCR venv (has cv2) via subprocess with a
fake OCR engine (no real paddleocr, no network). Skips if BETGUARD_OCR_PYTHON
is not configured.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _ocr_python() -> str | None:
    p = os.environ.get("BETGUARD_OCR_PYTHON", "")
    return p if p and os.path.isfile(p) else None


@pytest.fixture(scope="module")
def ocr_python() -> str | None:
    return _ocr_python()


def _run_inline(script: str, ocr_python: str) -> dict:
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    script = f"import sys; sys.path.insert(0, {str(repo / 'tools' / 'vision')!r})\n" + script
    proc = subprocess.run(
        [ocr_python, "-c", script],
        capture_output=True, text=True, timeout=120, shell=False, env=env,
    )
    assert proc.returncode == 0, f"worker failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


_SEGMENT_SCRIPT = textwrap.dedent("""
import json, sys
import cv2
import numpy as np
from layout_segmenter import segment_and_ocr

# Synthetic 400x500 image with 4 distinct colored regions
img = np.zeros((500, 400, 3), dtype=np.uint8)
img[:] = (20, 20, 20)
img[40:315, 12:128] = (40, 40, 200)    # left_numbers: red (BGR)
img[40:315, 140:256] = (40, 200, 40)   # center_numbers: green
img[40:315, 268:384] = (200, 40, 40)   # right_numbers: blue
img[340:475, 12:388] = (40, 200, 200)  # bottom: yellow

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"
cv2.imwrite(path, img)

class FakeEngine:
    def __init__(self):
        self.calls = []
        self.counts = {}
    def _region(self, path):
        # Region name is embedded in the unique crop filename
        name = path.replace("\\\\", "/").split("/")[-1]
        for r in ("left_numbers", "center_numbers", "right_numbers", "bottom"):
            if r in name:
                return r
        return "unknown"
    def _res(self, texts, scores, polys, boxes):
        return [{"res": {"rec_texts": texts, "rec_scores": scores,
                         "rec_polys": polys, "rec_boxes": boxes}}]
    def predict(self, path):
        self.calls.append(path)
        region = self._region(path)
        self.counts[region] = self.counts.get(region, 0) + 1
        if self.counts[region] == 1:
            # Region-level: left has TWO rows 60px apart; others one row
            if region == "left_numbers":
                return self._res(
                    ["05", "15"], [0.9, 0.8],
                    [[[10, 10], [50, 10], [50, 30], [10, 30]],
                     [[10, 70], [50, 70], [50, 90], [10, 90]]],
                    [[10, 10, 50, 30], [10, 70, 50, 90]])
            return self._res(["09"], [0.9], [[[10, 10], [50, 10], [50, 30], [10, 30]]], [[10, 10, 50, 30]])
        # Per-line re-OCR: single item
        return self._res(["05"], [0.9], [[[10, 10], [50, 10], [50, 30], [10, 30]]], [[10, 10, 50, 30]])

engine = FakeEngine()
result = segment_and_ocr(path, ocr_engine=engine)
result["_fake_calls"] = engine.calls
print(json.dumps(result, ensure_ascii=False, default=str))
""").strip()


@pytest.mark.skipif(not _ocr_python(), reason="BETGUARD_OCR_PYTHON not configured")
class TestFourRegionIntegration:
    def test_four_regions_all_present(self, ocr_python, tmp_path):
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT + f"\n"
        result = _run_inline(
            script.replace('path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
                           f'path = {img_path!r}'),
            ocr_python,
        )
        assert result.get("ok") is True
        names = {r["name"] for r in result["regions"]}
        assert names == {"left_numbers", "center_numbers", "right_numbers", "bottom"}, names

    def test_regions_not_all_left(self, ocr_python, tmp_path):
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT.replace(
            'path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
            f'path = {img_path!r}')
        result = _run_inline(script, ocr_python)
        regions_with_lines = {r["name"] for r in result["regions"] if r.get("lines")}
        assert len(regions_with_lines) >= 3, f"only {regions_with_lines} had lines"

    def test_crop_hashes_distinct(self, ocr_python, tmp_path):
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT.replace(
            'path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
            f'path = {img_path!r}')
        result = _run_inline(script, ocr_python)
        hashes = [r["crop_hash"] for r in result["regions"]]
        assert len(set(hashes)) == 4, f"crop hashes not distinct: {hashes}"

    def test_rows_not_merged(self, ocr_python, tmp_path):
        """Left region has 2 rows 60px apart — must NOT merge into 1."""
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT.replace(
            'path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
            f'path = {img_path!r}')
        result = _run_inline(script, ocr_python)
        left = next(r for r in result["regions"] if r["name"] == "left_numbers")
        assert left["row_count"] == 2, f"rows merged: {left['row_count']}"

    def test_region_level_crops_unique_paths(self, ocr_python, tmp_path):
        """First call per region must be a unique crop path (no overwrite)."""
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT.replace(
            'path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
            f'path = {img_path!r}')
        result = _run_inline(script, ocr_python)
        calls = result["_fake_calls"]
        region_calls = [c for c in calls if "crop_" in Path(c).name]
        assert len(region_calls) == 4, f"expected 4 region crops, got {len(region_calls)}: {region_calls}"
        assert len(set(region_calls)) == 4, "region crop paths not unique"

    def test_zero_detection_region_reported(self, ocr_python, tmp_path):
        """A region with no detections must still appear with detection_count=0."""
        img_path = str(tmp_path / "synthetic.png")
        script = _SEGMENT_SCRIPT.replace(
            'path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic_betslip.png"',
            f'path = {img_path!r}')
        result = _run_inline(script, ocr_python)
        for r in result["regions"]:
            assert "detection_count" in r
            assert "row_count" in r


_CLOSED_LOOP_SCRIPT = textwrap.dedent("""
import json, os, sys
import cv2
import numpy as np
from layout_segmenter import segment_and_ocr

# High-res synthetic 800x1000 with 4 separated text lines (left region full width)
img = np.full((1000, 800, 3), 255, dtype=np.uint8)
for i, y in enumerate([100, 300, 500, 700]):
    cv2.rectangle(img, (50, y), (750, y + 50), (0, 0, 0), -1)

out_dir = sys.argv[1]
img_path = os.path.join(out_dir, "source.png")
cv2.imwrite(img_path, img)

class FakeEngine:
    def __init__(self):
        self.calls = []
        self.counts = {}
    def _region(self, path):
        name = path.replace("\\\\", "/").split("/")[-1]
        for r in ("left_numbers", "center_numbers", "right_numbers", "bottom"):
            if r in name:
                return r
        return "unknown"
    def _res(self, texts, scores, polys, boxes):
        return [{"res": {"rec_texts": texts, "rec_scores": scores,
                         "rec_polys": polys, "rec_boxes": boxes}}]
    def predict(self, path):
        self.calls.append(path)
        region = self._region(path)
        self.counts[region] = self.counts.get(region, 0) + 1
        if self.counts[region] == 1 and region == "left_numbers":
            # 4 rows, 200px apart (well separated)
            return self._res(
                ["05", "15", "25", "35"], [0.9] * 4,
                [[[50, 100], [200, 100], [200, 150], [50, 150]],
                 [[50, 300], [200, 300], [200, 350], [50, 350]],
                 [[50, 500], [200, 500], [200, 550], [50, 550]],
                 [[50, 700], [200, 700], [200, 750], [50, 750]]],
                [[50, 100, 200, 150], [50, 300, 200, 350],
                 [50, 500, 200, 550], [50, 700, 200, 750]])
        if region == "left_numbers":
            # per-line re-OCR returns single item
            texts = ["05", "15", "25", "35"]
            idx = len([c for c in self.calls if "line_left_numbers" in c]) - 1
            idx = max(0, min(idx, 3))
            return self._res([texts[idx]], [0.9],
                             [[[50, 100], [200, 100], [200, 150], [50, 150]]],
                             [[50, 100, 200, 150]])
        return self._res([], [], [], [])

engine = FakeEngine()
crop_dir = os.path.join(out_dir, "crops")
result = segment_and_ocr(img_path, ocr_engine=engine, crop_output_dir=crop_dir)
print(json.dumps(result, ensure_ascii=False, default=str))
""").strip()


@pytest.mark.skipif(not _ocr_python(), reason="BETGUARD_OCR_PYTHON not configured")
class TestClosedLoopCorrection:
    def _run(self, ocr_python, tmp_path) -> dict:
        out = tmp_path / "flow"
        out.mkdir(exist_ok=True)
        repo = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        script = (
            f"import sys; sys.path.insert(0, {str(repo / 'tools' / 'vision')!r})\n"
            + _CLOSED_LOOP_SCRIPT
        )
        proc = subprocess.run(
            [ocr_python, "-c", script, str(out)],
            capture_output=True, text=True, timeout=120, shell=False, env=env,
        )
        assert proc.returncode == 0, f"worker failed: {proc.stderr[:2000]}"
        return json.loads(proc.stdout)

    def test_crops_non_overlapping_and_full_width(self, ocr_python, tmp_path):
        result = self._run(ocr_python, tmp_path)
        lines = [l for r in result["regions"] if r["name"] == "left_numbers" for l in r["lines"]]
        assert len(lines) == 4, f"expected 4 rows, got {len(lines)}"
        # Non-overlapping y ranges
        ys = sorted((l["crop_bbox"][1], l["crop_bbox"][1] + l["crop_bbox"][3]) for l in lines)
        for i in range(1, len(ys)):
            assert ys[i][0] >= ys[i - 1][1], f"crops overlap: {ys}"
        # Full region width: bbox x2-x1 == region width
        region = next(r for r in result["regions"] if r["name"] == "left_numbers")
        rw = region["pixel_bbox"][2]
        for l in lines:
            assert l["crop_bbox"][2] == rw, f"not full width: {l['crop_bbox']}"
        # All trainable (clean lines, high-res)
        assert all(l["trainable"] for l in lines), [l["unusable_reason"] for l in lines]

    def test_closed_loop_3_confirmed_1_skipped(self, ocr_python, tmp_path):
        result = self._run(ocr_python, tmp_path)
        crop_dir = str(tmp_path / "flow" / "crops")
        ws = str(tmp_path / "flow" / "ws")

        from betguard.vision.correction import (
            CorrectionRecord, CorrectionStatus, export_training_list,
            find_pending_crops, save_correction, summary,
        )
        pending = find_pending_crops(crop_dir, ws)
        assert len(pending) == 4, f"expected 4 pending, got {len(pending)}"

        # Confirm 3, skip 1 — via the same record path the server uses
        for i, c in enumerate(pending[:3]):
            save_correction(CorrectionRecord(
                crop_path=c["crop_path"], source_image="source.png", region=c["region"],
                bbox=c["bbox"], raw_ocr_text=c["raw_ocr_text"],
                corrected_text=f"{10 + i * 10:02d}", confidence=c["confidence"],
                status=CorrectionStatus.CONFIRMED, trainable=True,
            ), ws)
        save_correction(CorrectionRecord(
            crop_path=pending[3]["crop_path"], source_image="source.png",
            region=pending[3]["region"], bbox=pending[3]["bbox"],
            raw_ocr_text=pending[3]["raw_ocr_text"], confidence=pending[3]["confidence"],
            status=CorrectionStatus.SKIPPED, trainable=True,
        ), ws)

        s = summary(ws)
        assert s["confirmed"] == 3
        assert s["skipped"] == 1

        out = str(tmp_path / "flow" / "training.tsv")
        n = export_training_list(ws, out)
        assert n == 3
        with open(out, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        assert len(lines) == 3
        for line in lines:
            assert "\t" in line
            assert line.split("\t")[1] in ("10", "20", "30")

    def test_crop_traceable(self, ocr_python, tmp_path):
        result = self._run(ocr_python, tmp_path)
        lines = [l for r in result["regions"] if r["name"] == "left_numbers" for l in r["lines"]]
        for l in lines:
            assert l["crop_path"]
            assert os.path.isfile(l["crop_path"]), l["crop_path"]
            sidecar = l["crop_path"] + ".json"
            assert os.path.isfile(sidecar), sidecar
            with open(sidecar, encoding="utf-8") as f:
                meta = json.load(f)
            assert meta["source_image"]
            assert meta["region"] == "left_numbers"
            assert meta["bbox"]
            assert meta["trainable"] is True
