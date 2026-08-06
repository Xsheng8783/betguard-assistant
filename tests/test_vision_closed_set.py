"""Test closed character-set normalizer and validator."""

from __future__ import annotations

import pytest

from betguard.vision.closed_set import (
    ClosedSetIssue,
    normalize_fullwidth_digits,
    normalize_symbols,
    normalize_text,
    normalize_token,
    normalize_whitespace,
    parse_multi_category_shared,
    parse_multiplier_text,
    parse_paren_number_set,
    parse_shared_multiplier,
    parse_tail_expansion,
    validate_group_structure,
    validate_label,
    validate_layout,
    validate_line,
    validate_multiplier,
    validate_number_token,
    validate_scope,
    validate_tail_expansion,
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
        r = normalize_token("05,")
        assert r.requires_human_confirmation is True

    def test_v2_dot_allowed_in_raw_context(self):
        # V2: dot is legal (paren number separator / decimal point)
        assert validate_multiplier("三×0.5") == []

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
        # single digits are kept as written (no auto-padding)
        assert tv.canonical == "5"

    def test_39_valid(self):
        assert validate_number_token("39").issues == []

    def test_49_valid_mark_six(self):
        assert validate_number_token("49").issues == []

    def test_50_rejected(self):
        tv = validate_number_token("50")
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

    def test_decimal_legal(self):
        assert validate_multiplier("×0.5") == []
        assert validate_multiplier("×0.3") == []
        assert validate_multiplier("×0.2") == []

    def test_leading_dot_rejected(self):
        issues = validate_multiplier(".5")
        assert issues != []

    def test_trailing_dot_rejected(self):
        issues = validate_multiplier("0.")
        assert issues != []

    def test_double_decimal_rejected(self):
        issues = validate_multiplier("0.5.2")
        assert issues != []

    def test_negative_rejected(self):
        issues = validate_multiplier("-0.5")
        assert issues != []

    def test_scientific_notation_rejected(self):
        issues = validate_multiplier("5e-1")
        assert issues != []

    def test_multiple_multipliers_legal(self):
        assert validate_multiplier("三×0.5 四×3") == []

    def test_compound_multiplier_no_space_legal(self):
        assert validate_multiplier("三×0.5四×3") == []

    def test_shared_multiplier_marker_legal(self):
        assert validate_multiplier("三×0.3") == []

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
    def test_clean_line_still_requires_confirmation(self):
        # Policy: even a fully clean line requires human confirmation
        v = validate_line(
            number_groups=[["18", "26"]],
            multiplier_text="×1",
            raw_text="18 26 ×1",
            layout_hint="normal_like",
            uncertain=False,
        )
        assert v["needs_human_confirmation"] is True
        assert v["issues"] == []

    def test_out_of_range_flags(self):
        v = validate_line(
            number_groups=[["50"]], multiplier_text="×1",
            raw_text="50 ×1", layout_hint="normal_like", uncertain=False,
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
            number_groups=[["05", "50"]], multiplier_text="×1",
            raw_text="05 50 ×1", layout_hint="normal_like", uncertain=False,
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

    def test_star_not_auto_converted_to_multiply(self):
        r = normalize_token("*1")
        assert r.normalized is None  # * is not in the closed set
        assert r.requires_human_confirmation is True


class TestV2Semantics:
    def test_parse_multiplier_decimal(self):
        assert parse_multiplier_text("三×0.5") == [{"category": "三", "value_text": "0.5"}]

    def test_parse_multiplier_integer(self):
        assert parse_multiplier_text("四×3") == [{"category": "四", "value_text": "3"}]

    def test_parse_multiplier_multiple(self):
        assert parse_multiplier_text("三×0.5 四×3") == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "3"},
        ]

    def test_parse_multiplier_bare(self):
        assert parse_multiplier_text("×1") == [{"category": None, "value_text": "1"}]

    def test_parse_multiplier_invalid(self):
        assert parse_multiplier_text(".5") == []
        assert parse_multiplier_text("0.") == []
        assert parse_multiplier_text("0.5.2") == []
        assert parse_multiplier_text("5e-1") == []
        assert parse_multiplier_text("?") == []

    def test_parse_paren_number_set(self):
        assert parse_paren_number_set("(12.18.20.23)") == ["12", "18", "20", "23"]

    def test_parse_paren_number_set_fullwidth(self):
        assert parse_paren_number_set("（12.18.20.23）") == ["12", "18", "20", "23"]

    def test_parse_paren_unknown(self):
        assert parse_paren_number_set("(1?.18.20.23)") == ["1?", "18", "20", "23"]
    def test_parse_paren_rejects_letters(self):
        assert parse_paren_number_set("(1B.18)") is None

    def test_parse_paren_rejects_unclosed(self):
        assert parse_paren_number_set("12.18.20.23") is None

    def test_parse_shared_multiplier(self):
        r = parse_shared_multiplier("各=三×0.3")
        assert r == {"multipliers": [{"category": "三", "value_text": "0.3"}], "scope": "all_groups_in_region"}

    def test_parse_shared_multiplier_no_equals(self):
        # real slips have no '=': 各三×0.3 is the canonical V2 form
        r = parse_shared_multiplier("各三×0.3")
        assert r == {"multipliers": [{"category": "三", "value_text": "0.3"}], "scope": "all_groups_in_region"}

    def test_parse_shared_multiplier_ge_two_three(self):
        r = parse_shared_multiplier("各二三×0.5")
        assert r["multipliers"] == [
            {"category": "二", "value_text": "0.5"},
            {"category": "三", "value_text": "0.5"},
        ]
        assert r["scope"] == "all_groups_in_region"

    def test_parse_multiplier_compound_no_space(self):
        assert parse_multiplier_text("三×0.5四×3") == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "3"},
        ]

    def test_parse_shared_multiplier_rejects_plain(self):
        assert parse_shared_multiplier("三×0.3") is None

    def test_parse_shared_multiplier_fullwidth(self):
        r = parse_shared_multiplier("各＝三×0.3")
        assert r is not None and r["multipliers"][0]["value_text"] == "0.3"

    def test_scope_validation(self):
        assert validate_scope("current_group") == []
        assert validate_scope("all_groups_in_region") == []
        assert validate_scope("unresolved_region") == []
        assert validate_scope("everywhere") != []

    def test_fullwidth_punct_normalized(self):
        assert normalize_text("（12.18.20.23）＝三×0.5") == "(12.18.20.23)=三×0.5"


class TestContextValidation:
    """Legal characters do NOT make a segment syntactically legal."""

    def test_legal_number_set_with_multipliers(self):
        v = validate_line(
            number_groups=[["12", "18", "20", "23"]],
            multiplier_text="三×0.5 四×3",
            raw_text="(12.18.20.23) 三×0.5 四×3",
            layout_hint="normal_like", uncertain=False,
        )
        assert v["semantics"]["layout"] == "number_set"
        assert v["semantics"]["numbers"] == ["12", "18", "20", "23"]
        assert v["semantics"]["multipliers"] == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "3"},
        ]
        assert v["semantics"]["scope"] == "current_group"

    def test_bare_dot_numbers_without_parens_ambiguous(self):
        # 12.18.20.23 without parens has no declared purpose → ambiguous
        v = validate_line(
            number_groups=[["12", "18", "20", "23"]],
            multiplier_text=None,
            raw_text="12.18.20.23",
            layout_hint="normal_like", uncertain=False,
        )
        assert "ambiguous_symbol" in v["issues"]
        assert v["needs_human_confirmation"] is True

    def test_category_equals_value_rejected(self):
        assert validate_multiplier("三=0.5") != []
        assert validate_multiplier("12=18") != []

    def test_unbalanced_parens_rejected(self):
        assert parse_paren_number_set("(12.18.20.23") is None
        assert parse_paren_number_set("12.18.20.23)") is None
        assert parse_paren_number_set("((12.18))") is None
        assert parse_paren_number_set("()") is None

    def test_shared_multiplier_missing_parts_rejected(self):
        assert parse_shared_multiplier("各三0.3") is None  # missing ×
        assert parse_shared_multiplier("各×0.3") is None  # missing category
        assert parse_shared_multiplier("各=×0.3") is None   # missing category
        assert parse_shared_multiplier("各=三0.3") is None   # missing ×
        assert parse_shared_multiplier("各=三×.3") is None   # leading dot
        assert validate_multiplier("三×0.5.2") != []         # double decimal


class TestSharedScopeSafety:
    def test_shared_without_region_scope_unresolved(self):
        v = validate_line(
            number_groups=[], multiplier_text="三×0.3",
            raw_text="各=三×0.3", layout_hint="unknown", uncertain=False,
            region_bound=False,
        )
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["scope"] == "unresolved_region"
        assert v["semantics"]["needs_human_confirmation"] is True
        assert v["needs_human_confirmation"] is True

    def test_shared_with_region_scope_all_groups(self):
        v = validate_line(
            number_groups=[], multiplier_text="三×0.3",
            raw_text="各=三×0.3", layout_hint="unknown", uncertain=False,
            region_bound=True,
        )
        assert v["semantics"]["scope"] == "all_groups_in_region"
        assert v["semantics"]["needs_human_confirmation"] is True

    def test_unresolved_scope_blocks_assisted_fill(self):
        from betguard.vision.paid_fallback import _safety_flags
        safety = _safety_flags()
        assert safety["webfill_allowed"] is False
        assert safety["auto_submit"] is False
        assert safety["auto_confirm"] is False
        assert safety["real_site_operation"] is False

    def test_normal_row_semantics(self):
        v = validate_line(
            number_groups=[["18", "26"]], multiplier_text="×1",
            raw_text="18 26 ×1", layout_hint="normal_like", uncertain=False,
        )
        assert v["semantics"]["layout"] == "normal_row"
        assert v["semantics"]["numbers"] == ["18", "26"]
        assert v["semantics"]["scope"] == "current_group"


class TestConfirmationInvariant:
    """top-level needs_human_confirmation must ALWAYS equal semantics'
    needs_human_confirmation, and both must be True (policy)."""

    def _assert_invariant(self, v):
        assert v["needs_human_confirmation"] is True
        if v["semantics"] is not None:
            assert v["semantics"]["needs_human_confirmation"] is True
            assert v["needs_human_confirmation"] == v["semantics"]["needs_human_confirmation"]

    def test_legal_normal_row(self):
        v = validate_line(
            number_groups=[["01", "20"]], multiplier_text="×1",
            raw_text="01 20 ×1", layout_hint="normal_like", uncertain=False,
        )
        self._assert_invariant(v)

    def test_legal_number_set(self):
        v = validate_line(
            number_groups=[["12", "18", "20", "23"]], multiplier_text="三×0.5 四×3",
            raw_text="(12.18.20.23) 三×0.5 四×3", layout_hint="normal_like", uncertain=False,
        )
        self._assert_invariant(v)

    def test_shared_multiplier_no_region(self):
        v = validate_line(
            number_groups=[], multiplier_text="三×0.3",
            raw_text="各=三×0.3", layout_hint="unknown", uncertain=False,
            region_bound=False,
        )
        self._assert_invariant(v)

    def test_shared_multiplier_with_region(self):
        v = validate_line(
            number_groups=[], multiplier_text="三×0.3",
            raw_text="各=三×0.3", layout_hint="unknown", uncertain=False,
            region_bound=True,
        )
        self._assert_invariant(v)

    def test_invalid_syntax(self):
        v = validate_line(
            number_groups=[["12"]], multiplier_text="三×0.5.2",
            raw_text="12 三×0.5.2", layout_hint="normal_like", uncertain=False,
        )
        self._assert_invariant(v)
        assert "ambiguous_symbol" in v["issues"]


class TestMultiCategoryShared:
    """二三x0.3 — multi-category shared multiplier (user-confirmed syntax)."""

    def test_normalize_lowercase_x(self):
        assert normalize_text("二三x0.3") == "二三×0.3"

    def test_normalize_uppercase_x(self):
        assert normalize_text("二三X0.3") == "二三×0.3"

    def test_normalize_already_multiply(self):
        assert normalize_text("二三×0.3") == "二三×0.3"

    def test_star_not_normalized(self):
        assert normalize_text("二三*0.3") != "二三×0.3"

    def test_parse_multiplier_text_expands(self):
        assert parse_multiplier_text("二三x0.3") == [
            {"category": "二", "value_text": "0.3"},
            {"category": "三", "value_text": "0.3"},
        ]

    def test_parse_er_si_x1(self):
        assert parse_multiplier_text("二四X1") == [
            {"category": "二", "value_text": "1"},
            {"category": "四", "value_text": "1"},
        ]

    def test_parse_san_si_x05(self):
        assert parse_multiplier_text("三四x0.5") == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "0.5"},
        ]

    def test_parse_full_three(self):
        assert parse_multiplier_text("二三四x0.2") == [
            {"category": "二", "value_text": "0.2"},
            {"category": "三", "value_text": "0.2"},
            {"category": "四", "value_text": "0.2"},
        ]

    def test_duplicate_category_rejected(self):
        assert parse_multiplier_text("二二x0.3") == []
        assert validate_multiplier("二二x0.3") != []

    def test_illegal_category_rejected(self):
        assert parse_multiplier_text("二五x0.3") == []

    def test_missing_category_rejected(self):
        assert parse_multiplier_text("x0.3") == [{"category": None, "value_text": "0.3"}]
        # bare x0.3 as shared requires category — multi-category parser rejects
        assert parse_multi_category_shared("x0.3") is None

    def test_missing_multiply_rejected(self):
        assert parse_multi_category_shared("二三0.3") is None

    def test_missing_value_rejected(self):
        assert parse_multi_category_shared("二三x") is None
        assert parse_multi_category_shared("二三x.") is None

    def test_invalid_decimal_rejected(self):
        assert parse_multi_category_shared("二三x.3") is None
        assert parse_multi_category_shared("二三x0.") is None
        assert parse_multi_category_shared("二三x0.3.2") is None
        assert parse_multi_category_shared("二三x-0.3") is None
        assert parse_multi_category_shared("二三x3e-1") is None

    def test_star_or_ge_mixed_rejected(self):
        assert parse_multi_category_shared("二三*x0.3") is None
        assert parse_multi_category_shared("二三各x0.3") is None

    def test_legal_x_no_disallowed(self):
        v = validate_line(
            number_groups=[], multiplier_text=None, raw_text="二三x0.3",
            layout_hint="unknown", uncertain=False, region_bound=False,
        )
        assert "disallowed_character" not in v["issues"]
        assert "ambiguous_symbol" not in v["issues"]
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["scope"] == "unresolved_region"

    def test_legal_x_uppercase_no_disallowed(self):
        v = validate_line(
            number_groups=[], multiplier_text=None, raw_text="二三X0.3",
            layout_hint="unknown", uncertain=False, region_bound=False,
        )
        assert "disallowed_character" not in v["issues"]
        assert v["semantics"]["multipliers"] == [
            {"category": "二", "value_text": "0.3"},
            {"category": "三", "value_text": "0.3"},
        ]

    def test_bound_scope_current_group_without_ge(self):
        v = validate_line(
            number_groups=[], multiplier_text=None, raw_text="二三x0.3",
            layout_hint="unknown", uncertain=False, region_bound=True,
        )
        assert v["semantics"]["scope"] == "current_group"

    def test_ge_equals_still_works(self):
        v = validate_line(
            number_groups=[], multiplier_text=None, raw_text="各=三×0.3",
            layout_hint="unknown", uncertain=False, region_bound=False,
        )
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["multipliers"] == [{"category": "三", "value_text": "0.3"}]
        assert v["semantics"]["scope"] == "unresolved_region"

    def test_number_set_still_works(self):
        v = validate_line(
            number_groups=[["12", "18", "20", "23"]], multiplier_text="三×0.5 四×3",
            raw_text="(12.18.20.23) 三×0.5 四×3", layout_hint="normal_like",
            uncertain=False,
        )
        assert v["semantics"]["layout"] == "number_set"
        assert v["semantics"]["multipliers"] == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "3"},
        ]


class TestTailExpansion:
    """13X24X8尾 → 13/24/08 18 28 38 (tail-digit expansion)."""

    def test_full_example(self):
        assert parse_tail_expansion("13X24X8尾") == ["13", "24", "08", "18", "28", "38"]

    def test_full_example_with_multiplier(self):
        assert parse_tail_expansion("13X24X8尾 二三X1") == ["13", "24", "08", "18", "28", "38"]

    def test_bare_tail(self):
        assert parse_tail_expansion("X8尾") == ["08", "18", "28", "38"]

    def test_tail_zero(self):
        assert parse_tail_expansion("X0尾") == ["10", "20", "30"]

    def test_tail_nine_with_multiplier(self):
        assert parse_tail_expansion("X9尾 四X3") == ["09", "19", "29", "39"]

    def test_no_x_prefix(self):
        assert parse_tail_expansion("8尾") == ["08", "18", "28", "38"]

    def test_unknown_token_kept(self):
        r = parse_tail_expansion("1?X24X8尾")
        assert r is not None and "1?" in r

    def test_invalid_tail_ambiguous(self):
        assert validate_tail_expansion("尾") != []
        assert parse_tail_expansion("13X24X8尾 垃圾") is None
        assert parse_tail_expansion("13X尾") is None

    def test_normalize_x_forms(self):
        assert parse_tail_expansion("13X24X8尾") == parse_tail_expansion("13x24x8尾")
