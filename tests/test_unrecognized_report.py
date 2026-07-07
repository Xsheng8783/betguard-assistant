"""Tests for the unrecognized format report (v1).

Covers:
  A. valid-only batch produces no report interference
  B. classification by category
  C. category counts correct
  D. max 5 samples per category
  E. malformed queue does not crash
  F. Web workbench shows link when unrecognized items exist
  G. /runs/unrecognized_*.html renders correctly
  H. report page has no accept-valid / assisted-fill / submit / confirm / DONE buttons
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from betguard.webfill.unrecognized_report import (
    CATEGORY_RULES,
    build_report_data,
    build_report_from_queue_path,
    classify_item,
    has_unrecognized,
    extract_items,
    render_report_html,
    write_report_files,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    *,
    index: int = 0,
    status: str = "BLOCKED",
    original_text: str = "17.29.1000",
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "index": index,
        "status": status,
        "original_text": original_text,
        "errors": errors or [],
        "warnings": warnings or [],
    }


def _make_queue(items: list[dict], *, valid_count: int = 0) -> dict:
    return {
        "items": items,
        "preprocessing": {
            "summary": {
                "valid_count": valid_count,
                "needs_review_count": sum(
                    1 for i in items if i.get("status", "").lower() == "needs_review"
                ),
                "invalid_unsupported_count": sum(
                    1 for i in items if i.get("status", "").lower() == "blocked"
                ),
            },
            "watchlist_count": 0,
        },
    }


# ---------------------------------------------------------------------------
# Section A -- valid-only batch produces no report interference
# ---------------------------------------------------------------------------


def test_valid_only_batch_has_no_unrecognized() -> None:
    queue = _make_queue([], valid_count=3)
    assert not has_unrecognized(queue)
    data = build_report_data(queue)
    assert data["total_blocked"] == 0
    assert data["total_valid"] == 3
    assert data["categories"] == []


def test_valid_only_report_html_does_not_interfere(tmp_path: Path) -> None:
    queue = _make_queue([], valid_count=5)
    data = build_report_data(queue)
    html = render_report_html(data)
    assert "所有" in html
    assert "無未辨識格式" in html
    # No table if all valid
    assert "<table" not in html


# ---------------------------------------------------------------------------
# Section B -- classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "errors, warnings, expected_category",
    [
        (["missing money"], [], "missing_money"),
        (["missing amount"], [], "missing_money"),
        (["requires at least 3 numbers"], [], "insufficient_numbers"),
        (["四星 requires at least 4 numbers"], [], "four_star_insufficient"),
        (["customer-specific shorthand requires manual review"], [], "customer_shorthand"),
        (["number out of range 40; valid range is 1-39"], [], "invalid_number"),
        (["unsupported characters: 改"], [], "unsupported_characters"),
        ([], ["game prefix requires manual review"], "hk_prefix"),
        ([], ["suspicious pasted token requires manual review"], "suspected_concatenated"),
        (["ambiguous money format"], [], "ambiguous_amount"),
        (["unknown error type"], [], "other"),
    ],
)
def test_classify_item(
    errors: list[str], warnings: list[str], expected_category: str
) -> None:
    item = _make_item(errors=errors, warnings=warnings)
    assert classify_item(item) == expected_category


# ---------------------------------------------------------------------------
# Section C -- category counts
# ---------------------------------------------------------------------------


def test_category_counts() -> None:
    items = [
        _make_item(index=0, errors=["missing money"]),
        _make_item(index=1, errors=["missing money"]),
        _make_item(index=2, errors=["requires at least 3 numbers"]),
        _make_item(index=3, errors=["customer-specific shorthand requires manual review"]),
        _make_item(index=4, errors=["unsupported characters: 改"]),
    ]
    queue = _make_queue(items, valid_count=0)
    data = build_report_data(queue)
    cats = {c["key"]: c["count"] for c in data["categories"]}
    assert cats.get("missing_money") == 2
    assert cats.get("insufficient_numbers") == 1
    assert cats.get("customer_shorthand") == 1
    assert cats.get("unsupported_characters") == 1
    assert data["total_blocked"] == 5
    assert data["total_categorized"] == 5


# ---------------------------------------------------------------------------
# Section D -- max 5 samples per category
# ---------------------------------------------------------------------------


def test_max_samples_per_category() -> None:
    many_items = [
        _make_item(index=i, errors=["missing money"]) for i in range(10)
    ]
    queue = _make_queue(many_items, valid_count=0)
    data = build_report_data(queue)
    mm = next(c for c in data["categories"] if c["key"] == "missing_money")
    assert mm["count"] == 10
    assert len(mm["samples"]) == 5


# ---------------------------------------------------------------------------
# Section E -- malformed queue does not crash
# ---------------------------------------------------------------------------


def test_malformed_queue_path(tmp_path: Path) -> None:
    bad_path = tmp_path / "does_not_exist.json"
    data = build_report_from_queue_path(bad_path)
    assert data["total_blocked"] == 0
    assert "error" in data


def test_empty_queue_does_not_crash(tmp_path: Path) -> None:
    data = build_report_data({})
    assert data["total_blocked"] == 0


# ---------------------------------------------------------------------------
# Section F -- write_report_files
# ---------------------------------------------------------------------------


def test_write_report_files(tmp_path: Path) -> None:
    items = [
        _make_item(index=0, errors=["missing money"]),
        _make_item(index=1, errors=["requires at least 3 numbers"]),
    ]
    queue = _make_queue(items, valid_count=1)
    qp = tmp_path / "queue.json"
    qp.write_text(json.dumps(queue), encoding="utf-8")
    html_path, json_path, data = write_report_files(qp)
    assert html_path.exists()
    assert json_path.exists()
    assert data["total_blocked"] == 2
    assert data["total_valid"] == 1
    assert data["total_categorized"] == 2
    # HTML is valid
    html_text = html_path.read_text(encoding="utf-8")
    assert "<!doctype html" in html_text
    assert "missing money" in html_text or "缺少金額" in html_text


# ---------------------------------------------------------------------------
# Section G -- report page has no danger actions
# ---------------------------------------------------------------------------


def test_report_html_no_danger_actions() -> None:
    items = [
        _make_item(index=0, errors=["missing money"]),
        _make_item(index=1, errors=["requires at least 3 numbers"]),
    ]
    queue = _make_queue(items, valid_count=0)
    data = build_report_data(queue)
    html = render_report_html(data)
    # No POST forms, no fill/submit/accept buttons
    assert 'method="post"' not in html.lower()
    assert 'method="POST"' not in html
    # No danger button text
    for forbidden in [
        "accept-valid", "assisted-fill", "送出注單", "確認對話框",
        "送出", "填入", "auto-submit", "DONE",
    ]:
        assert forbidden not in html, (
            f"report HTML must not contain {forbidden!r}"
        )


# ---------------------------------------------------------------------------
# Section H -- has_unrecognized
# ---------------------------------------------------------------------------


def test_has_unrecognized_returns_false_for_valid() -> None:
    q = {"preprocessing": {"summary": {"valid_count": 5, "needs_review_count": 0, "invalid_unsupported_count": 0}, "watchlist_count": 0}}
    assert not has_unrecognized(q)


def test_has_unrecognized_returns_true_for_needs_review() -> None:
    q = {"preprocessing": {"summary": {"valid_count": 1, "needs_review_count": 1, "invalid_unsupported_count": 0}, "watchlist_count": 0}}
    assert has_unrecognized(q)


def test_has_unrecognized_returns_true_for_invalid() -> None:
    q = {"preprocessing": {"summary": {"valid_count": 0, "needs_review_count": 0, "invalid_unsupported_count": 1}, "watchlist_count": 0}}
    assert has_unrecognized(q)


# ---------------------------------------------------------------------------
# Section I -- extract_items filters correctly
# ---------------------------------------------------------------------------


def test_extract_items_includes_blocked() -> None:
    q = _make_queue([
        _make_item(index=0, status="BLOCKED", errors=["missing money"]),
        _make_item(index=1, status="PENDING"),
    ])
    extracted = extract_items(q)
    assert len(extracted) == 1
    assert extracted[0]["index"] == 0


def test_extract_items_includes_items_with_warnings() -> None:
    q = _make_queue([
        _make_item(index=0, status="PENDING", warnings=["game prefix requires manual review"]),
    ])
    extracted = extract_items(q)
    assert len(extracted) == 1