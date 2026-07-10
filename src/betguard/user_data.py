"""User data directory resolution for packaged and development installs.

Development: uses the project-relative ``runs/`` directory.
Packaged: uses ``%USERPROFILE%\\Documents\\Betguard Assistant Data\\``.
"""
from __future__ import annotations

import os
import sys


def _is_packaged() -> bool:
    """Detect PyInstaller one-folder bundle."""
    return getattr(sys, "frozen", False)


def get_data_dir() -> str:
    """Return the root data directory for user data (runs, logs, etc.)."""
    if _is_packaged():
        base = os.path.join(os.path.expanduser("~"), "Documents", "Betguard Assistant Data")
    else:
        # Development: use the traditional project-relative runs/
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
        base = os.path.abspath(base)
    os.makedirs(base, exist_ok=True)
    return base


def get_runs_dir() -> str:
    d = os.path.join(get_data_dir(), "runs")
    os.makedirs(d, exist_ok=True)
    return d
