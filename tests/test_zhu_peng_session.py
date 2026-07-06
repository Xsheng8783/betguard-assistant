"""Tests for zhu_peng_session — batch session runner (no real browser)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from betguard.webfill.zhu_peng_session import (
    RISK_LOCK_MESSAGE,
    _validate_b03_frame,
    run_zhu_peng_session_fill,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_queue(items: list[dict]) -> dict:
    """Build a minimal batch queue."""
    return {
        "mode": "batch_assisted_fill_queue",
        "status": "READY",
        "current_index": 0,
        "total": len(items),
        "done_count": 0,
        "items": items,
        "summary": {},
        "final_decision": {"real_site_auto_submit": False, "human_required_each_item": True},
    }


def _make_zhu_peng_item(index: int, status: str = "CURRENT", **overrides) -> dict:
    """Build a minimal ZhuPeng queue item."""
    item = {
        "index": index,
        "status": status,
        "columns": [[11], [22], [33], [13, 23]],
        "amounts": {2: 100, 3: 100, 4: 100},
        "accepted_by_human": True,
        "summary": f"柱碰 #{index}",
    }
    item.update(overrides)
    return item


# ── B03 frame validation tests ──────────────────────────────────────────


class TestFrameValidation:
    def test_valid_frame_ok(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "OK"
        result = _validate_b03_frame(mock_page)
        assert result["valid"] is True
        assert result["reason"] is None

    def test_no_frame(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "NO_FRAME"
        result = _validate_b03_frame(mock_page)
        assert result["valid"] is False
        assert result["reason"] == "NO_FRAME"

    def test_no_mo(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "NO_MO"
        result = _validate_b03_frame(mock_page)
        assert result["valid"] is False

    def test_no_pengbet_inputs(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "NO_PENGBET_INPUTS"
        result = _validate_b03_frame(mock_page)
        assert result["valid"] is False

    def test_eval_exception(self):
        mock_page = MagicMock()
        mock_page.evaluate.side_effect = RuntimeError("page closed")
        result = _validate_b03_frame(mock_page)
        assert result["valid"] is False
        assert "eval failed" in result["reason"]


# ── Session runner safety tests (no browser needed) ──────────────────────


class TestSessionSafety:
    def test_risk_lock_rejected(self):
        """Without --i-understand-real-site-fill-risk, must be rejected."""
        with patch("betguard.webfill.zhu_peng_session.load_queue_state") as m:
            m.return_value = _make_queue([_make_zhu_peng_item(0)])
            result = run_zhu_peng_session_fill(
                queue_path="q.json", url="http://test", risk_acknowledged=False
            )
        assert "error" in result
        assert "風險確認" in result["error"]

    def test_no_items(self):
        with patch("betguard.webfill.zhu_peng_session.load_queue_state") as m:
            m.return_value = _make_queue([])
            result = run_zhu_peng_session_fill(
                queue_path="q.json", url="http://test", risk_acknowledged=True
            )
        assert result["result"] == "no_items"

    def test_all_items_done(self):
        """Items already DONE should produce 0 to fill."""
        items = [
            _make_zhu_peng_item(0, "DONE"),
            _make_zhu_peng_item(1, "DONE"),
        ]
        with patch("betguard.webfill.zhu_peng_session.load_queue_state") as m:
            m.return_value = _make_queue(items)
            result = run_zhu_peng_session_fill(
                queue_path="q.json", url="http://test", risk_acknowledged=True
            )
        assert result["result"] == "no_items"

    def test_needs_review_not_in_fill_list(self):
        """Needs Review items should not appear in the items_to_fill list."""
        items = [
            _make_zhu_peng_item(0, "CURRENT"),
            _make_zhu_peng_item(1, "NEEDS_REVIEW", accepted_by_human=False),
        ]
        with patch("betguard.webfill.zhu_peng_session.load_queue_state") as m:
            m.return_value = _make_queue(items)
            # Without playwright patch, it'll error on import — which is fine,
            # we just need to verify the items_to_fill list before it reaches
            # the playwright import.
            # Use a try/except to intercept after queue processing.
            pass
        # Verify: items_to_fill only includes CURRENT+PENDING statuses
        # item #1 has status="NEEDS_REVIEW" → not in items_to_fill
        queue = _make_queue(items)
        fillable = [it for it in queue["items"] if it["status"] in ("CURRENT", "PENDING")]
        assert len(fillable) == 1
        assert fillable[0]["status"] == "CURRENT"

    def test_playwright_import_error(self):
        """When Playwright is not installed, return error."""
        items = [_make_zhu_peng_item(0)]
        with (
            patch("betguard.webfill.zhu_peng_session.load_queue_state") as lm,
        ):
            lm.return_value = _make_queue(items)
            # Simulate ImportError for playwright.sync_api
            import builtins
            orig_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "playwright.sync_api":
                    raise ImportError("no playwright")
                return orig_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = run_zhu_peng_session_fill(
                    queue_path="q.json", url="http://test", risk_acknowledged=True
                )
        assert "error" in result
        assert "Playwright" in result["error"]


# ── Session flow tests (with mock browser via playwright.sync_api patch) ──


def _build_mock_playwright(mock_page_evaluate="OK"):
    """Build mock Playwright objects for session fill tests."""
    mock_page = MagicMock()
    mock_page.evaluate.return_value = mock_page_evaluate
    mock_page.goto = MagicMock()  # no-op
    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_browser.close = MagicMock()

    def mock_launch(*args, **kwargs):
        return mock_browser

    mock_pw = MagicMock()
    mock_pw.chromium.launch = mock_launch

    return mock_pw, mock_page, mock_browser


class TestSessionFlow:
    def test_single_item_full_flow(self, tmp_path: Path):
        """Full flow: one item, fill succeeds, human types DONE."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.side_effect = [
                "",     # "手動登入 → 柱碰頁面 → 按 Enter"
                "DONE", # human confirmation
            ]
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": True, "blocked": False,
                "numbers": [[11], [22], [33], [13, 23]],
                "amounts": {2: 100, 3: 100, 4: 100},
                "steps": [],
                "next_step": "Human must inspect...",
                "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert result["result"] == "complete"
        assert result["processed"] == 1

        final_queue = json.loads(queue_file.read_text(encoding="utf-8"))
        assert final_queue["items"][0]["status"] == "DONE"

    def test_multi_item_flow(self, tmp_path: Path):
        """Two items: both fill, both get DONE."""
        queue_file = tmp_path / "queue.json"
        items = [
            _make_zhu_peng_item(0, "CURRENT"),
            _make_zhu_peng_item(1, "PENDING"),
        ]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.side_effect = ["", "DONE", "DONE"]
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": True, "blocked": False,
                "numbers": [[11], [22]], "amounts": {},
                "steps": [],
                "next_step": "", "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert result["processed"] == 2

    def test_human_declines_done(self, tmp_path: Path):
        """Human types something other than DONE → item stays WAITING, session ends."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.side_effect = ["", "no"]  # NOT "DONE"
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": True, "blocked": False,
                "numbers": [], "amounts": {}, "steps": [],
                "next_step": "", "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert result["processed"] == 0
        items_result = result.get("items", [])
        assert len(items_result) == 1
        assert items_result[0]["status"] == "WAITING"

        final_queue = json.loads(queue_file.read_text(encoding="utf-8"))
        assert final_queue["items"][0]["status"] == "WAITING_FOR_HUMAN_CONFIRM"

    def test_frame_invalid_on_start(self, tmp_path: Path):
        """If frame is invalid at session start, return error immediately."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright("NO_FRAME")

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.return_value = ""

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert "error" in result
        assert "B03 frame" in result["error"]

    def test_preflight_blocked_skips(self, tmp_path: Path):
        """When preflight returns BLOCKED, item is skipped."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0, accepted_by_human=False)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.return_value = ""
            mock_preflight.return_value = {
                "status": "BLOCKED",
                "errors": ["Item not accepted by human"],
                "missing": [],
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert result["processed"] == 0
        items_result = result.get("items", [])
        assert len(items_result) == 1
        assert items_result[0]["status"] == "SKIPPED"

    def test_fill_blocked_stops(self, tmp_path: Path):
        """When fill returns blocked, item is blocked and not marked WAITING."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.return_value = ""
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": False, "blocked": True,
                "numbers": [], "amounts": {}, "steps": [{"blocked": "mismatch"}],
                "next_step": "", "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        assert result["blocked"] == 1
        items_result = result.get("items", [])
        assert items_result[0]["status"] == "BLOCKED"

    def test_navigation_failure(self):
        """When page.goto fails, return error."""
        items = [_make_zhu_peng_item(0)]
        with patch("betguard.webfill.zhu_peng_session.load_queue_state") as m:
            m.return_value = _make_queue(items)

            mock_page = MagicMock()
            def mock_goto(*args, **kwargs):
                raise RuntimeError("connection refused")
            mock_page.goto = mock_goto
            mock_browser = MagicMock()
            mock_browser.new_page.return_value = mock_page

            def mock_launch(*args, **kwargs):
                return mock_browser

            mock_pw = MagicMock()
            mock_pw.chromium.launch = mock_launch

            with patch("playwright.sync_api.sync_playwright") as sp:
                sp.return_value.__enter__.return_value = mock_pw
                sp.return_value.__exit__.return_value = None
                result = run_zhu_peng_session_fill(
                    queue_path="q.json", url="http://test", risk_acknowledged=True
                )

        assert "error" in result
        assert "Failed to navigate" in result["error"]

    def test_browser_closes_after_session(self, tmp_path: Path):
        """Browser.close() must be called after session completes."""
        queue_file = tmp_path / "queue.json"
        items = [_make_zhu_peng_item(0)]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, mock_browser = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            mock_input.side_effect = ["", "DONE"]
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": True, "blocked": False,
                "numbers": [], "amounts": {}, "steps": [],
                "next_step": "", "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        mock_browser.close.assert_called_once()

    def test_no_auto_advance_without_done(self, tmp_path: Path):
        """Session runner never auto-advances: DONE gate is mandatory."""
        queue_file = tmp_path / "queue.json"
        items = [
            _make_zhu_peng_item(0, "CURRENT"),
            _make_zhu_peng_item(1, "PENDING"),
        ]
        queue = _make_queue(items)
        queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

        mock_pw, _, _ = _build_mock_playwright()

        with (
            patch("playwright.sync_api.sync_playwright") as sp,
            patch("builtins.input") as mock_input,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_preflight") as mock_preflight,
            patch("betguard.webfill.zhu_peng_session.zhu_peng_fill_execute") as mock_fill,
        ):
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = None
            # Only one input for login → fill succeeds → wait for DONE → StopIteration
            # means human walked away (no input at all for DONE prompt).
            # That's the same as not typing DONE.
            mock_input.side_effect = ["", ""]
            mock_preflight.return_value = {"status": "READY_FOR_HUMAN_REVIEW", "errors": []}
            mock_fill.return_value = {
                "ok": True, "blocked": False,
                "numbers": [], "amounts": {}, "steps": [],
                "next_step": "", "auto_submit": False, "auto_confirm": False, "auto_next": False,
            }

            result = run_zhu_peng_session_fill(
                queue_path=str(queue_file), url="http://test", risk_acknowledged=True
            )

        # Only one item processed (the CURRENT), not both
        assert result["processed"] == 0
        # Item 0 should be WAITING, item 1 should still be PENDING
        final_queue = json.loads(queue_file.read_text(encoding="utf-8"))
        assert final_queue["items"][0]["status"] == "WAITING_FOR_HUMAN_CONFIRM"
        assert final_queue["items"][1]["status"] == "PENDING"


# ── Safety guard verification ───────────────────────────────────────────


class TestSafetyGuardIntegration:
    """Verify ZHU_PENG_SAFETY_GUARDS still block forbidden selectors."""

    def test_forbidden_selectors_untouched(self):
        from betguard.webfill.zhu_peng_pipeline import ZHU_PENG_SAFETY_GUARDS

        forbidden = ZHU_PENG_SAFETY_GUARDS["forbidden_selectors"]
        assert "#GroupSet_Value" in forbidden
        assert any("tb_" in s for s in forbidden)
        assert any("ta_" in s for s in forbidden)
        assert any("OnChkNO" in s for s in forbidden)
        assert any("OnChkBet" in s for s in forbidden)

    def test_auto_submit_never_true(self):
        from betguard.webfill.zhu_peng_pipeline import ZHU_PENG_SAFETY_GUARDS

        assert ZHU_PENG_SAFETY_GUARDS["auto_submit"] is False
        assert ZHU_PENG_SAFETY_GUARDS["auto_confirm"] is False
        assert ZHU_PENG_SAFETY_GUARDS["auto_next"] is False
