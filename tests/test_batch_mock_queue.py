import inspect

import pytest

from betguard.webfill import batch_mock_queue
from betguard.webfill.batch_mock_queue import (
    BATCH_BLOCKED,
    COMPLETED,
    DONE,
    MOCK_FILL_FAILED,
    NEEDS_REVIEW,
    PENDING,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    advance_queue_after_human_confirm,
    build_batch_mock_queue,
    format_pretty_batch_mock_queue,
    load_queue_state,
    mark_current_item_done_by_human,
    run_current_mock_queue_item,
    save_queue_state,
)


OK_TEXT = "\n".join(
    [
        "06.13.23.22 二三50",
        "17.20/28/34 二三1",
        "32車10元",
    ]
)


def test_build_queue_all_ok() -> None:
    queue = build_batch_mock_queue(OK_TEXT)

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["summary"]["total"] == 3
    assert queue["summary"]["ok"] == 3
    assert [item["status"] for item in queue["items"]] == [PENDING, PENDING, PENDING]


def test_build_queue_blocked_when_duplicate_error() -> None:
    queue = build_batch_mock_queue("06.13.23.22 二三50\n13.13 二100")

    assert queue["status"] == NEEDS_REVIEW
    assert queue["summary"]["blocked"] == 1
    assert all(item["status"] == "BLOCKED" for item in queue["items"])
    assert queue["items"][1]["danger_buttons_clicked"] == []


def test_build_queue_blocked_when_warning() -> None:
    queue = build_batch_mock_queue("06.13.23 二三")

    assert queue["status"] == BATCH_BLOCKED
    assert queue["summary"]["blocked"] == 1


def test_run_current_mock_queue_item_only_processes_first_pending() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))

    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][0]["selected_numbers"] == ["06", "13", "23", "22"]
    assert queue["items"][0]["filled_amounts"] == {"二星": 50, "三星": 50}
    assert queue["items"][1]["status"] == PENDING
    assert queue["items"][1]["selected_numbers"] == []


def test_first_item_waits_for_human_confirm() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))

    assert queue["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["summary"]["current_index"] == 1


def test_cannot_mark_done_without_waiting() -> None:
    queue = build_batch_mock_queue(OK_TEXT)

    with pytest.raises(ValueError):
        mark_current_item_done_by_human(queue)


def test_mark_done_moves_to_next_ready_state() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    queue = mark_current_item_done_by_human(queue)

    assert queue["items"][0]["status"] == DONE
    assert queue["status"] == READY_FOR_QUEUE
    assert queue["summary"]["current_index"] == 2


def test_advance_after_human_confirm_processes_second_item_without_skipping() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    queue = advance_queue_after_human_confirm(queue)

    assert queue["items"][0]["status"] == DONE
    assert queue["items"][1]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][1]["selected_columns"] == [
        {"column": 1, "numbers": [17, 20]},
        {"column": 2, "numbers": [28]},
        {"column": 3, "numbers": [34]},
    ]
    assert queue["items"][1]["filled_amounts"] == {"二星": 100, "三星": 100}
    assert queue["items"][2]["status"] == PENDING
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["status"] != BATCH_BLOCKED
    assert queue["summary"]["blocked"] == 0
    assert queue["summary"]["failed"] == 0
    assert queue["summary"]["current_index"] == 2


def test_all_valid_ready_queue_requires_accept_before_first_mock_next() -> None:
    queue = build_batch_mock_queue(OK_TEXT)
    assert queue["status"] == READY_FOR_QUEUE
    assert all(item["status"] == PENDING for item in queue["items"])
    assert "approved_fill_queue" not in queue

    with pytest.raises(ValueError, match="approved fill queue is empty"):
        advance_queue_after_human_confirm(queue)

    accepted = accept_valid_candidates_for_mock_queue(queue)
    accepted = advance_queue_after_human_confirm(accepted)

    assert accepted["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert accepted["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert [item["status"] for item in accepted["items"][1:]] == [PENDING, PENDING]
    assert accepted["items"][0]["danger_buttons_clicked"] == []
    assert accepted["final_decision"]["real_site_operation"] is False
    assert accepted["final_decision"]["auto_submit"] is False
    assert accepted["final_decision"]["human_required_each_item"] is True


def test_needs_review_batch_still_cannot_mock_before_accept_valid() -> None:
    queue = build_batch_mock_queue("06.13.23.22 二三50..2星寫2")
    assert queue["status"] == NEEDS_REVIEW

    with pytest.raises(ValueError):
        advance_queue_after_human_confirm(queue)
    with pytest.raises(ValueError):
        run_current_mock_queue_item(queue)


def test_first_mock_next_on_ready_queue_processes_only_one_item() -> None:
    queue = accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT))
    queue = advance_queue_after_human_confirm(queue)

    waiting = [item for item in queue["items"] if item["status"] == WAITING_FOR_HUMAN_CONFIRM]
    done = [item for item in queue["items"] if item["status"] == DONE]
    assert len(waiting) == 1
    assert len(done) == 0

    queue = advance_queue_after_human_confirm(queue)
    waiting = [item for item in queue["items"] if item["status"] == WAITING_FOR_HUMAN_CONFIRM]
    done = [item for item in queue["items"] if item["status"] == DONE]
    assert len(waiting) == 1
    assert len(done) == 1
    assert queue["items"][2]["status"] == PENDING


def test_car_bet_succeeds_after_column_without_becoming_batch_blocked() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    queue = advance_queue_after_human_confirm(queue)
    queue = advance_queue_after_human_confirm(queue)

    assert queue["items"][1]["status"] == DONE
    assert queue["items"][2]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][2]["bet_type"] == "car"
    assert queue["items"][2]["selected_car_number"] == 32
    assert queue["items"][2]["car_units"] == 0.1
    assert queue["items"][2]["filled_amount"] == 10
    assert queue["items"][2]["danger_buttons_clicked"] == []
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["status"] != BATCH_BLOCKED
    assert queue["summary"]["current_index"] == 3
    assert queue["summary"]["remaining"] == 0


def test_failed_queue_next_does_not_traceback_at_state_layer() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("06.13.23.22 二三50")))
    queue["status"] = MOCK_FILL_FAILED

    with pytest.raises(ValueError, match="queue is stopped at MOCK_FILL_FAILED; fix or skip is not implemented yet"):
        advance_queue_after_human_confirm(queue)


def test_pending_items_are_not_counted_as_blocked() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))

    assert queue["summary"]["pending"] == 2
    assert queue["summary"]["blocked"] == 0


def test_current_item_display_shows_car_when_waiting() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    queue = advance_queue_after_human_confirm(queue)
    queue = advance_queue_after_human_confirm(queue)
    pretty = format_pretty_batch_mock_queue(queue)

    assert "[3] WAITING_FOR_HUMAN_CONFIRM" in pretty
    assert "Car:" in pretty
    assert "- Number: 32" in pretty
    assert "- Units: 0.1" in pretty
    assert "- Amount: 10" in pretty


def test_car_can_be_first_item_in_queue() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("08車20元\n06.13.23.22 二三50")))

    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][0]["bet_type"] == "car"
    assert queue["items"][0]["selected_car_number"] == 8
    assert queue["items"][0]["car_units"] == 0.2
    assert queue["items"][0]["filled_amount"] == 20
    assert queue["items"][0]["selected_numbers"] == []
    assert queue["items"][0]["selected_columns"] == []


def test_car_can_be_middle_item_and_queue_continues() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("06.13.23.22 二三50\n08車20元\n07.08.09 二三50")))
    queue = advance_queue_after_human_confirm(queue)

    assert queue["items"][1]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["items"][1]["bet_type"] == "car"
    assert queue["items"][1]["selected_car_number"] == 8
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM

    queue = advance_queue_after_human_confirm(queue)
    assert queue["items"][2]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM


def test_invalid_car_input_blocks_review_without_normal_or_column_routing() -> None:
    queue = build_batch_mock_queue("車10元")

    assert queue["status"] == BATCH_BLOCKED
    assert queue["items"][0]["status"] == "BLOCKED"
    assert queue["items"][0]["review_result"]["type"] == "car"
    assert "missing car number" in queue["items"][0]["errors"]


def test_mark_done_requires_exactly_one_waiting_item() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("06.13.23.22 二三50\n07.08.09 二三50")))
    queue["items"][1]["status"] = WAITING_FOR_HUMAN_CONFIRM

    with pytest.raises(ValueError, match="queue has multiple waiting items"):
        mark_current_item_done_by_human(queue)


def test_queue_completed_after_last_done() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("06.13.23.22 二三50")))
    queue = mark_current_item_done_by_human(queue)

    assert queue["items"][0]["status"] == DONE
    assert queue["status"] == COMPLETED
    assert queue["summary"]["remaining"] == 0


def test_advance_queue_completes_all_normal_items() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue("06.13.23.22 二三50\n07.08.09 二三50")))
    queue = advance_queue_after_human_confirm(queue)
    assert queue["items"][0]["status"] == DONE
    assert queue["items"][1]["status"] == WAITING_FOR_HUMAN_CONFIRM

    queue = advance_queue_after_human_confirm(queue)
    assert queue["status"] == COMPLETED
    assert [item["status"] for item in queue["items"]] == [DONE, DONE]


def test_advance_queue_completes_normal_column_car_flow() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    queue = advance_queue_after_human_confirm(queue)
    queue = advance_queue_after_human_confirm(queue)
    queue = advance_queue_after_human_confirm(queue)

    assert queue["status"] == COMPLETED
    assert [item["status"] for item in queue["items"]] == [DONE, DONE, DONE]
    assert queue["summary"]["remaining"] == 0


def test_danger_buttons_clicked_is_always_empty() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))

    assert queue["items"][0]["danger_buttons_detected"]
    assert queue["items"][0]["danger_buttons_clicked"] == []


def test_final_decision_never_allows_real_site_or_auto_submit() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))

    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False
    assert queue["final_decision"]["human_required_each_item"] is True


def test_queue_state_can_save_and_load(tmp_path) -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(OK_TEXT)))
    path = tmp_path / "queue_state.json"

    save_queue_state(queue, path)
    loaded = load_queue_state(path)

    assert loaded["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert loaded["items"][0]["selected_numbers"] == ["06", "13", "23", "22"]


def test_batch_mock_queue_source_has_no_submit_operation() -> None:
    source = inspect.getsource(batch_mock_queue)

    assert "submit(" not in source


# --- Next Action display by queue status ---


def test_next_action_completed_shows_no_next_action() -> None:
    """COMPLETED queue must not suggest batch-mock-next."""
    queue = {
        "status": COMPLETED,
        "summary": {"total": 1, "ok": 1, "blocked": 0, "current_index": 0, "remaining": 0},
        "items": [{"index": 0, "status": DONE, "original": "done"}],
        "final_decision": {"real_site_operation": False, "auto_submit": False, "human_required_each_item": True},
    }
    pretty = format_pretty_batch_mock_queue(queue)

    assert "Queue 已完成" in pretty
    assert "batch-mock-next" not in pretty


def test_next_action_waiting_shows_human_confirm_done() -> None:
    """WAITING_FOR_HUMAN_CONFIRM queue shows --batch-human-confirm-current-done."""
    queue = {
        "status": WAITING_FOR_HUMAN_CONFIRM,
        "summary": {"total": 1, "ok": 1, "blocked": 0, "current_index": 1, "remaining": 1},
        "items": [{"index": 0, "status": WAITING_FOR_HUMAN_CONFIRM, "original": "waiting"}],
        "final_decision": {"real_site_operation": False, "auto_submit": False, "human_required_each_item": True},
    }
    pretty = format_pretty_batch_mock_queue(queue)

    assert "batch-human-confirm-current-done" in pretty
    assert "batch-mock-next" not in pretty


def test_next_action_ready_keeps_mock_next() -> None:
    """READY_FOR_QUEUE still shows batch-mock-next for mock flow."""
    queue = {
        "status": READY_FOR_QUEUE,
        "summary": {"total": 2, "ok": 2, "blocked": 0, "current_index": 1, "remaining": 1},
        "items": [
            {"index": 0, "status": DONE, "original": "done"},
            {"index": 1, "status": PENDING, "original": "pending"},
        ],
        "final_decision": {"real_site_operation": False, "auto_submit": False, "human_required_each_item": True},
    }
    pretty = format_pretty_batch_mock_queue(queue)

    assert "batch-mock-next" in pretty
    assert "Queue 已完成" not in pretty


def test_next_action_blocked_shows_blocked() -> None:
    """BATCH_BLOCKED queue shows blocked message, not mock-next."""
    queue = {
        "status": BATCH_BLOCKED,
        "summary": {"total": 0, "ok": 0, "blocked": 1, "current_index": 0, "remaining": 0},
        "items": [],
        "final_decision": {"real_site_operation": False, "auto_submit": False, "human_required_each_item": True},
    }
    pretty = format_pretty_batch_mock_queue(queue)

    assert "Queue 已封鎖" in pretty
    assert "batch-mock-next" not in pretty
