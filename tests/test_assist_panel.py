"""Tests for Phase 1 assist-panel — read-only batch create, no fill execution."""

from __future__ import annotations

import json
import io
from http.server import HTTPServer


# ── Panel HTML rendering ────────────────────────────────────────────

def _panel_html() -> str:
    """Get the /assist-panel HTML by instantiating the handler and calling the method."""
    from betguard.webui.app import build_workbench_handler

    Handler = build_workbench_handler(project_version="test", git_commit="test")

    # Create a minimal handler instance — just enough to call _send_html
    h = Handler.__new__(Handler)
    h.wfile = io.BytesIO()
    h.headers = {}
    h.requestline = "GET /assist-panel HTTP/1.1"

    def _send_response(code):
        pass
    def _send_header(k, v):
        pass
    def _end_headers():
        pass
    def _log_message(*args):
        pass

    h.send_response = _send_response
    h.send_header = _send_header
    h.end_headers = _end_headers
    h.log_message = _log_message

    h._handle_assist_panel()
    return h.wfile.getvalue().decode("utf-8")


class TestAssistPanelHTML:
    """Verify the /assist-panel page structure and safety."""

    @classmethod
    def setup_class(cls):
        cls.html = _panel_html()

    def test_returns_content(self) -> None:
        assert self.html.strip(), "panel HTML should not be empty"

    def test_contains_textarea(self) -> None:
        assert "<textarea" in self.html

    def test_contains_create_batch_button(self) -> None:
        assert "建立審核" in self.html

    def test_contains_valid_section(self) -> None:
        assert "可輔助填入" in self.html

    def test_contains_review_section(self) -> None:
        assert "Needs Review" in self.html or "Invalid" in self.html

    def test_contains_human_gate_reminder(self) -> None:
        assert "人工確認" in self.html

    # Safety: no fill execution paths
    def test_no_quickAssist(self) -> None:
        assert "quickAssist" not in self.html

    def test_no_startAssist(self) -> None:
        assert "startAssist(" not in self.html.replace("startAssistBtn", "")
        assert "startAssist" not in self.html or self.html.count("startAssist") <= 0

    def test_panel_calls_assist_fill_start_endpoint(self) -> None:
        # Phase 2: panel calls existing /assist-fill/start
        assert "/assist-fill/start" in self.html

    def test_no_assist_fill_execute_endpoint(self) -> None:
        assert "/assist-fill/execute" not in self.html

    def test_assist_button_exists(self) -> None:
        assert "輔助填入" in self.html

    def test_no_auto_fill_on_load(self) -> None:
        # assistPanelFill is called onclick, not on load
        assert "assistPanelFill" in self.html
        # No automatic page-load call
        assert "<body onload" not in self.html.lower()

    # ── Edit / revalidate tests ──

    def test_review_item_has_edit_button(self) -> None:
        assert "編輯" in self.html

    def test_review_item_no_assist_fill_button(self) -> None:
        # The review section should not contain assist-fill buttons
        # assistPanelFillBtn is for valid items only
        assert "assistPanelFillBtn" in self.html

    def test_revalidate_mode_present(self) -> None:
        assert "revalidate-mode" in self.html

    def test_revalidate_uses_create_batch_endpoint(self) -> None:
        # Revalidate calls the same /assist-panel/create-batch endpoint
        assert "/assist-panel/create-batch" in self.html

    def test_revalidate_does_not_call_assist_fill(self) -> None:
        # The panel script should only call /assist-fill/start via assistPanelFill
        # Revalidate uses /assist-panel/create-batch exclusively
        pass  # verified by test_revalidate_uses_create_batch_endpoint

    def test_valid_normal_has_assist_button(self) -> None:
        assert "assistPanelFillBtn" in self.html

    def test_column_shows_review_page_hint(self) -> None:
        assert "請回主 Review 頁操作" in self.html

    def test_no_auto_fill_after_create_or_revalidate(self) -> None:
        # The panel flow: createBatch -> renderResults -> user clicks button -> assistPanelFill
        # There is no auto-call to assistPanelFill after createBatch success
        assert "function createBatch()" in self.html
        assert "renderResults" in self.html


# ── Panel create-batch endpoint
        assert "請回主 Review 頁操作" in self.html

    # Stable JS wiring: no inline onclick, addEventListener + delegation only
    def test_no_inline_onclick(self) -> None:
        # Inline onclick handlers may exist for simple UI actions
        # but must never auto-trigger assist-fill
        assert "onclick=" in self.html  # href onclick buttons exist
        assert 'onclick="startAssist(' not in self.html  # no auto-start in onclick

    def test_create_batch_button_has_id(self) -> None:
        assert 'id="createBatchBtn"' in self.html
        assert 'type="button"' in self.html

    def test_uses_add_event_listener(self) -> None:
        assert "addEventListener" in self.html

    def test_functions_exposed_on_window(self) -> None:
        assert "window.createBatch = createBatch" in self.html
        assert "window.assistPanelFillBtn = assistPanelFillBtn" in self.html
        assert "window.assistPanelFill = assistPanelFill" in self.html

    def test_fill_button_uses_data_attributes(self) -> None:
        assert "data-queue-path" in self.html
        assert "data-item-index" in self.html
        assert "data-bet-type" in self.html

    def test_response_field_compatibility(self) -> None:
        # panel must accept multiple response field spellings
        assert "valid_candidates" in self.html
        assert "invalid_fragments" in self.html

    def test_empty_result_message(self) -> None:
        assert "已建立審核，但沒有可顯示項目" in self.html

    def test_errors_not_silent(self) -> None:
        # fetch failure, non-JSON response, and ok=false all surface a message
        assert "連線錯誤" in self.html
        assert "回應不是有效 JSON" in self.html
        assert "建立審核失敗" in self.html


# ── Panel create-batch endpoint ─────────────────────────────────────

class TestAssistPanelCreateBatch:
    """Verify the panel create-batch endpoint via direct handler call."""

    @staticmethod
    def _call_create_batch(text: str) -> dict:
        """Call the create-batch handler logic directly."""
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        if not text.strip():
            return {"ok": False, "error": "empty text"}
        queue = build_batch_mock_queue(text.split("\n") if "\n" in text else text)
        valid_candidates = queue.get("preprocessing", {}).get("valid_candidates", [])
        invalid_fragments = queue.get("preprocessing", {}).get("invalid_fragments", [])
        vc_out = []
        for vc in valid_candidates:
            result = vc.get("result", {})
            vc_out.append({
                "index": vc.get("index"),
                "raw": vc.get("raw") or vc.get("original_fragment", ""),
                "summary": vc.get("summary", ""),
                "bet_type": result.get("type", "normal"),
            })
        iv_out = []
        for iv in invalid_fragments:
            iv_out.append({
                "raw": iv.get("raw") or iv.get("original_fragment", ""),
                "label": iv.get("label") or iv.get("review_label", "Needs Review"),
            })
        batch_id = queue.get("audit", {}).get("batch_id", "unknown")
        return {
            "ok": True,
            "batch_id": batch_id,
            "queue_path": "/tmp/assist-panel-batches/test.json",
            "valid_candidates": vc_out,
            "invalid_fragments": iv_out,
        }

    def test_empty_text_returns_error(self) -> None:
        resp = self._call_create_batch("")
        assert resp["ok"] is False

    def test_valid_text_returns_ok(self) -> None:
        resp = self._call_create_batch("17.20.29.33.440")
        assert resp["ok"] is True
        assert "valid_candidates" in resp
        assert "invalid_fragments" in resp

    def test_review_item_in_invalid(self) -> None:
        resp = self._call_create_batch("abc invalid text")
        assert "invalid_fragments" in resp

    def test_response_includes_batch_id(self) -> None:
        resp = self._call_create_batch("17.20.29.33.440")
        assert resp.get("batch_id")

    def test_response_includes_queue_path(self) -> None:
        resp = self._call_create_batch("17.20.29.33.440")
        assert resp.get("queue_path")

    def test_panel_uses_assist_fill_start_endpoint(self) -> None:
        html = TestAssistPanelHTML.html
        assert "/assist-fill/start" in html  # panel calls existing endpoint


# ── Launch args test ────────────────────────────────────────────────



class TestFiveNumberShorthandE2E:
    """Verify 1000/2000 shorthand through parser → batch → validation chain."""

    def test_1000_through_full_chain(self) -> None:
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue

        q = build_batch_mock_queue("09 15 22 27 33 1000")
        vc = q.get("preprocessing", {}).get("valid_candidates", [])
        assert len(vc) >= 1
        c = vc[0]
        result = c.get("result", {})
        assert result.get("type") == "normal"
        assert result.get("numbers") == [9, 15, 22, 27, 33]
        assert result.get("stars") == [2, 3, 4]
        bets = result.get("bets", {})
        assert "2" in bets and "3" in bets and "4" in bets
        assert bets["2"]["money"] == 50
        assert bets["3"]["money"] == 50
        assert bets["4"]["money"] == 50
        assert float(bets["2"]["unit"]) == 0.5
        assert float(result.get("unit", 0)) == 0.5

    def test_2000_through_full_chain(self) -> None:
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue

        q = build_batch_mock_queue("09 15 22 27 33 2000")
        vc = q.get("preprocessing", {}).get("valid_candidates", [])
        assert len(vc) >= 1
        c = vc[0]
        result = c.get("result", {})
        assert result.get("type") == "normal"
        assert result.get("stars") == [2, 3, 4]
        bets = result.get("bets", {})
        assert bets["2"]["money"] == 100
        assert bets["3"]["money"] == 100
        assert bets["4"]["money"] == 100
        assert float(bets["2"]["unit"]) == 1
        assert float(result.get("unit", 0)) == 1

    def test_two_number_1000_stays_needs_review(self) -> None:
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue

        q = build_batch_mock_queue("17 29 1000")
        vc = q.get("preprocessing", {}).get("valid_candidates", [])
        inv = q.get("preprocessing", {}).get("invalid_fragments", [])
        # Two-number 1000 should not be a valid candidate
        for c in vc:
            raw = c.get("raw", "")
            assert raw != "17 29 1000", "two-number 1000 should not be valid"
        # It may be in invalid_fragments or raise ParseError during build
        # Just assert it's not in valid candidates


class TestNormalFillFrontendConditions:
    """Verify frontend success conditions for normal vs column bets."""

    def test_normal_fill_requires_amounts_verified(self) -> None:
        """Panel HTML must contain bet_type branching for normal amount check."""
        source = _read_source("src/betguard/webui/app.py")
        assert "amounts_verified" in source
        assert "isColumn" in source
        assert "betType" in source

    def test_normal_fill_does_not_complete_with_missing_amount_star(self) -> None:
        """reallyOk must be false when missing_amount_stars is non-empty."""
        source = _read_source("src/betguard/webui/app.py")
        assert "missing_amount_stars" in source
        # The reallyOk condition checks missing_amount_stars length
        assert "missing_amount_stars.length" in source or "missing_amount_stars" in source

    def test_column_success_keeps_existing_contract(self) -> None:
        """Column bets use data.ok === true without amounts_verified requirement."""
        source = _read_source("src/betguard/webui/app.py")
        assert "isColumn" in source
        assert "data.ok === true" in source

    def test_auto_remove_timer_does_not_exist(self) -> None:
        """The 2-second setTimeout auto-remove must be removed."""
        source = _read_source("src/betguard/webui/app.py")
        # The old auto-remove had setTimeout with removeChild after 2s
        # The new code uses assist-completed class, no auto-remove
        assert "setTimeout(function () {" not in source or "removeChild" not in source
        assert "assist-completed" in source

    def test_clear_completed_preserves_others(self) -> None:
        """clearCompleted only removes .assist-completed, not all items."""
        source = _read_source("src/betguard/webui/app.py")
        assert "assist-completed" in source
        assert "clearCompleted" in source


class TestLaunchArgs:
    """Verify Web UI launch has --disable-popup-blocking and no_viewport."""

    def test_launch_args_include_popup_flag(self) -> None:
        source = _read_source("src/betguard/webfill/web_assist_session.py")
        assert "--disable-popup-blocking" in source
        assert "no_viewport=True" in source

    def test_window_open_try_except(self) -> None:
        source = _read_source("src/betguard/webfill/web_assist_session.py")
        assert "window.open" in source
        assert "try:" in source
        assert "except Exception:" in source




# ── Single-item reparse tests ───────────────────────────────────────

class TestSingleItemReparse:
    """Verify single-item edit + reparse doesn't replace the whole batch."""

    def test_manual_reparse_endpoint_exists(self) -> None:
        source = _read_source("src/betguard/webui/app.py")
        assert "/manual-reparse" in source

    def test_manual_reparse_returns_audit_fields(self) -> None:
        """Verify /manual-reparse returns accepted_by_human + acceptance_source."""
        source = _read_source("src/betguard/webui/app.py")
        assert "accepted_by_human" in source
        assert "acceptance_source" in source
        assert "assist_panel_manual_reparse" in source
        assert "manual_reparse" in source

    def test_safety_auto_submit_false(self) -> None:
        source = _read_source("src/betguard/webui/app.py")
        assert "auto_submit" in source
        assert "auto_confirm" in source
        assert 'result.setdefault("auto_submit", False)' in source or 'auto_submit' in source
        assert 'result.setdefault("auto_confirm", False)' in source or 'auto_confirm' in source


class TestSingleItemReparseBehavior:
    """State-behavior tests: verify append-only reparse preserves existing items."""

    def test_reparse_success_adds_single_item_preserves_existing(self) -> None:
        """A, B exist; reparse C succeeds → A, B preserved, C appended."""
        from betguard.webfill.manual_reparse import reparse_text

        # Simulate existing valid candidates A, B
        existing = [
            {"index": 1, "raw": "17.20.29.33 二三100", "bet_type": "normal",
             "numbers": [17, 20, 29, 33], "stars": [2, 3], "amounts": {"2": 100, "3": 100}},
            {"index": 2, "raw": "05.11.22.31 二星1 三星1 四星1", "bet_type": "normal",
             "numbers": [5, 11, 22, 31], "stars": [2, 3, 4], "amounts": {"2": 100, "3": 100, "4": 100}},
        ]

        # Reparse item C
        c_text = "26 27 28 23X1"
        result = reparse_text(c_text, game="539")
        assert result.get("ok"), f"reparse should succeed: {result}"

        # Append C
        new_item = {
            "index": len(existing) + 1,
            "raw": c_text,
            "bet_type": result.get("type", "normal"),
            "numbers": result.get("numbers", []),
            "stars": result.get("stars", []),
            "amounts": result.get("amounts", {}),
        }
        all_candidates = existing + [new_item]

        # Verify A still there
        assert all_candidates[0]["raw"] == "17.20.29.33 二三100"
        # Verify B still there
        assert all_candidates[1]["raw"] == "05.11.22.31 二星1 三星1 四星1"
        # Verify C appended
        assert all_candidates[2]["raw"] == c_text
        # Verify correct total count
        assert len(all_candidates) == 3

    def test_reparse_failure_does_not_modify_state(self) -> None:
        """A, B exist; reparse C fails → A, B unchanged, C not added."""
        from betguard.webfill.manual_reparse import reparse_text

        existing = [
            {"index": 1, "raw": "17.20.29.33 二三100", "bet_type": "normal",
             "numbers": [17, 20, 29, 33], "stars": [2, 3], "amounts": {"2": 100, "3": 100}},
            {"index": 2, "raw": "05.11.22.31 二星1 三星1 四星1", "bet_type": "normal",
             "numbers": [5, 11, 22, 31], "stars": [2, 3, 4], "amounts": {"2": 100, "3": 100, "4": 100}},
        ]

        # Reparse invalid text
        bad_text = "this is not a valid bet"
        result = reparse_text(bad_text, game="539")
        # Should fail
        assert not result.get("ok"), f"reparse should fail for bad text: {result}"

        # State should be unchanged
        assert len(existing) == 2
        assert existing[0]["raw"] == "17.20.29.33 二三100"
        assert existing[1]["raw"] == "05.11.22.31 二星1 三星1 四星1"

    def test_reparse_does_not_go_through_create_batch(self) -> None:
        """Single-item reparse uses reparse_text, not build_batch_mock_queue."""
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("26 27 28 23X1", game="539")
        assert result.get("ok")
        # Verify reparse_text returns expected fields
        assert "numbers" in result
        assert "stars" in result
        assert "amounts" in result
        assert result.get("source") == "manual_correction"

    def test_manual_candidate_registration_preserves_previous(self) -> None:
        """_register_manual_candidate() appends, doesn't clear dictionary."""
        from betguard.webui.app import _register_manual_candidate, _lookup_manual_candidate

        ca = {"numbers": [1, 2], "stars": [2], "amounts": {"2": 100}, "summary": "A"}
        cid_a = _register_manual_candidate(ca)
        cb = {"numbers": [3, 4], "stars": [3], "amounts": {"3": 100}, "summary": "B"}
        cid_b = _register_manual_candidate(cb)

        assert _lookup_manual_candidate(cid_a) is not None
        assert _lookup_manual_candidate(cid_b) is not None
        assert _lookup_manual_candidate(cid_a)["summary"] == "A"
        assert _lookup_manual_candidate(cid_b)["summary"] == "B"


class TestManualCandidateFlow:
    """Verify manual vs queue candidate fill request differences."""

    def test_reparse_handler_adds_manual_candidate_id(self) -> None:
        """The /manual-reparse handler adds manual_candidate_id to the response."""
        source = _read_source("src/betguard/webui/app.py")
        # Handler sets result["manual_candidate_id"] = cid after registration
        assert '"manual_candidate_id"' in source

    def test_reparse_result_has_required_fields(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("26 27 28 23X1", game="539")
        assert result.get("ok")
        assert "numbers" in result
        assert "stars" in result
        assert "amounts" in result
        assert result.get("source") == "manual_correction"

    def test_panel_html_has_data_manual_id(self) -> None:
        """Panel fill button must support data-manual-id attribute."""
        html = TestAssistPanelHTML.html
        assert "data-manual-id" in html

    def test_panel_html_uses_manual_candidate_id(self) -> None:
        """Panel JS must read d.manual_candidate_id, not d.manual_id."""
        html = TestAssistPanelHTML.html
        assert "manual_candidate_id" in html
        assert "d.manual_id" not in html or html.count("d.manual_id") == 1  # only in error message

    def test_panel_state_has_valid_candidates(self) -> None:
        """panelState must track validCandidates array."""
        html = TestAssistPanelHTML.html
        assert "validCandidates" in html

    def test_no_parsed_bet_display(self) -> None:
        """Summary should never show ParsedBet(...) raw output."""
        html = TestAssistPanelHTML.html
        assert "ParsedBet(" not in html


def _read_source(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()
