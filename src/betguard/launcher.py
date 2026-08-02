"""Windows launcher for Betguard 牌單助手 -- packaged app entry point.

Starts the local web server, waits for it, then opens the browser.
Handles re-launch gracefully (no duplicate server).
In frozen mode, sets PLAYWRIGHT_BROWSERS_PATH to the bundled Chromium.

Frozen mode: main EXE accepts --serve flag to run as server subprocess.
Development mode: uses ``python -m betguard.webui.app`` directly.
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
LICENSE_URL = f"http://{HOST}:{PORT}/license"


def _resolve_start_url() -> str:
    """Pick the URL to open based on license status.

    - inactive / expired / unreadable  -> /license
    - active                          -> /
    Never lets an unlicensed state reach the real fill flow.
    """
    try:
        from betguard.license import license_status
        status = license_status()
    except Exception:
        return LICENSE_URL
    return APP_URL if status.get("status") == "active" else LICENSE_URL


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _log_error(msg: str) -> None:
    """Write error to UTF-8 log file in Documents."""
    try:
        log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Betguard 牌單助手 Data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "launcher.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        _log_error(f"(stdout encoding error) {msg}")


def _show_error(title: str, message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        _log_error(f"{title}: {message}")


def _run_server() -> None:
    """Directly start the web UI server. Used for both --serve mode and dev mode."""
    _setup_bundled_chromium()
    # Remove --serve from argv so argparse in server doesn't reject it
    sys.argv = [a for a in sys.argv if a != "--serve"]
    from betguard.webui.app import main as server_main
    server_main()


def _setup_bundled_chromium() -> None:
    if not _is_frozen():
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    internal = os.path.join(exe_dir, "_internal", "playwright-browsers")
    browser_dir = os.path.join(exe_dir, "playwright-browsers")
    if os.path.isdir(internal):
        browser_dir = internal
    if not os.path.isdir(browser_dir):
        msg = f"找不到 bundled Chromium\n預期路徑: {browser_dir}\n請重新安裝 Betguard 牌單助手"
        _show_error("Betguard 牌單助手", msg)
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


def _start_server() -> subprocess.Popen | None:
    """Start the server subprocess. In frozen mode uses --serve, in dev mode uses python -m."""
    if _is_frozen():
        cmd = [sys.executable, "--serve"]
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    else:
        cmd = [sys.executable, "-X", "utf8", "-m", "betguard.webui.app"]
        creationflags = 0
    log_file = _open_server_log()
    try:
        return subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception as e:
        _log_error(f"Server start failed: {e}")
        return None


def _open_server_log():
    """Open server log for writing. In frozen mode, use UTF-8 log file."""
    try:
        log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Betguard 牌單助手 Data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        return open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8")
    except Exception:
        return subprocess.DEVNULL


def main() -> None:
    # --serve mode: the EXE is being invoked as a server subprocess
    if "--serve" in sys.argv:
        _run_server()
        return

    try:
        _setup_bundled_chromium()

        if _is_server_running():
            _safe_print("Betguard 牌單助手 已在執行中，開啟瀏覽器...")
            try:
                webbrowser.open(_resolve_start_url())
            except Exception:
                _log_error("webbrowser.open failed (server already running)")
            return

        _safe_print("啟動 Betguard 牌單助手...")
        proc = _start_server()
        if proc is None:
            msg = "無法啟動伺服器，請檢查安裝是否完整"
            _show_error("Betguard 牌單助手", msg)
            _log_error(msg)
            return

        for _ in range(20):
            if _is_server_running():
                try:
                    webbrowser.open(_resolve_start_url())
                except Exception:
                    _log_error("webbrowser.open failed")
                _safe_print("Betguard 牌單助手 已啟動")
                return
            time.sleep(0.5)

        msg = f"伺服器啟動逾時，請手動開啟: {APP_URL}"
        _show_error("Betguard 牌單助手", msg)
        _log_error(msg)

    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _log_error(tb)
        _show_error("Betguard 牌單助手 錯誤", tb[-500:])
        sys.exit(1)


if __name__ == "__main__":
    main()