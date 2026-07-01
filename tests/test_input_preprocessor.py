from __future__ import annotations

from betguard.input_preprocessor import preprocess_batch_input
from betguard.webfill.batch_mock_queue import (
    BATCH_BLOCKED,
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
    format_pretty_batch_mock_queue,
    reject_batch_review,
)


TWO = "\u4e8c"
THREE = "\u4e09"
ALT_TWO = "\u5169"
CAR = "\u8eca"
YUAN = "\u5143"
ARM = "\u81c2"


def _candidate_raws(text: str) -> list[str]:
    return [item["raw"] for item in preprocess_batch_input(text)["candidate_bet_lines"]]


def test_clean_single_bet_line_is_unchanged() -> None:
    text = f"06.13.23.22 {TWO}{THREE}50"

    report = preprocess_batch_input(text)

    assert _candidate_raws(text) == [text]
    assert report["ignored_metadata_lines"] == []


def test_line_metadata_is_ignored_and_only_bets_are_candidates() -> None:
    bet = f"06.13.23.22 {TWO}{THREE}50"
    text = "\n".join(
        [
            "\u4e0a\u534812:04",
            "\u4e0b\u53483:21",
            "2026/07/02",
            "7\u67082\u65e5 \u661f\u671f\u56db",
            "Masm Anakq",
            "\u738b\u5c0f\u660e",
            "\u5df2\u8b80",
            "\u4ee5\u4e0b\u662f\u6295\u6ce8",
            bet,
        ]
    )

    report = preprocess_batch_input(text)

    assert [item["raw"] for item in report["candidate_bet_lines"]] == [bet]
    assert report["summary"]["ignored_metadata_count"] == 8


def test_repeated_dots_split_bet_groups_without_touching_single_dot_numbers() -> None:
    text = f"06.13.23.22 {TWO}{THREE}50..32{CAR}10{YUAN}"

    report = preprocess_batch_input(text)

    assert [item["raw"] for item in report["candidate_bet_lines"]] == [
        f"06.13.23.22 {TWO}{THREE}50",
        f"32{CAR}10{YUAN}",
    ]


def test_batch_queue_accepts_repeated_dot_candidates() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO}{THREE}50..32{CAR}10{YUAN}")

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["preprocessing"]["summary"]["candidate_count"] == 2
    assert queue["items"][0]["review_result"]["type"] == "normal"
    assert queue["items"][1]["review_result"]["type"] == "car"


def test_mixed_valid_and_invalid_fragments_do_not_traceback() -> None:
    text = "..".join(
        [
            f"06.13.23.22 {TWO}{THREE}50",
            f"32{CAR}10{YUAN}",
            f"40{CAR}10{YUAN}",
            f"33.27.30.{ALT_TWO}600{THREE}200{ARM}",
        ]
    )

    queue = build_batch_mock_queue(text)

    assert queue["status"] == NEEDS_REVIEW
    assert queue["preprocessing_status"] == "NEEDS_REVIEW"
    assert queue["summary"]["total"] == 4
    assert queue["preprocessing"]["summary"]["candidate_count"] == 4
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 2
    assert queue["preprocessing"]["summary"]["valid_count"] == 2
    assert queue["items"][0]["review_result"]["status"] == "ok"
    assert queue["items"][1]["review_result"]["status"] == "ok"
    assert queue["items"][2]["review_result"]["status"] == "error"
    assert queue["items"][3]["review_result"]["status"] == "error"
    assert queue["items"][0]["original"] == f"06.13.23.22 {TWO}{THREE}50"
    assert queue["items"][1]["original"] == f"32{CAR}10{YUAN}"


def test_long_pasted_input_splits_and_keeps_long_number_auditable() -> None:
    text = (
        f"15.29.1000..15.29.33.20.30.234.100..32.23.15.20.14..."
        f"234.100.11.09,10.{ALT_TWO}{THREE}200..21.20.23.32.05.06.."
        f"234.100..33.27.30.{ALT_TWO}600{THREE}200{ARM}"
    )

    queue = build_batch_mock_queue(text)

    assert queue["summary"]["total"] == 7
    assert queue["items"][0]["original"] == "15.29.1000"
    assert 1000 not in queue["items"][0]["review_result"].get("numbers", [])
    assert queue["status"] == NEEDS_REVIEW
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] >= 1


def test_queue_pretty_report_shows_preprocessing_summary_and_fragments() -> None:
    text = "\n".join(
        [
            "\u4e0a\u534812:04",
            "Masm Anakq",
            f"06.13.23.22 {TWO}{THREE}50..40{CAR}10{YUAN}",
        ]
    )

    pretty = format_pretty_batch_mock_queue(build_batch_mock_queue(text))

    assert "Preprocessing:" in pretty
    assert "- candidates: 2" in pretty
    assert "- ignored metadata: 2" in pretty
    assert "- invalid/unsupported: 1" in pretty
    assert f"[1] BLOCKED 06.13.23.22 {TWO}{THREE}50" in pretty
    assert f"[2] BLOCKED 40{CAR}10{YUAN}" in pretty


def test_accept_valid_candidates_from_needs_review_starts_first_valid_item() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO}{THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")

    accepted = accept_valid_candidates_for_mock_queue(queue, run_first=True)

    assert accepted["status"] == "WAITING_FOR_HUMAN_CONFIRM"
    assert [item["original"] for item in accepted["items"]] == [f"06.13.23.22 {TWO}{THREE}50", f"32{CAR}10{YUAN}"]
    audit = accepted["preprocessing"]["original_review_audit"]
    assert audit["invalid_fragments"][0]["raw"] == f"40{CAR}10{YUAN}"
    assert accepted["items"][0]["status"] == "WAITING_FOR_HUMAN_CONFIRM"


def test_reject_needs_review_batch_blocks_without_mock_fill() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO}{THREE}50..40{CAR}10{YUAN}")

    rejected = reject_batch_review(queue)

    assert rejected["status"] == "REJECTED"
    assert rejected["errors"] == ["batch review rejected by user"]
    assert all(item["status"] == "BLOCKED" for item in rejected["items"])


def test_no_valid_candidates_is_blocked_and_cannot_accept() -> None:
    queue = build_batch_mock_queue(f"\u4e0a\u534812:04\n40{CAR}10{YUAN}")

    assert queue["status"] == BATCH_BLOCKED
    assert queue["preprocessing_status"] == "BLOCKED"
    assert queue["preprocessing"]["summary"]["valid_count"] == 0

    try:
        accept_valid_candidates_for_mock_queue(queue)
    except ValueError as exc:
        assert "only allowed when status is NEEDS_REVIEW" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
