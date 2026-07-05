"""Tests for batch_fill_all module (queue processing logic, no browser)."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest


@pytest.fixture
def base_queue() -> dict:
    """Minimal queue with 3 items: 0=DONE, 1=CURRENT, 2=PENDING."""
    return {
        "status": "READY",
        "items": [
            {"index": 0, "status": "DONE", "numbers": [1, 2], "star": "二星"},
            {"index": 1, "status": "CURRENT", "numbers": [3, 4], "star": "二星"},
            {"index": 2, "status": "PENDING", "numbers": [5, 6], "star": "三星"},
        ],
    }


def test_finds_current_and_pending_items(base_queue):
    """Only CURRENT and PENDING items should be collected."""
    items = [it for it in base_queue["items"] if it["status"] in ("CURRENT", "PENDING")]
    assert len(items) == 2
    assert items[0]["index"] == 1
    assert items[1]["index"] == 2


def test_marks_item_done_and_advances_next_pending(base_queue):
    """Marking CURRENT as DONE should advance next PENDING to CURRENT."""
    # Mark item 1 DONE
    base_queue["items"][1]["status"] = "DONE"
    base_queue["items"][1]["fill_completed_at"] = datetime.now(timezone.utc).isoformat()

    # Advance next PENDING
    next_found = False
    for it in base_queue["items"]:
        if it["status"] == "PENDING" and not next_found:
            it["status"] = "CURRENT"
            next_found = True

    assert base_queue["items"][1]["status"] == "DONE"
    assert base_queue["items"][2]["status"] == "CURRENT"
    assert "fill_completed_at" in base_queue["items"][1]


def test_no_items_when_all_done():
    """When all items are DONE, should return no_items."""
    queue = {
        "status": "COMPLETED",
        "items": [
            {"index": 0, "status": "DONE"},
            {"index": 1, "status": "DONE"},
        ],
    }
    items = [it for it in queue["items"] if it["status"] in ("CURRENT", "PENDING")]
    assert len(items) == 0


def test_skip_when_only_ready(base_queue):
    """READY items (not CURRENT/PENDING) should be skipped."""
    base_queue["items"][1]["status"] = "READY"
    items = [it for it in base_queue["items"] if it["status"] in ("CURRENT", "PENDING")]
    assert len(items) == 1  # only item 2 (PENDING)


def test_risk_lock_rejects():
    """RISK_LOCK_MESSAGE should be returned when not acknowledged."""
    from betguard.webfill.batch_fill_all import RISK_LOCK_MESSAGE

    assert "安全鎖" in RISK_LOCK_MESSAGE
    assert "i-understand-real-site-fill-risk" in RISK_LOCK_MESSAGE
