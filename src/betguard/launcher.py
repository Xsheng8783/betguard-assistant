"""Windows launcher for Betguard Assistant — packaged app entry point.

Starts the local web server, waits for it, then opens the browser.
Handles re-launch gracefully (no duplicate server).
In frozen mode, sets PLAYWRIGHT_BROWSERS_PATH to the bundled Chromium.
"""
from __future__ import annotations

import http.client
import os
import subprocess
import sys
import time
import webbrowser


PORT = 8765
HOST = "127.0.0.1"
APP_URL = f"http://{HOST}:{PORT}/assist-panel"


def _is_frozen() -> bool:
    """True when running as PyInstaller bundled executable."""
    return getattr(sys, "frozen", False)


def _setup_bundled_chromium() -> None:
    """Set PLAYWRIGHT_BROWSERS_PATH to the bundled Chromium directory."""
    if not _is_frozen():
        return
    # The launcher EXE is in the dist root; playwright-browsers is alongside
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    # PyInstaller one-folder puts data in _internal/ subdirectory
    internal = os.path.join(exe_dir, "_internal", "playwright-browsers")
    browser_dir = os.path.join(exe_dir, "playwright-browsers")
    if os.path.isdir(internal):
        browser_dir = internal
    if os.path.isdir(browser_dir):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_dir
    else:
        print("找不到 bundled Chromium，請重新安裝 Betguard Assistant")
        print(f"預期路徑: {browser_dir}")
        input("按 Enter 關閉...")
        sys.exit(1)


def _is_server_running() -> bool:
    """Check if the Betguard server is already listening on PORT."""
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        conn.close()
        return resp.status is not None
    except Exception:
        return False


def _find_project_root() -> str:
    """Find the project root directory (where the server module lives)."""
    # Packaged: launcher lives next to the app bundle
    launcher_dir = os.path.dirname(os.path.abspath(__file__))
    # Try 'src' sibling directory (development layout)
    src_dir = os.path.join(launcher_dir, "src")
    if os.path.isdir(src_dir):
        return launcher_dir
    # Packaged: launcher is in the flat dist directory
    return launcher_dir


def _start_server(project_root: str) -> subprocess.Popen | None:
    """Start the web UI server as a subprocess. Returns the process handle."""
    env = os.environ.copy()
    # Ensure src is on PYTHONPATH for module discovery
    src_path = os.path.join(project_root, "src")
    if os.path.isdir(src_path):
        env["PYTHONPATH"] = src_path
    else:
        # Packaged: PYTHONPATH is the dist dir
        env["PYTHONPATH"] = project_root

    cmd = [sys.executable, "-X", "utf8", "-m", "betguard.webui.app"]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc
    except Exception as e:
        print(f"無法啟動伺服器: {e}")
        return None


def main() -> None:
    _setup_bundled_chromium()
    if _is_server_running():
        print("Betguard Assistant 已在執行中，開啟瀏覽器...")
        webbrowser.open(APP_URL)
        return

    project_root = _find_project_root()
    print(f"啟動 Betguard Assistant...")
    proc = _start_server(project_root)
    if proc is None:
        print("❌ 無法啟動伺服器，請檢查安裝是否完整")
        input("按 Enter 關閉...")
        sys.exit(1)

    # Wait for server to be ready
    for _ in range(20):
        if _is_server_running():
            webbrowser.open(APP_URL)
            print("Betguard Assistant 已啟動")
            return
        time.sleep(0.5)

    print("⚠️ 伺服器啟動逾時，請手動開啟: " + APP_URL)
    input("按 Enter 關閉...")


if __name__ == "__main__":
    main()
