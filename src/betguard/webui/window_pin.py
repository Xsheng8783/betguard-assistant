"""Cross-platform window pinning utility.

On Windows, uses ctypes + user32.SetWindowPos to toggle always-on-top.
On non-Windows, returns unsupported."""

from __future__ import annotations

import sys

_WINDOW_TITLE = "Betguard \u724c\u55ae\u52a9\u624b"  # "Betguard 牌單助手"


def _find_window_by_title(title: str) -> int | None:
    """Find a top-level window by exact title. Returns HWND or None."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HWND = wintypes.HWND
    LPCWSTR = ctypes.c_wchar_p

    user32.FindWindowW.argtypes = [LPCWSTR, LPCWSTR]
    user32.FindWindowW.restype = HWND

    hwnd = user32.FindWindowW(None, title)
    return hwnd if hwnd else None


def set_always_on_top(enable: bool) -> dict:
    """Toggle always-on-top for the Betguard assist panel window.

    Returns {"ok": True, "pinned": bool} or {"ok": False, "error": str}.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "僅支援 Windows", "pinned": False}

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HWND = wintypes.HWND
    UINT = wintypes.UINT
    BOOL = wintypes.BOOL
    INT = wintypes.INT

    user32.SetWindowPos.argtypes = [HWND, HWND, INT, INT, INT, INT, UINT]
    user32.SetWindowPos.restype = BOOL

    hwnd = _find_window_by_title(_WINDOW_TITLE)
    if not hwnd:
        # Try partial match (some browsers append to title)
        user32.EnumWindows = ctypes.windll.user32.EnumWindows
        user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM), wintypes.LPARAM]
        user32.EnumWindows.restype = BOOL

        found = []
        def _enum_callback(hwnd_ex, _lparam):
            length = user32.GetWindowTextLengthW(hwnd_ex)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd_ex, buf, length + 1)
                if _WINDOW_TITLE in buf.value:
                    found.append(hwnd_ex)
            return True

        callback = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)(_enum_callback)
        user32.EnumWindows(callback, 0)
        if found:
            hwnd = found[0]
        else:
            return {"ok": False, "error": f"找不到「{_WINDOW_TITLE}」視窗，請確認面板已開啟", "pinned": False}

    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    flags = SWP_NOMOVE | SWP_NOSIZE

    if enable:
        result = user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    else:
        result = user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)

    return {"ok": bool(result), "pinned": enable}