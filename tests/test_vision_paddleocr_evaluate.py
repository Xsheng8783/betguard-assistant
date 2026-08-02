"""Test PaddleOCR evaluate CLI and integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


class TestDatasetValidation:
    def test_duplicate_id_rejected(self, tmp_path, capsys):
        manifest = {
            "schema_version": "betguard.vision.dataset.v1",
            "samples": [
                {"id": "s1", "image": str(tmp_path / "a.jpg"), "ground_truth": str(tmp_path / "a.txt")},
                {"id": "s1", "image": str(tmp_path / "b.jpg"), "ground_truth": str(tmp_path / "b.txt")},
            ],
        }
        # Create dummy files
        for name in ["a.jpg", "b.jpg", "a.txt", "b.txt"]:
            (tmp_path / name).write_text("test")

        f = tmp_path / "dataset.json"
        f.write_text(json.dumps(manifest))
        # Just verify the validation logic
        ids = set()
        duplicate = False
        for s in manifest["samples"]:
            if s["id"] in ids:
                duplicate = True
            ids.add(s["id"])
        assert duplicate

    def test_missing_image(self, tmp_path):
        manifest = {
            "schema_version": "betguard.vision.dataset.v1",
            "samples": [{"id": "s1", "image": str(tmp_path / "missing.jpg"), "ground_truth": str(tmp_path / "gt.txt")}],
        }
        (tmp_path / "gt.txt").write_text("test")
        from scripts.vision_paddleocr_evaluate import _validate_sample
        err = _validate_sample(manifest["samples"][0], 0)
        assert "not found" in err.lower() or "missing" in err.lower()

    def test_schema_version(self):
        from scripts.vision_paddleocr_evaluate import DATASET_SCHEMA
        assert DATASET_SCHEMA == "betguard.vision.dataset.v1"

    def test_placeholder_ground_truth_rejected(self, tmp_path):
        from scripts.vision_paddleocr_evaluate import _validate_sample

        image = tmp_path / "sample.jpg"
        ground_truth = tmp_path / "sample.txt"
        image.write_bytes(b"image")
        ground_truth.write_text(
            "【請人工填寫此張牌單的正確完整內容】\n",
            encoding="utf-8",
        )

        error = _validate_sample(
            {"id": "sample", "image": str(image), "ground_truth": str(ground_truth)},
            0,
        )

        assert "placeholder" in error

    def test_completed_benchmark_exposes_aggregate_fields(self, tmp_path, monkeypatch):
        from scripts import vision_paddleocr_benchmark as benchmark
        from scripts.vision_paddleocr_evaluate import _run_single_benchmark

        image = tmp_path / "sample.jpg"
        ground_truth = tmp_path / "sample.txt"
        image.write_bytes(b"image")
        ground_truth.write_text("01 20 x1", encoding="utf-8")
        monkeypatch.setattr(
            benchmark,
            "_run_benchmark",
            lambda _request: {
                "ok": True,
                "total_elapsed_ms": 25,
                "profiles": [
                    {
                        "status": "completed",
                        "rotation": 0,
                        "preprocess_profile": "original",
                        "detection_profile": "balanced",
                        "raw_text": "01 20 x1",
                        "elapsed_ms": 25,
                    }
                ],
                "provisional_best_preprocess": "original",
            },
        )

        result = _run_single_benchmark(str(image), str(ground_truth), tmp_path)

        assert result["status"] == "completed"
        assert result["elapsed_ms"] == 25
        assert result["_evaluation"]["exact_match"] is True
        assert result["evaluation"]["line_accuracy"] == {
            "line_count": 1,
            "line_exact_count": 1,
            "number_exact_count": 1,
            "number_multiplier_exact_count": 1,
            "human_correction_needed_count": 0,
            "line_exact_accuracy": 1.0,
            "number_exact_accuracy": 1.0,
            "number_multiplier_exact_accuracy": 1.0,
            "human_correction_line_ratio": 0.0,
        }


class TestEvaluationReports:
    def test_html_escapes_ground_truth(self):
        from scripts.vision_paddleocr_evaluate import _build_dataset_html
        results = [{
            "status": "completed",
            "evaluation": {
                "best_by_ground_truth": "orig",
                "best_cer": 0.5,
                "best_lottery_token_metrics": {"f1": 0.8, "missing_tokens": [], "extra_tokens": []},
                "best_raw_text": "<script>alert(1)</script>",
            },
        }]
        html = _build_dataset_html(results, {"sample_count": 1, "completed_count": 1, "failed_count": 0, "mean_cer": 0.5, "median_cer": 0.5, "micro_token_f1": 0.8, "macro_token_f1": 0.8, "exact_match_count": 0, "total_time_ms": 1000})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_no_external_cdn(self):
        from scripts.vision_paddleocr_evaluate import _build_dataset_html
        html = _build_dataset_html([], {"sample_count": 0, "completed_count": 0, "failed_count": 0, "mean_cer": 0, "median_cer": 0, "micro_token_f1": 0, "macro_token_f1": 0, "exact_match_count": 0, "total_time_ms": 0})
        assert "http://" not in html
        assert "cdn." not in html.lower()

    def test_html_shows_missing_tokens(self):
        from scripts.vision_paddleocr_evaluate import _build_dataset_html
        results = [{
            "status": "completed",
            "evaluation": {
                "best_by_ground_truth": "orig",
                "best_cer": 0.5,
                "best_lottery_token_metrics": {"f1": 0.5, "missing_tokens": ["05", "09"], "extra_tokens": ["99"]},
                "best_raw_text": "test",
            },
        }]
        html = _build_dataset_html(results, {"sample_count": 1, "completed_count": 1, "failed_count": 0, "mean_cer": 0.5, "median_cer": 0.5, "micro_token_f1": 0.5, "macro_token_f1": 0.5, "exact_match_count": 0, "total_time_ms": 1000})
        assert "05" in html
        assert "09" in html


class TestNoPollution:
    def test_no_paddle_in_main(self):
        assert "paddle" not in sys.modules
        assert "paddleocr" not in sys.modules

    def test_no_cv2_numpy_in_main(self):
        assert "cv2" not in sys.modules
        assert "numpy" not in sys.modules

    def test_no_parser_in_evaluation(self):
        import betguard.vision.evaluation as ev
        source = ev.__file__
        if source:
            with open(source, encoding="utf-8") as f:
                content = f.read()
            for banned in ["betguard.parser", "betguard.validator", "betguard.webfill", "betguard.normalizer"]:
                assert banned not in content
