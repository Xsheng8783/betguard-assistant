from __future__ import annotations

from betguard.input_preprocessor import preprocess_batch_input
from betguard.review import review_text
from betguard.webfill.batch_mock_queue import NEEDS_REVIEW, READY_FOR_QUEUE, build_batch_mock_queue


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


def test_paired_slash_dunhao_column_group_with_numeric_stars() -> None:
    result = _result(f"12/17{DUN}20/06 23{STAR}X1")

    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[12, 17], [20, 6]]
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
    result = _result(f"17.20/14.18/25.29{TWO}{THREE}{FOUR}x0.5")

    assert result["status"] == "ok"
    assert result["type"] == "column"
    assert result["columns"] == [[17, 20], [14, 18], [25, 29]]
    assert result["stars"] == [2, 3, 4]
    assert result["unit"] == 0.5
    assert result["money"] == 50


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


def test_equals_with_ping_still_needs_review_or_invalid() -> None:
    queue = build_batch_mock_queue(f"15.25.16=100{PING}")

    assert queue["status"] == "BATCH_BLOCKED"
    assert queue["items"][0]["review_result"]["status"] == "error"
    assert PING in ";".join(queue["items"][0]["review_result"]["errors"])


def test_hk_ping_still_needs_review_or_invalid() -> None:
    queue = build_batch_mock_queue(f"10.16.28.09=50{DUN}hk{PING}")

    assert queue["status"] == "BATCH_BLOCKED"
    assert queue["items"][0]["review_result"]["status"] == "error"
    assert PING in ";".join(queue["items"][0]["review_result"]["errors"])


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

    assert queue["status"] == NEEDS_REVIEW
    assert any(f"13{DUN}25{DUN}28{DUN}29{DUN}37" in raw for raw in raws)
    assert f"09{CAR}2{UNIT}" in raws
    assert f"29{CAR}2{UNIT}" in raws
    assert f"25{CAR}1{UNIT}" in raws
    assert f"36{CAR}1{UNIT}" in raws
    assert f"12{CAR}1{UNIT}" in raws
    assert "2.3.4.x0.5" not in raws
    assert "539" not in raws
    assert queue["preprocessing"]["summary"]["valid_count"] > 0
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] > 0
    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False
    assert all(item["danger_buttons_clicked"] == [] for item in queue["items"])
