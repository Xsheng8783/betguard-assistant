import json
import sys

import pytest

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    COMPLETED,
    CURRENT,
    DONE,
    PENDING,
    READY,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
    format_pretty_batch_queue,
    get_current_item,
    mark_current_done_by_human,
    mark_current_waiting_for_human,
    reset_batch_queue,
)


TWO_THREE = "\u4e8c\u4e09"
YUAN = "\u5143"
CAR = "\u8eca"


def review_result(text: str) -> dict:
    return attach_summaries(review_text(text).to_dict())


def ok_batch_text() -> str:
    return "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
            f"32{CAR}10{YUAN}",
        ]
    )


def test_build_queue_all_ok() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    assert queue["status"] == READY
    assert queue["current_index"] == 0
    assert queue["total"] == 3
    assert queue["done_count"] == 0
    assert [item["status"] for item in queue["items"]] == [CURRENT, PENDING, PENDING]
    assert queue["items"][0]["index"] == 0
    assert queue["items"][0]["original_text"]
    assert queue["items"][0]["summary"]
    assert queue["items"][0]["parsed"]["status"] == "ok"


def test_build_queue_blocked_when_review_error() -> None:
    queue = build_batch_queue(review_result(f"06.13.23/50\n13.13 {TWO_THREE}100"))

    assert queue["status"] == BATCH_BLOCKED
    assert queue["reason"] == "review result is not ok"
    assert all(item["status"] == "BLOCKED" for item in queue["items"])


def test_build_queue_blocked_when_review_warning() -> None:
    queue = build_batch_queue(review_result("06.13.23"))

    assert queue["status"] == BATCH_BLOCKED
    assert queue["reason"] == "review result is not ok"
    assert queue["items"][0]["warnings"] == ["missing money"]


def test_get_current_item_returns_current_only() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    current = get_current_item(queue)

    assert current is not None
    assert current["index"] == 0
    assert current["status"] == CURRENT


def test_mark_current_waiting_for_human() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    updated = mark_current_waiting_for_human(queue)

    assert updated["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert updated["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert updated["current_index"] == 0


def test_mark_done_moves_to_next() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))
    waiting = mark_current_waiting_for_human(queue)

    updated = mark_current_done_by_human(waiting)

    assert updated["items"][0]["status"] == DONE
    assert updated["items"][1]["status"] == CURRENT
    assert updated["status"] == READY
    assert updated["current_index"] == 1
    assert updated["done_count"] == 1


def test_queue_completed() -> None:
    queue = build_batch_queue(review_result(f"06.13.23.22 {TWO_THREE}50"))
    waiting = mark_current_waiting_for_human(queue)

    updated = mark_current_done_by_human(waiting)

    assert updated["items"][0]["status"] == DONE
    assert updated["status"] == COMPLETED
    assert updated["current_index"] is None
    assert updated["done_count"] == 1


def test_cannot_done_without_waiting() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    with pytest.raises(ValueError, match="WAITING_FOR_HUMAN_CONFIRM"):
        mark_current_done_by_human(queue)


def test_reset_batch_queue_returns_first_current() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))
    waiting = mark_current_waiting_for_human(queue)
    moved = mark_current_done_by_human(waiting)

    reset = reset_batch_queue(moved)

    assert reset["status"] == READY
    assert reset["current_index"] == 0
    assert reset["done_count"] == 0
    assert [item["status"] for item in reset["items"]] == [CURRENT, PENDING, PENDING]


def test_final_decision_auto_submit_is_always_false() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))
    waiting = mark_current_waiting_for_human(queue)
    done = mark_current_done_by_human(waiting)

    assert queue["final_decision"]["real_site_auto_submit"] is False
    assert waiting["final_decision"]["real_site_auto_submit"] is False
    assert done["final_decision"]["real_site_auto_submit"] is False


def test_pretty_batch_queue_ready_format() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    pretty = format_pretty_batch_queue(queue)

    assert "Batch Queue READY" in pretty
    assert "Total: 3" in pretty
    assert "Current: 1/3" in pretty
    assert "[1] CURRENT" in pretty
    assert "[2] PENDING" in pretty
    assert "- Auto submit: false" in pretty


def test_pretty_batch_queue_blocked_format() -> None:
    queue = build_batch_queue(review_result(f"13.13 {TWO_THREE}100"))

    pretty = format_pretty_batch_queue(queue)

    assert "Batch Queue BLOCKED" in pretty
    assert "Reason: review result is not ok" in pretty


def test_cli_build_batch_queue_pretty(capsys, monkeypatch, tmp_path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text(ok_batch_text(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--build-batch-queue",
            "--file",
            str(input_file),
            "--pretty",
        ],
    )

    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Batch Queue READY" in output
    assert "[1] CURRENT" in output


def test_cli_build_batch_queue_from_file_outputs_json(capsys, monkeypatch, tmp_path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text(ok_batch_text(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--build-batch-queue",
            "--file",
            str(input_file),
        ],
    )

    webfill_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == READY
    assert output["items"][0]["status"] == CURRENT
    assert output["final_decision"]["real_site_auto_submit"] is False
