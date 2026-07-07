"""Unit tests for web_assisted_fill_executor — import + safety invariants only.

Browser-dependent tests are in test_real_site_assisted_fill.py.
"""

from __future__ import annotations

import pytest


class TestWebAssistedFillExecutorImport:
    """Verify the executor module imports cleanly."""

    def test_executor_module_imports(self) -> None:
        from betguard.webfill import web_assisted_fill_executor  # noqa: F401


class TestWebAssistedFillExecutorSafety:
    """Verify safety invariants: blocked paths, no browser needed."""

    def test_empty_numbers_is_blocked(self) -> None:
        from betguard.webfill.web_assisted_fill_executor import (
            execute_web_assisted_fill_one_item,
        )

        result = execute_web_assisted_fill_one_item(
            numbers=[], stars=[2, 3, 4], amounts={"2": 50}
        )
        assert result["ok"] is False
        assert "numbers" in result.get("error", "").lower()

    def test_empty_stars_is_blocked(self) -> None:
        from betguard.webfill.web_assisted_fill_executor import (
            execute_web_assisted_fill_one_item,
        )

        result = execute_web_assisted_fill_one_item(
            numbers=[17, 20], stars=[], amounts={}
        )
        assert result["ok"] is False
        assert "stars" in result.get("error", "").lower()

    def test_empty_amounts_is_blocked(self) -> None:
        from betguard.webfill.web_assisted_fill_executor import (
            execute_web_assisted_fill_one_item,
        )

        result = execute_web_assisted_fill_one_item(
            numbers=[17, 20], stars=[2, 3], amounts={}
        )
        assert result["ok"] is False
        assert "amounts" in result.get("error", "").lower()


class TestStarMapping:
    """Verify star-to-position mapping."""

    def test_star_position_covers_234(self) -> None:
        from betguard.webfill.web_assisted_fill_executor import STAR_TO_POSITION

        assert STAR_TO_POSITION == {2: 0, 3: 1, 4: 2}

    def test_star_names(self) -> None:
        from betguard.webfill.web_assisted_fill_executor import STAR_NAMES

        assert STAR_NAMES[2] == "二星"
        assert STAR_NAMES[3] == "三星"
        assert STAR_NAMES[4] == "四星"
