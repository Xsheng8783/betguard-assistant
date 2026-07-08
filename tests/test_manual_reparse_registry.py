"""Unit tests for manual candidate registry (no HTTP, no IO)."""

import pytest

from betguard.webui.app import (
    _lookup_manual_candidate,
    _register_manual_candidate,
)


class TestManualCandidateRegistry:
    def test_register_returns_id_with_manual_prefix(self) -> None:
        cid = _register_manual_candidate({
            "numbers": [6, 13, 23, 22],
            "stars": [2, 3, 4],
            "amounts": {"2": 100, "3": 100, "4": 100},
            "summary": "test",
            "game": "539",
        })
        assert cid.startswith("manual-")
        assert len(cid) > len("manual-")

    def test_lookup_returns_registered_candidate(self) -> None:
        cid = _register_manual_candidate({
            "numbers": [1, 3, 5],
            "stars": [2],
            "amounts": {"2": 50},
            "summary": "test2",
            "game": "539",
        })
        candidate = _lookup_manual_candidate(cid)
        assert candidate is not None
        assert candidate["numbers"] == [1, 3, 5]
        assert candidate["stars"] == [2]
        assert candidate["amounts"] == {"2": 50}

    def test_lookup_unknown_id_returns_none(self) -> None:
        assert _lookup_manual_candidate("nonexistent-id") is None
        assert _lookup_manual_candidate("manual-00000000") is None

    def test_multiple_registrations_have_unique_ids(self) -> None:
        cid1 = _register_manual_candidate({"numbers": [1]})
        cid2 = _register_manual_candidate({"numbers": [2]})
        assert cid1 != cid2
        assert _lookup_manual_candidate(cid1)["numbers"] == [1]
        assert _lookup_manual_candidate(cid2)["numbers"] == [2]


class TestManualReparseFunction:
    def test_valid_text_returns_ok_true(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("06.13.23.22 234.100")
        assert result["ok"] is True
        assert result["numbers"] == [6, 13, 23, 22]
        assert result["money"] == 100

    def test_invalid_text_returns_ok_false(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("99.98.97 234.100")
        assert result["ok"] is False
        assert result["reason"] == "needs_review"

    def test_empty_text_returns_ok_false(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("")
        assert result["ok"] is False

    def test_parse_error_text_returns_ok_false(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("just plain text no numbers")
        assert result["ok"] is False
        assert "error" in result

    def test_manual_correction_source_tag(self) -> None:
        from betguard.webfill.manual_reparse import reparse_text

        result = reparse_text("06.13.23.22 234.100")
        assert result["source"] == "manual_correction"
