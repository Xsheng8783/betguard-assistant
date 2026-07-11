"""Pure-function verification of filled amounts — no Playwright required."""

from __future__ import annotations


def _verify_filled_amounts(
    expected_amounts: dict[int, int],
    filled: list[dict],
) -> dict:
    """Verify that all expected amounts were filled and read back correctly.

    Args:
        expected_amounts: {star: amount} for stars that must be filled (amount > 0).
        filled: List of per-star results from _fill_amounts_on_b03.

    Returns:
        {"amounts_verified": bool, "missing_amount_stars": [...], "amount_mismatches": [...]}
    """
    if not expected_amounts and not filled:
        return {"amounts_verified": False, "missing_amount_stars": [], "amount_mismatches": []}

    if not expected_amounts:
        return {"amounts_verified": False, "missing_amount_stars": [], "amount_mismatches": []}

    # Build result map: star → first result; detect duplicates as uncertainty
    results: dict[int, dict] = {}
    duplicate_stars: set[int] = set()
    for r in filled:
        star = r.get("star")
        if star is None:
            continue
        if star in results:
            duplicate_stars.add(star)
        else:
            results[star] = r

    missing: list[int] = []
    mismatches: list[dict] = []
    all_ok = True

    for star, expected in expected_amounts.items():
        ar = results.get(star)
        if ar is None:
            missing.append(star)
            all_ok = False
            continue

        executed = bool(ar.get("executed", False))
        verified = bool(ar.get("verified", False))  # safe default: False
        actual = str(ar.get("actual_amount", ""))

        if not executed or not verified:
            all_ok = False
            if star not in missing:
                missing.append(star)
            mismatches.append({
                "star": star,
                "expected": expected,
                "actual": actual,
                "executed": executed,
                "verified": verified,
            })
            continue

        # Compare normalized values
        exp_str = str(expected).strip().lstrip("0") or "0"
        act_str = actual.strip().lstrip("0") or "0"
        if exp_str != act_str:
            all_ok = False
            mismatches.append({
                "star": star,
                "expected": expected,
                "actual": actual,
                "executed": True,
                "verified": False,
                "error": "amount readback mismatch",
            })

    amounts_verified = all_ok and len(missing) == 0 and len(mismatches) == 0 and len(duplicate_stars) == 0

    return {
        "amounts_verified": amounts_verified,
        "missing_amount_stars": missing,
        "amount_mismatches": mismatches,
    }
