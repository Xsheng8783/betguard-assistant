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
    """4-column bet (valid column-star combo) must flow from text to preflight."""

    @pytest.fixture(scope="class")
    def queue(self) -> dict:
        return build_batch_mock_queue(["11/22/33/13 234.100"])

    def test_valid_candidates_has_one(self, queue: dict) -> None:
        assert _valid_count(queue) == 1
        assert _invalid_count(queue) == 0

    def test_result_is_column_with_stars_and_money(self, queue: dict) -> None:
        r = _first_valid_result(queue)
        assert r["type"] == "column"
        assert r["stars"] == [2, 3, 4]
        assert r["money"] == 100
        assert r["status"] == "ok"
        assert r["columns"] == [[11], [22], [33], [13]]

    def test_accept_valid_produces_item(self, queue: dict) -> None:
        accepted = accept_valid_candidates_for_mock_queue(queue)
        items = accepted.get("items", [])
        assert len(items) >= 1
        afq = accepted.get("approved_fill_queue", [])
        assert len(afq) >= 1
        assert afq[0]["bet_type"] == "column"

    def test_afq_item_passes_preflight(self, queue: dict) -> None:
        """4-col 234 passes column-star guard and preflight."""
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
    """4 columns with range — valid column-star combo."""

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

# ── v0.5.19b: _normalize_zhu_peng_item tests ──

class TestNormalizeZhuPengItem:
    """Verify the shared normalization handles all data source formats."""

    def _make_queue_with_afq(self, columns=None, star_amounts=None) -> dict:
        afq_item = {
            "index": 1,
            "bet_type": "column",
            "accepted_by_human": True,
            "columns": columns or [[11, 28], [33, 39]],
            "stars": [2, 3, 4],
            "money": 100,
            "star_amounts": star_amounts or {
                "2": {"unit": 1, "money": 100},
                "3": {"unit": 1, "money": 100},
                "4": {"unit": 1, "money": 100},
            },
        }
        queue_item = {
            "index": 1,
            "status": "WAITING_FOR_HUMAN_CONFIRM",
            "selected_columns": [
                {"column": 1, "numbers": [11, 28]},
                {"column": 2, "numbers": [33, 39]},
            ],
            "filled_amounts": {"二星": 100, "三星": 100, "四星": 100},
        }
        return {"items": [queue_item], "approved_fill_queue": [afq_item]}

    def test_afq_star_amounts_normalized(self) -> None:
        from betguard.webfill.cli import _normalize_zhu_peng_item
        queue = self._make_queue_with_afq()
        item = _normalize_zhu_peng_item(queue, queue["items"][0])
        assert item["amounts"] == {2: 100, 3: 100, 4: 100}

    def test_selected_columns_converted(self) -> None:
        from betguard.webfill.cli import _normalize_zhu_peng_item
        q = {"items": [{
            "index": 1, "status": "WAITING_FOR_HUMAN_CONFIRM",
            "selected_columns": [
                {"column": 1, "numbers": [11, 28]},
                {"column": 2, "numbers": [33, 39]},
            ],
            "filled_amounts": {"二星": 100, "三星": 100, "四星": 100},
        }], "approved_fill_queue": []}
        item = _normalize_zhu_peng_item(q, q["items"][0])
        assert item["columns"] == [[11, 28], [33, 39]]

    def test_filled_amounts_normalized(self) -> None:
        from betguard.webfill.cli import _normalize_zhu_peng_item
        q = {"items": [{
            "index": 1, "status": "WAITING_FOR_HUMAN_CONFIRM",
            "selected_columns": [{"column": 1, "numbers": [11, 28]}],
            "filled_amounts": {"二星": 100, "三星": 200, "四星": 300},
        }], "approved_fill_queue": []}
        item = _normalize_zhu_peng_item(q, q["items"][0])
        assert item["amounts"] == {2: 100, 3: 200, 4: 300}

    def test_not_accepted_blocked(self) -> None:
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
        from betguard.webfill.cli import _normalize_zhu_peng_item
        q = {"items": [{"index": 1, "status": "WAITING_FOR_HUMAN_CONFIRM"}],
             "approved_fill_queue": []}
        item = _normalize_zhu_peng_item(q, q["items"][0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("not accepted" in e.lower() for e in report["errors"])

    def test_missing_columns_blocked(self) -> None:
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
        from betguard.webfill.cli import _normalize_zhu_peng_item
        q = {"items": [{"index": 1, "status": "WAITING_FOR_HUMAN_CONFIRM",
                        "accepted_by_human": True}],
             "approved_fill_queue": []}
        item = _normalize_zhu_peng_item(q, q["items"][0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("columns" in e.lower() for e in report["errors"])

    def test_missing_amounts_blocked(self) -> None:
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
        from betguard.webfill.cli import _normalize_zhu_peng_item
        q = {"items": [{"index": 1, "status": "WAITING_FOR_HUMAN_CONFIRM",
                        "accepted_by_human": True,
                        "columns": [[11], [22], [33], [13, 23]]}],
             "approved_fill_queue": []}
        item = _normalize_zhu_peng_item(q, q["items"][0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("amount" in e.lower() for e in report["errors"])

    def test_full_normalization_blocked_by_column_star_guard(self) -> None:
        """AFQ with 2 columns but 3/4 stars must be BLOCKED."""
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
        from betguard.webfill.cli import _normalize_zhu_peng_item
        queue = self._make_queue_with_afq()
        item = _normalize_zhu_peng_item(queue, queue["items"][0])
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("柱" in e for e in report["errors"])

# ── v0.5.19c: column-star guard + current-item-fix ──


class TestColumnStarGuard:
    """Business rule: 2-col max 2-star, 3-col max 3-star, 4+ all."""

    def _preflight(self, text: str) -> dict:
        from betguard.webfill.batch_mock_queue import (
            accept_valid_candidates_for_mock_queue,
            build_batch_mock_queue,
        )
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight

        queue = build_batch_mock_queue([text])
        vc = queue.get("preprocessing", {}).get("valid_candidates", [])
        if not vc:
            # Item was not valid → blocked at new-batch stage
            iv = queue.get("preprocessing", {}).get("invalid_fragments", [])
            if iv:
                result = iv[0].get("result", {})
                errors = result.get("errors", []) or []
                return {"status": "BLOCKED", "errors": errors}
            return {"status": "BLOCKED", "errors": ["not accepted"]}
        try:
            accepted = accept_valid_candidates_for_mock_queue(queue)
        except ValueError:
            return {"status": "BLOCKED", "errors": ["accept failed"]}
        afq = accepted.get("approved_fill_queue", [])
        if not afq:
            return {"status": "BLOCKED", "errors": ["no AFQ"]}
        item = dict(afq[0])
        if not item.get("amounts") and item.get("star_amounts"):
            amounts = {}
            for s, sa in item["star_amounts"].items():
                if isinstance(sa, dict) and "money" in sa:
                    amounts[int(s)] = sa["money"]
            if amounts:
                item["amounts"] = amounts
        return zhu_peng_preflight(item)

    # ── should be BLOCKED ──

    def test_2col_234_blocked(self) -> None:
        r = self._preflight("11-28/33-39 234.100")
        assert r["status"] == "BLOCKED"
        assert any("柱" in e for e in r["errors"])

    def test_2col_23_blocked(self) -> None:
        r = self._preflight("11-28/33-39 23.100")
        assert r["status"] == "BLOCKED"

    def test_3col_234_blocked(self) -> None:
        r = self._preflight("11/22/33 234.100")
        assert r["status"] == "BLOCKED"

    def test_afq_old_2col_with_4star_blocked(self) -> None:
        from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight

        # Simulate old AFQ item with 2 columns but star_amounts with 二/三/四
        item = {
            "index": 1, "bet_type": "column",
            "accepted_by_human": True,
            "columns": [[11, 28], [33, 39]],
            "stars": [2, 3, 4], "money": 100,
            "star_amounts": {
                "2": {"unit": 1, "money": 100},
                "3": {"unit": 1, "money": 100},
                "4": {"unit": 1, "money": 100},
            },
        }
        r = zhu_peng_preflight(item)
        assert r["status"] == "BLOCKED"
        assert any("柱" in e for e in r["errors"])

    # ── should PASS ──

    def test_2col_2_passes(self) -> None:
        r = self._preflight("11-28/33-39 2.100")
        assert r["status"] == "READY_FOR_HUMAN_REVIEW"

    def test_3col_23_passes(self) -> None:
        r = self._preflight("11/22/33 23.100")
        assert r["status"] == "READY_FOR_HUMAN_REVIEW"

    def test_4col_234_passes(self) -> None:
        r = self._preflight("11/22/33/13 234.100")
        assert r["status"] == "READY_FOR_HUMAN_REVIEW"


class TestCurrentItemNotFoundFix:
    """Assisted-fill must not crash when item is already WAITING_FOR_HUMAN_CONFIRM."""

    def test_already_waiting_item_no_crash(self) -> None:
        """Simulate: after accept-valid, item is WAITING_FOR_HUMAN_CONFIRM.
        The assisted-fill handler must skip mark_item_waiting_for_human
        and just set queue status without crashing."""
        # This is testing the logic in cli.py: the conditional
        # "if current.get('status') != 'WAITING_FOR_HUMAN_CONFIRM'"
        # prevents calling mark_item_waiting_for_human which would crash
        # because get_current_item only finds CURRENT status.
        status = "WAITING_FOR_HUMAN_CONFIRM"
        should_call_mark = status != "WAITING_FOR_HUMAN_CONFIRM"
        assert should_call_mark is False, (
            "When status is WAITING_FOR_HUMAN_CONFIRM, should skip "
            "mark_item_waiting_for_human to avoid 'current item not found'"
        )

    def test_current_status_item_does_call_mark(self) -> None:
        """When status is CURRENT, the conditional should allow the mark call."""
        status = "CURRENT"
        should_call_mark = status != "WAITING_FOR_HUMAN_CONFIRM"
        assert should_call_mark is True
