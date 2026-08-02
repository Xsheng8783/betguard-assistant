"""Test closed character-set normalizer and validator."""

from __future__ import annotations

import pytest

from betguard.vision.closed_set import (
    ClosedSetIssue,
    normalize_fullwidth_digits,
    normalize_symbols,
    normalize_token,
    normalize_whitespace,
    validate_group_structure,
    validate_label,
    validate_layout,
    validate_line,
    validate_multiplier,
    validate_number_token,
)


class TestNormalizeSymbols:
    def test_capital_x_to_multiply(self):
        assert normalize_symbols("X") == "×"

    def test_lower_x_to_multiply(self):
        assert normalize_symbols("x") == "×"

    def test_multiply_stays(self):
        assert normalize_symbols("×") == "×"

    def test_in_context(self):
        assert normalize_symbols("18 26 x1") == "18 26 ×1"


class TestNormalizeFullwidth:
    def test_fullwidth_digits(self):
        assert normalize_fullwidth_digits("０１２３") == "0123"

    def test_halfwidth_unchanged(self):
        assert normalize_fullwidth_digits("0123") == "0123"


class TestNormalizeWhitespace:
    def test_collapse(self):
        assert normalize_whitespace("05   09\n15") == "05 09 15"

    def test_strip(self):
        assert normalize_whitespace("  05  ") == "05"


class TestNormalizeToken:
    def test_clean_token(self):
        r = normalize_token("05")
        assert r.normalized == "05"
        assert r.requires_human_confirmation is False

    def test_english_letter_flagged(self):
        r = normalize_token("3B")
        assert r.normalized is None
        assert r.requires_human_confirmation is True
        assert r.reason == ClosedSetIssue.DISALLOWED_CHARACTER.value
        assert "38" in r.candidates  # candidate only, never auto-applied

    def test_O7_not_silently_fixed(self):
        r = normalize_token("O7")
        assert r.normalized is None
        assert r.requires_human_confirmation is True
        assert "07" in r.candidates

    def test_chinese_outside_closed_set_flagged(self):
        r = normalize_token("包")
        assert r.requires_human_confirmation is True
        assert r.reason == ClosedSetIssue.DISALLOWED_CHARACTER.value

    def test_punctuation_flagged(self):
        r = normalize_token("05.")
        assert r.requires_human_confirmation is True

    def test_question_mark_unknown(self):
        r = normalize_token("?")
        assert r.requires_human_confirmation is True
        assert r.reason == ClosedSetIssue.UNKNOWN_TOKEN.value


class TestValidateNumberToken:
    def test_valid_range(self):
        tv = validate_number_token("05")
        assert tv.canonical == "05"
        assert tv.issues == []

    def test_single_digit_canonical(self):
        tv = validate_number_token("5")
        assert tv.canonical == "05"

    def test_39_valid(self):
        assert validate_number_token("39").issues == []

    def test_40_rejected(self):
        tv = validate_number_token("40")
        assert ClosedSetIssue.INVALID_NUMBER_RANGE.value in tv.issues
        assert tv.requires_human_confirmation is True

    def test_00_rejected(self):
        tv = validate_number_token("00")
        assert ClosedSetIssue.INVALID_NUMBER_RANGE.value in tv.issues

    def test_question_mark_unknown(self):
        tv = validate_number_token("?")
        assert ClosedSetIssue.UNKNOWN_TOKEN.value in tv.issues

    def test_incomplete_number(self):
        tv = validate_number_token("1?")
        assert ClosedSetIssue.INCOMPLETE_NUMBER.value in tv.issues

    def test_letters_rejected(self):
        tv = validate_number_token("3B")
        assert ClosedSetIssue.DISALLOWED_CHARACTER.value in tv.issues

    def test_empty_unknown(self):
        tv = validate_number_token("")
        assert tv.requires_human_confirmation is True


class TestValidateMultiplier:
    def test_null_ok(self):
        assert validate_multiplier(None) == []

    def test_integer_ok(self):
        assert validate_multiplier("×1") == []
        assert validate_multiplier("1") == []

    def test_star_integer_ok(self):
        assert validate_multiplier("二三×1") == []

    def test_decimal_ambiguous(self):
        issues = validate_multiplier("×0.5")
        assert ClosedSetIssue.AMBIGUOUS_SYMBOL.value in issues

    def test_x_normalized_to_multiply(self):
        # X/x normalize to × first (requirement: X/x/× are the same token)
        assert validate_multiplier("x1") == []
        assert validate_multiplier("X1") == []

    def test_real_letter_disallowed(self):
        issues = validate_multiplier("B1")
        assert ClosedSetIssue.DISALLOWED_CHARACTER.value in issues

    def test_unknown(self):
        issues = validate_multiplier("?")
        assert ClosedSetIssue.UNKNOWN_TOKEN.value in issues


class TestValidateStructure:
    def test_layout_ok(self):
        assert validate_layout("normal") == []
        assert validate_layout("column") == []
        assert validate_layout("auto") == []
        assert validate_layout("mixed") == []

    def test_layout_uncertain(self):
        assert ClosedSetIssue.LAYOUT_UNCERTAIN.value in validate_layout("banana")

    def test_label_ok(self):
        assert validate_label("二") == []
        assert validate_label("三") == []
        assert validate_label("四") == []
        assert validate_label(None) == []

    def test_label_rejected(self):
        assert ClosedSetIssue.DISALLOWED_CHARACTER.value in validate_label("五")

    def test_group_structure_ok(self):
        assert validate_group_structure([["05"]], "×1") == []

    def test_group_structure_uncertain(self):
        issues = validate_group_structure([], "×1")
        assert ClosedSetIssue.GROUP_STRUCTURE_UNCERTAIN.value in issues


class TestValidateLine:
    def test_clean_line_no_confirmation(self):
        v = validate_line(
            number_groups=[["18", "26"]],
            multiplier_text="×1",
            raw_text="18 26 ×1",
            layout_hint="normal_like",
            uncertain=False,
        )
        assert v["needs_human_confirmation"] is False
        assert v["issues"] == []

    def test_out_of_range_flags(self):
        v = validate_line(
            number_groups=[["40"]], multiplier_text="×1",
            raw_text="40 ×1", layout_hint="normal_like", uncertain=False,
        )
        assert v["needs_human_confirmation"] is True
        assert ClosedSetIssue.INVALID_NUMBER_RANGE.value in v["issues"]

    def test_letter_flags(self):
        v = validate_line(
            number_groups=[["3B"]], multiplier_text="×1",
            raw_text="3B ×1", layout_hint="normal_like", uncertain=False,
        )
        assert v["needs_human_confirmation"] is True
        assert ClosedSetIssue.DISALLOWED_CHARACTER.value in v["issues"]

    def test_uncertain_flags(self):
        v = validate_line(
            number_groups=[["1?"]], multiplier_text="×1",
            raw_text="1? ×1", layout_hint="unknown", uncertain=True,
        )
        assert v["needs_human_confirmation"] is True

    def test_unknown_layout_flags(self):
        v = validate_line(
            number_groups=[["05"]], multiplier_text=None,
            raw_text="05", layout_hint="unknown", uncertain=False,
        )
        assert v["needs_human_confirmation"] is True

    def test_token_validations_attached(self):
        v = validate_line(
            number_groups=[["05", "40"]], multiplier_text="×1",
            raw_text="05 40 ×1", layout_hint="normal_like", uncertain=False,
        )
        assert len(v["token_validations"]) == 2
        assert v["token_validations"][0]["canonical"] == "05"
        assert v["token_validations"][1]["requires_human_confirmation"] is True


class TestNoAutoGuess:
    def test_english_text_never_silently_accepted(self):
        r = normalize_token("please")
        assert r.normalized is None
        assert r.requires_human_confirmation is True

    def test_candidates_never_auto_applied(self):
        r = normalize_token("O7")
        assert r.normalized is None  # must stay None, not "07"
        assert "07" in r.candidates  # offered only as candidate
