"""Tests for the local bet note LLM UI (loopback, mock providers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from betguard.vision.note_ui import NoteUIApp, RULES


class TestArrange:
    def test_normal_rows(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("01 20 ×1\n18 26 ×1")
        assert "01 20 二×1" in d["output"]
        assert "18 26 二×1" in d["output"]

    def test_number_set_multi_multiplier(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("(12.18.20.23) 三×0.5 四×3")
        assert "12 18 20 23 三×0.5" in d["output"]
        assert "12 18 20 23 四×3" in d["output"]

    def test_tail_expansion(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("13X24X8尾 二三X1")
        assert "13 24 08 18 28 38" in d["output"]

    def test_column_matrix(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("01 X 10 X 17\n02   11   18\n03   12   27")
        assert "01 02 03 / 10 11 12 / 17 18 27" in d["output"]

    def test_shared_multiplier_binds(self, tmp_path):
        # shared multiplier binds to the preceding number lines and expands
        app = NoteUIApp(tmp_path)
        d = app.arrange("14 16 22 28\n各=三×0.3")
        assert "14 16 22 28 三×0.3" in d["output"]
        assert "[shared]" not in d["output"]
        assert d["parse_completed"] is True
        assert d["status"] == "OK"

    def test_unparseable_flagged(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("垃圾內容")
        assert "⚠" in d["output"] or any("UNPARSEABLE" in p for p in d["problems"])

    def test_empty(self, tmp_path):
        app = NoteUIApp(tmp_path)
        d = app.arrange("")
        assert "無輸出" in d["output"]


class TestRecognize:
    def test_requires_data_url(self, tmp_path):
        app = NoteUIApp(tmp_path)
        assert app.recognize({"image": "not-a-data-url"})["error"]

    def test_gemini_missing_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        app = NoteUIApp(tmp_path)
        d = app.recognize({"image": "data:image/png;base64,AAAA", "provider": "gemini"})
        assert "error" in d


class TestProvState:
    def test_no_keys(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        app = NoteUIApp(tmp_path)
        state = app.provstate()
        assert state == {"Gemini": False, "OpenAI": False}

    def test_gemini_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        app = NoteUIApp(tmp_path)
        assert app.provstate()["Gemini"] is True


class TestPage:
    def test_rules_rendered_in_page(self):
        from betguard.vision import note_ui
        assert "尾" in note_ui.PAGE
        assert "矩陣" in note_ui.PAGE
        assert "127.0.0.1" in note_ui.PAGE
        assert "C:" not in note_ui.PAGE

    def test_rules_list_complete(self):
        assert len(RULES) == 6
        assert "01 到 39" in RULES[0]
        assert "二X1" in RULES[1]
        assert "矩陣" in RULES[5]


class TestSafetyRules:
    """Deterministic parser safety: never invent rates/stars, never leak
    [shared] into success output, keep matrix columns."""

    def _app(self, tmp_path):
        return NoteUIApp(tmp_path)

    def test_a_four_numbers_no_rate_not_four_x1(self, tmp_path):
        d = self._app(tmp_path).arrange("14 16 22 28")
        assert "四×1" not in d["output"]
        assert any("MISSING_RATE" in p for p in d["problems"])
        assert d["parse_completed"] is False
        assert d["status"] == "PENDING_HUMAN_CONFIRMATION"
        assert d["assist_fill_allowed"] is False

    def test_b_five_numbers_no_rate_not_five_x1(self, tmp_path):
        d = self._app(tmp_path).arrange("11 22 24 29 35")
        assert "五×1" not in d["output"]
        assert any("MISSING_RATE" in p for p in d["problems"])
        assert d["parse_completed"] is False

    def test_c_ten_groups_shared_er_san_05(self, tmp_path):
        raw = "\n".join([f"{n:02d} {n+1:02d} {n+2:02d} {n+3:02d}" for n in range(1, 41, 4)])
        raw += "\n各=二三×0.5"
        d = self._app(tmp_path).arrange(raw)
        lines = d["output"].splitlines()
        assert len(lines) == 20  # 10 groups × 2 categories
        assert all(line.endswith("二×0.5") or line.endswith("三×0.5") for line in lines)
        assert d["parse_completed"] is True
        assert d["status"] == "OK"

    def test_d_seven_groups_shared_er_san_03(self, tmp_path):
        raw = "\n".join([f"{n:02d} {n+1:02d} {n+2:02d} {n+3:02d} {n+4:02d}" for n in range(1, 36, 5)])
        raw += "\n各=二三×0.3"
        d = self._app(tmp_path).arrange(raw)
        lines = d["output"].splitlines()
        assert len(lines) == 14  # 7 groups × 2 categories
        assert d["parse_completed"] is True

    def test_e_shared_without_target_blocked(self, tmp_path):
        d = self._app(tmp_path).arrange("各=二三×0.5")
        assert any("SHARED_WITHOUT_TARGET" in p for p in d["problems"])
        assert d["status"] == "PENDING_HUMAN_CONFIRMATION"
        assert d["parse_completed"] is False

    def test_f_shared_marker_never_in_success(self, tmp_path):
        # even a bound shared must never leak "[shared]" text
        d = self._app(tmp_path).arrange("14 16 22 28\n各=三×0.3")
        assert "[shared]" not in d["output"]
        # unbound shared leaks nothing either
        d2 = self._app(tmp_path).arrange("各=三×0.3")
        assert "[shared]" not in d2["output"]
        assert any("SHARED_WITHOUT_TARGET" in p for p in d2["problems"])

    def test_g_3x3_matrix_keeps_columns(self, tmp_path):
        d = self._app(tmp_path).arrange("01 X 10 X 17\n02   11   18\n03   12   27")
        assert "01 02 03 / 10 11 12 / 17 18 27" in d["output"]
        assert d["matrix_kept"] is True if "matrix_kept" in d else True
        # structure preserved: no flattening into one row
        assert "01 02 03 10 11 12 17 18 27" not in d["output"]

    def test_h_normal_slip_unchanged(self, tmp_path):
        d = self._app(tmp_path).arrange("01 20 ×1\n18 26 ×1")
        assert "01 20 二×1" in d["output"]
        assert "18 26 二×1" in d["output"]
        assert d["parse_completed"] is True
        assert d["status"] == "OK"
