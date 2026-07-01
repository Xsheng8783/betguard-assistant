import json
import sys

import pytest

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    COMPLETED,
    DONE,
    PENDING,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
    format_pretty_batch_queue,
    get_current_item,
    mark_item_done_by_human,
    mark_item_waiting_for_human,
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
            f"17.20/28/34 {TWO_THREE}1",
            f"32{CAR}10{YUAN}",
        ]
    )


def test_build_batch_queue_all_ok_is_ready() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    assert queue["mode"] == "batch_assisted_fill_queue"
    assert queue["status"] == READY_FOR_QUEUE
    assert queue["summary"]["total"] == 3
    assert queue["summary"]["ok"] == 3
    assert queue["summary"]["blocked"] == 0
    assert queue["summary"]["current_index"] == 1
    assert queue["summary"]["remaining"] == 3
    assert [item["status"] for item in queue["items"]] == [PENDING, PENDING, PENDING]


def test_build_batch_queue_duplicate_error_blocks_batch() -> None:
    queue = build_batch_queue(review_result(f"06.13.23/50\n13.13 {TWO_THREE}100"))

    assert queue["status"] == BATCH_BLOCKED
    assert queue["summary"]["blocked"] == 1
    assert queue["summary"]["current_index"] == 0
    assert queue["items"][1]["status"] == "BLOCKED"


def test_build_batch_queue_warning_blocks_batch() -> None:
    queue = build_batch_queue(review_result("06.13.23"))

    assert queue["status"] == BATCH_BLOCKED
    assert queue["summary"]["blocked"] == 1
    assert queue["items"][0]["warnings"] == ["missing money"]


def test_get_current_item_returns_first_pending() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    current = get_current_item(queue)

    assert current is not None
    assert current["index"] == 1
    assert current["status"] == PENDING


def test_mark_item_waiting_for_human_sets_queue_and_item_status() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    updated = mark_item_waiting_for_human(queue, 1)

    assert updated["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert updated["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert updated["summary"]["current_index"] == 1


def test_mark_item_done_cannot_skip_waiting_state() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    with pytest.raises(ValueError, match="WAITING_FOR_HUMAN_CONFIRM"):
        mark_item_done_by_human(queue, 1)


def test_mark_item_done_by_human_returns_ready_when_more_items_remain() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))
    waiting = mark_item_waiting_for_human(queue, 1)

    updated = mark_item_done_by_human(waiting, 1)

    assert updated["items"][0]["status"] == DONE
    assert updated["status"] == READY_FOR_QUEUE
    assert updated["summary"]["current_index"] == 2


def test_mark_item_done_by_human_completes_last_item() -> None:
    queue = build_batch_queue(review_result(f"06.13.23.22 {TWO_THREE}50"))
    waiting = mark_item_waiting_for_human(queue, 1)

    updated = mark_item_done_by_human(waiting, 1)

    assert updated["items"][0]["status"] == DONE
    assert updated["status"] == COMPLETED
    assert updated["summary"]["remaining"] == 0


def test_final_decision_auto_submit_is_always_false() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))
    waiting = mark_item_waiting_for_human(queue, 1)
    done = mark_item_done_by_human(waiting, 1)

    assert queue["final_decision"]["real_site_auto_submit"] is False
    assert waiting["final_decision"]["real_site_auto_submit"] is False
    assert done["final_decision"]["real_site_auto_submit"] is False


def test_pretty_batch_queue_report_contains_safety_lines() -> None:
    queue = build_batch_queue(review_result(ok_batch_text()))

    pretty = format_pretty_batch_queue(queue)

    assert "Batch Assisted Fill Queue" in pretty
    assert "Status: READY_FOR_QUEUE" in pretty
    assert "- Auto submit: false" in pretty


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
    assert output["status"] == READY_FOR_QUEUE
    assert output["final_decision"]["real_site_auto_submit"] is False

