"""Tests for window_pin — cross-platform safety."""

from __future__ import annotations

import sys
from unittest import mock

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from betguard.webui import window_pin


class TestWindowPin:
    """Verify window_pin module safety and cross-platform behavior."""

    def test_module_imports_cleanly(self) -> None:
        assert window_pin._WINDOW_TITLE == "Betguard 輔助面板"

    def test_set_always_on_top_returns_dict(self) -> None:
        result = window_pin.set_always_on_top(True)
        assert isinstance(result, dict)
        assert "ok" in result

    def test_non_windows_returns_unsupported(self, monkeypatch) -> None:
        monkeypatch.setattr(window_pin.sys, "platform", "linux")
        r = window_pin.set_always_on_top(True)
        assert r["ok"] is False
        assert "error" in r

    def test_find_window_exact_match(self) -> None:
        # On Windows, _find_window_by_title uses real ctypes — just verify it returns int or None
        hwnd = window_pin._find_window_by_title("test_nonexistent_window_title_xyz")
        assert hwnd is None or isinstance(hwnd, int)

    def test_set_always_on_top_pin_flag(self) -> None:
        r = window_pin.set_always_on_top(True)
        assert "pinned" in r
        assert isinstance(r["pinned"], bool)
        r2 = window_pin.set_always_on_top(False)
        assert "pinned" in r2
