"""Safe re-parse wrapper -- no file writes, no queue state changes.

Used by ``POST /manual-reparse`` to re-validate manually corrected text
without touching runs/ or approved_fill_queue.
"""

from __future__ import annotations

from typing import Any


def reparse_text(text: str, *, game: str = "auto") -> dict[str, Any]:
    """Re-parse a single line of corrected text using the standard pipeline.

    Returns:
        On success: {ok: True, numbers: [...], stars: [...], money: int,
                     amounts: {...}, summary: str}
        On failure: {ok: False, error: str, reason: str}
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text", "reason": "empty"}

    # Resolve game default
    actual_game = game if game and game != "auto" else "539"

    try:
        from betguard.parser import parse_line
        from betguard.validator import validate_bet
    except ImportError as exc:
        return {"ok": False, "error": f"parser not available: {exc}", "reason": "import_error"}

    try:
        parsed = parse_line(text, default_game=actual_game)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"parse error: {exc}",
            "reason": "parse_error",
        }

    validation = validate_bet(parsed)
    if validation.status != "ok":
        return {
            "ok": False,
            "error": _describe_validation_failure(validation),
            "reason": "needs_review",
        }

    # Build per-star amounts from parsed bet
    amounts: dict[str, int] = {}
    money = parsed.money or 0
    stars: list[int] = list(parsed.stars)

    # If parsed has per-star bets, use those amounts
    if parsed.bets:
        for star_key, bet_entry in parsed.bets.items():
            try:
                s = int(star_key)
            except (ValueError, TypeError):
                continue
            amt = getattr(bet_entry, "money", 0) or 0
            if amt:
                amounts[str(s)] = amt
    elif money and stars:
        # Single money applied to all stars
        for s in stars:
            amounts[str(s)] = money

    numbers = [n for n in parsed.numbers if n]

    if not numbers:
        return {
            "ok": False,
            "error": "no numbers parsed",
            "reason": "needs_review",
        }
    if not stars:
        return {
            "ok": False,
            "error": "no stars parsed",
            "reason": "needs_review",
        }

    return {
        "ok": True,
        "numbers": numbers,
        "stars": sorted(set(stars)),
        "money": money,
        "amounts": amounts,
        "summary": _format_summary(parsed),
        "source": "manual_correction",
        "type": getattr(parsed, "type", "normal"),
        "columns": getattr(parsed, "columns", None),
    }


def _describe_validation_failure(validation: Any) -> str:
    """Produce a human-readable reason from a ValidationResult."""
    parts: list[str] = []
    if validation.errors:
        parts.extend(validation.errors)
    if validation.warnings:
        parts.extend(validation.warnings)
    return "; ".join(parts) if parts else "validation failed"


def _format_summary(parsed: Any) -> str:
    """Format a ParsedBet as a compact Chinese summary string."""
    try:
        from betguard.models import to_dict

        d = to_dict(parsed)
        nums = ",".join(str(n) for n in d.get("numbers", []))
        stars_list = d.get("stars", [])
        stars_str = (
            "".join(str(s) for s in stars_list) + "星" if stars_list else "?"
        )
        money = d.get("money", 0)
        return f"{nums}｜{stars_str}｜{money}元"
    except Exception:
        return str(parsed)
