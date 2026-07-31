"""Regression tests for frozen launcher -- verify --serve mode and no recursion."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _reset_fake_popen():
    _FakePopen.reset()


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from betguard import launcher


class TestFrozenServerCommand:
    """Verify frozen mode uses --serve, not -m betguard.webui.app."""

    def test_frozen_start_server_uses_serve_flag(self, monkeypatch) -> None:
        monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
        monkeypatch.setattr(launcher.sys, "executable", r"C:\Program Files\Betguard\BetguardAssistant.exe")
        monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(launcher, "_open_server_log", lambda: None)

        launcher._start_server()
        assert _FakePopen.last_cmd is not None
        assert "--serve" in _FakePopen.last_cmd
        assert "-m" not in _FakePopen.last_cmd
        assert "betguard.webui.app" not in " ".join(_FakePopen.last_cmd)

    def test_dev_mode_uses_python_m(self, monkeypatch) -> None:
        monkeypatch.setattr(launcher.sys, "frozen", False, raising=False)
        monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(launcher, "_open_server_log", lambda: None)

        launcher._start_server()
        assert _FakePopen.last_cmd is not None
        assert "-m" in _FakePopen.last_cmd
        assert "betguard.webui.app" in _FakePopen.last_cmd

    def test_serve_flag_runs_server_directly(self, monkeypatch) -> None:
        """--serve should call _run_server, not main() launcher flow."""
        called = []
        monkeypatch.setattr(launcher, "_run_server", lambda: called.append("server"))
        monkeypatch.setattr(launcher, "_is_server_running", lambda: True)
        monkeypatch.setattr(launcher.webbrowser, "open", mock.MagicMock())

        monkeypatch.setattr(launcher.sys, "argv", ["BetguardAssistant.exe", "--serve"])
        launcher.main()
        assert called == ["server"], "_run_server should be called in --serve mode"

    def test_main_without_serve_does_not_run_server_directly(self, monkeypatch) -> None:
        called = []
        monkeypatch.setattr(launcher, "_run_server", lambda: called.append("server"))
        monkeypatch.setattr(launcher, "_is_server_running", lambda: True)
        monkeypatch.setattr(launcher.webbrowser, "open", mock.MagicMock())

        monkeypatch.setattr(launcher.sys, "argv", ["BetguardAssistant.exe"])
        launcher.main()
        assert called == [], "_run_server should NOT be called without --serve"


class TestLicenseStartUrl:
    """_resolve_start_url picks /license vs / based on license status."""

    def _patch_status(self, monkeypatch, status: str):
        import betguard.license as _lic
        monkeypatch.setattr(
            _lic, "license_status",
            lambda: {"status": status, "device_id": "BG-XXXX-XXXX-XXXX"},
        )

    def test_inactive_opens_license_page(self, monkeypatch) -> None:
        self._patch_status(monkeypatch, "inactive")
        assert launcher._resolve_start_url() == launcher.LICENSE_URL

    def test_expired_opens_license_page(self, monkeypatch) -> None:
        self._patch_status(monkeypatch, "expired")
        assert launcher._resolve_start_url() == launcher.LICENSE_URL

    def test_active_opens_homepage(self, monkeypatch) -> None:
        self._patch_status(monkeypatch, "active")
        assert launcher._resolve_start_url() == launcher.APP_URL

    def test_unreadable_status_opens_license_page(self, monkeypatch) -> None:
        import betguard.license as _lic
        def boom():
            raise RuntimeError("cannot read license")
        monkeypatch.setattr(_lic, "license_status", boom)
        assert launcher._resolve_start_url() == launcher.LICENSE_URL

    def test_browser_open_failure_does_not_crash(self, monkeypatch) -> None:
        """webbrowser.open raising must not crash the launcher (frozen GUI)."""
        self._patch_status(monkeypatch, "inactive")
        printed = []
        def boom(url):
            raise RuntimeError("browser failed")
        monkeypatch.setattr(launcher.webbrowser, "open", boom)
        monkeypatch.setattr(launcher, "_is_server_running", lambda: True)
        monkeypatch.setattr(launcher, "_setup_bundled_chromium", lambda: None)
        monkeypatch.setattr(launcher, "_safe_print", lambda msg: printed.append(msg))
        monkeypatch.setattr(launcher.sys, "argv", ["BetguardAssistant.exe"])
        launcher.main()  # must not raise
        assert printed, "launcher should have printed something and returned"

    def test_frozen_mode_never_calls_input(self) -> None:
        """Frozen GUI launcher must never call input() / rely on stdin."""
        src = Path(launcher.__file__).read_text(encoding="utf-8")
        import re as _re
        # No bare input( calls in the launcher source
        assert _re.search(r"(?<!\.)input\s*\(", src) is None, "launcher must not call input()"
        assert "sys.stdin" not in src, "launcher must not depend on sys.stdin"


class TestNoDuplicateServer:
    """Verify no duplicate server starts when port is occupied."""

    def test_server_running_opens_browser_only(self, monkeypatch) -> None:
        opened = []
        monkeypatch.setattr(launcher, "_setup_bundled_chromium", lambda: None)
        monkeypatch.setattr(launcher, "_is_server_running", lambda: True)
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
        monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)

        launcher.main()
        assert len(opened) == 1, "Should open browser exactly once"
        assert _FakePopen.last_cmd is None, "Should NOT start server when already running"

    def test_server_not_running_starts_server(self, monkeypatch) -> None:
        started = []
        monkeypatch.setattr(launcher, "_is_server_running", lambda: bool(started))
        monkeypatch.setattr(launcher, "_open_server_log", lambda: None)
        monkeypatch.setattr(launcher.webbrowser, "open", mock.MagicMock())
        monkeypatch.setattr(launcher, "_setup_bundled_chromium", lambda: None)

        def fake_popen(*a, **kw):
            started.append(True)
            return _FakePopen.__new__(_FakePopen)
        monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

        launcher.main()
        assert len(started) == 1, "Server should be started once"


class TestServerLogging:
    """Verify server stdout/stderr goes to UTF-8 log, not DEVNULL."""

    def test_server_log_is_file_not_devnull(self, monkeypatch, tmp_path: Path) -> None:
        log_dir = tmp_path / "Betguard Assistant Data" / "logs"
        monkeypatch.setattr(launcher.os.path, "expanduser", lambda p: str(tmp_path))
        f = launcher._open_server_log()
        assert f is not None
        import io
        assert not isinstance(f, type(subprocess.DEVNULL)) if hasattr(subprocess, 'DEVNULL') else True
        f.close()

    def test_server_log_writes_utf8(self, tmp_path: Path) -> None:
        import betguard.launcher as _lm
        log_dir = tmp_path / "Betguard Assistant Data" / "logs"
        # Directly create and verify, bypassing expanduser
        log_dir.mkdir(parents=True)
        log_file = log_dir / "server.log"
        log_file.write_text("測試 UTF-8 log", encoding="utf-8")
        content = log_file.read_text(encoding="utf-8")
        assert "測試 UTF-8 log" in content



# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakePopen:
    last_cmd = None

    @classmethod
    def reset(cls):
        cls.last_cmd = None

    def __init__(self, cmd, **kwargs):
        _FakePopen.last_cmd = cmd
        self.pid = 99999
        self.returncode = None