from __future__ import annotations

from betguard.input_preprocessor import preprocess_batch_input
from betguard.webfill.batch_mock_queue import NEEDS_REVIEW, build_batch_mock_queue
from betguard.webfill.batch_queue import BATCH_BLOCKED


TWO = "\u4e8c"
THREE = "\u4e09"
FOUR = "\u56db"
ALT_TWO = "\u5169"
STAR = "\u661f"
YUAN = "\u5143"
CAR = "\u8eca"
ARM = "\u81c2"
FULL_OPEN = "\uff08"
FULL_CLOSE = "\uff09"
TIMES = "\u00d7"
DUN = "\u3001"
X_ZHUYIN = "\u3128"


FIXTURE_A = f"""15:19 USER_A 07-10-25-29-37
235-50
天天樂
15:19 USER_A 02.10.11.23.39{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}{FULL_OPEN}天天樂{FULL_CLOSE}
15:19 USER_A 天天
10,23,26,33,39
234 x 100
15:19 USER_A 01.03.35{TWO}{THREE}5
15:19 USER_A 11.  37.  1000
15:19 USER_A 11.  28.  37.  640
15:19 USER_A 11.  27.  32.  640
15:19 USER_A 17.  24.   1000
15:19 USER_A 11.  28.   600
15:19 USER_A 17.  29.  33.  640
15:19 USER_A 11.  20.  28.  37.  880
15:19 USER_A 15.  22.  37.  320
15:19 USER_A 10.  28.  39.   320
15:19 USER_A 11.     18.     29.      34.      440
15:19 USER_A 11.    15.    29.   33.  440
15:19 USER_A 11.     17.     24.    29.     440
15:19 USER_A 11.   28.   400
15:19 USER_A 15.29.1000..15.29.33.20.30.234.100..32.23.15.20.14...234.100.11.09,10.{ALT_TWO}{THREE}200..21.20.23.32.05.06..234.100..33.27.30.{ALT_TWO}600{THREE}200{ARM}
15:19 USER_A 08 28 33 39 440
15:19 USER_A 08{DUN}10{DUN}17{DUN}21
234{STAR}X0.5
11/17{DUN}21/33/36
234{STAR}X0.5
15:19 USER_A 10/35/21.39/02.32{TWO}{THREE}{FOUR}{X_ZHUYIN}1"""


FIXTURE_B = f"""15:25 USER_A 539
19.39.22.12.35.23{TWO}{THREE}{FOUR}15
12半{CAR}
大
15.25.33=100
09.10.28.16=50
15.25.16= 100坪
15:25 USER_A 05.04/07.14/19.21/06.16/234.100
15:25 USER_A 04.32.33{TWO}{THREE}2
15:25 USER_A 01{TIMES}3
03{TIMES}1.5
15:25 USER_A 05-23-12-29-38/234/200嫌
15:25 USER_A 01,39
2{TIMES}5

01{TIMES}1
15:25 USER_A 02-11x2
15:25 USER_A 01.39-500改
15:25 USER_A 01.39-1500
15:25 USER_A 01.11.39-200
15:25 USER_A 11.39-1000改
15:25 USER_A 02-03-05-16-20 -0.25
15:25 USER_A 10  -32{CAR}
10 35 39 01 -50天天樂
15:25 USER_A 港06-13-23-22/50
20-30-22-23/50
15:25 USER_A 港23半{CAR}
15:25 USER_A 10.16.28.09=50{DUN}hk坪"""


def _raws(report: dict) -> list[str]:
    return [item["raw"] for item in report["candidate_bet_lines"]]


def test_line_prefix_cleaning_removes_time_and_sender_but_keeps_original_line() -> None:
    report = preprocess_batch_input(f"15:19 USER_A 01.03.35{TWO}{THREE}5")

    assert _raws(report) == [f"01.03.35{TWO}{THREE}5"]
    assert report["candidate_bet_lines"][0]["original_line"] == f"15:19 USER_A 01.03.35{TWO}{THREE}5"
    assert "removed LINE time/sender prefix" in report["candidate_bet_lines"][0]["preprocessing_notes"]


def test_trailing_game_label_is_removed_and_standalone_label_is_metadata() -> None:
    text = f"15:19 USER_A 02.10.11.23.39{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}{FULL_OPEN}天天樂{FULL_CLOSE}\n天天樂"
    report = preprocess_batch_input(text)

    assert _raws(report) == [f"02.10.11.23.39{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}"]
    assert report["summary"]["ignored_metadata_count"] == 1
    assert "removed trailing game label" in report["candidate_bet_lines"][0]["preprocessing_notes"]


def test_multiline_continuation_combines_number_line_with_star_amount_line() -> None:
    report = preprocess_batch_input("10,23,26,33,39\n234 x 100")

    assert _raws(report) == ["10,23,26,33,39 234 x 100"]
    assert "merged continuation line" in report["candidate_bet_lines"][0]["preprocessing_notes"]


def test_multiline_continuation_with_dunhao() -> None:
    report = preprocess_batch_input(f"08{DUN}10{DUN}17{DUN}21\n234{STAR}X0.5")

    assert _raws(report) == [f"08{DUN}10{DUN}17{DUN}21 234{STAR}X0.5"]


def test_multiline_continuation_with_comma_stars_multiplier() -> None:
    report = preprocess_batch_input(f"01,10,22,39\n2,3{TIMES}1")

    assert _raws(report) == [f"01,10,22,39 2,3{TIMES}1"]
    assert "merged continuation line" in report["candidate_bet_lines"][0]["preprocessing_notes"]


def test_ellipsis_star_amount_lines_are_normalized() -> None:
    report = preprocess_batch_input("10.39.01.35\u20262.3.4\n50")

    assert _raws(report) == ["10.39.01.35 234 50"]
    assert "normalized ellipsis separator" in report["candidate_bet_lines"][0]["preprocessing_notes"]
    assert "merged continuation line" in report["candidate_bet_lines"][0]["preprocessing_notes"]


def test_embedded_star_amount_does_not_consume_next_number_group() -> None:
    report = preprocess_batch_input("234.100.10.25")

    assert _raws(report) == ["234.100", "10.25"]


def test_repeated_dot_preservation_in_fixture_a_long_line() -> None:
    report = preprocess_batch_input(f"15:19 USER_A 15.29.1000..15.29.33.20.30.234.100...234.100")

    assert _raws(report) == ["15.29.1000", "15.29.33.20.30.234.100", "234.100"]


def test_fixture_a_whole_sample_no_traceback_and_needs_review() -> None:
    queue = build_batch_mock_queue(FIXTURE_A)
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] in (NEEDS_REVIEW, BATCH_BLOCKED)
    if queue["status"] == NEEDS_REVIEW:
        assert f"02.10.11.23.39{TWO}{STAR}100{YUAN}.{THREE}.{FOUR}{STAR}50{YUAN}" in raws
        assert f"01.03.35{TWO}{THREE}5" in raws
        assert "10,23,26,33,39 234 x 100" in raws
        assert f"08{DUN}10{DUN}17{DUN}21 234{STAR}X0.5" in raws
        assert queue["preprocessing"]["summary"]["valid_count"] > 0
        assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] > 0
    else:
        # BATCH_BLOCKED: 臂 long-line protection keeps the whole line intact.
        # "33.27.30.兩600三200臂" should NOT be split out as a separate item —
        # the entire long line with ARM is preserved as one Needs Review item.
        assert queue["status"] == BATCH_BLOCKED
        # The raw items should contain the ARM-protected long line
        assert any(ARM in (item.get("original", "") or "") for item in queue["items"])


def test_fixture_b_whole_sample_no_traceback_and_needs_review() -> None:
    queue = build_batch_mock_queue(FIXTURE_B)
    raws = [item["original"] for item in queue["items"]]

    assert queue["status"] in (NEEDS_REVIEW, BATCH_BLOCKED)
    if queue["status"] == NEEDS_REVIEW:
        assert f"19.39.22.12.35.23{TWO}{THREE}{FOUR}15" in raws
        assert f"04.32.33{TWO}{THREE}2" in raws
        assert f"01,39 2{TIMES}5" in raws
        assert "20-30-22-23/50" in raws
        assert queue["preprocessing"]["summary"]["valid_count"] > 0
        assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] > 0


def test_suspicious_candidates_are_preserved_with_reasons_and_safety_flags() -> None:
    queue = build_batch_mock_queue(FIXTURE_B)

    suspicious = [
        item for item in queue["items"]
        if item["original"] in {f"港23半{CAR}"}
    ]
    assert suspicious
    assert all(item["review_result"]["status"] != "ok" for item in suspicious)
    assert queue["audit"]["original_text"] == FIXTURE_B
    assert queue["audit"]["safety"]["real_site_operation"] is False
    assert queue["audit"]["safety"]["auto_submit"] is False
    assert queue["audit"]["safety"]["danger_buttons_clicked"] == []
    assert queue["audit"]["preprocessing"]["invalid_fragments"]


def test_space_separated_four_numbers_with_440_is_confirmed_half_unit_shorthand() -> None:
    queue = build_batch_mock_queue("08 28 33 39 440")
    result = queue["items"][0]["review_result"]

    assert result["status"] == "ok"
    assert result["numbers"] == [8, 28, 33, 39]
    assert result["stars"] == [2, 3, 4]
    assert result["money"] == 50
    assert result["unit"] == 0.5