"""Windows launcher for Betguard Assistant — packaged app entry point.

Starts the local web server, waits for it, then opens the browser.
Handles re-launch gracefully (no duplicate server).
In frozen mode, sets PLAYWRIGHT_BROWSERS_PATH to the bundled Chromium.

All user-facing messages use plain Chinese — no emoji (cp950 safe).
Errors are logged to UTF-8 log file and shown via messagebox.
"""
from __future__ import annotations

import http.client
import os
import subprocess
import sys
import time
import traceback
import webbrowser


PORT = 8765
HOST = "127.0.0.1"
APP_URL = f"http://{HOST}:{PORT}/"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _log_error(msg: str) -> None:
    """Write error to UTF-8 log file in Documents."""
    try:
        log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Betguard Assistant Data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "launcher.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass  # never crash on logging failure


def _safe_print(msg: str) -> None:
    """Print only if stdout supports it; fall back to logging."""
    try:
        print(msg)
    except UnicodeEncodeError:
        _log_error(f"(stdout encoding error) {msg}")


def _show_error(title: str, message: str) -> None:
    """Show error via messagebox (no emoji, cp950 safe)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        _log_error(f"{title}: {message}")


def _setup_bundled_chromium() -> None:
    if not _is_frozen():
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    internal = os.path.join(exe_dir, "_internal", "playwright-browsers")
    browser_dir = os.path.join(exe_dir, "playwright-browsers")
    if os.path.isdir(internal):
        browser_dir = internal
    if not os.path.isdir(browser_dir):
        msg = f"找不到 bundled Chromium\n預期路徑: {browser_dir}\n請重新安裝 Betguard Assistant"
        _show_error("Betguard Assistant", msg)
        _log_error(msg)
        sys.exit(1)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_dir


def _is_server_running() -> bool:
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        conn.close()
        return resp.status is not None
    except Exception:
        return False


def _find_project_root() -> str:
    launcher_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(launcher_dir, "src")
    if os.path.isdir(src_dir):
        return launcher_dir
    return launcher_dir


def _start_server(project_root: str) -> subprocess.Popen | None:
    env = os.environ.copy()
    src_path = os.path.join(project_root, "src")
    if os.path.isdir(src_path):
        env["PYTHONPATH"] = src_path
    else:
        env["PYTHONPATH"] = project_root
    cmd = [sys.executable, "-X", "utf8", "-m", "betguard.webui.app"]
    try:
        return subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        _log_error(f"Server start failed: {e}")
        return None


def main() -> None:
    try:
        _setup_bundled_chromium()

        if _is_server_running():
            _safe_print("Betguard Assistant 已在執行中，開啟瀏覽器...")
            webbrowser.open(APP_URL)
            return

        project_root = _find_project_root()
        _safe_print("啟動 Betguard Assistant...")
        proc = _start_server(project_root)
        if proc is None:
            msg = "無法啟動伺服器，請檢查安裝是否完整"
            _show_error("Betguard Assistant", msg)
            _log_error(msg)
            sys.exit(1)

        for _ in range(20):
            if _is_server_running():
                webbrowser.open(APP_URL)
                _safe_print("Betguard Assistant 已啟動")
                return
            time.sleep(0.5)

        msg = "伺服器啟動逾時，請手動開啟: " + APP_URL
        _show_error("Betguard Assistant", msg)
        _log_error(msg)
        sys.exit(1)

    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _log_error(tb)
        msg = f"Betguard Assistant 發生錯誤\n\n{tb[-500:]}"
        _show_error("Betguard Assistant 錯誤", msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
