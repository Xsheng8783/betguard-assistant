from __future__ import annotations

import pytest

from betguard.webfill.batch_mock_queue import (
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
)


TWO = "\u4e8c"
THREE = "\u4e09"
FOUR = "\u56db"
ALT_TWO = "\u5169"
STAR = "\u661f"
YUAN = "\u5143"
CAR = "\u8eca"
TIMES = "\u00d7"
DUN = "\u3001"
ARM = "\u81c2"
PING = "\u576a"


REVIEW_POLICY_CASES = [
    f"港23半{CAR}",
    "大24",
    f"01{TIMES}3",
    f"03{TIMES}1.5",
    f"01{TIMES}1",
    "01.39-500改",
    "11.39-1000改",
    "02-03-05-16-20 -0.25",
    "02-03-05-16-20-0.1",
    "01 15 27 39 07 -0.3",
    "港06-13-23-22/50",
    "05-23-12-29-38/234/200嫌",
    f"33.27.30.{ALT_TWO}600{THREE}200{ARM}",
    "11.37.1000",
    "15.29.1000",
    "11.37.600",
    "11.28.400",
    "234.100",
    "234x100",
    f"1000{ARM}",
    ARM,
    "各10",
    "寫",
    "改",
]


VALID_POLICY_CASES = {
    f"02.10.11.23.39{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}": {
        "type": "normal",
        "numbers": [2, 10, 11, 23, 39],
        "stars": [2, 3, 4],
        "bets": {"2": 100, "3": 50, "4": 50},
    },
    "10,23,26,33,39 234 x 100": {
        "type": "normal",
        "numbers": [10, 23, 26, 33, 39],
        "stars": [2, 3, 4],
        "money": 100,
        "unit": 1,
    },
    f"01.03.35{TWO}{THREE}5": {
        "type": "normal",
        "numbers": [1, 3, 35],
        "stars": [2, 3],
        "money": 500,
        "unit": 5,
    },
    "11.28.37.640": {
        "type": "normal",
        "numbers": [11, 28, 37],
        "stars": [2, 3],
        "money": 200,
        "unit": 2,
    },
    "08 28 33 39 440": {
        "type": "normal",
        "numbers": [8, 28, 33, 39],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"19.39.22.12.35.23{TWO}{THREE}{FOUR}15": {
        "type": "normal",
        "numbers": [19, 39, 22, 12, 35, 23],
        "stars": [2, 3, 4],
        "money": 15,
        "unit": 0.15,
    },
    f"04.32.33{TWO}{THREE}2": {
        "type": "normal",
        "numbers": [4, 32, 33],
        "stars": [2, 3],
        "money": 200,
        "unit": 2,
    },
    f"01,39 2{TIMES}5": {
        "type": "normal",
        "numbers": [1, 39],
        "stars": [2],
        "money": 500,
        "unit": 5,
    },
    f"08{DUN}10{DUN}17{DUN}21 234{STAR}X0.5": {
        "type": "normal",
        "numbers": [8, 10, 17, 21],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"11/17{DUN}21/33/36 234{STAR}X0.5": {
        "type": "column",
        "columns": [[11], [17, 21], [33], [36]],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"10/35/21.39/02.32{TWO}{THREE}{FOUR}x1": {
        "type": "column",
        "columns": [[10], [35], [21, 39], [2, 32]],
        "stars": [2, 3, 4],
        "money": 100,
        "unit": 1,
    },
    "02-11x2": {
        "type": "normal",
        "numbers": [2, 11],
        "stars": [2],
        "money": 200,
        "unit": 2,
    },
    "01.39-1500": {
        "type": "normal",
        "numbers": [1, 39],
        "stars": [2],
        "money": 1500,
        "unit": 15,
    },
    "01.11.39-200": {
        "type": "normal",
        "numbers": [1, 11, 39],
        "stars": [2, 3],
        "money": 200,
        "unit": 2,
    },
    "20-30-22-23/50": {
        "type": "normal",
        "numbers": [20, 30, 22, 23],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"32{CAR}10{YUAN}": {
        "type": "car",
        "number": 32,
        "money": 10,
        "car_units": 0.1,
    },
    f"12半{CAR}": {
        "type": "car",
        "number": 12,
        "money": 50,
        "car_units": 0.5,
    },
    f"10 -60{CAR}": {
        "type": "car",
        "number": 10,
        "money": 6000,
        "car_units": 60,
    },
    "15.25.33=100": {
        "type": "normal",
        "numbers": [15, 25, 33],
        "stars": [2, 3],
        "money": 100,
        "unit": 1,
    },
    "09.10.28.16=50": {
        "type": "normal",
        "numbers": [9, 10, 28, 16],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"15.25.16= 100{PING}": {
        "type": "normal",
        "numbers": [15, 25, 16],
        "stars": [2, 3],
        "money": 100,
        "unit": 1,
    },
    f"10.16.28.09=50{DUN}hk{PING}": {
        "type": "normal",
        "numbers": [10, 16, 28, 9],
        "stars": [2, 3, 4],
        "money": 50,
        "unit": 0.5,
    },
    f"17.20/28/34 {TWO}{THREE}1": {
        "type": "column",
        "columns": [[17, 20], [28], [34]],
        "stars": [2, 3],
        "money": 100,
        "unit": 1,
    },
    f"06.13.23.22 {TWO}{THREE}50": {
        "type": "normal",
        "numbers": [6, 13, 23, 22],
        "stars": [2, 3],
        "money": 50,
        "unit": 0.5,
    },
}


@pytest.mark.parametrize("text", REVIEW_POLICY_CASES)
def test_real_sample_review_policy_cases_are_not_clean_valid(text: str) -> None:
    queue = build_batch_mock_queue(text)
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] in {"warning", "error"}
    assert item["original_fragment"] == text
    assert result.get("warnings") or result.get("errors")
    assert queue["audit"]["preprocessing"]["invalid_fragments"][0]["raw"] == text
    audit_item = queue["audit"]["items"][0]
    assert audit_item["original_fragment"] == text
    assert audit_item["mock_result_summary"]["danger_buttons_clicked"] == []


@pytest.mark.parametrize("text", list(VALID_POLICY_CASES))
def test_real_sample_valid_policy_cases_keep_expected_semantics(text: str) -> None:
    queue = build_batch_mock_queue(text)
    result = queue["items"][0]["review_result"]
    expected = VALID_POLICY_CASES[text]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["type"] == expected["type"]
    for key in ("numbers", "columns", "number", "stars", "money", "unit", "car_units"):
        if key in expected:
            assert result.get(key) == expected[key]
    if "bets" in expected:
        for star, money in expected["bets"].items():
            assert result["bets"][star]["money"] == money
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False


def test_mixed_real_sample_policy_queue_and_accept_valid_flow() -> None:
    text = f"06.13.23.22 {TWO}{THREE}50..港23半{CAR}..12半{CAR}..32{CAR}10{YUAN}..2星寫2..15.25.33=100..08 28 33 39 440"

    queue = build_batch_mock_queue(text)

    assert queue["status"] == NEEDS_REVIEW
    assert [item["raw"] for item in queue["preprocessing"]["valid_candidates"]] == [
        f"06.13.23.22 {TWO}{THREE}50",
        f"12半{CAR}",
        f"32{CAR}10{YUAN}",
        "15.25.33=100",
        "08 28 33 39 440",
    ]
    assert [item["raw"] for item in queue["preprocessing"]["invalid_fragments"]] == [
        f"港23半{CAR}",
        "2星寫2",
    ]
    assert all(item["selected_numbers"] == [] for item in queue["items"])

    accepted = accept_valid_candidates_for_mock_queue(queue, run_first=True)
    assert accepted["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert [item["original"] for item in accepted["items"]] == [
        f"06.13.23.22 {TWO}{THREE}50",
        f"12半{CAR}",
        f"32{CAR}10{YUAN}",
        "15.25.33=100",
        "08 28 33 39 440",
    ]
    assert accepted["preprocessing"]["original_review_audit"]["invalid_fragments"][0]["raw"] == f"港23半{CAR}"
    assert accepted["preprocessing"]["original_review_audit"]["invalid_fragments"][1]["raw"] == "2星寫2"
    assert accepted["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert accepted["audit"]["safety"]["real_site_operation"] is False
    assert accepted["audit"]["safety"]["auto_submit"] is False
    assert accepted["audit"]["safety"]["danger_buttons_clicked"] == []
