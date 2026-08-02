"""Test evaluation metrics: Levenshtein, CER, digits, lottery tokens, ranking."""

from __future__ import annotations

import pytest

from betguard.vision.evaluation import (
    character_error_rate,
    evaluate_characters,
    evaluate_digits,
    evaluate_lottery_tokens,
    evaluate_line_accuracy,
    evaluate_variant,
    levenshtein_distance,
    load_ground_truth,
    rank_by_ground_truth,
    compute_aggregate,
    _extract_lottery_tokens,
    _canonical_two_digit,
)


class TestLevenshtein:
    def test_identical(self):
        assert levenshtein_distance("abc", "abc") == 0

    def test_insertion(self):
        assert levenshtein_distance("abc", "abxc") == 1

    def test_deletion(self):
        assert levenshtein_distance("abc", "ac") == 1

    def test_substitution(self):
        assert levenshtein_distance("abc", "axc") == 1

    def test_chinese(self):
        assert levenshtein_distance("包", "回") == 1
        assert levenshtein_distance("05 09", "05 09") == 0

    def test_empty_expected(self):
        assert levenshtein_distance("", "abc") == 3

    def test_empty_actual(self):
        assert levenshtein_distance("abc", "") == 3

    def test_both_empty(self):
        assert levenshtein_distance("", "") == 0

    def test_deterministic(self):
        for _ in range(10):
            assert levenshtein_distance("05 09 15", "05 18 15") == 2

    def test_long_string(self):
        s = "a" * 1000
        assert levenshtein_distance(s, s) == 0


class TestCER:
    def test_identical(self):
        assert character_error_rate("abc", "abc") == 0.0

    def test_empty_both(self):
        assert character_error_rate("", "") == 0.0

    def test_empty_expected(self):
        assert character_error_rate("", "abc") == 1.0

    def test_empty_actual(self):
        assert character_error_rate("abc", "") == 1.0

    def test_partial(self):
        assert character_error_rate("abc", "axc") == 1/3


class TestCharacterMetrics:
    def test_exact_match(self):
        r = evaluate_characters("abc", "abc")
        assert r.exact_match is True
        assert r.cer == 0.0


class TestDigitMetrics:
    def test_perfect(self):
        r = evaluate_digits("05 09 15", "05 09 15")
        assert r.precision == 1.0
        assert r.recall == 1.0
        assert r.f1 == 1.0

    def test_partial(self):
        r = evaluate_digits("123", "12")
        assert r.matched_count == 2

    def test_duplicates(self):
        r = evaluate_digits("11", "1")
        assert r.expected_count == 2
        assert r.actual_count == 1
        assert r.matched_count == 1

    def test_no_digits(self):
        r = evaluate_digits("abc", "def")
        assert r.precision == 1.0
        assert r.recall == 1.0

    def test_empty_expected(self):
        r = evaluate_digits("", "12")
        assert r.recall == 0.0

    def test_empty_actual(self):
        r = evaluate_digits("12", "")
        assert r.precision == 0.0


class TestLotteryTokens:
    def test_canonical(self):
        assert _canonical_two_digit("1") == "01"
        assert _canonical_two_digit("5") == "05"
        assert _canonical_two_digit("09") == "09"
        assert _canonical_two_digit("39") == "39"

    def test_range_filter(self):
        tokens = _extract_lottery_tokens("05 39 40 00")
        assert "05" in tokens
        assert "39" in tokens
        assert "40" not in tokens
        assert "00" not in tokens

    def test_three_digit_excluded(self):
        tokens = _extract_lottery_tokens("123 05 456")
        assert tokens == ["05"]

    def test_perfect_token_match(self):
        r = evaluate_lottery_tokens("05 09 15", "05 09 15")
        assert r.precision == 1.0
        assert r.f1 == 1.0

    def test_missing_tokens(self):
        r = evaluate_lottery_tokens("05 09 15", "05")
        assert "09" in r.missing_tokens or "15" in r.missing_tokens

    def test_extra_tokens(self):
        r = evaluate_lottery_tokens("05", "05 09 15")
        assert len(r.extra_tokens) >= 2

    def test_duplicate_tokens_multiset(self):
        r = evaluate_lottery_tokens("05 05", "05")
        assert r.expected_count == 2
        assert r.actual_count == 1
        assert r.matched_count == 1

    def test_f1_zero(self):
        r = evaluate_lottery_tokens("05 09", "")
        assert r.f1 == 0.0

    def test_note_present(self):
        r = evaluate_lottery_tokens("05", "05")
        d = r.to_dict()
        assert "note" in d
        assert "NOT" in d["note"]


class TestLineAccuracyMetrics:
    def test_perfect_line_number_and_multiplier_accuracy(self):
        metrics = evaluate_line_accuracy(
            ["06 13 23 22 x1"],
            ["06 13 23 22 x1"],
        )
        assert metrics.line_exact_accuracy == 1.0
        assert metrics.number_exact_accuracy == 1.0
        assert metrics.number_multiplier_exact_accuracy == 1.0
        assert metrics.human_correction_line_ratio == 0.0

    def test_number_correct_but_multiplier_wrong_needs_correction(self):
        metrics = evaluate_line_accuracy(
            ["06 13 x1"],
            ["06 13 x2"],
        )
        assert metrics.number_exact_accuracy == 1.0
        assert metrics.number_multiplier_exact_accuracy == 0.0
        assert metrics.human_correction_line_ratio == 1.0

    def test_uncertain_line_counts_as_needing_human_correction(self):
        metrics = evaluate_line_accuracy(
            ["06 13 x1"],
            ["06 13 x1"],
            uncertain_lines={0},
        )
        assert metrics.line_exact_accuracy == 1.0
        assert metrics.human_correction_line_ratio == 1.0


class TestRanking:
    def _variant(self, text: str, rotation: int = 0, pp: str = "orig", det: str = "bal", ms: float = 1000, **extra) -> dict:
        v = {
            "rotation": rotation, "preprocess_profile": pp, "detection_profile": det,
            "status": "completed", "raw_text": text, "elapsed_ms": ms,
        }
        v.update(extra)
        return v

    def test_best_by_f1(self):
        variants = [
            self._variant("05 09 15"),    # perfect match
            self._variant("05 xx 15"),    # partial
            self._variant("xx xx xx"),    # bad
        ]
        gt = "05 09 15"
        best_id, ranked = rank_by_ground_truth(variants, gt)
        assert "orig" in best_id

    def test_heuristic_not_overwritten(self):
        variants = [self._variant("05 09", heuristic_score=100)]
        gt = "05 09"
        best_id, ranked = rank_by_ground_truth(variants, gt)
        assert ranked[0].get("heuristic_score") == 100

    def test_failed_excluded(self):
        variants = [
            {"status": "failed", "raw_text": "", "rotation": 0, "preprocess_profile": "x", "detection_profile": "y"},
            self._variant("05", heuristic_score=10),
        ]
        best_id, ranked = rank_by_ground_truth(variants, "05")
        assert len(ranked) == 2

    def test_stable_tie_break(self):
        v1 = self._variant("05", ms=100)
        v2 = self._variant("05", ms=200)
        best_id, ranked = rank_by_ground_truth([v1, v2], "05")
        assert ranked[0]["elapsed_ms"] == 100


class TestGroundTruthFile:
    def test_crlf_normalized(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_bytes(b"05\r\n09\r\n15\r\n")
        result = load_ground_truth(str(f))
        assert "\r" not in result
        assert "\n" in result

    def test_bom_removed(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_bytes(b"\xef\xbb\xbf05 09 15")
        result = load_ground_truth(str(f))
        assert result == "05 09 15"

    def test_intra_line_whitespace_preserved(self, tmp_path):
        f = tmp_path / "gt.txt"
        content = "05   09"
        f.write_text(content, encoding="utf-8")
        result = load_ground_truth(str(f))
        assert result == content

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_ground_truth("/nonexistent/file.txt")

    def test_url_rejected(self):
        with pytest.raises(ValueError, match="URL"):
            load_ground_truth("http://example.com/gt.txt")

    def test_symlink_rejected(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("test")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(f)
            with pytest.raises(ValueError, match="Symlink"):
                load_ground_truth(str(link))
        except OSError:
            pytest.skip("Symlink not supported on this platform")


class TestAggregateMetrics:
    def test_basic(self):
        samples = [
            {"status": "completed", "elapsed_ms": 1000, "_evaluation": {
                "cer": 0.1, "exact_match": True,
                "digit_metrics": {"matched_count": 5, "expected_count": 5, "actual_count": 5},
                "lottery_token_metrics": {"matched_count": 3, "expected_count": 3, "actual_count": 3, "f1": 1.0},
            }},
            {"status": "completed", "elapsed_ms": 2000, "_evaluation": {
                "cer": 0.3, "exact_match": False,
                "digit_metrics": {"matched_count": 2, "expected_count": 5, "actual_count": 3},
                "lottery_token_metrics": {"matched_count": 1, "expected_count": 3, "actual_count": 2, "f1": 0.4},
            }},
        ]
        ag = compute_aggregate(samples)
        assert ag.sample_count == 2
        assert ag.completed_count == 2
        assert ag.exact_match_count == 1
        assert ag.mean_cer == pytest.approx(0.2)

    def test_failed_not_silently_excluded(self):
        samples = [
            {"status": "failed", "elapsed_ms": 0},
            {"status": "completed", "elapsed_ms": 1000, "_evaluation": {
                "cer": 0.0, "exact_match": True,
                "digit_metrics": {"matched_count": 1, "expected_count": 1, "actual_count": 1},
                "lottery_token_metrics": {"matched_count": 1, "expected_count": 1, "actual_count": 1, "f1": 1.0},
            }},
        ]
        ag = compute_aggregate(samples)
        assert ag.failed_count == 1
        assert ag.completed_count == 1
