"""Test worker protocol types: WorkerOcrItem, WorkerResponse, sort_ocr_items."""

from __future__ import annotations

import pytest

from betguard.vision.worker_protocol import (
    sort_ocr_items,
    WorkerOcrItem,
    WorkerResponse,
    WORKER_PROTOCOL_VERSION,
)


class TestWorkerOcrItem:
    def test_valid_item(self):
        item = WorkerOcrItem.from_dict({
            "text": "hello",
            "score": 0.95,
            "polygon": [[0, 0], [100, 0], [100, 30], [0, 30]],
            "box": [0, 0, 100, 30],
        })
        assert item.text == "hello"
        assert item.score == 0.95
        assert len(item.polygon) == 4

    def test_invalid_polygon_not_list(self):
        with pytest.raises(ValueError, match="Invalid polygon"):
            WorkerOcrItem.from_dict({"text": "x", "score": 1.0, "polygon": "not-a-list"})

    def test_invalid_polygon_bad_points(self):
        with pytest.raises(ValueError, match="Invalid polygon"):
            WorkerOcrItem.from_dict({"text": "x", "score": 1.0, "polygon": [[0]]})

    def test_missing_score_defaults_zero(self):
        item = WorkerOcrItem.from_dict({"text": "x", "polygon": [[0, 0], [1, 0], [1, 1]]})
        assert item.score == 0.0

    def test_non_numeric_score_converted(self):
        item = WorkerOcrItem.from_dict({"text": "x", "score": "0.85", "polygon": [[0, 0], [1, 0], [1, 1]]})
        assert item.score == 0.85


class TestSortOcrItems:
    def test_sort_by_y_then_x(self):
        items = [
            WorkerOcrItem("A", 1.0, [[50, 100], [60, 100], [60, 110], [50, 110]]),
            WorkerOcrItem("B", 1.0, [[10, 10], [30, 10], [30, 20], [10, 20]]),
            WorkerOcrItem("C", 1.0, [[10, 10], [30, 10], [30, 20], [10, 20]]),  # same pos as B
        ]
        sorted_items = sort_ocr_items(items)
        # B and C should come before A (lower y). B before C (stable, B was first)
        assert sorted_items[0].text == "B"
        assert sorted_items[1].text == "C"
        assert sorted_items[2].text == "A"

    def test_stable_on_tie(self):
        items = [
            WorkerOcrItem("first", 1.0, [[0, 0], [1, 0], [1, 1], [0, 1]]),
            WorkerOcrItem("second", 1.0, [[0, 0], [1, 0], [1, 1], [0, 1]]),
            WorkerOcrItem("third", 1.0, [[0, 0], [1, 0], [1, 1], [0, 1]]),
        ]
        sorted_items = sort_ocr_items(items)
        assert sorted_items[0].text == "first"
        assert sorted_items[1].text == "second"
        assert sorted_items[2].text == "third"


class TestWorkerResponse:
    def test_valid_response(self):
        d = {
            "ok": True,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "request_id": "req-123",
            "engine": {"name": "paddleocr"},
            "elapsed_ms": 1234.5,
            "items": [
                {"text": "abc", "score": 0.99, "polygon": [[0, 0], [1, 0], [1, 1]]},
            ],
            "warnings": [],
        }
        resp = WorkerResponse.from_dict(d)
        assert resp.ok is True
        assert resp.request_id == "req-123"
        assert len(resp.items) == 1

    def test_invalid_item_raises(self):
        d = {
            "ok": True,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "request_id": "r",
            "items": [{"text": "x", "score": 1.0, "polygon": "bad"}],
        }
        with pytest.raises(ValueError, match="Invalid OCR item"):
            WorkerResponse.from_dict(d)

    def test_error_response(self):
        d = {
            "ok": False,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "request_id": "r",
            "error": {"code": "TEST", "message": "fail"},
        }
        resp = WorkerResponse.from_dict(d)
        assert resp.ok is False
        assert resp.error["code"] == "TEST"
