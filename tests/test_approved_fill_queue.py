from __future__ import annotations

import pytest

from betguard.webfill.batch_mock_queue import (
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


def test_needs_review_queue_has_no_approved_fill_queue() -> None:
    queue = build_batch_mock_queue(MIXED_TEXT)

    assert queue["status"] == NEEDS_REVIEW
    assert "approved_fill_queue" not in queue or not queue["approved_fill_queue"]
    assert all(item["status"] == "BLOCKED" for item in queue["items"])
    assert queue["audit"]["approved_fill_queue"]["approved_fill_queue_count"] == 0


def test_accept_valid_creates_approved_fill_queue_from_valid_only() -> None:
    queue = build_batch_mock_queue(MIXED_TEXT)
    accepted = accept_valid_candidates_for_mock_queue(queue)

    approved = accepted["approved_fill_queue"]
    assert len(approved) == 2
    assert [entry["original_fragment"] for entry in approved] == [
        "06.13.23.22 二三50",
        "32車10元",
    ]
    for entry in approved:
        assert entry["accepted_by_human"] is True
        assert entry["accepted_at"]
        assert entry["review_result"]["status"] == "ok"
        assert entry["audit_snapshot"]["review_action"] == "accept_valid"
        assert entry["audit_snapshot"]["batch_id"]


def test_invalid_review_items_are_excluded_from_approved_fill_queue() -> None:
    queue = build_batch_mock_queue(MIXED_TEXT)
    accepted = accept_valid_candidates_for_mock_queue(queue)

    fragments = [entry["original_fragment"] for entry in accepted["approved_fill_queue"]]
    assert "2星X2" not in fragments
    assert all(entry["review_result"]["status"] == "ok" for entry in accepted["approved_fill_queue"])
    audit = accepted["audit"]["approved_fill_queue"]
    assert audit["human_accepted_only"] is True


def test_mock_next_refuses_before_accept_valid() -> None:
    needs_review = build_batch_mock_queue(MIXED_TEXT)
    with pytest.raises(ValueError):
        advance_queue_after_human_confirm(needs_review)

    all_valid = build_batch_mock_queue(ALL_VALID_TEXT)
    assert all_valid["status"] == READY_FOR_QUEUE
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        advance_queue_after_human_confirm(all_valid)
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        run_current_mock_queue_item(all_valid)
    assert all(item["status"] == PENDING for item in all_valid["items"])


def test_mock_next_refuses_when_approved_queue_empty_or_not_human_accepted() -> None:
    queue = build_batch_mock_queue(ALL_VALID_TEXT)
    queue["approved_fill_queue"] = []
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        advance_queue_after_human_confirm(queue)

    queue["approved_fill_queue"] = [{"original_fragment": "06.13.23.22 二三50", "accepted_by_human": False}]
    with pytest.raises(ValueError, match="approved fill queue is empty"):
        advance_queue_after_human_confirm(queue)


def test_mock_next_uses_approved_fill_queue_after_accept_valid() -> None:
    accepted = accept_valid_candidates_for_mock_queue(build_batch_mock_queue(ALL_VALID_TEXT))
    assert len(accepted["approved_fill_queue"]) == 3

    advanced = advance_queue_after_human_confirm(accepted)

    assert advanced["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert advanced["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert advanced["items"][0]["original"] == advanced["approved_fill_queue"][0]["original_fragment"]
    assert [item["status"] for item in advanced["items"][1:]] == [PENDING, PENDING]


def test_audit_contains_approved_fill_queue_counts() -> None:
    accepted = accept_valid_candidates_for_mock_queue(build_batch_mock_queue(MIXED_TEXT))
    audit = accepted["audit"]["approved_fill_queue"]

    assert audit["approved_fill_queue_count"] == 2
    assert audit["human_accepted_count"] == 2
    assert audit["accepted_valid_count"] == 2
    assert audit["excluded_invalid_count"] == 1
    assert audit["human_accepted_only"] is True
    assert audit["real_site_operation"] is False
    assert audit["auto_submit"] is False


def test_safety_flags_remain_unchanged_through_approved_flow() -> None:
    accepted = accept_valid_candidates_for_mock_queue(build_batch_mock_queue(ALL_VALID_TEXT))
    advanced = advance_queue_after_human_confirm(accepted)

    assert advanced["final_decision"]["real_site_operation"] is False
    assert advanced["final_decision"]["auto_submit"] is False
    assert advanced["final_decision"]["human_required_each_item"] is True
    assert advanced["audit"]["safety"]["real_site_operation"] is False
    assert advanced["audit"]["safety"]["auto_submit"] is False
    assert advanced["audit"]["safety"]["danger_buttons_clicked"] == []


def test_approved_entry_preserves_per_star_amounts_from_write_groups() -> None:
    accepted = accept_valid_candidates_for_mock_queue(
        build_batch_mock_queue("16.19.28.33\n2星寫2  3.4星寫1")
    )
    entry = accepted["approved_fill_queue"][0]

    assert entry["bet_type"] == "normal"
    assert entry["numbers"] == [16, 19, 28, 33]
    assert entry["stars"] == [2, 3, 4]
    assert entry["star_amounts"]["2"]["money"] == 200
    assert entry["star_amounts"]["3"]["money"] == 100
    assert entry["star_amounts"]["4"]["money"] == 100
    assert entry["star_amounts"]["2"]["unit"] == 2


def test_approved_entry_preserves_per_star_amounts_from_yuan_groups() -> None:
    accepted = accept_valid_candidates_for_mock_queue(
        build_batch_mock_queue("02.10.11.23.39二星100元.三.四星50元")
    )
    entry = accepted["approved_fill_queue"][0]

    assert entry["numbers"] == [2, 10, 11, 23, 39]
    assert entry["star_amounts"]["2"]["money"] == 100
    assert entry["star_amounts"]["3"]["money"] == 50
    assert entry["star_amounts"]["4"]["money"] == 50


def test_approved_entry_expands_uniform_amount_to_all_stars() -> None:
    accepted = accept_valid_candidates_for_mock_queue(
        build_batch_mock_queue("19.39.22.12.35.23二三四15")
    )
    entry = accepted["approved_fill_queue"][0]

    assert entry["numbers"] == [19, 39, 22, 12, 35, 23]
    assert entry["stars"] == [2, 3, 4]
    assert entry["unit"] == 0.15
    assert entry["money"] == 15
    assert entry["star_amounts"] == {
        "2": {"unit": 0.15, "money": 15},
        "3": {"unit": 0.15, "money": 15},
        "4": {"unit": 0.15, "money": 15},
    }


def test_approved_entry_preserves_car_units_and_money() -> None:
    accepted = accept_valid_candidates_for_mock_queue(build_batch_mock_queue("32車10元\n01×3"))
    car_small, car_big = accepted["approved_fill_queue"]

    assert car_small["bet_type"] == "car"
    assert car_small["number"] == 32
    assert car_small["car_units"] == 0.1
    assert car_small["money"] == 10

    assert car_big["bet_type"] == "car"
    assert car_big["number"] == 1
    assert car_big["car_units"] == 3
    assert car_big["money"] == 300
