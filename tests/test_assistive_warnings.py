"""Tests for assistive_warnings — conservative warning classification."""
from betguard.vision.assistive_warnings import (
    WarningCode,
    WarningSeverity,
    analyze_line,
    analyze_session_lines,
    warn_ambiguous_glyphs,
    warn_bottom_special,
    warn_duplicate_number,
    warn_invalid_charset,
    warn_invalid_multiplier,
    warn_leading_zero,
    warn_number_out_of_range,
    warn_possible_row_omission,
    warn_unparsed,
)


def _codes(ws):
    return [w.code for w in ws]


def _sev(ws, code):
    for w in ws:
        if w.code == code:
            return w.severity
    return None


class TestBlockers:
    def test_invalid_charset_blocker(self):
        ws = warn_invalid_charset("01 20 x1 @")
        assert _codes(ws) == [WarningCode.INVALID_CHARSET.value]
        assert _sev(ws, WarningCode.INVALID_CHARSET.value) == \
            WarningSeverity.BLOCKER.value

    def test_charset_allows_closed_set_and_unknown(self):
        assert warn_invalid_charset("01 20 x1") == []
        assert warn_invalid_charset("? ? ?") == []

    def test_out_of_range_blocker(self):
        ws = warn_number_out_of_range("45 02 11")
        assert _codes(ws) == [WarningCode.NUMBER_OUT_OF_RANGE.value]
        assert _sev(ws, WarningCode.NUMBER_OUT_OF_RANGE.value) == \
            WarningSeverity.BLOCKER.value

    def test_in_range_ok(self):
        assert warn_number_out_of_range("01 20 39") == []


class TestWarnings:
    def test_duplicate_number(self):
        ws = warn_duplicate_number("07 07 21")
        assert _codes(ws) == [WarningCode.DUPLICATE_NUMBER.value]
        assert _sev(ws, WarningCode.DUPLICATE_NUMBER.value) == \
            WarningSeverity.WARNING.value

    def test_duplicate_unique_ok(self):
        assert warn_duplicate_number("07 21 35") == []

    def test_invalid_multiplier(self):
        ws = warn_invalid_multiplier("01 20 x")  # x without number
        assert _codes(ws) == [WarningCode.INVALID_MULTIPLIER.value]

    def test_valid_multiplier_ok(self):
        assert warn_invalid_multiplier("01 20 二X1") == []

    def test_bottom_special_parenthesis(self):
        ws = warn_bottom_special("(08.10.18.20) 三X0.5")
        assert _codes(ws) == [WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value]
        assert _sev(ws, WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value) == \
            WarningSeverity.WARNING.value  # not a BLOCKER

    def test_bottom_special_tail(self):
        assert _codes(warn_bottom_special("13X24X8尾 二三X1")) == \
            [WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value]

    def test_unparsed(self):
        ws = warn_unparsed("這行無法解析")
        assert _codes(ws) == [WarningCode.UNPARSED_CONTENT.value]

    def test_parsed_ok(self):
        assert warn_unparsed("01 20 x1") == []


class TestRiskHighlights:
    def test_ambiguous_2_3(self):
        ws = warn_ambiguous_glyphs("18 26 x1")
        assert WarningCode.AMBIGUOUS_2_3.value in _codes(ws)
        assert _sev(ws, WarningCode.AMBIGUOUS_2_3.value) == \
            WarningSeverity.RISK_HIGHLIGHT.value  # does not block

    def test_ambiguous_6_8(self):
        assert WarningCode.AMBIGUOUS_6_8.value in \
            _codes(warn_ambiguous_glyphs("06 15 24 37"))

    def test_ambiguous_1_7(self):
        assert WarningCode.AMBIGUOUS_1_7.value in \
            _codes(warn_ambiguous_glyphs("07 19 x1"))

    def test_leading_zero_review(self):
        ws = warn_leading_zero("4 11 x1")  # bare single digit
        assert _codes(ws) == [WarningCode.LEADING_ZERO_REVIEW.value]

    def test_leading_zero_no_false_positive(self):
        assert warn_leading_zero("04 11 x1") == []


class TestNoAutoCorrection:
    def test_analyze_never_returns_fixed_text(self):
        for text in ["01 >0 x1", "18 36 x1", "4 11 x1", "45 02 x1"]:
            for w in analyze_line(text):
                assert "corrected" not in w.message.lower()
                assert "replace" not in w.message.lower()
                assert "自動修正" not in w.message
                assert "改為" not in w.message

    def test_warnings_do_not_modify_input(self):
        text = "01 >0 x1"
        before = text
        analyze_line(text)
        assert text == before  # input untouched


class TestDedup:
    def test_same_code_once_per_line(self):
        ws = analyze_line("06 08 16 18 28")  # 6/8 and 1/7-ish all present
        codes = [w.code for w in ws]
        assert len(codes) == len(set(codes))  # no repeated codes

    def test_ambiguous_pairs_single_warning_each(self):
        ws = warn_ambiguous_glyphs("26 28 27")
        assert _codes(ws).count(WarningCode.AMBIGUOUS_2_3.value) == 1
        assert _codes(ws).count(WarningCode.AMBIGUOUS_6_8.value) == 1


class TestSessionLevel:
    def test_possible_row_omission(self):
        ws = warn_possible_row_omission(expected_physical_rows=47,
                                        returned_rows=35)
        assert _codes(ws) == [WarningCode.POSSIBLE_ROW_OMISSION.value]
        assert _sev(ws, WarningCode.POSSIBLE_ROW_OMISSION.value) == \
            WarningSeverity.WARNING.value

    def test_no_omission_when_counts_match(self):
        assert warn_possible_row_omission(47, 47) == []
        assert warn_possible_row_omission(47, 50) == []

    def test_omission_skipped_when_expected_unknown(self):
        assert warn_possible_row_omission(0, 35) == []

    def test_analyze_session_lines(self):
        lines = ["01 20 x1", "這行無法解析", "", "45 02 x1"]
        out = analyze_session_lines(lines, expected_physical_rows=47)
        assert 0 in out["lines"]  # line 0 has warnings? no — valid
        assert 1 in out["lines"]  # unparsed
        assert 3 in out["lines"]  # out of range
        assert out["session"]  # omission warning
        codes = {w["code"] for w in out["session"]}
        assert WarningCode.POSSIBLE_ROW_OMISSION.value in codes


class TestMultiplierExclusion:
    """Multiplier segments (X1, 二X1, 三X0.5, 四X10, x0.2) must NOT be
    treated as bet numbers."""

    def test_x1_only_numbers(self):
        ws = warn_number_out_of_range("01 20 二X1")
        assert ws == []  # 1 is multiplier, not a bet number

    def test_x1_no_1_7_hint(self):
        # 02/03 contain no 1 or 7; the multiplier "二X1"'s 1 must not
        # trigger AMBIGUOUS_1_7
        ws = warn_ambiguous_glyphs("02 03 二X1")
        assert WarningCode.AMBIGUOUS_1_7.value not in _codes(ws)

    def test_1_7_hint_still_fires_for_bet_number_1(self):
        # bet number "01" legitimately contains 1 -> hint is correct
        ws = warn_ambiguous_glyphs("01 20 二X1")
        assert WarningCode.AMBIGUOUS_1_7.value in _codes(ws)

    def test_x1_no_leading_zero_warning(self):
        ws = warn_leading_zero("01 20 二X1")
        assert ws == []

    def test_san_x0_5_no_range_error(self):
        assert warn_number_out_of_range("三X0.5") == []
        assert warn_leading_zero("三X0.5") == []
        assert warn_ambiguous_glyphs("三X0.5") == []

    def test_six_x10_not_bet_number(self):
        ws = warn_number_out_of_range("04 11 四X10")
        assert ws == []  # 10 in multiplier is not a bet number

    def test_out_of_range_40_detected(self):
        ws = warn_number_out_of_range("40 01 二X1")
        assert _codes(ws) == [WarningCode.NUMBER_OUT_OF_RANGE.value]
        assert "40" in ws[0].details["tokens"]

    def test_zero_zero_out_of_range(self):
        ws = warn_number_out_of_range("00 20 二X1")
        assert _codes(ws) == [WarningCode.NUMBER_OUT_OF_RANGE.value]

    def test_duplicate_excludes_multiplier(self):
        assert warn_duplicate_number("07 07 二X1") != []  # real dup
        assert warn_duplicate_number("07 21 x1") == []  # x1 not a dup


class TestUnknownMarker:
    def test_unknown_marker_blocker(self):
        ws = analyze_line("01 ? 二X1")
        assert WarningCode.UNKNOWN_MARKER.value in _codes(ws)
        assert _sev(ws, WarningCode.UNKNOWN_MARKER.value) == \
            WarningSeverity.BLOCKER.value

    def test_no_unknown_marker_without_question(self):
        assert WarningCode.UNKNOWN_MARKER.value not in \
            _codes(analyze_line("01 20 二X1"))

    def test_question_marker_count(self):
        ws = analyze_line("? ? 二X1")
        for w in ws:
            if w.code == WarningCode.UNKNOWN_MARKER.value:
                assert w.details["count"] == 2


class TestBottomSpecial:
    def test_slash_matrix(self):
        ws = warn_bottom_special("01 02 03 / 10 11 12 / 17 18 27 三X0.2")
        assert _codes(ws) == [WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value]
        assert "/" in ws[0].details["markers"]

    def test_parentheses(self):
        ws = warn_bottom_special("(08.10.18.20) 三X0.5")
        assert "括號" in ws[0].details["markers"]

    def test_ge_marker(self):
        ws = warn_bottom_special("各二三x0.5")
        assert "各" in ws[0].details["markers"]

    def test_tail_marker(self):
        ws = warn_bottom_special("13X24X8尾 二三X1")
        assert "尾" in ws[0].details["markers"]

    def test_multiple_chinese_multipliers(self):
        ws = warn_bottom_special("三X0.5 四X3")
        assert "多組倍率" in ws[0].details["markers"]

    def test_bottom_special_is_warning_not_blocker(self):
        ws = analyze_line("(08.10.18.20) 三X0.5")
        assert _sev(ws, WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value) == \
            WarningSeverity.WARNING.value

    def test_plain_line_no_bottom_warning(self):
        assert warn_bottom_special("01 20 二X1") == []
