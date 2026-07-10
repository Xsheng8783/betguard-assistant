"""pytest configuration — global fixtures for isolation and safety."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_runs_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect RUNS_DIR and history to tmp_path for every test.

    This prevents tests from writing to the real runs/ directory
    or runs/history/orders.jsonl.
    """
    try:
        from betguard.webui import app as _webui

        monkeypatch.setattr(_webui, "RUNS_DIR", tmp_path / "runs")
    except Exception:
        pass

    try:
        import betguard.webfill.history as _hist

        monkeypatch.setattr(_hist, "HISTORY_DIR", tmp_path / "runs" / "history")
        monkeypatch.setattr(_hist, "HISTORY_FILE", tmp_path / "runs" / "history" / "orders.jsonl")
    except Exception:
        pass
