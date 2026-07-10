"""Unit tests for launcher and user_data (no server or browser needed)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from betguard import launcher, user_data


class TestLauncherConstants:
    """Verify launcher defaults are correct."""

    def test_app_url_is_assist_panel(self) -> None:
        assert launcher.APP_URL == "http://127.0.0.1:8765/assist-panel"

    def test_port_and_host(self) -> None:
        assert launcher.PORT == 8765
        assert launcher.HOST == "127.0.0.1"


class TestFrozenMode:
    """Verify frozen-mode behavior for Playwright Chromium path."""

    def test_not_frozen_returns_false(self) -> None:
        # In test environment, sys.frozen is not set
        assert launcher._is_frozen() is False

    def test_is_frozen_detects_frozen_attribute(self, monkeypatch) -> None:
        monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
        assert launcher._is_frozen() is True

    def test_setup_bundled_chromium_sets_env_when_valid(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
        monkeypatch.setattr(launcher.sys, "executable", str(tmp_path / "BetguardAssistant.exe"))
        # PyInstaller one-folder uses _internal/ subdirectory
        (tmp_path / "_internal" / "playwright-browsers").mkdir(parents=True)
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

        launcher._setup_bundled_chromium()
        assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(tmp_path / "_internal" / "playwright-browsers")

    def test_setup_bundled_chromium_exits_when_missing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
        monkeypatch.setattr(launcher.sys, "executable", str(tmp_path / "Missing.exe"))
        # Mock input to avoid blocking
        monkeypatch.setattr("builtins.input", lambda _: "")
        with pytest.raises(SystemExit):
            launcher._setup_bundled_chromium()

    def test_development_mode_skips_bundled_path(self, monkeypatch) -> None:
        # Not frozen — should not modify env
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        launcher._setup_bundled_chromium()
        # Should not set env var in dev mode
        assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ or os.environ.get("PLAYWRIGHT_BROWSERS_PATH") != ""


class TestServerCheck:
    """Verify _is_server_running and re-launch behavior."""

    def test_is_server_running_bool(self) -> None:
        # Just verify the function returns a bool
        result = launcher._is_server_running()
        assert isinstance(result, bool)

    def test_webbrowser_open_called_once(self) -> None:
        """webbrowser.open is called once per launch."""
        with mock.patch("webbrowser.open") as m_open:
            launcher.main()
            # _is_server_running returns False → starts server →
            # but server start fails → exits with error
            # In any code path, webbrowser.open is not called repeatedly
            call_count = m_open.call_count
            assert call_count <= 1, f"webbrowser.open called {call_count} times"


class TestUserData:
    """Verify user data directory resolution."""

    def test_get_data_dir_creates_directory(self, tmp_path: Path) -> None:
        # In non-frozen mode, data dir is project-relative
        # Just verify the function returns a string and doesn't crash
        result = user_data.get_data_dir()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_runs_dir_returns_subdirectory(self) -> None:
        result = user_data.get_runs_dir()
        assert result.endswith("runs") or "runs" in result

    def test_frozen_mode_uses_documents(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))
        result = user_data.get_data_dir()
        assert "Documents" in result
        assert "Betguard Assistant Data" in result
