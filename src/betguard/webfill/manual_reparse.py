"""Safe re-parse wrapper -- no file writes, no queue state changes.

Used by ``POST /manual-reparse`` to re-validate manually corrected text
without touching runs/ or approved_fill_queue.
"""

from __future__ import annotations

from typing import Any


def _normalize_x_chain(original_text: str, bet_type: str, columns):
    """Existing review-console hook: preserve the parser's structure verbatim.

    Previously this hook flattened single-number columns after successful
    parsing. Initial parse, review and editing must now share one contract.
    """
    return bet_type, columns

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
    if actual_game not in {"539", "六合"}:
        return {"ok": False, "error": "請選擇 539 或六合。", "reason": "invalid_game"}
    if "\n" in text or "各" in text:
        from betguard.webfill.batch_mock_queue import build_batch_mock_queue
        from betguard.webfill.text_preview import candidate_preview
        queue = build_batch_mock_queue(text, game=actual_game)
        preprocessing = queue["preprocessing"]
        if preprocessing["invalid_fragments"] or not preprocessing["valid_candidates"]:
            return {"ok": False, "error": "仍有無法完整解析的文字，請保留原文修正。", "reason": "needs_review"}
        candidates = [
            {**candidate_preview(vc["result"], game=actual_game), "raw": vc["raw"],
             "original_lines": vc.get("original_lines", []), "ok": True}
            for vc in preprocessing["valid_candidates"]
        ]
        return {**candidates[0], "candidates": candidates}

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
    if parsed.game != actual_game:
        return {"ok": False, "error": "牌文彩種與工作彩種不一致，請明確選擇後重新解析。", "reason": "game_conflict"}
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
    if parsed.type == "car" and parsed.number is not None:
        numbers = [parsed.number]

    if not numbers:
        return {
            "ok": False,
            "error": "no numbers parsed",
            "reason": "needs_review",
        }
    if not stars and getattr(parsed, "type", "") != "car":
        return {
            "ok": False,
            "error": "no stars parsed",
            "reason": "needs_review",
        }

    bet_type = getattr(parsed, "type", "normal")
    columns = getattr(parsed, "columns", None)
    # The same parser owns structure for initial parse AND edited text.
    # Never reinterpret its single-number columns as a flat normal bet.

    from betguard.models import BetReport
    from betguard.webfill.text_preview import candidate_preview
    preview = candidate_preview(BetReport(parsed, validation).to_dict(), game=actual_game)
    preview["bet_type"] = bet_type
    return {
        **preview,
        "ok": True,
        "numbers": numbers,
        "stars": sorted(set(stars)),
        "money": money,
        "amounts": amounts,
        "summary": preview["summary"],
        "source": "manual_correction",
        "type": bet_type,
        "columns": columns,
        "game": actual_game,
        "fill_supported": bet_type in {"normal", "column"} and bool(amounts),
    }


def _describe_validation_failure(validation: Any) -> str:
    """Produce a human-readable reason from a ValidationResult."""
    parts: list[str] = []
    if validation.errors:
        parts.extend(validation.errors)
    if validation.warnings:
        parts.extend(validation.warnings)
    return "; ".join(parts) if parts else "validation failed"
