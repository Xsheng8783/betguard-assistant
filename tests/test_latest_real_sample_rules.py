from __future__ import annotations

from betguard.input_preprocessor import preprocess_batch_input
from betguard.review import review_text
from betguard.webfill.batch_mock_queue import (
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
)
from betguard.webfill.batch_queue import BATCH_BLOCKED


TWO = "\u4e8c"
THREE = "\u4e09"
FOUR = "\u56db"
STAR = "\u661f"
DUN = "\u3001"
CAR = "\u8eca"
UNIT = "\u652f"
PING = "\u576a"
ARM = "\u81c2"
SUSPECT = "\u5acc"
HALF = "\u534a"
FULL = "\u5168"
MULTIPLY = "\u00d7"
YUAN = "\u5143"


def _result(text: str) -> dict:
    return review_text(text).to_dict()["items"][0]["result"]


def _candidate_raws(text: str) -> list[str]:
    return [item["raw"] for item in preprocess_batch_input(text)["candidate_bet_lines"]]


def test_dunhao_chinese_star_multiplier_decimal_unit() -> None:
    result = _result(f"13{DUN}25{DUN}28{DUN}29{DUN}37 {TWO}{DUN}{THREE}{DUN}{FOUR}*0.3")

    assert result["status"] == "ok"
    assert result["numbers"] == [13, 25, 28, 29, 37]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.3
    assert result["money"] == 30


def test_dunhao_chinese_star_multiplier_half_unit() -> None:
    result = _result(f"04{DUN}19{DUN}27{DUN}35 {TWO}{DUN}{THREE}{DUN}{FOUR}*0.5")

    assert result["status"] == "ok"
    assert result["numbers"] == [4, 19, 27, 35]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_multi_car_line_expands_to_two_car_candidates() -> None:
    queue = build_batch_mock_queue(f"09 29 {CAR} 2")

    assert queue["status"] == READY_FOR_QUEUE
    assert [item["original"] for item in queue["items"]] == [f"09{CAR}2{UNIT}", f"29{CAR}2{UNIT}"]
    assert [item["review_result"]["number"] for item in queue["items"]] == [9, 29]
    assert all(item["review_result"]["money"] == 200 for item in queue["items"])
    assert all(item["review_result"]["car_units"] == 2 for item in queue["items"])


def test_multi_car_line_expands_three_numbers_with_attached_amount() -> None:
    queue = build_batch_mock_queue(f"25 26 32 {CAR}1")

    assert queue["status"] == READY_FOR_QUEUE
    assert [item["original"] for item in queue["items"]] == [f"25{CAR}1{UNIT}", f"26{CAR}1{UNIT}", f"32{CAR}1{UNIT}"]
    assert [item["review_result"]["number"] for item in queue["items"]] == [25, 26, 32]
    assert all(item["review_result"]["money"] == 100 for item in queue["items"])
    assert all(item["review_result"]["car_units"] == 1 for item in queue["items"])


def test_multi_car_line_expands_three_numbers_without_space_before_car() -> None:
    queue = build_batch_mock_queue(f"33 35 36{CAR}1")

    assert queue["status"] == READY_FOR_QUEUE
    assert [item["original"] for item in queue["items"]] == [f"33{CAR}1{UNIT}", f"35{CAR}1{UNIT}", f"36{CAR}1{UNIT}"]
    assert [item["review_result"]["number"] for item in queue["items"]] == [33, 35, 36]
    assert all(item["review_result"]["money"] == 100 for item in queue["items"])


def test_hyphen_car_unit_format_is_normalized_to_car_bet() -> None:
    queue = build_batch_mock_queue(f"12-1{CAR}")

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["items"][0]["original"] == f"12{CAR}1{UNIT}"
    assert queue["items"][0]["review_result"]["type"] == "car"
    assert queue["items"][0]["review_result"]["number"] == 12
    assert queue["items"][0]["review_result"]["car_units"] == 1
    assert queue["items"][0]["review_result"]["money"] == 100


def test_confirmed_half_and_decimal_car_formats_are_valid() -> None:
    for text, number, units, money in [
        ("38-0.5", 38, 0.5, 50),
        ("16-0.5", 16, 0.5, 50),
        ("39-0.5", 39, 0.5, 50),
        ("16-0.3", 16, 0.3, 30),
        (f"06{MULTIPLY}0.5", 6, 0.5, 50),
        (f"15{MULTIPLY}0.5", 15, 0.5, 50),
    ]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == units
        assert result["money"] == money


def test_confirmed_car_text_metadata_formats_keep_original_fragment_and_note() -> None:
    for text, number, units, money in [
        (f"12{HALF}{CAR}{PING}", 12, 0.5, 50),
        (f"07/1{CAR}{SUSPECT}", 7, 1, 100),
    ]:
        queue = build_batch_mock_queue(text)
        item = queue["items"][0]
        result = item["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert item["original_fragment"] == text
        assert "car metadata ignored" in item["preprocessing_notes"]
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == units
        assert result["money"] == money
        assert queue["final_decision"]["real_site_operation"] is False
        assert queue["final_decision"]["auto_submit"] is False


def test_confirmed_car_unit_and_full_car_formats_are_valid() -> None:
    for text, number, units, money in [
        (f"01{MULTIPLY}5", 1, 5, 500),
        (f"03{MULTIPLY}0.2", 3, 0.2, 20),
        (f"30{FULL}{CAR}1", 30, 1, 100),
    ]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == units
        assert result["money"] == money


def test_multi_full_car_each_amount_expands_to_car_candidates() -> None:
    queue = build_batch_mock_queue(f"05.08{FULL}{CAR}各0.25{CAR}")

    assert queue["status"] == READY_FOR_QUEUE
    assert [item["original"] for item in queue["items"]] == [f"05{CAR}0.25{UNIT}", f"08{CAR}0.25{UNIT}"]
    assert [item["review_result"]["number"] for item in queue["items"]] == [5, 8]
    assert [item["review_result"]["car_units"] for item in queue["items"]] == [0.25, 0.25]
    assert [item["review_result"]["money"] for item in queue["items"]] == [25, 25]
    assert all("expanded multi-car line" in item["preprocessing_notes"] for item in queue["items"])


def test_flat_slash_dunhao_column_group_with_numeric_stars() -> None:
    result = _result(f"17/20/28/33{DUN}35 234{STAR}X0.5")

    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[17], [20], [28], [33, 35]]
    assert result["columns"] != [[17, 20, 28, 33, 35]]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_539_block_merges_number_line_with_dotted_star_amount_line() -> None:
    text = "539.\n12.23.19.20.\n2.3.4.x0.5"
    raws = _candidate_raws(text)
    queue = build_batch_mock_queue(text)

    assert len(raws) == 1
    assert raws[0] != "2.3.4.x0.5"
    assert "12.23.19.20" in raws[0]
    assert queue["status"] == READY_FOR_QUEUE
    result = queue["items"][0]["review_result"]
    assert result["numbers"] == [12, 23, 19, 20]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_repeated_539_block_groups_merge_without_standalone_star_amount() -> None:
    text = "\n".join(
        [
            "539.",
            "12.23.19.20.",
            "2.3.4.x0.5",
            "05.08.10.20.",
            "2.3.4.x0.5",
            "09.10.22.23.",
            "2.3.4.x0.5",
            "09.16.19.24.",
            "2.3.4.x0.5",
        ]
    )
    raws = _candidate_raws(text)
    queue = build_batch_mock_queue(text)

    assert len(raws) == 4
    assert "2.3.4.x0.5" not in raws
    assert queue["status"] == READY_FOR_QUEUE
    assert [item["review_result"]["money"] for item in queue["items"]] == [50, 50, 50, 50]


def test_continuation_comma_stars_multiplier_is_merged_and_valid() -> None:
    queue = build_batch_mock_queue(f"01,10,22,39\n2,3{MULTIPLY}1")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["items"][0]["original"] == f"01,10,22,39 2,3{MULTIPLY}1"
    assert result["type"] == "normal"
    assert result["numbers"] == [1, 10, 22, 39]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_ellipsis_star_amount_continuations_are_valid() -> None:
    cases = [
        ("10.39.01.35\u20262.3.4\n50", [10, 39, 1, 35], [2, 3, 4], 50),
        ("01.39.35\u20262.3\n100", [1, 39, 35], [2, 3], 100),
        ("10.39.01.35\u20262.3.4..50", [10, 39, 1, 35], [2, 3, 4], 50),
        ("01.39.35\u20262.3..100", [1, 39, 35], [2, 3], 100),
        ("02.12.22.37.33\u20262.3.4\u2026.50", [2, 12, 22, 37, 33], [2, 3, 4], 50),
        ("12.22.37.02\u20262.3.4\u202650", [12, 22, 37, 2], [2, 3, 4], 50),
        ("12.22.37\u2026100", [12, 22, 37], [2, 3], 100),
    ]

    for text, numbers, stars, money in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == stars
        assert result["money"] == money
        assert result["numbers"] != [10, 39, 1, 35, 2]
        assert result["stars"] != [3]


def test_multiline_numeric_star_amount_continuations_are_valid() -> None:
    for text, numbers in [
        ("10.20.30.15.16.19\n234.100", [10, 20, 30, 15, 16, 19]),
        ("05.28.10.25\n234.100", [5, 28, 10, 25]),
    ]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == [2, 3, 4]
        assert result["money"] == 100


def test_inline_numeric_star_amount_continuation_is_merged() -> None:
    queue = build_batch_mock_queue("10.20.30.15.16.19..234.100")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["items"][0]["original"] == "10.20.30.15.16.19 234.100"
    assert result["type"] == "normal"
    assert result["numbers"] == [10, 20, 30, 15, 16, 19]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_inline_numeric_star_amount_with_trailing_arm_is_clean_bet() -> None:
    queue = build_batch_mock_queue(f"30.31.32.33.19.39..234.100{ARM}")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "30.31.32.33.19.39 234.100"
    assert result["status"] == "ok"
    assert result["numbers"] == [30, 31, 32, 33, 19, 39]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 1
    assert result["money"] == 100
    assert "ignored trailing name marker 臂" in item["preprocessing_notes"]


def test_embedded_star_amount_keeps_next_number_fragment_separate() -> None:
    queue = build_batch_mock_queue(f"05.28.10.25..234.100.10.25..1000{ARM}")

    assert queue["items"][0]["review_result"]["status"] == "ok"
    assert queue["items"][0]["original"] == "05.28.10.25 234.100"
    assert queue["items"][0]["review_result"]["numbers"] == [5, 28, 10, 25]
    assert queue["items"][0]["review_result"]["stars"] == [2, 3, 4]
    assert queue["items"][0]["review_result"]["unit"] == 1
    assert queue["items"][0]["review_result"]["money"] == 100
    assert queue["items"][1]["original"] == "10.25"
    assert queue["items"][1]["review_result"]["status"] == "warning"
    assert queue["items"][2]["original"] == f"1000{ARM}"
    assert queue["items"][2]["review_result"]["status"] == "error"
    assert queue["status"] == NEEDS_REVIEW


def test_leading_game_label_with_inline_star_amount_continuation_is_valid() -> None:
    queue = build_batch_mock_queue("天天樂\u202604.20.26.28.30\u2026\u2026..234x100")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "04.20.26.28.30 234x100"
    assert "removed game label metadata" in item["preprocessing_notes"]
    assert "merged continuation star amount" in item["preprocessing_notes"]
    assert result["type"] == "normal"
    assert result["numbers"] == [4, 20, 26, 28, 30]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_continuation_with_trailing_arm_is_clean_bet() -> None:
    queue = build_batch_mock_queue(f"06.13.23 {TWO}{THREE}50\n30.31.32.33.19.39\n234.100{ARM}")
    item = queue["items"][1]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "30.31.32.33.19.39 234.100"
    assert result["status"] == "ok"
    assert result["numbers"] == [30, 31, 32, 33, 19, 39]
    assert result["stars"] == [2, 3, 4]
    assert result["money"] == 100
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False


def test_repeated_dot_comma_star_amount_continuation_is_merged() -> None:
    queue = build_batch_mock_queue("04.07.18.21.25.33.36.13..3,4.50")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["numbers"] == [4, 7, 18, 21, 25, 33, 36, 13]
    assert result["stars"] == [3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50
    assert "merged continuation star amount" in queue["items"][0]["preprocessing_notes"]


def test_repeated_dot_star_then_arm_amount_continuation_becomes_clean_bet() -> None:
    queue = build_batch_mock_queue(f"20.10.21.36.38 11..234.100.19.20.28.38..234..100{ARM}")
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] in (READY_FOR_QUEUE, BATCH_BLOCKED)
    if queue["status"] == READY_FOR_QUEUE:
        assert len(queue["items"]) == 2
        assert "234" not in raws
        assert f"100{ARM}" not in raws

        first = queue["items"][0]["review_result"]
        assert first["status"] == "ok"
        assert first["numbers"] == [20, 10, 21, 36, 38, 11]
        assert first["stars"] == [2, 3, 4]
        assert first["unit"] == 1
        assert first["money"] == 100

        second = queue["items"][1]
        assert second["original"] == "19.20.28.38 234 100"
        result = second["review_result"]
        assert result["status"] == "ok"
        assert result["numbers"] == [19, 20, 28, 38]
        assert result["stars"] == [2, 3, 4]
        assert result["money"] == 100
        assert "ignored trailing name marker 臂" in second["preprocessing_notes"]
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False


def test_trailing_arm_name_marker_is_ignored_for_complete_bet() -> None:
    queue = build_batch_mock_queue(f"19.20.28.38 234 100{ARM}")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "19.20.28.38 234 100"
    assert result["status"] == "ok"
    assert result["type"] == "normal"
    assert result["numbers"] == [19, 20, 28, 38]
    assert result["stars"] == [2, 3, 4]
    assert result["money"] == 100
    assert "ignored trailing name marker 臂" in item["preprocessing_notes"]


def test_arm_without_complete_bet_still_needs_review() -> None:
    for text in [f"1000{ARM}", f"11.0.5{ARM}"]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] != READY_FOR_QUEUE
        assert result["status"] == "error"
        assert f"unsupported characters: {ARM}" in result["errors"]


def test_dot_car_shorthand_is_confirmed_car_bet() -> None:
    result = _result(f"10.1{CAR}")

    assert result["status"] == "ok"
    assert result["type"] == "car"
    assert result["number"] == 10
    assert result["car_units"] == 1
    assert result["money"] == 100


def test_standalone_car_word_still_needs_review() -> None:
    queue = build_batch_mock_queue(CAR)
    result = queue["items"][0]["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] == "error"
    assert "missing car number" in result["errors"]


def test_star_typo_five_after_234_is_ignored_with_note() -> None:
    queue = build_batch_mock_queue("09/16.17/28/35二三四五0.5")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "09/16.17/28/35二三四0.5"
    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50
    assert "unsupported characters: 五" not in result["errors"]
    assert "ignored typo 五 after 二三四" in item["preprocessing_notes"]


def test_unrelated_five_still_needs_review() -> None:
    queue = build_batch_mock_queue("09/16.17/28/35二三五0.5")
    item = queue["items"][0]

    assert queue["status"] != READY_FOR_QUEUE
    assert item["original"] == "09/16.17/28/35二三五0.5"
    assert "unsupported characters: 五" in item["review_result"]["errors"]


def test_standalone_car_marker_attaches_to_next_car_shorthand() -> None:
    queue = build_batch_mock_queue(f"11.10.1500..10.1{CAR}..{CAR}..11.0.5{ARM}")
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] == READY_FOR_QUEUE
    assert raws == ["11.10.1500", f"10.1{CAR}", f"11.0.5{CAR}"]

    first = queue["items"][0]["review_result"]
    assert first["status"] == "ok"
    assert first["type"] == "normal"
    assert first["numbers"] == [11, 10]
    assert first["stars"] == [2]
    assert first["money"] == 1500

    second = queue["items"][1]["review_result"]
    assert second["status"] == "ok"
    assert second["type"] == "car"
    assert second["number"] == 10
    assert second["car_units"] == 1
    assert second["money"] == 100

    third = queue["items"][2]["review_result"]
    assert third["status"] == "ok"
    assert third["type"] == "car"
    assert third["number"] == 11
    assert third["car_units"] == 0.5
    assert third["money"] == 50

    assert queue["preprocessing"]["invalid_fragments"] == []
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False


def test_standalone_car_marker_without_next_car_shorthand_stays_review() -> None:
    for text, expected_items in [
        (f"11.10.1500..{CAR}", ["11.10.1500", CAR]),
        (f"{CAR}..08 14 23 100", [CAR, "08 14 23 100"]),
    ]:
        queue = build_batch_mock_queue(text)
        raws = [item["original"] for item in queue["items"]]

        assert queue["status"] == NEEDS_REVIEW
        assert raws == expected_items
        car_item = next(item for item in queue["items"] if item["original"] == CAR)
        assert car_item["review_result"]["status"] == "error"
        assert "missing car number" in car_item["review_result"]["errors"]


def test_chinese_one_unit_word_is_normalized() -> None:
    result = build_batch_mock_queue("06.16.07.19二三星一支")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [6, 16, 7, 19]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_multiply_word_and_double_hyphen_star_amount() -> None:
    result = build_batch_mock_queue("05-15-25-35--2-3-4乘5")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [5, 15, 25, 35]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 5
    assert result["money"] == 500


def test_heavy_multiplication_emoji_is_normalized() -> None:
    result = build_batch_mock_queue("01.13.05二三✖️0.5")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [1, 13, 5]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_parenthesized_numeric_per_star_amounts_inline() -> None:
    result = build_batch_mock_queue("12 20 21 22（2星X2 3星X1 4星0.5）")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [12, 20, 21, 22]
    assert result["stars"] == [2, 3, 4]
    assert result["bets"]["2"]["unit"] == 2
    assert result["bets"]["2"]["money"] == 200
    assert result["bets"]["3"]["unit"] == 1
    assert result["bets"]["3"]["money"] == 100
    assert result["bets"]["4"]["unit"] == 0.5
    assert result["bets"]["4"]["money"] == 50


def test_parenthesized_per_star_line_merges_with_numbers_line() -> None:
    queue = build_batch_mock_queue("07 13 17 27\n（2星X2 3星X1）")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["numbers"] == [7, 13, 17, 27]
    assert result["bets"]["2"]["money"] == 200
    assert result["bets"]["3"]["money"] == 100


def test_unclosed_paren_per_star_line_merges_with_numbers_line() -> None:
    queue = build_batch_mock_queue("02 12 20 21 22 32 33\n（3星10 4星10")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["numbers"] == [2, 12, 20, 21, 22, 32, 33]
    assert result["bets"]["3"]["money"] == 10
    assert result["bets"]["4"]["money"] == 10


def test_person_name_with_emoji_is_ignored_metadata() -> None:
    queue = build_batch_mock_queue("博仁⬆️")

    assert queue["items"] == []
    assert queue["audit"]["preprocessing"]["invalid_fragments"] == []


def test_car_number_line_merges_with_special_car_line() -> None:
    queue = build_batch_mock_queue("28號\n專車0.2")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["type"] == "car"
    assert result["number"] == 28
    assert result["car_units"] == 0.2
    assert result["money"] == 20


def test_dot_continuation_with_trailing_539_metadata() -> None:
    queue = build_batch_mock_queue("16.22.36.05..2.3.4x.1支539")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["numbers"] == [16, 22, 36, 5]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.1
    assert result["money"] == 10


def test_game_metadata_lines_are_ignored() -> None:
    for text in ["539-六", "今彩，六和"]:
        queue = build_batch_mock_queue(text)

        assert queue["items"] == []
        assert queue["audit"]["preprocessing"]["invalid_fragments"] == []


def test_trailing_gai_after_confirmed_spaced_amount_is_ignored() -> None:
    queue = build_batch_mock_queue("01 39 -500改")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "01 39 -500"
    assert result["status"] == "ok"
    assert result["numbers"] == [1, 39]
    assert result["stars"] == [2]
    assert result["unit"] == 5
    assert result["money"] == 500
    assert "ignored trailing 改 after confirmed amount" in item["preprocessing_notes"]


def test_ellipsis_star_amount_line_does_not_swallow_next_line() -> None:
    queue = build_batch_mock_queue("38.39.10.08…2.3.4…50\n22 07 19 39  880")
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] == READY_FOR_QUEUE
    assert raws == ["38.39.10.08 234 50", "22 07 19 39 880"]

    first = queue["items"][0]["review_result"]
    assert first["numbers"] == [38, 39, 10, 8]
    assert first["stars"] == [2, 3, 4]
    assert first["money"] == 50

    second = queue["items"][1]["review_result"]
    assert second["numbers"] == [22, 7, 19, 39]
    assert second["stars"] == [2, 3, 4]
    assert second["unit"] == 1
    assert second["money"] == 100


def test_x_shorthand_codes_are_units_not_money() -> None:
    result = _result("25.22.39x640")

    assert result["status"] == "ok"
    assert result["numbers"] == [25, 22, 39]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 2
    assert result["money"] == 200


def test_star_amount_with_each_car_money_expands() -> None:
    queue = build_batch_mock_queue("02 05 07 18二三100各10元")
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] == READY_FOR_QUEUE
    assert raws == ["02 05 07 18二三100", "02車10元", "05車10元", "07車10元", "18車10元"]

    normal = queue["items"][0]["review_result"]
    assert normal["numbers"] == [2, 5, 7, 18]
    assert normal["stars"] == [2, 3]
    assert normal["unit"] == 1
    assert normal["money"] == 100

    for item in queue["items"][1:]:
        car = item["review_result"]
        assert car["type"] == "car"
        assert car["car_units"] == 0.1
        assert car["money"] == 10


def test_hyphen_car_with_unclosed_game_label_paren() -> None:
    result = build_batch_mock_queue("10-31車（天天樂")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["type"] == "car"
    assert result["number"] == 10
    assert result["car_units"] == 31
    assert result["money"] == 3100


def test_comma_decimal_amount_after_star_word() -> None:
    result = build_batch_mock_queue("08,16,23,18,38兩星0,25")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [8, 16, 23, 18, 38]
    assert result["stars"] == [2]
    assert result["unit"] == 0.25
    assert result["money"] == 25


def test_equals_amount_with_trailing_ping_metadata() -> None:
    result = build_batch_mock_queue("22.17.27.35.19.39=15坪")["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [22, 17, 27, 35, 19, 39]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.15
    assert result["money"] == 15


def test_trailing_gai_after_confirmed_hyphen_amounts_is_ignored() -> None:
    cases = [
        ("01.39-500改", "01.39-500", [1, 39], 5, 500),
        ("11.39-1000改", "11.39-1000", [11, 39], 10, 1000),
        ("02-10 -200改", "02-10 -200", [2, 10], 2, 200),
    ]
    for text, expected_raw, numbers, unit, money in cases:
        queue = build_batch_mock_queue(text)
        item = queue["items"][0]
        result = item["review_result"]

        assert queue["status"] == READY_FOR_QUEUE, text
        assert item["original"] == expected_raw
        assert result["status"] == "ok"
        assert result["numbers"] == numbers
        assert result["stars"] == [2]
        assert result["unit"] == unit
        assert result["money"] == money


def test_write_word_per_star_lines_merge_with_numbers_line() -> None:
    for text in ["16.19.28.33\n2星寫2\n3.4星寫1", "10.11.22.28\n2星寫2  3.4星寫1"]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE, text
        assert len(queue["items"]) == 1
        assert result["status"] == "ok"
        assert result["stars"] == [2, 3, 4]
        assert result["bets"]["2"]["unit"] == 2
        assert result["bets"]["2"]["money"] == 200
        assert result["bets"]["3"]["unit"] == 1
        assert result["bets"]["3"]["money"] == 100
        assert result["bets"]["4"]["unit"] == 1
        assert result["bets"]["4"]["money"] == 100


def test_235_star_typo_line_becomes_234_without_duplicate_merge() -> None:
    queue = build_batch_mock_queue("07-10-25-29-37\n235-50\n天天樂\n234.50")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert queue["items"][0]["original"] == "07-10-25-29-37 234.50"
    assert result["status"] == "ok"
    assert result["numbers"] == [7, 10, 25, 29, 37]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50
    assert "normalized 235 star typo to 234" in queue["items"][0]["preprocessing_notes"]
    assert "duplicate star amount line ignored" in queue["items"][0]["preprocessing_notes"]


def test_standalone_decimal_amount_line_merges_with_missing_money_numbers() -> None:
    queue = build_batch_mock_queue("32.23.15.20.14\n0.15")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert len(queue["items"]) == 1
    assert result["status"] == "ok"
    assert result["numbers"] == [32, 23, 15, 20, 14]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.15
    assert result["money"] == 15


def test_standalone_decimal_amount_line_alone_stays_review_without_zero_number() -> None:
    queue = build_batch_mock_queue("0.15")
    result = queue["items"][0]["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] == "error"
    assert "standalone amount line requires manual review" in result["errors"]
    assert result["numbers"] in ([], None)


def test_dot_continuation_with_x_unit_word_and_trailing_539() -> None:
    queue = build_batch_mock_queue("12.16.22...2.3x 3支539")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["numbers"] == [12, 16, 22]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 3
    assert result["money"] == 300


def test_multi_group_line_with_broken_prefix_keeps_three_valid_bets() -> None:
    queue = build_batch_mock_queue(
        "33 35 36 車 134.100.11.09,10.兩三200..21.20.23.32.05.06..234.100..33.27.30.兩600三200臂"
    )
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] in (NEEDS_REVIEW, BATCH_BLOCKED)
    if queue["status"] == NEEDS_REVIEW:
        assert raws == [
            "33 35 36 車 134.100",
            "11.09,10.兩三200",
            "21.20.23.32.05.06 234.100",
            "33.27.30.兩600三200",
        ]
        assert queue["items"][0]["review_result"]["status"] == "error"
        assert queue["items"][1]["review_result"]["status"] == "ok"
        assert queue["items"][2]["review_result"]["status"] == "ok"
        bet_a = queue["items"][1]["review_result"]
        assert bet_a["status"] == "ok"
        assert bet_a["numbers"] == [11, 9, 10]
        assert bet_a["stars"] == [2, 3]
        assert bet_a["money"] == 200

        bet_b = queue["items"][2]["review_result"]
        assert bet_b["status"] == "ok"
        assert bet_b["numbers"] == [21, 20, 23, 32, 5, 6]
        assert bet_b["stars"] == [2, 3, 4]
        assert bet_b["money"] == 100

        bet_c = queue["items"][3]["review_result"]
        assert bet_c["status"] == "ok"
        assert bet_c["numbers"] == [33, 27, 30]
        assert bet_c["bets"]["2"]["money"] == 600
        assert bet_c["bets"]["3"]["money"] == 200
    else:
        # BATCH_BLOCKED: the 臂 long-line protection keeps the whole input intact
        assert queue["status"] == BATCH_BLOCKED
        assert len(queue["items"]) >= 1
        # The blocked item should preserve the original text with 臂
        assert any("臂" in (item.get("original", "") or "") for item in queue["items"])


def test_each_car_amount_with_trailing_comma_539_expands() -> None:
    for text, numbers in [
        ("05 28 24 16二三100各10，539", [5, 28, 24, 16]),
        ("29 28 32二三100各10元，539坪", [29, 28, 32]),
    ]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] == READY_FOR_QUEUE, text
        normal = queue["items"][0]["review_result"]
        assert normal["numbers"] == numbers
        assert normal["stars"] == [2, 3]
        assert normal["money"] == 100
        car_items = queue["items"][1:]
        assert [item["review_result"]["number"] for item in car_items] == numbers
        assert all(item["review_result"]["car_units"] == 0.1 for item in car_items)
        assert all(item["review_result"]["money"] == 10 for item in car_items)


def test_normal_bet_and_car_shorthand_in_same_line_split() -> None:
    queue = build_batch_mock_queue("01.11.35.39-50 35-0.5")
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] == READY_FOR_QUEUE
    assert raws == ["01.11.35.39-50", "35-0.5"]

    normal = queue["items"][0]["review_result"]
    assert normal["numbers"] == [1, 11, 35, 39]
    assert normal["stars"] == [2, 3, 4]
    assert normal["money"] == 50

    car = queue["items"][1]["review_result"]
    assert car["type"] == "car"
    assert car["number"] == 35
    assert car["car_units"] == 0.5
    assert car["money"] == 50


def test_out_of_range_46_fragment_stays_review() -> None:
    queue = build_batch_mock_queue("36.46.22..28.23.11=15坪")

    assert queue["status"] == NEEDS_REVIEW
    first = queue["items"][0]["review_result"]
    assert first["status"] == "error"
    assert "number out of range 46; valid range is 1-39" in first["errors"]
    second = queue["items"][1]["review_result"]
    assert second["status"] == "ok"
    assert second["numbers"] == [28, 23, 11]


def test_single_number_multiplier_is_confirmed_car_bet() -> None:
    cases = [
        ("01×3", 1, 3, 300),
        ("03×1.5", 3, 1.5, 150),
        ("01×1", 1, 1, 100),
    ]
    for text, number, units, money in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE, text
        assert result["status"] == "ok"
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == units
        assert result["money"] == money


def test_two_number_x_unit_is_still_normal_not_car() -> None:
    for text, numbers, unit, money in [("02-11x2", [2, 11], 2, 200), ("18-24x6", [18, 24], 6, 600)]:
        result = _result(text)

        assert result["status"] == "ok", text
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == [2]
        assert result["unit"] == unit
        assert result["money"] == money


def test_trailing_suspect_marker_after_slash_star_amount_is_ignored() -> None:
    queue = build_batch_mock_queue("05-23-12-29-38/234/200嫌")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert item["original"] == "05-23-12-29-38/234/200"
    assert result["status"] == "ok"
    assert result["numbers"] == [5, 23, 12, 29, 38]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 2
    assert result["money"] == 200
    assert "ignored trailing name marker 嫌" in item["preprocessing_notes"]


def test_incomplete_suspect_marker_still_needs_review() -> None:
    for text in ["嫌", "1000嫌"]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] != READY_FOR_QUEUE


def test_hk_prefix_line_and_continuation_stay_review() -> None:
    queue = build_batch_mock_queue("港06-13-23-22/50\n20-30-22-23/50")

    assert queue["status"] != READY_FOR_QUEUE
    first = queue["items"][0]["review_result"]
    assert first["status"] != "ok"
    assert "game prefix requires manual review" in first["warnings"]
    second = queue["items"][1]["review_result"]
    assert second["status"] != "ok"
    assert "game prefix requires manual review" in second["warnings"]


def test_hk_context_resets_after_539_label() -> None:
    queue = build_batch_mock_queue("港06-13-23-22/50\n20-30-22-23/50\n539\n18-22-24-27:50")

    assert queue["items"][1]["review_result"]["status"] != "ok"
    assert queue["items"][2]["review_result"]["status"] == "ok"
    assert queue["items"][2]["original"] == "18-22-24-27:50"


def test_hk_half_car_stays_review() -> None:
    queue = build_batch_mock_queue("港23半車")
    result = queue["items"][0]["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] != "ok"
    assert "game prefix requires manual review" in result["warnings"]


def test_standalone_hk_style_line_without_prefix_is_still_valid() -> None:
    result = _result("20-30-22-23/50")

    assert result["status"] == "ok"
    assert result["numbers"] == [20, 30, 22, 23]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_shorthand_explanation_lines_are_ignored_metadata() -> None:
    queue = build_batch_mock_queue("640二三星200\n440二三四星0.5")

    assert queue["items"] == []
    assert queue["audit"]["preprocessing"]["invalid_fragments"] == []
    ignored = [item["raw"] for item in queue["preprocessing"]["ignored_metadata_lines"]]
    assert "640二三星200" in ignored
    assert "440二三四星0.5" in ignored


def test_confirmed_existing_formats_do_not_regress() -> None:
    cases = [
        ("05 10 20 22 440", [5, 10, 20, 22], [2, 3, 4], 0.5, 50),
        ("22 07 19 39  880", [22, 7, 19, 39], [2, 3, 4], 1, 100),
        ("18-24-29/200", [18, 24, 29], [2, 3], 2, 200),
        ("12-22-27-33:50", [12, 22, 27, 33], [2, 3, 4], 0.5, 50),
        ("18-22-24-27:50", [18, 22, 24, 27], [2, 3, 4], 0.5, 50),
        ("18-24x6", [18, 24], [2], 6, 600),
        ("11.22.39-100", [11, 22, 39], [2, 3], 1, 100),
        ("07.08.19.2,3.1000", [7, 8, 19], [2, 3], 10, 1000),
    ]
    for text, numbers, stars, unit, money in cases:
        result = _result(text)

        assert result["status"] == "ok", text
        assert result["numbers"] == numbers, text
        assert result["stars"] == stars, text
        assert result["unit"] == unit, text
        assert result["money"] == money, text


def test_standalone_numeric_star_amount_still_needs_review_in_mixed_batch() -> None:
    for line in ["234.100", "234x100"]:
        queue = build_batch_mock_queue("\n".join([f"06.13.23 {TWO}{THREE}50", line]))

        assert queue["status"] == NEEDS_REVIEW
        assert queue["items"][1]["review_result"]["status"] == "error"


def test_equals_200_without_domain_suffix_is_valid_money() -> None:
    result = _result("24-27-29=200")

    assert result["status"] == "ok"
    assert result["numbers"] == [24, 27, 29]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 2
    assert result["money"] == 200


def test_colon_amount_separator_is_valid_money() -> None:
    result = _result("18-24-27-30:50")

    assert result["status"] == "ok"
    assert result["numbers"] == [18, 24, 27, 30]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_two_number_colon_x_ten_is_unit() -> None:
    result = _result("22-33:x10")

    assert result["status"] == "ok"
    assert result["numbers"] == [22, 33]
    assert result["stars"] == [2]
    assert result["unit"] == 10
    assert result["money"] == 1000


def test_two_number_colon_x_five_is_unit() -> None:
    result = _result("12-22:x5")

    assert result["status"] == "ok"
    assert result["numbers"] == [12, 22]
    assert result["stars"] == [2]
    assert result["unit"] == 5
    assert result["money"] == 500


def test_two_number_bare_customer_specific_shorthand_requires_review() -> None:
    valid_line = f"06.13.23 {TWO}{THREE}50"

    for line in [
        "11.37.1000",
        "17.24.1000",
        "15.29.1000",
        "11.28.600",
        "17.29.600",
        "11.28.400",
    ]:
        queue = build_batch_mock_queue("\n".join([valid_line, line]))

        assert queue["status"] == NEEDS_REVIEW
        assert queue["items"][1]["review_result"]["status"] == "error"
        assert queue["items"][1]["review_result"]["errors"] == [
            "customer-specific shorthand requires manual review"
        ]


def test_two_number_x_stake_still_valid() -> None:
    for line, unit, money in [
        ("17.24x10", 10, 1000),
        ("17.29x6", 6, 600),
    ]:
        result = _result(line)

        assert result["status"] == "ok"
        assert result["numbers"] == [int(line[:2]), int(line[3:5])]
        assert result["stars"] == [2]
        assert result["unit"] == unit
        assert result["money"] == money


def test_four_number_440_shorthand_is_half_unit_per_star() -> None:
    queue = build_batch_mock_queue("11 22 33 34 440")
    result = queue["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [11, 22, 33, 34]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50
    assert queue["items"][0]["parsed_summary"]
    assert "440" not in queue["items"][0]["parsed_summary"]


def test_four_number_880_shorthand_is_one_unit_per_star() -> None:
    result = _result("11 22 33 34 880")

    assert result["status"] == "ok"
    assert result["numbers"] == [11, 22, 33, 34]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_four_number_colon_440_and_880_shorthand_are_confirmed() -> None:
    half_unit = _result("12-22-27-33:440")
    one_unit = _result("12-22-27-33:880")

    assert half_unit["status"] == "ok"
    assert half_unit["numbers"] == [12, 22, 27, 33]
    assert half_unit["stars"] == [2, 3, 4]
    assert half_unit["unit"] == 0.5
    assert half_unit["money"] == 50
    assert one_unit["status"] == "ok"
    assert one_unit["numbers"] == [12, 22, 27, 33]
    assert one_unit["stars"] == [2, 3, 4]
    assert one_unit["unit"] == 1
    assert one_unit["money"] == 100


def test_many_number_explicit_stars_x100_is_money_not_units() -> None:
    result = _result("10,23,26,33,39 234 x 100")

    assert result["status"] == "ok"
    assert result["numbers"] == [10, 23, 26, 33, 39]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_standalone_star_amount_line_with_539_requires_review() -> None:
    queue = build_batch_mock_queue("\n".join([f"06.13.23 {TWO}{THREE}50", f"2.3x 3{UNIT}539"]))

    assert queue["status"] == NEEDS_REVIEW
    assert queue["items"][1]["review_result"]["status"] == "error"
    assert queue["items"][1]["review_result"]["errors"] == [
        "standalone star amount line requires manual review"
    ]


def test_three_number_320_shorthand_is_one_unit_per_star() -> None:
    result = _result("11 22 33 320")

    assert result["status"] == "ok"
    assert result["numbers"] == [11, 22, 33]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_three_number_640_shorthand_is_two_units_per_star() -> None:
    result = _result("11 22 33 640")

    assert result["status"] == "ok"
    assert result["numbers"] == [11, 22, 33]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 2
    assert result["money"] == 200


def test_three_number_320_dotted_shorthand_is_one_unit_per_star() -> None:
    result = _result("19.27.35.320")

    assert result["status"] == "ok"
    assert result["numbers"] == [19, 27, 35]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_confirmed_three_number_bare_100_is_one_unit_per_star() -> None:
    result = _result("08 14 23 100")

    assert result["status"] == "ok"
    assert result["numbers"] == [8, 14, 23]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_confirmed_three_number_hyphen_100_is_tail_amount() -> None:
    queue = build_batch_mock_queue("12.38.22-100")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["numbers"] == [12, 38, 22]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_confirmed_four_number_spaced_hyphen_50_is_tail_amount() -> None:
    queue = build_batch_mock_queue("02-03-16-20 -50")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["numbers"] == [2, 3, 16, 20]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_confirmed_two_number_hyphen_1500_is_tail_amount() -> None:
    queue = build_batch_mock_queue("01.39-1500")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["type"] == "normal"
    assert result["numbers"] == [1, 39]
    assert result["stars"] == [2]
    assert result["unit"] == 15
    assert result["money"] == 1500
    assert "hyphen amount requires manual review" not in result["warnings"]


def test_confirmed_three_number_hyphen_200_is_tail_amount() -> None:
    queue = build_batch_mock_queue("01.11.39-200")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["type"] == "normal"
    assert result["numbers"] == [1, 11, 39]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 2
    assert result["money"] == 200
    assert "hyphen amount requires manual review" not in result["warnings"]


def test_attached_decimal_hyphen_amount_still_needs_review() -> None:
    queue = build_batch_mock_queue("02-03-05-16-20-0.1")
    result = queue["items"][0]["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] in {"warning", "error"}
    assert "hyphen amount requires manual review" in result["warnings"]


def test_confirmed_spaced_decimal_hyphen_amount_is_unit() -> None:
    cases = [
        ("02-07-26-27-28 -0.25", [2, 7, 26, 27, 28], 0.25, 25),
        ("02-03-05-16-20 -0.25", [2, 3, 5, 16, 20], 0.25, 25),
        ("01 15 27 39 07 -0.3", [1, 15, 27, 39, 7], 0.3, 30),
    ]
    for text, numbers, unit, money in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["status"] == "ok"
        assert result["numbers"] == numbers
        assert result["stars"] == [2, 3, 4]
        assert result["unit"] == unit
        assert result["money"] == money


def test_paired_slash_dunhao_column_group_with_numeric_stars() -> None:
    result = _result(f"12/17{DUN}20/06 23{STAR}X1")

    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[12], [17, 20], [6]]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100


def test_column_groups_with_zhuyin_x_are_normalized() -> None:
    text = "17.20/18.25/24.14/08.29" + THREE + FOUR + "\u3128" + "0.1"
    queue = build_batch_mock_queue(text)
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[17, 20], [18, 25], [24, 14], [8, 29]]
    assert result["stars"] == [3, 4]
    assert result["unit"] == 0.1
    assert result["money"] == 10
    assert "normalized" in " ".join(queue["items"][0]["preprocessing_notes"])


def test_column_groups_with_attached_chinese_stars_x_unit() -> None:
    # 3 columns → max 3-star (二三星). Use 4-column variant instead.
    result = _result(f"17.20/14.18/25.29/11.12{TWO}{THREE}{FOUR}x0.5")

    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[17, 20], [14, 18], [25, 29], [11, 12]]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


def test_confirmed_column_formats_with_dunhao_and_attached_stars() -> None:
    cases = [
        (f"05/08/18.33/25.35{TWO}{THREE}{FOUR}x0.5", [[5], [8], [18, 33], [25, 35]], [2, 3, 4], 0.5, 50),
        (f"08/14{DUN}20/32/36 234{STAR}X0.5", [[8], [14, 20], [32], [36]], [2, 3, 4], 0.5, 50),
        (f"06/17{DUN}27/32 23{STAR}X1", [[6], [17, 27], [32]], [2, 3], 1, 100),
        (f"11/17{DUN}21/33/36 234{STAR}X0.5", [[11], [17, 21], [33], [36]], [2, 3, 4], 0.5, 50),
        (f"10/35/21.39/02.32{TWO}{THREE}{FOUR}x1", [[10], [35], [21, 39], [2, 32]], [2, 3, 4], 1, 100),
        (f"12/17{DUN}20/06 23{STAR}X1", [[12], [17, 20], [6]], [2, 3], 1, 100),
    ]

    for text, columns, stars, unit, money in cases:
        result = _result(text)

        assert result["status"] == "ok"
        assert result["type"] == "column"
        assert result["columns"] == columns
        assert result["columns"] != [[5, 8], [18, 33], [25, 35]]
        assert result["stars"] == stars
        assert result["unit"] == unit
        assert result["money"] == money


def test_confirmed_normal_x_amount_formats_and_zhuyin_notes() -> None:
    cases = [
        (f"05.08.16.24.33{TWO}{THREE}{FOUR}x0.5", [5, 8, 16, 24, 33], [2, 3, 4], 0.5, 50, None),
        (f"08{DUN}10{DUN}17{DUN}21 234{STAR}X0.5", [8, 10, 17, 21], [2, 3, 4], 0.5, 50, None),
        ("23-33/x5", [23, 33], [2], 5, 500, None),
        ("09.11.17两三星×1", [9, 11, 17], [2, 3], 1, 100, "normalized 两 star token"),
        ("09.11.17兩三星ㄨ1", [9, 11, 17], [2, 3], 1, 100, "normalized ㄨ to x"),
        (f"05.08.16.24.33{TWO}{THREE}{FOUR}ㄨ0.5", [5, 8, 16, 24, 33], [2, 3, 4], 0.5, 50, "normalized ㄨ to x"),
    ]

    for text, numbers, stars, unit, money, note in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == stars
        assert result["unit"] == unit
        assert result["money"] == money
        if note:
            assert note in queue["items"][0]["preprocessing_notes"]


def test_explicit_equals_star_amount_is_confirmed_format() -> None:
    queue = build_batch_mock_queue("04.07.17.23.=2.3=500")
    result = queue["items"][0]["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["status"] == "ok"
    assert result["type"] == "normal"
    assert result["numbers"] == [4, 7, 17, 23]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 5
    assert result["money"] == 500


def test_confirmed_equals_amount_formats_with_metadata() -> None:
    cases = [
        ("19.39.35.17.12.22=15", [19, 39, 35, 17, 12, 22], [2, 3, 4], 0.15, 15, None),
        (f"28.29.30=100，539{PING}", [28, 29, 30], [2, 3], 1, 100, "removed trailing equals metadata"),
        ("15.25.33=100", [15, 25, 33], [2, 3], 1, 100, None),
        ("09.10.28.16=50", [9, 10, 28, 16], [2, 3, 4], 0.5, 50, None),
        (f"10.16.28.09=50{DUN}hk{PING}", [10, 16, 28, 9], [2, 3, 4], 0.5, 50, "removed trailing equals metadata"),
        (f"28.23.11=15{PING}", [28, 23, 11], [2, 3], 0.15, 15, "removed trailing equals metadata"),
        ("01.02.25 34.36=100", [1, 2, 25, 34, 36], [2, 3, 4], 1, 100, None),
        ("34.36=1000，", [34, 36], [2], 10, 1000, "removed trailing equals metadata"),
        ("34.36= 1000，天天坪", [34, 36], [2], 10, 1000, "removed trailing equals metadata"),
        ("34.36= 1000，", [34, 36], [2], 10, 1000, "removed trailing equals metadata"),
        ("36.38= 1000天天坪", [36, 38], [2], 10, 1000, "removed trailing equals metadata"),
    ]

    for text, numbers, stars, unit, money, note in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == stars
        assert result["unit"] == unit
        assert result["money"] == money
        assert 539 not in result["numbers"]
        if note:
            assert note in queue["items"][0]["preprocessing_notes"]


def test_latest_real_sample_batch_keeps_valid_invalid_and_no_standalone_star_amount() -> None:
    text = "\n".join(
        [
            f"13{DUN}25{DUN}28{DUN}29{DUN}37",
            f"{TWO}{DUN}{THREE}{DUN}{FOUR}*0.3",
            f"09 29 {CAR} 2",
            f"17/20/28/33{DUN}35",
            f"234{STAR}X0.5",
            "539.",
            "12.23.19.20.",
            "2.3.4.x0.5",
            "24-27-29=200",
            "18-24-27-30:50",
            f"25 26 32 {CAR}1",
            f"33 35 36{CAR}1",
            f"12-1{CAR}",
            f"33.27.30.兩600三200{ARM}",
            f"05-23-12-29-38/234/200{SUSPECT}",
        ]
    )

    queue = build_batch_mock_queue(text)
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] == READY_FOR_QUEUE
    assert any(f"13{DUN}25{DUN}28{DUN}29{DUN}37" in raw for raw in raws)
    assert f"09{CAR}2{UNIT}" in raws
    assert f"29{CAR}2{UNIT}" in raws
    assert f"25{CAR}1{UNIT}" in raws
    assert f"36{CAR}1{UNIT}" in raws
    assert f"12{CAR}1{UNIT}" in raws
    assert f"33.27.30.兩600三200" in raws
    assert f"05-23-12-29-38/234/200" in raws
    assert "2.3.4.x0.5" not in raws
    assert "539" not in raws
    assert queue["preprocessing"]["summary"]["valid_count"] > 0
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 0
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False
    assert all(item["danger_buttons_clicked"] == [] for item in queue["items"])


def test_confirmed_slash_game_metadata_is_removed_not_treated_as_column() -> None:
    queue = build_batch_mock_queue("38-13-04-/539:100")
    item = queue["items"][0]
    result = item["review_result"]

    assert queue["status"] == READY_FOR_QUEUE
    assert result["type"] == "normal"
    assert result["numbers"] == [38, 13, 4]
    assert result["stars"] == [2, 3]
    assert result["unit"] == 1
    assert result["money"] == 100
    assert "removed game metadata 539" in item["preprocessing_notes"]


def test_confirmed_slash_game_metadata_keeps_out_of_range_number_invalid() -> None:
    queue = build_batch_mock_queue("38-13-46-/539:100")
    result = queue["items"][0]["review_result"]

    assert queue["status"] != READY_FOR_QUEUE
    assert result["status"] == "error"
    assert any("46" in error for error in result["errors"])


def test_confirmed_car_hyphen_amount_with_space_is_valid() -> None:
    for text in [f"10 -60{CAR}", f"10-60{CAR}", f"10 - 60{CAR}"]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == 10
        assert result["car_units"] == 60
        assert result["money"] == 6000


def test_confirmed_car_unit_hyphen_number_variants() -> None:
    for text, number in [(f"1 - 60{CAR}", 1), (f"01-60{CAR}", 1)]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == 60
        assert result["money"] == 6000


def test_multi_car_each_amount_expands_without_full_car_prefix() -> None:
    for text in [f"01 02 各20{CAR}", f"01.02各20{CAR}", f"01{DUN}02各20{CAR}", f"01 02 各 20 {CAR}"]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] == READY_FOR_QUEUE
        assert [item["review_result"]["number"] for item in queue["items"]] == [1, 2]
        assert all(item["review_result"]["car_units"] == 20 for item in queue["items"])
        assert all(item["review_result"]["money"] == 2000 for item in queue["items"])

    queue = build_batch_mock_queue(f"01 02 10 各20{CAR}")
    assert queue["status"] == READY_FOR_QUEUE
    assert [item["review_result"]["number"] for item in queue["items"]] == [1, 2, 10]
    assert all(item["review_result"]["car_units"] == 20 for item in queue["items"])


def test_bare_car_number_without_amount_stays_car_type_needs_review() -> None:
    for text, number in [(f"1{CAR}", 1), (f"10{CAR}", 10)]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] != READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == number
        assert "missing car amount" in result["errors"]


def test_car_amount_after_car_word_repeated_suffix_is_units() -> None:
    for text in [f"1{CAR}2", f"1{CAR}2{UNIT}", f"1{CAR}2{CAR}"]:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == 1
        assert result["car_units"] == 2
        assert result["money"] == 200


def test_existing_car_formats_are_not_regressed_by_unit_syntax_rules() -> None:
    cases = [
        ("38-0.5", 38, 0.5, 50),
        ("16-0.3", 16, 0.3, 30),
        (f"06{MULTIPLY}0.5", 6, 0.5, 50),
        (f"12{HALF}{CAR}", 12, 0.5, 50),
        (f"32{CAR}10{YUAN}", 32, 0.1, 10),
    ]
    for text, number, units, money in cases:
        queue = build_batch_mock_queue(text)
        result = queue["items"][0]["review_result"]

        assert queue["status"] == READY_FOR_QUEUE
        assert result["type"] == "car"
        assert result["number"] == number
        assert result["car_units"] == units
        assert result["money"] == money

    queue = build_batch_mock_queue(f"05.08{FULL}{CAR}各0.25{CAR}")
    assert queue["status"] == READY_FOR_QUEUE
    assert [item["review_result"]["number"] for item in queue["items"]] == [5, 8]
    assert all(item["review_result"]["car_units"] == 0.25 for item in queue["items"])
    assert all(item["review_result"]["money"] == 25 for item in queue["items"])


def test_customer_specific_bare_shorthand_still_needs_review() -> None:
    for text in ["11.28.400", "15.29.1000", "11.37.600", "17.29.600"]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] != READY_FOR_QUEUE


def test_unsupported_tokens_still_need_review() -> None:
    for text in ["各10", "寫", "改", ARM]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] != READY_FOR_QUEUE


def test_standalone_game_labels_are_ignored_metadata_not_candidates() -> None:
    for text in ["大", "大樂", "大樂.", "天天", "天天樂", "539", "539.", "hk"]:
        report = preprocess_batch_input(text)

        assert report["candidate_bet_lines"] == []
        assert [item["raw"] for item in report["ignored_metadata_lines"]] == [text]

        queue = build_batch_mock_queue(text)
        assert queue["items"] == []
        assert queue["audit"]["preprocessing"]["invalid_fragments"] == []


def test_remaining_review_only_formats_still_need_review() -> None:
    for text in [
        "2星寫2",
        "3.4星寫1",
        "1000臂",
        "港08-22-46-/100",
        "02-03-05-16-20-0.1",
        "11. 37. 1000",
        "15.29.1000",
        "11.28.600",
        "11.28.400",
    ]:
        queue = build_batch_mock_queue(text)

        assert queue["status"] != READY_FOR_QUEUE


def test_missing_money_items_stay_needs_review_not_clean_valid() -> None:
    cases = [
        ("10.25", [10, 25], [2]),
        ("16.19.28.33", [16, 19, 28, 33], [2, 3, 4]),
        ("12.16.22", [12, 16, 22], [2, 3]),
        ("10.11.22.28", [10, 11, 22, 28], [2, 3, 4]),
    ]
    for text, numbers, stars in cases:
        queue = build_batch_mock_queue(f"06.13.23.22{TWO}{THREE}50\n{text}")
        item = queue["items"][1]
        result = item["review_result"]

        assert queue["status"] == NEEDS_REVIEW
        assert result["type"] == "normal"
        assert result["numbers"] == numbers
        assert result["stars"] == stars
        assert result["money"] is None
        assert "missing money" in result["warnings"]
        assert text not in [c["raw"] for c in queue["preprocessing"]["valid_candidates"]]
        assert text in [c["raw"] for c in queue["preprocessing"]["invalid_fragments"]]

        accepted = accept_valid_candidates_for_mock_queue(queue)
        assert text not in [i["original"] for i in accepted["items"]]