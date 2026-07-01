from betguard.models import BetReport
from betguard.parser import parse_line
from betguard.validator import validate_bet


CAR = "\u8eca"
YUAN = "\u5143"
FULL_OPEN = "\uff08"
FULL_CLOSE = "\uff09"


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
