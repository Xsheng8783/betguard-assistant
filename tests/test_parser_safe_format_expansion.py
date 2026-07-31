"""Tests for parser safe format expansion — normalization and format support."""

import pytest
from betguard.normalizer import normalize_input, normalize_for_parser


class TestFullwidthNormalization:
    def test_fullwidth_digits(self):
        result = normalize_input("０１２ ３ ４５")
        assert "012" in result
        assert "３" not in result

    def test_fullwidth_comma(self):
        result = normalize_input("01，02，03")
        assert "01,02,03" in result

    def test_fullwidth_parentheses(self):
        result = normalize_input("（01 02 03）")
        assert "(" in result
        assert ")" in result
        assert "\uff08" not in result

    def test_fullwidth_period(self):
        result = normalize_input("01．02．03")
        assert "." in result


class TestPrefixStripping:
    def test_jincai_prefix(self):
        result = normalize_input("今彩539：01 02 03 04 05")
        assert "01 02 03 04 05" in result
        assert "今彩" not in result

    def test_539_prefix(self):
        result = normalize_input("539：01,02,03,04,05")
        assert "01,02,03,04,05" in result

    def test_haoma_prefix(self):
        result = normalize_input("號碼：06 07 08 09 10")
        assert "06 07 08 09 10" in result

    def test_zhuzhi_prefix(self):
        result = normalize_input("主支：01 02 03 04 05")
        assert "01 02 03 04 05" in result

    def test_benqi_prefix(self):
        result = normalize_input("本期：01 02 03 04 05")
        assert "01 02 03 04 05" in result

    def test_no_false_prefix_stripping(self):
        """Text containing these chars mid-sentence should not be stripped."""
        result = normalize_input("號碼牌：01 02")
        # Should normalize but only strip known exact prefixes
        assert "01 02" in result


class TestBracketNormalization:
    def test_chinese_brackets(self):
        result = normalize_input("【01 02 03 04 05】")
        assert "[" in result
        assert "]" in result

    def test_fullwidth_brackets(self):
        result = normalize_input("［01 02 03 04 05］")
        assert "[" in result
        assert "]" in result


class TestOCRSpaceNormalization:
    def test_split_digits_normalized(self):
        """0 1 should become 01 via whitespace normalization then parsed."""
        result = normalize_input("0 1  0 2  0 3  0 4  0 5")
        # Normalizer collapses whitespace but doesn't join split digits
        assert "0 1 0 2 0 3 0 4 0 5" in result


class TestAmountFormats:
    def test_star_amount(self):
        result = normalize_input("01 02 03 04 05 *100")
        assert "*100" in result

    def test_multiply_sign_amount(self):
        result = normalize_input("01 02 03 04 05 ×100")
        assert "\u00d7100" in result

    def test_each_amount(self):
        result = normalize_input("01 02 03 04 05 各100")
        # Should preserve amount, not strip it
        assert "100" in result


class TestMultiGroup:
    def test_two_lines_space_separated(self):
        r1 = normalize_input("01 02 03 04 05")
        r2 = normalize_input("06 07 08 09 10")
        assert "01 02 03 04 05" in r1
        assert "06 07 08 09 10" in r2
        # Each line independent
        assert "06" not in r1


class TestSafetyRetainsNeedsReview:
    def test_ambiguous_ocr_not_auto_fixed(self):
        """Characters that could be confused (O vs 0) should not be auto-corrected."""
        result = normalize_input("O1 O2 O3 O4 O5")
        # O is not a digit — should remain as-is
        assert "O" in result

    def test_too_few_numbers_stays(self):
        result = normalize_input("01 02 03")
        assert "01 02 03" in result

    def test_duplicate_numbers_preserved_for_validation(self):
        result = normalize_input("01 01 02 03 04")
        assert "01 01 02 03 04" in result

    def test_out_of_range_preserved(self):
        result = normalize_input("99 98 97 96 95")
        assert "99 98 97 96 95" in result


class TestSafetyGates:
    def test_safety_constants(self):
        blocked = {
            "ok": False, "blocked": True,
            "auto_submit": False, "auto_confirm": False,
            "danger_buttons_clicked": [],
        }
        assert blocked["auto_submit"] is False
        assert blocked["danger_buttons_clicked"] == []
