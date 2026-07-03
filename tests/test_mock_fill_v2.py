from __future__ import annotations

import pytest

from betguard.webfill.batch_mock_queue import (
    DONE,
    NEEDS_REVIEW,
    PENDING,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    advance_queue_after_human_confirm,
    build_batch_mock_queue,
    run_current_mock_queue_item,
)


ALL_VALID_TEXT = "\n".join(
    [
        "06.13.23.22 二三50",
        "17.20/28/34 二三1",
        "32車10元",
    ]
)
MIXED_TEXT = "06.13.23.22 二三50..2星寫2..32車10元"


def _accepted(text: str) -> dict:
    return accept_valid_candidates_for_mock_queue(build_batch_mock_queue(text))


def test_mock_next_refuses_before_accept_valid_v2() -> None:
    needs_review = build_batch_mock_queue(MIXED_TEXT)
    assert needs_review["status"] == NEEDS_REVIEW
    with pytest.raises(ValueError):
        advance_queue_after_human_confirm(needs_review)

    all_valid = build_batch_mock_queue(ALL_VALID_TEXT)
    assert all_valid["status"] == READY_FOR_QUEUE
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        advance_queue_after_human_confirm(all_valid)


def test_mock_next_refuses_when_approved_queue_missing_or_empty() -> None:
    queue = build_batch_mock_queue(ALL_VALID_TEXT)
    assert "approved_fill_queue" not in queue
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        run_current_mock_queue_item(queue)

    queue["approved_fill_queue"] = []
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        run_current_mock_queue_item(queue)


def test_mock_next_does_not_read_valid_candidates_directly() -> None:
    accepted = _accepted(ALL_VALID_TEXT)
    accepted["preprocessing"]["valid_candidates"].append({"raw": "09.10 二5"})

    advanced = advance_queue_after_human_confirm(accepted)

    assert advanced["summary"]["total"] == 3
    originals = [item["original"] for item in advanced["items"]]
    assert "09.10 二5" not in originals
    assert advanced["items"][0]["approved_source"]["original_fragment"] == "06.13.23.22 二三50"


def test_item_missing_from_approved_queue_cannot_be_mocked() -> None:
    accepted = _accepted(ALL_VALID_TEXT)
    accepted["approved_fill_queue"] = [
        entry for entry in accepted["approved_fill_queue"]
        if entry["original_fragment"] != "06.13.23.22 二三50"
    ]

    with pytest.raises(ValueError, match="not in the approved fill queue"):
        run_current_mock_queue_item(accepted)


def test_one_command_advances_only_one_item() -> None:
    queue = advance_queue_after_human_confirm(_accepted(ALL_VALID_TEXT))
    statuses = [item["status"] for item in queue["items"]]
    assert statuses == [WAITING_FOR_HUMAN_CONFIRM, PENDING, PENDING]

    queue = advance_queue_after_human_confirm(queue)
    statuses = [item["status"] for item in queue["items"]]
    assert statuses == [DONE, WAITING_FOR_HUMAN_CONFIRM, PENDING]


def test_invalid_review_items_are_never_mockable() -> None:
    accepted = _accepted(MIXED_TEXT)
    originals = [item["original"] for item in accepted["items"]]

    assert "2星X2" not in originals
    assert "2星寫2" not in originals
    assert len(accepted["items"]) == 2
    fragments = [entry["original_fragment"] for entry in accepted["approved_fill_queue"]]
    assert "2星X2" not in fragments


def test_normal_bet_mock_keeps_numbers_stars_unit_money() -> None:
    queue = advance_queue_after_human_confirm(_accepted("06.13.23.22 二三50"))
    item = queue["items"][0]
    source = item["approved_source"]

    assert item["selected_numbers"] == ["06", "13", "23", "22"]
    assert item["filled_amounts"] == {"二星": 50, "三星": 50}
    assert source["bet_type"] == "normal"
    assert source["numbers"] == [6, 13, 23, 22]
    assert source["stars"] == [2, 3]
    assert source["unit"] == 0.5
    assert source["money"] == 50
    assert source["from_approved_fill_queue"] is True
    assert source["accepted_by_human"] is True


def test_star_specific_mock_keeps_per_star_money() -> None:
    queue = advance_queue_after_human_confirm(_accepted("16.19.28.33\n2星寫2  3.4星寫1"))
    item = queue["items"][0]
    source = item["approved_source"]

    assert item["filled_amounts"] == {"二星": 200, "三星": 100, "四星": 100}
    assert source["star_amounts"]["2"]["money"] == 200
    assert source["star_amounts"]["3"]["money"] == 100
    assert source["star_amounts"]["4"]["money"] == 100


def test_car_bet_mock_keeps_number_units_money() -> None:
    queue = advance_queue_after_human_confirm(_accepted("32車10元"))
    item = queue["items"][0]
    source = item["approved_source"]

    assert item["selected_car_number"] == 32
    assert item["car_units"] == 0.1
    assert item["filled_amount"] == 10
    assert source["bet_type"] == "car"
    assert source["number"] == 32
    assert source["car_units"] == 0.1
    assert source["money"] == 10


def test_column_bet_mock_keeps_columns_stars_unit_money() -> None:
    queue = advance_queue_after_human_confirm(_accepted("17.20/28/34 二三1"))
    item = queue["items"][0]
    source = item["approved_source"]

    assert item["selected_columns"] == [
        {"column": 1, "numbers": [17, 20]},
        {"column": 2, "numbers": [28]},
        {"column": 3, "numbers": [34]},
    ]
    assert source["bet_type"] == "column"
    assert source["columns"] == [[17, 20], [28], [34]]
    assert source["stars"] == [2, 3]
    assert source["unit"] == 1
    assert source["money"] == 100
    assert "action_summary" in source


def test_audit_records_approved_fill_queue_source() -> None:
    queue = advance_queue_after_human_confirm(_accepted(ALL_VALID_TEXT))
    audit_item = queue["audit"]["items"][0]

    assert audit_item["approved_source"] is not None
    assert audit_item["approved_source"]["from_approved_fill_queue"] is True
    assert audit_item["approved_source"]["accepted_by_human"] is True
    assert audit_item["approved_source"]["source_index"] == 1
    assert audit_item["approved_source"]["original_fragment"] == "06.13.23.22 二三50"
    assert audit_item["approved_source"]["original_lines"]
    assert audit_item["human_confirmation_status"] == "waiting_for_human_confirm"
    assert queue["audit"]["items"][1]["approved_source"] is None


def test_safety_flags_remain_unchanged_after_mock_step() -> None:
    queue = advance_queue_after_human_confirm(_accepted(ALL_VALID_TEXT))

    assert queue["final_decision"]["real_site_operation"] is False
    assert queue["final_decision"]["auto_submit"] is False
    assert queue["final_decision"]["human_required_each_item"] is True
    assert queue["audit"]["safety"]["real_site_operation"] is False
    assert queue["audit"]["safety"]["auto_submit"] is False
    assert queue["audit"]["safety"]["danger_buttons_clicked"] == []


def test_mock_step_never_clicks_danger_buttons_or_submits() -> None:
    queue = advance_queue_after_human_confirm(_accepted(ALL_VALID_TEXT))
    item = queue["items"][0]

    assert item["danger_buttons_clicked"] == []
    assert item["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
