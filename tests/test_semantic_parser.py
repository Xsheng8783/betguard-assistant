"""M2: semantic parser tests for the canonical GT/OCR formats."""
from __future__ import annotations

from decimal import Decimal

import pytest

from betguard.parser import ParseError
from betguard.semantic_parser import (
    expand_tail,
    multiplier_to_amount,
    parse_ocr_text,
    parse_shared_multiplier,
)


def test_lianpeng_chinese_category_multiplier() -> None:
    bet = parse_ocr_text("05 09 23 26 二三四X1")

    assert bet.type == "normal"
    assert bet.numbers == [5, 9, 23, 26]
    assert bet.stars == [2, 3, 4]
    assert bet.unit == 1
    assert bet.money == 100
    assert bet.executable is True


def test_lianpeng_numeric_category_multiplier() -> None:
    bet = parse_ocr_text("12 19 02 18 39 234X0.5")

    assert bet.type == "normal"
    assert bet.numbers == [12, 19, 2, 18, 39]
    assert bet.stars == [2, 3, 4]
    assert bet.unit == 0.5
    assert bet.money == 50


def test_column_slash_space_canonical() -> None:
    bet = parse_ocr_text("05/08 09 23/10 20 29 二三X1.5")

    assert bet.type == "column"
    assert bet.columns == [[5], [8, 9, 23], [10, 20, 29]]
    assert bet.stars == [2, 3]
    assert bet.unit == 1.5
    assert bet.money == 150


def test_column_dot_separator() -> None:
    bet = parse_ocr_text("17.20/28/34 二三X1")

    assert bet.type == "column"
    assert bet.columns == [[17, 20], [28], [34]]
    assert bet.stars == [2, 3]
    assert bet.unit == 1


def test_column_x_separator() -> None:
    bet = parse_ocr_text("05×08×10 二三X1.5")

    assert bet.type == "column"
    assert bet.columns == [[5], [8], [10]]
    assert bet.stars == [2, 3]


def test_column_x_separator_with_tail() -> None:
    bet = parse_ocr_text("01×16×5尾 23X1")

    assert bet.type == "column"
    assert bet.columns == [[1], [16], [5, 15, 25, 35]]
    assert bet.stars == [2, 3]
    assert bet.unit == 1


def test_tail_shorthand_normal() -> None:
    bet = parse_ocr_text("8尾 23X1")

    assert bet.type == "normal"
    assert bet.numbers == [8, 18, 28, 38]
    assert bet.stars == [2, 3]


def test_compound_multiplier_no_space() -> None:
    bet = parse_ocr_text("09 10 24 36 38 三×0.5四×3")

    assert bet.type == "normal"
    assert bet.numbers == [9, 10, 24, 36, 38]
    assert bet.stars == [3, 4]
    assert bet.unit == 0.5
    assert "compound_multiplier" in bet.parse_notes


def test_compound_multiplier_with_space() -> None:
    bet = parse_ocr_text("09 10 24 36 38 三×0.5 四×3")

    assert bet.type == "normal"
    assert bet.stars == [3, 4]
    assert "compound_multiplier" in bet.parse_notes


def test_shared_multiplier_declaration() -> None:
    rule = parse_shared_multiplier("各二三×0.5", applies_to_line_ids=["L1", "L2"])

    assert rule["categories"] == [2, 3]
    assert rule["value_text"] == "0.5"
    assert rule["scope"] == "all_groups_in_region"
    assert rule["applies_to_line_ids"] == ["L1", "L2"]
    assert rule["executable"] is False


def test_current_group_multiplier_declaration() -> None:
    rule = parse_shared_multiplier("二三×0.5")

    assert rule["categories"] == [2, 3]
    assert rule["scope"] == "current_group"


def test_declaration_only_line_via_parse_ocr_text() -> None:
    bet = parse_ocr_text("各二三×0.5")

    assert bet.type == "shared_multiplier"
    assert bet.stars == [2, 3]
    assert bet.executable is False


def test_car_bet_hard_blocked() -> None:
    bet = parse_ocr_text("全車 15 25 各1.5車")

    assert bet.type == "car"
    assert bet.numbers == [15, 25]
    assert bet.car_units == 1.5
    assert bet.supported is False
    assert bet.executable is False
    assert bet.block_reason == "UNSUPPORTED_CAR_BET"


def test_car_each_half_unit() -> None:
    bet = parse_ocr_text("各0.5車")

    assert bet.type == "car"
    assert bet.car_units == 0.5
    assert bet.executable is False


def test_two_plain_numbers() -> None:
    bet = parse_ocr_text("15 25")

    assert bet.type == "normal"
    assert bet.numbers == [15, 25]
    assert bet.stars == []
    assert bet.unit is None


def test_equals_is_flagged_not_parsed() -> None:
    bet = parse_ocr_text("05 09 23 26 二三四=1")

    assert "LEGACY_OR_HALLUCINATED_EQUALS" in bet.parse_notes
    assert bet.executable is False
    assert bet.numbers == [5, 9, 23, 26]


def test_tail_expansion_539() -> None:
    assert expand_tail(8, game="539") == [8, 18, 28, 38]
    assert expand_tail(0, game="539") == [10, 20, 30]


def test_tail_expansion_mark_six() -> None:
    assert expand_tail(8, game="六合彩") == [8, 18, 28, 38, 48]
    assert expand_tail(0, game="六合彩") == [10, 20, 30, 40]
    assert expand_tail(9, game="六合彩") == [9, 19, 29, 39, 49]


def test_mark_six_numbers_accepted() -> None:
    bet = parse_ocr_text("08 33 6尾 23X1", game="六合彩")

    assert bet.type == "normal"
    assert 46 in bet.numbers  # 6尾 -> 06/16/26/36/46
    assert bet.executable is True


def test_out_of_range_rejected() -> None:
    with pytest.raises(ParseError):
        parse_ocr_text("45 46 二三X1", game="539")


def test_multiplier_to_amount_decimal() -> None:
    assert multiplier_to_amount("0.5") == Decimal("50")
    assert multiplier_to_amount("15") == Decimal("1500")
    assert multiplier_to_amount("0.3") == Decimal("30")


def test_bare_x_multiplier_not_treated_as_column() -> None:
    bet = parse_ocr_text("18 23 38 16 x0.5")

    assert bet.type == "normal"
    assert bet.numbers == [18, 23, 38, 16]
    assert bet.unit == 0.5
    assert bet.money == 50


def test_single_digit_category_with_space_not_a_number() -> None:
    bet = parse_ocr_text("11 19 24 25 3 ×1")

    assert bet.type == "normal"
    assert bet.numbers == [11, 19, 24, 25]
    assert bet.stars == [3]
    assert bet.unit == 1


def test_single_digit_category_attached() -> None:
    bet = parse_ocr_text("11 19 24 25 3X1")

    assert bet.numbers == [11, 19, 24, 25]
    assert bet.stars == [3]
    assert bet.unit == 1


def test_digit_pair_category_three_four() -> None:
    bet = parse_ocr_text("11 19 24 25 34×1")

    assert bet.numbers == [11, 19, 24, 25]
    assert bet.stars == [3, 4]
    assert bet.unit == 1


def test_stacked_category_slash_three_four() -> None:
    bet = parse_ocr_text("11.19.24.25 3/4×1")

    assert bet.type == "normal"
    assert bet.numbers == [11, 19, 24, 25]
    assert bet.stars == [3, 4]
    assert bet.unit == 1


def test_stacked_category_slash_two_three_decimal() -> None:
    bet = parse_ocr_text("09.17.26 2/3×0.5")

    assert bet.numbers == [9, 17, 26]
    assert bet.stars == [2, 3]
    assert bet.unit == 0.5


def test_fraction_glyph_three_four() -> None:
    bet = parse_ocr_text("11.19.24.25 ¾×1")

    assert bet.type == "normal"
    assert bet.numbers == [11, 19, 24, 25]
    assert bet.stars == [3, 4]
    assert bet.unit == 1


def test_fraction_glyph_two_three() -> None:
    bet = parse_ocr_text("09.17.26 ⅔×0.5")

    assert bet.numbers == [9, 17, 26]
    assert bet.stars == [2, 3]
    assert bet.unit == 0.5


def test_multiplier_in_middle_moved_to_end() -> None:
    bet = parse_ocr_text("17 234×0.5 29 38")

    assert bet.type == "normal"
    assert bet.numbers == [17, 29, 38]
    assert bet.stars == [2, 3, 4]
    assert bet.unit == 0.5


def test_short_multiplier_in_middle_moved_to_end() -> None:
    bet = parse_ocr_text("06.07.23 2×1")

    assert bet.numbers == [6, 7, 23]
    assert bet.stars == [2]
    assert bet.unit == 1
