"""Test ROI evaluation metrics and failure classification."""

from __future__ import annotations

import pytest

from betguard.vision.roi_eval import (
    classify_failure,
    evaluate_region,
    extract_multipliers,
    numbers_match,
    numbers_plus_multiplier_match,
    pick_best_version,
    summarize,
)


class TestMultipliers:
    def test_extract_x(self):
        assert extract_multipliers("01 20 x1") == ["1"]

    def test_extract_times_symbol(self):
        assert extract_multipliers("05 15 ×2") == ["2"]

    def test_none(self):
        assert extract_multipliers("01 20") == []


class TestNumbersMatch:
    def test_exact(self):
        assert numbers_match("01 20 x1", "01 20 x1") is True

    def test_order_insensitive(self):
        assert numbers_match("01 20", "20 01") is True

    def test_missing_number(self):
        assert numbers_match("01 20", "01") is False

    def test_wrong_number(self):
        assert numbers_match("01 20", "01 21") is False

    def test_duplicate_multiset(self):
        assert numbers_match("05 05", "05 05") is True
        assert numbers_match("05 05", "05") is False


class TestNumbersPlusMultiplier:
    def test_matching_multiplier(self):
        assert numbers_plus_multiplier_match("01 20 x1", "01 20 x1") is True

    def test_mismatched_multiplier(self):
        assert numbers_plus_multiplier_match("01 20 x1", "01 20 x2") is False

    def test_gt_no_multiplier_ignored(self):
        assert numbers_plus_multiplier_match("01 20", "01 20") is True

    def test_gt_multiplier_ocr_missing(self):
        assert numbers_plus_multiplier_match("01 20 x1", "01 20") is False


class TestClassifyFailure:
    def test_match(self):
        assert classify_failure("01 20", "01 20", 2) == "match"

    def test_detection_failure_no_items(self):
        assert classify_failure("01 20", "", 0) == "region_detection_failure"

    def test_detection_failure_blank_text(self):
        assert classify_failure("01 20", "  ", 1) == "region_detection_failure"

    def test_parser_failure_digits_but_no_tokens(self):
        # OCR saw digits "999" but token extraction (01-39) finds nothing
        assert classify_failure("01 20", "999", 1) == "parser_failure"

    def test_recognition_failure(self):
        assert classify_failure("01 20", "01 21", 2) == "ocr_recognition_failure"

    def test_recognition_failure_garbage(self):
        assert classify_failure("01 20", "包 回", 2) == "ocr_recognition_failure"


class TestEvaluateRegion:
    def test_perfect(self):
        ev = evaluate_region("01 20 x1", "01 20 x1", 3)
        assert ev.exact_group_match is True
        assert ev.number_exact_match is True
        assert ev.number_plus_multiplier_match is True
        assert ev.token_f1 == 1.0
        assert ev.cer == 0.0
        assert ev.human_correction_required is False
        assert ev.failure_type == "match"

    def test_wrong_numbers(self):
        ev = evaluate_region("01 20", "01 21", 2)
        assert ev.number_exact_match is False
        assert ev.human_correction_required is True
        assert ev.failure_type == "ocr_recognition_failure"

    def test_missed_region_not_excluded(self):
        ev = evaluate_region("01 20", "", 0)
        assert ev.failure_type == "region_detection_failure"
        assert ev.human_correction_required is True
        assert ev.token_f1 == 0.0


class TestPickBestVersion:
    def test_picks_highest_f1(self):
        pv = {
            "original": {"ocr_text": "01 21", "item_count": 2},
            "contrast": {"ocr_text": "01 20", "item_count": 2},
            "grid_suppressed": {"ocr_text": "xx", "item_count": 1},
        }
        best = pick_best_version(pv, gt_text="01 20")
        assert best["version"] == "contrast"
        assert best["ocr_text"] == "01 20"

    def test_tie_prefers_original(self):
        pv = {
            "original": {"ocr_text": "01 20", "item_count": 2},
            "contrast": {"ocr_text": "01 20", "item_count": 2},
        }
        best = pick_best_version(pv, gt_text="01 20")
        assert best["version"] == "original"

    def test_empty(self):
        best = pick_best_version({}, gt_text="01 20")
        assert best["version"] == "original"


class TestSummarize:
    def test_counts(self):
        evs = [
            evaluate_region("01 20", "01 20", 2),   # match
            evaluate_region("05 15", "05 16", 2),   # recognition failure
            evaluate_region("07 19", "", 0),        # detection failure
        ]
        s = summarize(evs)
        assert s.region_count == 3
        assert s.unmatched_region_count == 2
        assert s.number_exact_match_count == 1
        assert s.human_correction_required_count == 2
        assert s.failure_counts["ocr_recognition_failure"] == 1
        assert s.failure_counts["region_detection_failure"] == 1
        # F1s: 1.0 (match), 0.5 (partial), 0.0 (detection failure) → mean 0.5
        assert s.mean_token_f1 == pytest.approx(0.5, abs=1e-6)
