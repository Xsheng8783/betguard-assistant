from betguard.models import BetReport
from betguard.parser import parse_line
from betguard.validator import validate_bet


TWO_THREE = "\u4e8c\u4e09"
TWO_THREE_FOUR = "\u4e8c\u4e09\u56db"
ALT_TWO_THREE = "\u5169\u4e09"
THREE_STAR = "\u4e09\u661f"
FOUR_STAR = "\u56db\u661f"
YUAN = "\u5143"
UNIT = "\u652f"
IDEOGRAPHIC_COMMA = "\u3001"
JINCAI = "\u4eca\u5f69"


def report_for(text: str) -> dict:
    bet = parse_line(text)
    return BetReport(bet=bet, validation=validate_bet(bet)).to_dict()


def test_a_dash_slash_amount() -> None:
    report = report_for("06-13-23-22/50")

    assert report["game"] == "539"
    assert report["type"] == "normal"
    assert report["numbers"] == [6, 13, 23, 22]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_b_dot_slash_amount() -> None:
    report = report_for("01.17.23/100")

    assert report["numbers"] == [1, 17, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_c_chinese_star_combo_with_money() -> None:
    report = report_for(f"08.14.23 {TWO_THREE} 100")

    assert report["numbers"] == [8, 14, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_d_chinese_star_combo_with_attached_unit() -> None:
    report = report_for(f"07.12.13.23.25 {TWO_THREE_FOUR}1")

    assert report["numbers"] == [7, 12, 13, 23, 25]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_e_equals_small_number_means_unit() -> None:
    report = report_for("30.31=5")

    assert report["numbers"] == [30, 31]
    assert report["stars"] == [2]
    assert report["unit"] == 5
    assert report["money"] == 500


def test_f_equals_common_amount_means_money() -> None:
    report = report_for("28.29=500")

    assert report["numbers"] == [28, 29]
    assert report["stars"] == [2]
    assert report["unit"] == 5
    assert report["money"] == 500


def test_g_star_combo_with_attached_unit() -> None:
    report = report_for(f"04.30.31 {TWO_THREE}3")

    assert report["numbers"] == [4, 30, 31]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 3
    assert report["money"] == 300


def test_h_per_star_amounts() -> None:
    report = report_for(
        f"07.12.20.16.27.37.19 {THREE_STAR}10{YUAN} {FOUR_STAR}0.05{UNIT}"
    )

    assert report["numbers"] == [7, 12, 20, 16, 27, 37, 19]
    assert report["stars"] == [3, 4]
    assert report["bets"]["3"]["money"] == 10
    assert "unit" not in report["bets"]["3"]
    assert report["bets"]["4"]["unit"] == 0.05
    assert report["bets"]["4"]["money"] == 5
    assert report["status"] == "ok"


def test_i_duplicate_number_is_error() -> None:
    report = report_for(f"13.38.13 {TWO_THREE}100")

    assert report["status"] == "error"
    assert "duplicate number 13" in report["errors"]


def test_j_out_of_range_number_is_error() -> None:
    report = report_for(f"40.12 \u4e8c100")

    assert report["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in report["errors"]


def test_missing_money_is_warning_not_error() -> None:
    report = report_for("06.13.23")

    assert report["status"] == "warning"
    assert report["warnings"] == ["missing money"]
    assert report["errors"] == []


def test_numeric_star_keyword_with_comma() -> None:
    report = report_for("08.14.23 2,3 100")

    assert report["numbers"] == [8, 14, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_numeric_compact_star_keyword() -> None:
    report = report_for("07.12.13.23.25 234 1")

    assert report["numbers"] == [7, 12, 13, 23, 25]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_supported_number_separators_and_x_unit() -> None:
    report = report_for(f"01-17{IDEOGRAPHIC_COMMA}23,24 X0.5")

    assert report["numbers"] == [1, 17, 23, 24]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50


def test_game_marker_is_not_a_number() -> None:
    report = report_for(f"{JINCAI}539 06.13/50")

    assert report["numbers"] == [6, 13]
    assert report["stars"] == [2]
    assert report["unit"] == 0.5
    assert report["money"] == 50


def test_star_money_suffix_234_dot_100_after_ellipsis() -> None:
    report = report_for("20.24.12.15.06.35...234.100")

    assert report["numbers"] == [20, 24, 12, 15, 6, 35]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_star_money_suffix_234_dot_50() -> None:
    report = report_for("08.16.28.10.26.21.234.50")

    assert report["numbers"] == [8, 16, 28, 10, 26, 21]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50


def test_star_money_suffix_234_dot_100_with_23_number() -> None:
    report = report_for("03.30.18.23.33.39.234.100")

    assert report["numbers"] == [3, 30, 18, 23, 33, 39]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_chinese_alt_two_three_attached_money_suffix() -> None:
    report = report_for(f"18.24.31.{ALT_TWO_THREE}200")

    assert report["numbers"] == [18, 24, 31]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 2
    assert report["money"] == 200


def test_chinese_two_three_four_attached_money_suffix() -> None:
    report = report_for(f"07.22.09.11.38.39 {TWO_THREE_FOUR}50")

    assert report["numbers"] == [7, 22, 9, 11, 38, 39]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50


def test_star_money_suffix_23_dot_100() -> None:
    report = report_for("08.14.23.23.100")

    assert report["numbers"] == [8, 14, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100



def test_unsupported_character_and_ambiguous_long_number_are_reported() -> None:
    report = report_for("32.10,1500..15.23.32.10.20.21..234.100\u81c2")

    assert report["status"] == "error"
    assert "unsupported characters: \u81c2" in report["errors"]
    assert "ambiguous long number 1500" in report["errors"]


def test_duplicates_are_reported_when_long_suffix_is_clear() -> None:
    report = report_for("32.10.15.23.32.10.20.21.234.100")

    assert report["status"] == "error"
    assert "duplicate number 32" in report["errors"]
    assert "duplicate number 10" in report["errors"]


def test_long_suffix_clear_without_duplicates_is_ok() -> None:
    report = report_for("32.10.15.23.20.21.234.100")

    assert report["status"] == "ok"
    assert report["numbers"] == [32, 10, 15, 23, 20, 21]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_normal_spaces_star_multiplier() -> None:
    report = report_for("11 05 23  23 * 1")

    assert report["numbers"] == [11, 5, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_normal_parenthesized_chinese_star_unit() -> None:
    report = report_for("11 05 23 (二三星) 1支")

    assert report["numbers"] == [11, 5, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_normal_parenthesized_numeric_star_unit() -> None:
    report = report_for("11 05 23 (23) 1支")

    assert report["numbers"] == [11, 5, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_normal_star_equals_money_with_spaces() -> None:
    report = report_for("11 05 23 23=100")

    assert report["numbers"] == [11, 5, 23]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100


def test_original_text_preserved() -> None:
    text = "11 05 23 (23) 1支"
    bet = parse_line(text)

    assert bet.original_text == text
    assert bet.normalized_text


def test_multi_number_small_bare_amount_after_star_is_money() -> None:
    report = report_for(f"19.39.22.12.35.23 {TWO_THREE_FOUR} 15")

    assert report["numbers"] == [19, 39, 22, 12, 35, 23]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.15
    assert report["money"] == 15


def test_parenthesized_star_equals_money_regression() -> None:
    report = report_for("04-11-18(23=300)")

    assert report["numbers"] == [4, 11, 18]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 3
    assert report["money"] == 300


def test_star_asterisk_unit_regression() -> None:
    report = report_for("04-11-18 23*1")

    assert report["numbers"] == [4, 11, 18]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
