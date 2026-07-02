from betguard.models import BetReport
from betguard.parser import parse_line
from betguard.validator import validate_bet


CAR = "\u8eca"
YUAN = "\u5143"
FULL_OPEN = "\uff08"
FULL_CLOSE = "\uff09"
HALF = "\u534a"
FULL = "\u5168"
PING = "\u576a"
SUSPECT = "\u5acc"
MULTIPLY = "\u00d7"


def report_for(text: str) -> dict:
    bet = parse_line(text)
    return BetReport(bet=bet, validation=validate_bet(bet)).to_dict()


def test_car_a_full_width_parentheses_units() -> None:
    report = report_for(f"10{FULL_OPEN}2{CAR}{FULL_CLOSE}")

    assert report["type"] == "car"
    assert report["number"] == 10
    assert report["car_units"] == 2
    assert report["money"] == 200
    assert report["status"] == "ok"


def test_car_ascii_parentheses_units() -> None:
    report = report_for(f"10(2{CAR})")

    assert report["type"] == "car"
    assert report["number"] == 10
    assert report["car_units"] == 2
    assert report["money"] == 200
    assert report["status"] == "ok"


def test_car_b_space_then_unit() -> None:
    report = report_for(f"16 {CAR}1")

    assert report["number"] == 16
    assert report["car_units"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_car_no_space_then_unit() -> None:
    report = report_for(f"16{CAR}1")

    assert report["number"] == 16
    assert report["car_units"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_car_c_money_ten_yuan() -> None:
    report = report_for(f"32{CAR}10{YUAN}")

    assert report["number"] == 32
    assert report["car_units"] == 0.1
    assert report["money"] == 10
    assert report["status"] == "ok"


def test_car_money_ten_yuan_with_spaces() -> None:
    report = report_for(f"32 {CAR} 10{YUAN}")

    assert report["type"] == "car"
    assert report["number"] == 32
    assert report["car_units"] == 0.1
    assert report["money"] == 10
    assert report["status"] == "ok"


def test_car_bare_ten_is_money() -> None:
    report = report_for(f"32{CAR}10")

    assert report["type"] == "car"
    assert report["number"] == 32
    assert report["car_units"] == 0.1
    assert report["money"] == 10
    assert report["status"] == "ok"


def test_car_bare_ten_with_space_before_car_is_money() -> None:
    report = report_for(f"32 {CAR}10")

    assert report["number"] == 32
    assert report["car_units"] == 0.1
    assert report["money"] == 10
    assert report["status"] == "ok"


def test_car_bare_ten_with_space_after_car_is_money() -> None:
    report = report_for(f"32{CAR} 10")

    assert report["number"] == 32
    assert report["car_units"] == 0.1
    assert report["money"] == 10
    assert report["status"] == "ok"


def test_car_leading_zero_number_twenty_yuan() -> None:
    report = report_for(f"08{CAR}20{YUAN}")

    assert report["type"] == "car"
    assert report["number"] == 8
    assert report["car_units"] == 0.2
    assert report["money"] == 20
    assert report["status"] == "ok"


def test_car_d_money_fifty_yuan() -> None:
    report = report_for(f"32{CAR}50{YUAN}")

    assert report["number"] == 32
    assert report["car_units"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_car_money_one_hundred_yuan() -> None:
    report = report_for(f"32{CAR}100{YUAN}")

    assert report["number"] == 32
    assert report["car_units"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_car_39_one_hundred_yuan() -> None:
    report = report_for(f"39{CAR}100{YUAN}")

    assert report["type"] == "car"
    assert report["number"] == 39
    assert report["car_units"] == 1
    assert report["money"] == 100
    assert report["status"] == "ok"


def test_car_e_decimal_unit() -> None:
    report = report_for(f"32 {CAR} 0.5")

    assert report["number"] == 32
    assert report["car_units"] == 0.5
    assert report["money"] == 50
    assert report["status"] == "ok"


def test_car_half_decimal_hyphen_formats() -> None:
    for text, number, units, money in [
        ("38-0.5", 38, 0.5, 50),
        ("16-0.5", 16, 0.5, 50),
        ("39-0.5", 39, 0.5, 50),
        ("16-0.3", 16, 0.3, 30),
    ]:
        report = report_for(text)

        assert report["type"] == "car"
        assert report["number"] == number
        assert report["car_units"] == units
        assert report["money"] == money
        assert report["status"] == "ok"


def test_car_single_number_multiplier_formats() -> None:
    for text, number, units, money in [
        (f"06{MULTIPLY}0.5", 6, 0.5, 50),
        (f"15{MULTIPLY}0.5", 15, 0.5, 50),
        (f"01{MULTIPLY}5", 1, 5, 500),
        (f"03{MULTIPLY}0.2", 3, 0.2, 20),
    ]:
        report = report_for(text)

        assert report["type"] == "car"
        assert report["number"] == number
        assert report["car_units"] == units
        assert report["money"] == money
        assert report["status"] == "ok"


def test_car_half_text_and_metadata_formats() -> None:
    for text in [f"12{HALF}{CAR}", f"12{HALF}{CAR}{PING}"]:
        report = report_for(text)

        assert report["type"] == "car"
        assert report["number"] == 12
        assert report["car_units"] == 0.5
        assert report["money"] == 50
        assert report["status"] == "ok"


def test_car_slash_and_full_car_formats() -> None:
    slash = report_for(f"07/1{CAR}{SUSPECT}")
    full = report_for(f"30{FULL}{CAR}1")

    assert slash["type"] == "car"
    assert slash["number"] == 7
    assert slash["car_units"] == 1
    assert slash["money"] == 100
    assert slash["status"] == "ok"
    assert full["type"] == "car"
    assert full["number"] == 30
    assert full["car_units"] == 1
    assert full["money"] == 100
    assert full["status"] == "ok"


def test_car_f_out_of_range_number_is_error() -> None:
    report = report_for(f"40{CAR}1")

    assert report["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in report["errors"]


def test_car_g_zero_units_is_error() -> None:
    report = report_for(f"16{CAR}0")

    assert report["status"] == "error"
    assert "invalid car units" in report["errors"]


def test_car_h_missing_amount_is_error() -> None:
    report = report_for(f"16{CAR}")

    assert report["status"] == "error"
    assert "missing car amount" in report["errors"]


def test_car_zero_number_is_error() -> None:
    report = report_for(f"00{CAR}10{YUAN}")

    assert report["status"] == "error"
    assert "number out of range 0; valid range is 1-39" in report["errors"]


def test_car_missing_number_is_error() -> None:
    report = report_for(f"{CAR}10{YUAN}")

    assert report["status"] == "error"
    assert "missing car number" in report["errors"]
