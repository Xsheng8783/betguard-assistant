from __future__ import annotations

import pytest

from betguard.webfill.batch_mock_queue import (
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
)
from betguard.webfill.fill_preview import (
    ACCEPT_VALID_REQUIRED_MESSAGE,
    build_fill_preview,
    format_pretty_fill_preview,
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


def test_fill_preview_blocked_before_accept_valid() -> None:
    needs_review = build_batch_mock_queue(MIXED_TEXT)
    assert needs_review["status"] == NEEDS_REVIEW
    with pytest.raises(ValueError, match="Run accept-valid first."):
        build_fill_preview(needs_review)

    all_valid = build_batch_mock_queue(ALL_VALID_TEXT)
    assert all_valid["status"] == READY_FOR_QUEUE
    with pytest.raises(ValueError, match="Run accept-valid first."):
        build_fill_preview(all_valid)


def test_fill_preview_blocked_when_approved_queue_empty_or_not_human() -> None:
    queue = build_batch_mock_queue(ALL_VALID_TEXT)

    queue["approved_fill_queue"] = []
    with pytest.raises(ValueError, match=ACCEPT_VALID_REQUIRED_MESSAGE):
        build_fill_preview(queue)

    queue["approved_fill_queue"] = [{"original_fragment": "06.13.23.22 二三50", "accepted_by_human": False}]
    with pytest.raises(ValueError, match=ACCEPT_VALID_REQUIRED_MESSAGE):
        build_fill_preview(queue)


def test_fill_preview_reads_only_from_approved_fill_queue() -> None:
    accepted = _accepted(ALL_VALID_TEXT)
    accepted["preprocessing"]["valid_candidates"].append({"raw": "99.99 should not appear"})

    preview = build_fill_preview(accepted)

    assert preview["source"] == "approved_fill_queue"
    assert preview["count"] == len(accepted["approved_fill_queue"])
    fragments = [entry["original_fragment"] for entry in preview["entries"]]
    assert "99.99 should not appear" not in fragments


def test_invalid_review_items_never_appear_in_fill_preview() -> None:
    accepted = _accepted(MIXED_TEXT)
    preview = build_fill_preview(accepted)

    fragments = [entry["original_fragment"] for entry in preview["entries"]]
    assert fragments == ["06.13.23.22 二三50", "32車10元"]
    assert "2星X2" not in fragments
    assert "2星寫2" not in fragments


def test_normal_bet_preview_preserves_numbers_stars_unit_money() -> None:
    preview = build_fill_preview(_accepted("06.13.23.22 二三50"))
    entry = preview["entries"][0]

    assert entry["bet_type"] == "normal"
    assert entry["numbers"] == [6, 13, 23, 22]
    assert entry["stars"] == [2, 3]
    assert entry["unit"] == 0.5
    assert entry["money"] == 50
    assert entry["index"] == 1
    assert entry["source_index"] == 1
    assert "normal: select numbers 6,13,23,22" in entry["action_summary"]


def test_star_specific_amounts_are_preserved_in_preview() -> None:
    preview = build_fill_preview(_accepted("16.19.28.33\n2星寫2  3.4星寫1"))
    entry = preview["entries"][0]

    assert entry["star_amounts"]["2"]["money"] == 200
    assert entry["star_amounts"]["3"]["money"] == 100
    assert entry["star_amounts"]["4"]["money"] == 100
    assert entry["star_amounts"]["2"]["unit"] == 2
    assert "2星 200" in entry["action_summary"]
    assert "3星 100" in entry["action_summary"]


def test_car_preview_preserves_number_units_money() -> None:
    preview = build_fill_preview(_accepted("32車10元"))
    entry = preview["entries"][0]

    assert entry["bet_type"] == "car"
    assert entry["number"] == 32
    assert entry["car_units"] == 0.1
    assert entry["money"] == 10
    assert entry["action_summary"] == "car: select number 32; car_units 0.1; amount 10"


def test_column_preview_preserves_columns_stars_unit_money() -> None:
    preview = build_fill_preview(_accepted("17.20/28/34 二三1"))
    entry = preview["entries"][0]

    assert entry["bet_type"] == "column"
    assert entry["columns"] == [[17, 20], [28], [34]]
    assert entry["stars"] == [2, 3]
    assert entry["unit"] == 1
    assert entry["money"] == 100
    assert "column: select columns 17,20 | 28 | 34" in entry["action_summary"]


def test_preview_preserves_original_fragment_and_lines() -> None:
    preview = build_fill_preview(_accepted("16.19.28.33\n2星寫2  3.4星寫1"))
    entry = preview["entries"][0]

    assert entry["original_fragment"] == "16.19.28.33 二星2支 三四星1支"
    assert entry["original_lines"] == ["16.19.28.33", "2星寫2  3.4星寫1"]


def test_preview_does_not_change_safety_flags_or_queue_state() -> None:
    accepted = _accepted(ALL_VALID_TEXT)
    before_status = accepted["status"]
    before_items = [item["status"] for item in accepted["items"]]

    preview = build_fill_preview(accepted)

    assert accepted["status"] == before_status
    assert [item["status"] for item in accepted["items"]] == before_items
    assert accepted["final_decision"]["real_site_operation"] is False
    assert accepted["final_decision"]["auto_submit"] is False
    assert preview["safety"]["real_site_operation"] is False
    assert preview["safety"]["auto_submit"] is False
    assert preview["safety"]["danger_buttons_clicked"] == []
    assert preview["safety"]["human_required_each_item"] is True


def test_preview_never_clicks_or_operates_anything() -> None:
    accepted = _accepted(ALL_VALID_TEXT)
    preview = build_fill_preview(accepted)

    for entry in preview["entries"]:
        assert "click" not in entry["action_summary"].lower()
        assert "submit" not in entry["action_summary"].lower()
    pretty = format_pretty_fill_preview(preview)
    assert "real_site_operation=false" in pretty
    assert "auto_submit=false" in pretty
    import inspect

    from betguard.webfill import fill_preview as module

    source = inspect.getsource(module)
    assert ".click(" not in source
    assert "playwright" not in source.lower()
    assert "selenium" not in source.lower()
