"""Pure-function tests for _verify_filled_amounts — no Playwright required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from betguard.webfill.web_assisted_fill_executor import _verify_filled_amounts


EXPECTED_23 = {2: 100, 3: 100}
EXPECTED_234 = {2: 50, 3: 50, 4: 50}


class TestVerifyFilledAmounts:
    """Verify the pure _verify_filled_amounts function against edge cases."""

    def test_rejects_empty_filled_list(self) -> None:
        r = _verify_filled_amounts(EXPECTED_23, [])
        assert r["amounts_verified"] is False
        assert set(r["missing_amount_stars"]) == {2, 3}

    def test_rejects_empty_expected(self) -> None:
        r = _verify_filled_amounts({}, [{"star": 2, "executed": True, "verified": True, "actual_amount": "100"}])
        assert r["amounts_verified"] is False

    def test_rejects_missing_verified_field(self) -> None:
        # executed=True but no "verified" key — must fail because default is False
        r = _verify_filled_amounts(
            EXPECTED_23,
            [
                {"star": 2, "expected_amount": 100, "actual_amount": "100", "executed": True},
                {"star": 3, "expected_amount": 100, "actual_amount": "100", "executed": True},
            ],
        )
        assert r["amounts_verified"] is False

    def test_rejects_partial_star_results(self) -> None:
        r = _verify_filled_amounts(
            EXPECTED_23,
            [{"star": 2, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True}],
        )
        assert r["amounts_verified"] is False
        assert 3 in r["missing_amount_stars"]

    def test_reports_missing_star_three(self) -> None:
        r = _verify_filled_amounts(
            EXPECTED_234,
            [
                {"star": 2, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
                {"star": 4, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
            ],
        )
        assert r["amounts_verified"] is False
        assert r["missing_amount_stars"] == [3]

    def test_rejects_blank_readback(self) -> None:
        filled = [
            {"star": 2, "expected_amount": 100, "actual_amount": "", "executed": True, "verified": True},
            {"star": 3, "expected_amount": 100, "actual_amount": "", "executed": True, "verified": True},
        ]
        r = _verify_filled_amounts(EXPECTED_23, filled)
        assert r["amounts_verified"] is False

    def test_rejects_wrong_readback(self) -> None:
        filled = [
            {"star": 2, "expected_amount": 100, "actual_amount": "1000", "executed": True, "verified": True},
            {"star": 3, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
        ]
        r = _verify_filled_amounts(EXPECTED_23, filled)
        assert r["amounts_verified"] is False

    def test_accepts_all_expected_stars(self) -> None:
        filled = [
            {"star": 2, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
            {"star": 3, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
        ]
        r = _verify_filled_amounts(EXPECTED_23, filled)
        assert r["amounts_verified"] is True
        assert r["missing_amount_stars"] == []
        assert r["amount_mismatches"] == []

    def test_rejects_duplicate_star_results(self) -> None:
        filled = [
            {"star": 2, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
            {"star": 2, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
            {"star": 3, "expected_amount": 100, "actual_amount": "100", "executed": True, "verified": True},
        ]
        r = _verify_filled_amounts(EXPECTED_23, filled)
        assert r["amounts_verified"] is False

    def test_accepts_three_star_full_success(self) -> None:
        filled = [
            {"star": 2, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
            {"star": 3, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
            {"star": 4, "expected_amount": 50, "actual_amount": "50", "executed": True, "verified": True},
        ]
        r = _verify_filled_amounts(EXPECTED_234, filled)
        assert r["amounts_verified"] is True
