from betguard.review import build_report
from betguard.webfill.fill_plan import build_fill_plan


TWO_THREE = "\u4e8c\u4e09"
YUAN = "\u5143"
CAR = "\u8eca"
MULTIPLY = "\u00d7"


def result_for(text: str) -> dict:
    return build_report(text).to_dict()


def test_normal_fill_plan_formats_numbers_as_two_digits() -> None:
    plan = build_fill_plan(result_for("06-13-23-22/50"))

    assert plan["mode"] == "assisted_fill_plan_v0"
    assert plan["executable"] is False
    assert plan["requires_human_review"] is True
    assert plan["game"] == "539"
    assert plan["bet_type"] == "normal"
    assert plan["numbers"] == ["06", "13", "23", "22"]


def test_normal_fill_plan_converts_stars_to_chinese_labels() -> None:
    plan = build_fill_plan(result_for("06-13-23-22/50"))

    assert plan["stars"] == ["二星", "三星", "四星"]


def test_normal_fill_plan_applies_money_to_each_star() -> None:
    plan = build_fill_plan(result_for("06-13-23-22/50"))

    assert plan["amounts"] == {
        "二星": 50,
        "三星": 50,
        "四星": 50,
    }
    assert {"type": "set_amount", "star": "二星", "amount": 50} in plan["planned_steps"]
    assert {"type": "set_amount", "star": "三星", "amount": 50} in plan["planned_steps"]
    assert {"type": "set_amount", "star": "四星", "amount": 50} in plan["planned_steps"]


def test_duplicate_error_does_not_produce_plan() -> None:
    plan = build_fill_plan(result_for(f"13.38.13 {TWO_THREE}100"))

    assert plan["executable"] is False
    assert "planned_steps" not in plan
    assert "review result is not ok" in plan["errors"]


def test_column_is_unsupported_in_v0() -> None:
    plan = build_fill_plan(result_for(f"17.20/28/34 {TWO_THREE}{MULTIPLY}1"))

    assert plan["executable"] is False
    assert "unsupported in v0: column fill plan requires selector verification" in plan["errors"]


def test_car_is_disabled_in_v0() -> None:
    plan = build_fill_plan(result_for(f"32{CAR}10{YUAN}"))

    assert plan["executable"] is False
    assert "unsupported in v0: car page has no confirmation, fill plan disabled" in plan["errors"]


def test_forbidden_steps_are_always_present() -> None:
    plan = build_fill_plan(result_for("06-13-23-22/50"))

    assert "submit" in plan["forbidden_steps"]
    assert "confirm" in plan["forbidden_steps"]
    assert "send_bet" in plan["forbidden_steps"]
    assert "click_danger_button" in plan["forbidden_steps"]


def test_per_star_money_is_preserved() -> None:
    report = {
        "game": "539",
        "type": "normal",
        "numbers": [7, 12, 20],
        "stars": [3, 4],
        "unit": None,
        "money": None,
        "bets": {
            "3": {"money": 10},
            "4": {"unit": 0.05, "money": 5},
        },
        "status": "ok",
        "warnings": [],
        "errors": [],
    }

    plan = build_fill_plan(report)

    assert plan["amounts"] == {"三星": 10, "四星": 5}
