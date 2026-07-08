"""Regression tests for v0.5.19 zhu-peng text-to-preflight pipeline.

Verifies that column bet text flows correctly through:
  parser → validator → preprocessing → accept-valid → preflight
"""

from __future__ import annotations

import pytest

from betguard.webfill.batch_mock_queue import (
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
)
from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight


# ── helpers ────────────────────────────────────────────────────────────

def _normalize_afq_for_preflight(afq_item: dict) -> dict:
    """Convert star_amounts nested format to flat amounts for preflight."""
    item = dict(afq_item)
    if not item.get("amounts") and item.get("star_amounts"):
        amounts = {}
        for s, sa in item["star_amounts"].items():
            if isinstance(sa, dict) and "money" in sa:
                amounts[int(s)] = sa["money"]
        if amounts:
            item["amounts"] = amounts
    return item


def _valid_count(queue: dict) -> int:
    return len(queue.get("preprocessing", {}).get("valid_candidates", []))


def _invalid_count(queue: dict) -> int:
    return len(queue.get("preprocessing", {}).get("invalid_fragments", []))


def _first_valid_result(queue: dict) -> dict:
    vc = queue.get("preprocessing", {}).get("valid_candidates", [])
    return vc[0].get("result", {}) if vc else {}


# ── case 1: complete column bet → valid → preflight ready ─────────────


class TestCompleteColumnBetPasses:
    """11-28/33-39 234.100 must flow from text to preflight."""

    @pytest.fixture(scope="class")
    def queue(self) -> dict:
        return build_batch_mock_queue(["11-28/33-39 234.100"])

    def test_valid_candidates_has_one(self, queue: dict) -> None:
        assert _valid_count(queue) == 1
        assert _invalid_count(queue) == 0

    def test_result_is_column_with_stars_and_money(self, queue: dict) -> None:
        r = _first_valid_result(queue)
        assert r["type"] == "column"
        assert r["stars"] == [2, 3, 4]
        assert r["money"] == 100
        assert r["status"] == "ok"

    def test_accept_valid_produces_item(self, queue: dict) -> None:
        accepted = accept_valid_candidates_for_mock_queue(queue)
        items = accepted.get("items", [])
        assert len(items) >= 1
        afq = accepted.get("approved_fill_queue", [])
        assert len(afq) >= 1
        assert afq[0]["bet_type"] == "column"

    def test_afq_item_passes_preflight(self, queue: dict) -> None:
        accepted = accept_valid_candidates_for_mock_queue(queue)
        afq = accepted.get("approved_fill_queue", [])
        assert len(afq) == 1
        item = _normalize_afq_for_preflight(afq[0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"
        assert report["errors"] == []


# ── case 2: column missing stars → must NOT be valid ──────────────────


class TestColumnMissingStarsBlocked:
    """11-28/33-39 100 has no star suffix → blocked."""

    def test_valid_count_is_zero(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39 100"])
        assert _valid_count(queue) == 0
        assert _invalid_count(queue) == 1

    def test_result_is_warning(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39 100"])
        item = queue["preprocessing"]["invalid_fragments"][0]
        assert item["status"] == "warning"


# ── case 3: column missing amount → must NOT be valid ─────────────────


class TestColumnMissingAmountBlocked:
    """11-28/33-39 has no amount → blocked."""

    def test_valid_count_is_zero(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39"])
        assert _valid_count(queue) == 0
        assert _invalid_count(queue) == 1

    def test_result_is_warning(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39"])
        item = queue["preprocessing"]["invalid_fragments"][0]
        assert item["status"] == "warning"


# ── case 4: column with money but no star suffix → blocked ────────────


class TestColumnMoneyNoStarSuffixBlocked:
    """11-28/33-39 234 has money=234 but no star identification → blocked."""

    def test_valid_count_is_zero(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39 234"])
        assert _valid_count(queue) == 0
        assert _invalid_count(queue) == 1

    def test_result_has_empty_stars(self) -> None:
        queue = build_batch_mock_queue(["11-28/33-39 234"])
        item = queue["preprocessing"]["invalid_fragments"][0]
        result = item.get("result", {})
        assert result.get("stars") == [] or result.get("stars") is None


# ── case 5: non-column hyphen amount → NOT leaked ─────────────────────


class TestNonColumnHyphenAmountNotLeaked:
    """06-13-23 100 must remain warning — not accidentally made valid."""

    def test_valid_count_is_zero(self) -> None:
        queue = build_batch_mock_queue(["06-13-23 100"])
        assert _valid_count(queue) == 0
        assert _invalid_count(queue) >= 1

    def test_result_is_warning_not_ok(self) -> None:
        queue = build_batch_mock_queue(["06-13-23 100"])
        item = queue["preprocessing"]["invalid_fragments"][0]
        assert item["status"] == "warning"


# ── case 6: hyphen numbers parsed as standard bet → unchanged ─────────


class TestHyphenStandardBetUnchanged:
    """11-28-33-39 234.100 parsed as standard 4-number bet, not column."""

    def test_valid_count_is_one(self) -> None:
        queue = build_batch_mock_queue(["11-28-33-39 234.100"])
        assert _valid_count(queue) == 1

    def test_result_type_is_normal_not_column(self) -> None:
        queue = build_batch_mock_queue(["11-28-33-39 234.100"])
        r = _first_valid_result(queue)
        assert r["type"] == "normal"

    def test_stars_and_money_correct(self) -> None:
        queue = build_batch_mock_queue(["11-28-33-39 234.100"])
        r = _first_valid_result(queue)
        assert r["stars"] == [2, 3, 4]
        assert r["money"] == 100


# ── case 7: second column format 11/22/33/13-23 234.100 ───────────────


class TestSlashColumnFormat:
    """11/22/33/13-23 234.100 — slash-separated individual columns."""

    def test_valid_and_passes_preflight(self) -> None:
        queue = build_batch_mock_queue(["11/22/33/13-23 234.100"])
        assert _valid_count(queue) == 1
        r = _first_valid_result(queue)
        assert r["type"] == "column"
        # Accept and verify preflight
        accepted = accept_valid_candidates_for_mock_queue(queue)
        afq = accepted.get("approved_fill_queue", [])
        assert len(afq) == 1
        item = _normalize_afq_for_preflight(afq[0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"
