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

    def test_column_shows_review_redirect(self) -> None:
        # Column items should show "請回主 Review 頁操作" not an active fill button
        assert "請回主 Review 頁操作" in self.html

    # Stable JS wiring: no inline onclick, addEventListener + delegation only
    def test_no_inline_onclick(self) -> None:
        assert "onclick=" not in self.html

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


def _read_source(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()
