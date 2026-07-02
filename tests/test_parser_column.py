from betguard.models import BetReport
from betguard.parser import parse_line
from betguard.validator import validate_bet


TWO_THREE = "\u4e8c\u4e09"
TWO_THREE_FOUR = "\u4e8c\u4e09\u56db"
TOUCH = "\u78b0"
MULTIPLY = "\u00d7"


def report_for(text: str) -> dict:
    bet = parse_line(text)
    return BetReport(bet=bet, validation=validate_bet(bet)).to_dict()


def test_column_a_slash_columns_with_multiply_unit() -> None:
    report = report_for(f"17.20/28/34 {TWO_THREE}{MULTIPLY}1")

    assert report["type"] == "column"
    assert report["game"] == "539"
    assert report["columns"] == [[17, 20], [28], [34]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_column_b_slash_columns_with_numeric_stars_and_amount() -> None:
    report = report_for("07.14/19.21/23.33/01.39/234/100")

    assert report["type"] == "column"
    assert report["columns"] == [[7, 14], [19, 21], [23, 33], [1, 39]]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_slash_dunhao_keeps_slash_columns_and_merges_dunhao_within_column() -> None:
    report = report_for(f"17/20/28/33{IDEOGRAPHIC_COMMA}35 234\u661fX0.5")

    assert report["type"] == "column"
    assert report["columns"] == [[17], [20], [28], [33, 35]]
    assert report["columns"] != [[17, 20, 28, 33, 35]]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_column_c_chinese_stars_with_attached_money() -> None:
    report = report_for(f"24.32/13.23/16.36 {TWO_THREE}100")

    assert report["type"] == "column"
    assert report["columns"] == [[24, 32], [13, 23], [16, 36]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_column_d_touch_columns_with_decimal_unit() -> None:
    report = report_for(f"21{TOUCH}23{TOUCH}39{TOUCH}27.37 {TWO_THREE_FOUR}0.5")

    assert report["type"] == "column"
    assert report["columns"] == [[21], [23], [39], [27, 37]]
    assert report["stars"] == [2, 3, 4]
    assert report["unit"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_column_e_touch_columns_with_two_three_decimal_unit() -> None:
    report = report_for(f"02.10{TOUCH}11.25{TOUCH}34.39 {TWO_THREE}0.5")

    assert report["type"] == "column"
    assert report["columns"] == [[2, 10], [11, 25], [34, 39]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_column_f_duplicate_number_is_error() -> None:
    report = report_for(f"13.13/24 {TWO_THREE}100")

    assert report["type"] == "column"
    assert report["status"] == "error"
    assert "duplicate number 13" in report["errors"]


def test_column_g_out_of_range_number_is_error() -> None:
    report = report_for(f"41/24 {TWO_THREE}100")

    assert report["type"] == "column"
    assert report["status"] == "error"
    assert "number out of range 41; valid range is 1-39" in report["errors"]


def test_column_h_missing_amount_and_stars_warns() -> None:
    report = report_for("17.20/28/34")

    assert report["type"] == "column"
    assert report["columns"] == [[17, 20], [28], [34]]
    assert report["status"] == "warning"
    assert "missing amount" in report["warnings"]
    assert "missing stars" in report["warnings"]


TAIL = "\u5c3e"
YUAN = "\u5143"
IDEOGRAPHIC_COMMA = "\u3001"


def test_tail_a_prefix_tail_shorthand() -> None:
    report = report_for(f"{TAIL}2{IDEOGRAPHIC_COMMA}5 \u4e8c50{YUAN}")

    assert report["type"] == "column"
    assert report["columns"] == [[2, 12, 22, 32], [5, 15, 25, 35]]
    assert report["stars"] == [2]
    assert report["unit"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_tail_b_suffix_tail_touch_columns() -> None:
    report = report_for(f"2{TAIL}{TOUCH}5{TAIL} \u4e8c50{YUAN}")

    assert report["type"] == "column"
    assert report["columns"] == [[2, 12, 22, 32], [5, 15, 25, 35]]
    assert report["stars"] == [2]
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_tail_c_mixed_numbers_and_tail() -> None:
    report = report_for(f"13{TOUCH}24{TOUCH}5{TAIL} {TWO_THREE}1")

    assert report["type"] == "column"
    assert report["columns"] == [[13], [24], [5, 15, 25, 35]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_tail_d_multiply_columns_and_tail() -> None:
    report = report_for(f"13{MULTIPLY}24{MULTIPLY}8{TAIL} {TWO_THREE}{MULTIPLY}1")

    assert report["type"] == "column"
    assert report["columns"] == [[13], [24], [8, 18, 28, 38]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_tail_e_upper_x_columns_and_zero_tail() -> None:
    report = report_for(f"13X24X0{TAIL} {TWO_THREE}1")

    assert report["type"] == "column"
    assert report["columns"] == [[13], [24], [10, 20, 30]]
    assert report["stars"] == [2, 3]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_tail_f_expanded_tail_duplicate_is_error() -> None:
    report = report_for(f"13{TOUCH}24{TOUCH}3{TAIL} {TWO_THREE}1")

    assert report["type"] == "column"
    assert report["status"] == "error"
    assert "duplicate number 13" in report["errors"]


def test_tail_g_zero_and_nine_tail() -> None:
    report = report_for(f"{TAIL}0{IDEOGRAPHIC_COMMA}9 \u4e8c100")

    assert report["type"] == "column"
    assert report["columns"] == [[10, 20, 30], [9, 19, 29, 39]]
    assert report["stars"] == [2]
    assert report["unit"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"
