"""Test image quality assessment, strategy fallback, and correction workflow."""

from __future__ import annotations

import json
import os

import pytest

from betguard.vision.correction import (
    CorrectionRecord,
    CorrectionStatus,
    export_training_list,
    find_pending_crops,
    load_corrections,
    save_correction,
    scan_crop_meta,
    summary,
)
from betguard.vision.quality import (
    assess_image_quality,
    decide_ocr_strategy,
    estimate_text_height_from_boxes,
    quality_gate,
)


class TestQualityAssessment:
    def test_low_resolution_image_warned(self):
        q = assess_image_quality(image_width=346, image_height=477)
        assert q.low_resolution is True
        assert any("LOW_RESOLUTION" in w for w in q.warnings)

    def test_high_resolution_no_warning(self):
        q = assess_image_quality(image_width=1108, image_height=1477)
        assert q.low_resolution is False
        assert q.warnings == []

    def test_small_crop_warned(self):
        q = assess_image_quality(image_width=1108, image_height=1477, crop_width=100, crop_height=40)
        assert any("LOW_RESOLUTION" in w for w in q.warnings)

    def test_blurry_warned(self):
        q = assess_image_quality(image_width=800, image_height=600, blur_score=20.0)
        assert q.blurry is True
        assert any("BLURRY" in w for w in q.warnings)

    def test_sharp_not_blurry(self):
        q = assess_image_quality(image_width=800, image_height=600, blur_score=500.0)
        assert q.blurry is False

    def test_small_text_warned(self):
        q = assess_image_quality(image_width=800, image_height=600, estimated_text_height=8.0)
        assert q.text_too_small is True
        assert any("TEXT_TOO_SMALL" in w for w in q.warnings)

    def test_text_height_estimated_from_boxes(self):
        boxes = [[0, 0, 50, 30], [0, 40, 50, 70], [0, 80, 50, 100]]
        h = estimate_text_height_from_boxes(boxes)
        assert h == 30.0

    def test_text_height_none_for_empty(self):
        assert estimate_text_height_from_boxes([]) is None

    def test_quality_gate_blocks(self):
        q = assess_image_quality(image_width=200, image_height=300)
        gate = quality_gate(q)
        assert gate["blocked"] is True
        assert gate["reasons"]

    def test_quality_gate_passes_clean(self):
        q = assess_image_quality(image_width=1108, image_height=1477, blur_score=400.0, estimated_text_height=30.0)
        gate = quality_gate(q)
        assert gate["pass"] is True


class TestStrategyFallback:
    def test_layout_better_wins(self):
        r = decide_ocr_strategy(whole_image_f1=0.3, layout_f1=0.5)
        assert r["strategy"] == "layout"

    def test_whole_image_better_falls_back(self):
        r = decide_ocr_strategy(whole_image_f1=0.6, layout_f1=0.2)
        assert r["strategy"] == "whole-image"
        assert "fall" in r["reason"].lower()

    def test_equal_prefers_layout(self):
        r = decide_ocr_strategy(whole_image_f1=0.4, layout_f1=0.4)
        assert r["strategy"] == "layout"

    def test_missing_layout_falls_back(self):
        r = decide_ocr_strategy(whole_image_f1=0.4, layout_f1=None)
        assert r["strategy"] == "whole-image"

    def test_missing_whole_uses_layout(self):
        r = decide_ocr_strategy(whole_image_f1=None, layout_f1=0.4)
        assert r["strategy"] == "layout"

    def test_both_missing_whole_image(self):
        r = decide_ocr_strategy(whole_image_f1=None, layout_f1=None)
        assert r["strategy"] == "whole-image"


class TestCorrectionRecords:
    def _record(self, **kw) -> CorrectionRecord:
        defaults = dict(
            crop_path="C:/crops/left_numbers-row00.png",
            source_image="C:/samples/sample.jpg",
            region="left_numbers",
            bbox=[10, 10, 100, 30],
            raw_ocr_text="05",
            corrected_text="",
            status=CorrectionStatus.PENDING,
        )
        defaults.update(kw)
        return CorrectionRecord(**defaults)

    def test_confirmed_eligible(self, tmp_path):
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"fake")
        r = self._record(crop_path=str(crop), corrected_text="05", status=CorrectionStatus.CONFIRMED)
        assert r.is_training_eligible() is True

    def test_pending_not_eligible(self, tmp_path):
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"fake")
        r = self._record(crop_path=str(crop), corrected_text="05", status=CorrectionStatus.PENDING)
        assert r.is_training_eligible() is False

    def test_skipped_not_eligible(self, tmp_path):
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"fake")
        r = self._record(crop_path=str(crop), status=CorrectionStatus.SKIPPED)
        assert r.is_training_eligible() is False

    def test_confirmed_empty_text_not_eligible(self, tmp_path):
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"fake")
        r = self._record(crop_path=str(crop), corrected_text="", status=CorrectionStatus.CONFIRMED)
        assert r.is_training_eligible() is False

    def test_save_and_load(self, tmp_path):
        r = self._record(corrected_text="05", status=CorrectionStatus.CONFIRMED)
        save_correction(r, str(tmp_path))
        loaded = load_corrections(str(tmp_path))
        assert len(loaded) == 1
        assert loaded[0].corrected_text == "05"
        assert loaded[0].status == CorrectionStatus.CONFIRMED

    def test_export_only_confirmed(self, tmp_path):
        crop1 = tmp_path / "crop1.png"
        crop1.write_bytes(b"a")
        crop2 = tmp_path / "crop2.png"
        crop2.write_bytes(b"b")
        save_correction(self._record(crop_path=str(crop1), corrected_text="05", status=CorrectionStatus.CONFIRMED), str(tmp_path))
        save_correction(self._record(crop_path=str(crop2), corrected_text="09", status=CorrectionStatus.SKIPPED), str(tmp_path))

        out = tmp_path / "train.tsv"
        n = export_training_list(str(tmp_path), str(out))
        assert n == 1
        content = out.read_text(encoding="utf-8")
        assert "05" in content
        assert "09" not in content
        # Format: crop_path<TAB>corrected_text
        lines = [l for l in content.splitlines() if l.strip()]
        assert "\t" in lines[0]

    def test_export_uses_forward_slashes(self, tmp_path):
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"a")
        save_correction(self._record(crop_path=str(crop), corrected_text="15", status=CorrectionStatus.CONFIRMED), str(tmp_path))
        out = tmp_path / "train.tsv"
        export_training_list(str(tmp_path), str(out))
        content = out.read_text(encoding="utf-8")
        assert "/" in content.split("\t")[0]
        assert "\\" not in content.split("\t")[0]

    def test_ocr_guess_not_used_as_label(self, tmp_path):
        """OCR raw text must never be exported as training label without confirmation."""
        crop = tmp_path / "crop.png"
        crop.write_bytes(b"a")
        # Pending record with OCR text but no human confirmation
        save_correction(self._record(crop_path=str(crop), raw_ocr_text="包", corrected_text=""), str(tmp_path))
        out = tmp_path / "train.tsv"
        n = export_training_list(str(tmp_path), str(out))
        assert n == 0
        assert not out.exists() or out.read_text(encoding="utf-8").strip() == ""

    def test_summary_counts(self, tmp_path):
        crop = tmp_path / "c.png"
        crop.write_bytes(b"a")
        save_correction(self._record(crop_path=str(crop), corrected_text="05", status=CorrectionStatus.CONFIRMED), str(tmp_path))
        save_correction(self._record(crop_path=str(crop), status=CorrectionStatus.SKIPPED), str(tmp_path))
        s = summary(str(tmp_path))
        assert s["confirmed"] == 1
        assert s["skipped"] == 1


class TestTraceability:
    def test_record_has_source_and_bbox(self):
        r = CorrectionRecord(
            crop_path="crops/l.png", source_image="samples/a.jpg",
            region="left_numbers", bbox=[10, 20, 80, 30],
            raw_ocr_text="05", corrected_text="05",
        )
        d = r.to_dict()
        assert d["source_image"] == "samples/a.jpg"
        assert d["region"] == "left_numbers"
        assert d["bbox"] == [10, 20, 80, 30]
        assert d["crop_path"] == "crops/l.png"

    def test_find_pending_excludes_done(self, tmp_path):
        crop_dir = tmp_path / "crops"
        os.makedirs(crop_dir / "left_numbers")
        crop = crop_dir / "left_numbers" / "left_numbers-row00.png"
        crop.write_bytes(b"img")
        (crop_dir / "left_numbers" / "left_numbers-row00.png.json").write_text(
            json.dumps({"region": "left_numbers", "bbox": [1, 2, 3, 4], "raw_ocr_text": "05", "confidence": 0.9}),
            encoding="utf-8",
        )
        pending = find_pending_crops(str(crop_dir), str(tmp_path / "ws"))
        assert len(pending) == 1
        assert pending[0]["region"] == "left_numbers"
        assert pending[0]["raw_ocr_text"] == "05"

        # After confirming, no longer pending
        save_correction(CorrectionRecord(
            crop_path=str(crop), source_image="a.jpg", region="left_numbers",
            bbox=[1, 2, 3, 4], corrected_text="05", status=CorrectionStatus.CONFIRMED,
        ), str(tmp_path / "ws"))
        pending = find_pending_crops(str(crop_dir), str(tmp_path / "ws"))
        assert len(pending) == 0


class TestUnusableCrops:
    def _make_crop(self, tmp_path, name: str, trainable: bool, reason: str = "") -> str:
        crop_dir = tmp_path / "crops" / "left_numbers"
        os.makedirs(crop_dir, exist_ok=True)
        crop = crop_dir / f"{name}.png"
        crop.write_bytes(b"img")
        (crop_dir / f"{name}.png.json").write_text(json.dumps({
            "crop_path": str(crop), "source_image": "a.jpg", "region": "left_numbers",
            "bbox": [1, 2, 3, 4], "raw_ocr_text": "05", "confidence": 0.5,
            "trainable": trainable, "unusable_reason": reason,
        }), encoding="utf-8")
        return str(crop)

    def test_unusable_not_in_pending(self, tmp_path):
        self._make_crop(tmp_path, "row00", trainable=True)
        self._make_crop(tmp_path, "row01", trainable=False, reason="blank_ocr")
        pending = find_pending_crops(str(tmp_path / "crops"), str(tmp_path / "ws"))
        assert len(pending) == 1
        assert "row00" in pending[0]["crop_path"]

    def test_scan_counts(self, tmp_path):
        self._make_crop(tmp_path, "row00", trainable=True)
        self._make_crop(tmp_path, "row01", trainable=False, reason="blank_ocr")
        self._make_crop(tmp_path, "row02", trainable=False, reason="crop_too_small:20x10")
        scan = scan_crop_meta(str(tmp_path / "crops"))
        assert scan["trainable"] == 1
        assert scan["excluded"] == 2
        assert scan["reasons"].get("blank_ocr") == 1
        assert scan["reasons"].get("crop_too_small:20x10") == 1

    def test_non_trainable_cannot_be_confirmed_eligible(self, tmp_path):
        crop = self._make_crop(tmp_path, "row01", trainable=False, reason="blank_ocr")
        rec = CorrectionRecord(
            crop_path=crop, source_image="a.jpg", region="left_numbers",
            bbox=[1, 2, 3, 4], corrected_text="05", status=CorrectionStatus.CONFIRMED,
            trainable=False, unusable_reason="blank_ocr",
        )
        assert rec.is_training_eligible() is False

    def test_non_trainable_never_exported(self, tmp_path):
        crop = self._make_crop(tmp_path, "row01", trainable=False, reason="blank_ocr")
        save_correction(CorrectionRecord(
            crop_path=crop, source_image="a.jpg", region="left_numbers",
            bbox=[1, 2, 3, 4], corrected_text="05", status=CorrectionStatus.CONFIRMED,
            trainable=False, unusable_reason="blank_ocr",
        ), str(tmp_path / "ws"))
        out = tmp_path / "train.tsv"
        n = export_training_list(str(tmp_path / "ws"), str(out))
        assert n == 0
        assert not out.exists() or out.read_text(encoding="utf-8").strip() == ""

    def test_zero_trainable_page_message(self, tmp_path):
        from scripts.vision_correction_server import build_page
        self._make_crop(tmp_path, "row01", trainable=False, reason="low_resolution_original")
        html = build_page([], str(tmp_path / "ws"), str(tmp_path / "crops"))
        assert "此圖片沒有可用的訓練裁切" in html
        assert "排除" in html
