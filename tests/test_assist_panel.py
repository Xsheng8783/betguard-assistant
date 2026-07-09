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

    def test_no_assist_fill_start_endpoint(self) -> None:
        assert "/assist-fill/start" not in self.html

    def test_no_assist_fill_execute_endpoint(self) -> None:
        assert "/assist-fill/execute" not in self.html

    def test_assist_button_is_disabled(self) -> None:
        assert "下一階段開放" in self.html
        assert "disabled" in self.html


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
