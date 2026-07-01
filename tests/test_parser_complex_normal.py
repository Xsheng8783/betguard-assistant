from __future__ import annotations

import pytest

from betguard.formatter import format_bet_summary
from betguard.review import build_report
from betguard.webfill.batch_mock_queue import (
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_mock_queue,
    run_current_mock_queue_item,
)


TWO = "\u4e8c"
THREE = "\u4e09"
FOUR = "\u56db"
STAR = "\u661f"
YUAN = "\u5143"
CAR = "\u8eca"
ARM = "\u81c2"
FIVE = "\u4e94"


def _report(text: str) -> dict:
    return build_report(text).to_dict()


def _assert_complex_amounts(report: dict, expected: dict[str, int]) -> None:
    assert report["type"] == "normal"
    assert report["numbers"] == [2, 10, 11, 23, 39]
    assert report["stars"] == [int(star) for star in expected]
    for star, money in expected.items():
        assert report["bets"][star]["money"] == money


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (f"{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}", {"2": 100, "3": 50, "4": 50}),
        (f"{TWO}100.{THREE}{FOUR}50", {"2": 100, "3": 50, "4": 50}),
        (f"{TWO}100{THREE}50{FOUR}50", {"2": 100, "3": 50, "4": 50}),
        (f"{TWO}{THREE}100{FOUR}50", {"2": 100, "3": 100, "4": 50}),
        (f"{TWO}{THREE}{FOUR}50", {"2": 50, "3": 50, "4": 50}),
        (
            f"{TWO}{STAR}100{YUAN} {THREE}{STAR}50{YUAN} {FOUR}{STAR}50{YUAN}",
            {"2": 100, "3": 50, "4": 50},
        ),
    ],
)
def test_complex_normal_per_star_amount_variants(suffix: str, expected: dict[str, int]) -> None:
    report = _report(f"02.10.11.23.39 {suffix}")

    assert report["status"] == "ok"
    _assert_complex_amounts(report, expected)
    assert report["original_text"] == f"02.10.11.23.39 {suffix}"


def test_complex_normal_summary_shows_per_star_amounts() -> None:
    report = _report(f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}")

    assert format_bet_summary(report) == "一般：2, 10, 11, 23, 39｜二星100元｜三星50元｜四星50元"


def test_existing_normal_column_car_and_repeated_dot_batch_still_work() -> None:
    normal = _report(f"06.13.23.22 {TWO}{THREE}50")
    normal_234 = _report(f"06.13.23.22 {TWO}{THREE}{FOUR}50")
    column = _report(f"17.20/28/34 {TWO}{THREE}1")
    car = _report(f"32{CAR}10{YUAN}")
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO}{THREE}50..32{CAR}10{YUAN}")

    assert normal["status"] == "ok"
    assert normal["money"] == 50
    assert "bets" not in normal
    assert normal_234["status"] == "ok"
    assert normal_234["money"] == 50
    assert "bets" not in normal_234
    assert column["status"] == "ok"
    assert column["type"] == "column"
    assert car["status"] == "ok"
    assert car["type"] == "car"
    assert queue["status"] == "READY_FOR_QUEUE"
    assert [item["review_result"]["type"] for item in queue["items"]] == ["normal", "car"]


@pytest.mark.parametrize(
    ("text", "expected_error"),
    [
        (f"02.10.11.23.39 {TWO}{STAR}{YUAN}", "missing or unclear amount"),
        (f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}", "missing money for star"),
        (f"02.10.11.23.39 {FIVE}{STAR}50", "unsupported characters: 五"),
        (f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{ARM}50", f"unsupported characters: {ARM}"),
        (f"02.10.11.23.1000 {TWO}{THREE}50", "unsupported or unclear betting format"),
    ],
)
def test_complex_normal_invalid_cases_are_errors(text: str, expected_error: str) -> None:
    report = _report(text)

    assert report["status"] == "error"
    assert any(expected_error in error for error in report["errors"])


def test_complex_normal_can_enter_queue_and_mock_fill_waits_for_human() -> None:
    queue = build_batch_mock_queue(f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}")
    queue = run_current_mock_queue_item(queue)

    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][0]["filled_amounts"] == {
        f"{TWO}{STAR}": 100,
        f"{THREE}{STAR}": 50,
        f"{FOUR}{STAR}": 50,
    }
    assert queue["items"][0]["danger_buttons_clicked"] == []


def test_complex_normal_audit_preserves_summary_and_star_amounts() -> None:
    queue = build_batch_mock_queue(f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}")
    audit_item = queue["audit"]["items"][0]

    assert audit_item["original_fragment"] == f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}"
    assert audit_item["parsed_summary"] == "一般：2, 10, 11, 23, 39｜二星100元｜三星50元｜四星50元"
    assert audit_item["star_amounts"]["2"]["money"] == 100
    assert audit_item["star_amounts"]["3"]["money"] == 50
    assert audit_item["star_amounts"]["4"]["money"] == 50


def test_invalid_complex_input_audit_keeps_reason() -> None:
    queue = build_batch_mock_queue(f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{ARM}50")

    assert queue["status"] == "BATCH_BLOCKED"
    invalid = queue["audit"]["preprocessing"]["invalid_fragments"][0]
    assert invalid["raw"] == f"02.10.11.23.39 {TWO}{STAR}100{YUAN}.{ARM}50"
    assert any(f"unsupported characters: {ARM}" in error for error in invalid["errors"])
