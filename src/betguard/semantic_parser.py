"""M2 deterministic semantic parser for Betguard 539 / Mark Six OCR results.

Consumes the canonical GT / human-review text formats:

- 連碰 (normal):   ``05 09 23 26 二三四X1``
- 柱碰 (column):   ``05/08 09 23/10 20 29 二三X1.5`` (``/`` or ``×`` between
  columns; space / dot inside a column)
- 尾數:            ``8尾 23X1``, ``01×16×5尾 23X1``
- 複合倍率:        ``三×0.5四×3`` (with or without spaces)
- 共用倍率:        ``各二三×0.5`` (declaration, scope = whole region)
- 車玩法:          ``全車``, ``各N車`` -> recognized and hard-blocked
  (never executable)

Design notes:
- ``raw_text`` is always preserved as written; number/amount parsing happens
  in a deterministic layer, never in the vision model.
- Amount math uses ``Decimal`` (never float) via :func:`multiplier_to_amount`.
- The character ``=`` is NOT part of the real slip grammar; when present the
  result carries ``LEGACY_OR_HALLUCINATED_EQUALS`` and is flagged uncertain.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .models import ParsedBet

UNIT_AMOUNT = Decimal("100")  # 1 柱 = 100 元

CATEGORY_CN: dict[str, list[int]] = {
    "二三": [2, 3],
    "二三四": [2, 3, 4],
    "二": [2],
    "三四": [3, 4],
    "三": [3],
    "四": [4],
    "¾": [3, 4],  # stacked 3/4 written as a fraction glyph
    "⅔": [2, 3],  # stacked 2/3 written as a fraction glyph
}
CATEGORY_NUM: dict[str, list[int]] = {
    "23": [2, 3],
    "2.3": [2, 3],
    "234": [2, 3, 4],
    "2.3.4": [2, 3, 4],
    "23.4": [2, 3, 4],
}
CATEGORY_MAP: dict[str, list[int]] = {**CATEGORY_CN, **CATEGORY_NUM}
CATEGORY_TOKEN = r"(?:二三|二三四|三四|三|四|¾|⅔|23|2\.3|234|2\.3\.4|23\.4|二)"

TAIL_RE = re.compile(r"(?P<d>[0-9])\s*尾")
MULT_TAIL_RE = re.compile(
    rf"^(?P<body>.*?)(?P<mult>(?:{CATEGORY_TOKEN}\s*[xX×]\s*\d+(?:\.\d+)?\s*)+)$"
)
# Multiplier may appear ANYWHERE in the model text (e.g. "17 234×0.5 29 40").
# Rule: category×value is ALWAYS a multiplier and must be moved to the end.
# NOTE: "34" and single digits are intentionally excluded here (ambiguous
# with column numbers); they are handled by the standalone tail checks.
MULT_ANYWHERE_RE = re.compile(
    rf"(?:二三|二三四|三四|三|四|¾|⅔|23|2\.3|234|2\.3\.4|23\.4|二)\s*[xX×]\s*\d+(?:\.\d+)?"
)
COMPOUND_PART_RE = re.compile(
    rf"(?P<cat>{CATEGORY_TOKEN})\s*[xX×]\s*(?P<val>\d+(?:\.\d+)?)"
)
COLUMN_NUM_RE = re.compile(r"(?P<num>\d{1,2})尾|\d{1,2}")


def _game_key(game: str) -> str:
    g = (game or "539").strip().lower()
    if g in {"539"}:
        return "539"
    if g in {"mark_six", "六合彩", "hk", "hk6"}:
        return "mark_six"
    return "539"


def _max_number(game: str) -> int:
    return 49 if _game_key(game) == "mark_six" else 39


def expand_tail(digit: int, *, game: str = "539") -> list[int]:
    """Expand ``N尾`` shorthand.

    539:  N, N+10, N+20, N+30   (0尾 -> 10/20/30)
    六合彩: additionally N+40   (8尾 -> 08/18/28/38/48)
    """
    key = _game_key(game)
    if digit == 0:
        values = [10, 20, 30]
    else:
        values = [digit + 10 * k for k in range(4)]
    if key == "mark_six":
        values = list(dict.fromkeys(values + [digit + 40]))
    return [v for v in values if 1 <= v <= _max_number(game)]


def parse_categories(token: str) -> list[int] | None:
    t = token.strip()
    return CATEGORY_MAP.get(t)


def parse_multiplier_parts(mult_text: str) -> list[tuple[list[int], Decimal]]:
    parts: list[tuple[list[int], Decimal]] = []
    for m in COMPOUND_PART_RE.finditer(mult_text):
        cats = CATEGORY_MAP.get(m.group("cat"))
        if cats is None:
            continue
        try:
            value = Decimal(m.group("val"))
        except Exception:
            continue
        parts.append((cats, value))
    return parts


def multiplier_to_amount(value_text: str, unit_amount: Decimal = UNIT_AMOUNT) -> Decimal:
    """1 柱 = 100 元; e.g. 0.5 -> 50, 15 -> 1500. Decimal only."""
    return (Decimal(value_text) * unit_amount).quantize(Decimal("0.01"))


def _peel_multiplier(text: str) -> tuple[str, list[tuple[list[int], Decimal]]]:
    # 1) Multipliers found anywhere in the text are peeled and moved to the
    #    end (the category×value pattern is unambiguous).
    anywhere = list(MULT_ANYWHERE_RE.finditer(text))
    if anywhere:
        body = MULT_ANYWHERE_RE.sub(" ", text).strip()
        return body, parse_multiplier_parts(" ".join(m.group(0) for m in anywhere))
    m = MULT_TAIL_RE.match(text)
    if m:
        return m.group("body").strip(), parse_multiplier_parts(m.group("mult"))
    # Stacked category digits: "3/4×1" = 三四×1 (三 written above 四 on the
    # slip; the model may transcribe the stack as digit/digit).
    stacked = re.search(
        r"^(?P<body>.*?)(?:\s|^)(?P<a>[234])\s*/\s*(?P<b>[234])\s*[xX×]\s*(?P<val>[0-9](?:\.[0-9]+)?)\s*$",
        text,
    )
    if stacked:
        cats = sorted({int(stacked.group("a")), int(stacked.group("b"))})
        return stacked.group("body").strip(), [(cats, Decimal(stacked.group("val")))]
    # Standalone digit categories (2/3/4, 34) before ×: must be a separate
    # token (preceded by whitespace / start) AND the value must be a single
    # digit or decimal. A 2-digit value means both sides are lottery numbers,
    # so "34×40" stays a column while "… 3 ×1" / "… 34×1" are multipliers.
    digit_cat = re.search(
        r"^(?P<body>.*?)(?:\s|^)(?P<cat>34|[234])\s*[xX×]\s*(?P<val>[0-9](?:\.[0-9]+)?)\s*$",
        text,
    )
    if digit_cat:
        cats = {"34": [3, 4], "2": [2], "3": [3], "4": [4]}[digit_cat.group("cat")]
        return digit_cat.group("body").strip(), [(cats, Decimal(digit_cat.group("val")))]
    # Bare trailing ×value without a category token is a multiplier only when
    # the value is decimal, a single digit, or the × is a separate token
    # (preceded by whitespace). A × directly between two 2-digit numbers is a
    # column separator, NOT a multiplier (e.g. 05×08×10, 23×29).
    bare = re.search(r"^(?P<body>.*?)\s*[xX×]\s*(?P<val>\d+(?:\.\d+)?)\s*$", text)
    if bare:
        val = bare.group("val")
        body = bare.group("body")
        x_index = max(text.rfind("×"), text.rfind("x"), text.rfind("X"))
        separate_token = x_index > 0 and text[x_index - 1].isspace()
        if "." in val or len(val) == 1 or separate_token:
            return body.strip(), [([], Decimal(val))]
    return text, []


def _numbers_from_column_part(part: str, *, game: str) -> list[int]:
    nums: list[int] = []
    for tok in re.findall(r"\d{1,2}尾|\d{1,2}", part):
        tail = TAIL_RE.fullmatch(tok)
        if tail:
            nums.extend(expand_tail(int(tail.group("d")), game=game))
        else:
            nums.append(int(tok))
    return nums


def _split_columns(body: str, *, game: str) -> list[list[int]] | None:
    if "/" in body:
        raw_parts = [p for p in body.split("/")]
    else:
        raw_parts = re.split(r"\s*[xX×]\s*", body)
    raw_parts = [p for p in (x.strip() for x in raw_parts) if p]
    if len(raw_parts) < 2:
        return None
    columns = [_numbers_from_column_part(p, game=game) for p in raw_parts]
    if any(not col for col in columns):
        return None
    return columns


def _numbers_from_body(body: str, *, game: str) -> list[int]:
    nums: list[int] = []
    for tok in re.findall(r"\d{1,2}尾|\d{1,2}", body):
        tail = TAIL_RE.fullmatch(tok)
        if tail:
            nums.extend(expand_tail(int(tail.group("d")), game=game))
        else:
            nums.append(int(tok))
    return nums


def _parse_car_bet(text: str, *, game: str) -> ParsedBet | None:
    t = text.strip()
    if "車" not in t:
        return None
    car_match = re.search(r"(?:各)?\s*([\d.]+)\s*車", t)
    car_units = Decimal(car_match.group(1)) if car_match else Decimal("1")
    has_full = "全車" in t
    body = re.sub(r"各?\s*[\d.]+\s*車|全車", " ", t)
    body = re.sub(r"各|車", " ", body)
    numbers = _numbers_from_body(body, game=game)
    return ParsedBet(
        game=_game_key(game),
        type="car",
        numbers=numbers,
        stars=[],
        unit=None,
        money=int(multiplier_to_amount(str(car_units))) if car_units is not None else None,
        number=numbers[0] if len(numbers) == 1 else None,
        car_units=car_units,
        original_text=t,
        normalized_text=t,
        parse_notes=[
            "car_bet" if not has_full else "full_car_bet",
            "block_reason=UNSUPPORTED_CAR_BET",
            "unsupported_play_semantics",
        ],
        executable=False,
        supported=False,
        block_reason="UNSUPPORTED_CAR_BET",
    )


def parse_shared_multiplier(
    raw_text: str,
    *,
    game: str = "539",
    applies_to_line_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Parse a shared-multiplier declaration line like ``各二三×0.5``."""
    raw = (raw_text or "").strip()
    warnings: list[str] = []
    uncertain = False
    reason: str | None = None
    play_type: str | None = None
    play_text: str | None = None
    supported = True
    executable = False
    block_reason: str | None = None

    if "=" in raw:
        warnings.append("LEGACY_OR_HALLUCINATED_EQUALS")
        uncertain = True
        reason = reason or "unknown"

    if "車" in raw:
        play_type = "car_bet"
        play_text = raw
        supported = False
        executable = False
        block_reason = "UNSUPPORTED_CAR_BET"
        uncertain = True
        reason = "unsupported_play_semantics"
        scope = "all_groups_in_region" if "各" in raw else "current_group"
        return {
            "raw_text": raw,
            "categories": [],
            "value_text": None,
            "scope": scope,
            "applies_to_line_ids": list(applies_to_line_ids or []),
            "boundary_uncertain": False,
            "uncertain": uncertain,
            "uncertain_reason": reason,
            "warnings": warnings,
            "play_type": play_type,
            "play_text": play_text,
            "supported": supported,
            "executable": executable,
            "block_reason": block_reason,
        }

    body, parts = _peel_multiplier(raw)
    scope = "all_groups_in_region" if "各" in raw else "current_group"
    if not parts:
        uncertain = True
        reason = reason or "unclear_multiplier"
        warnings.append("multiplier_unparsed")
        return {
            "raw_text": raw,
            "categories": [],
            "value_text": None,
            "scope": scope,
            "applies_to_line_ids": list(applies_to_line_ids or []),
            "boundary_uncertain": False,
            "uncertain": uncertain,
            "uncertain_reason": reason,
            "warnings": warnings,
            "play_type": None,
            "play_text": None,
            "supported": supported,
            "executable": executable,
            "block_reason": block_reason,
        }
    cats = sorted({c for pair in parts for c in pair[0]})
    value_text = str(parts[0][1]) if len(parts) == 1 else "compound"
    return {
        "raw_text": raw,
        "categories": cats,
        "value_text": value_text,
        "scope": scope,
        "applies_to_line_ids": list(applies_to_line_ids or []),
        "boundary_uncertain": False,
        "uncertain": uncertain,
        "uncertain_reason": reason,
        "warnings": warnings,
        "play_type": None,
        "play_text": None,
        "supported": supported,
        "executable": executable,
        "block_reason": block_reason,
    }


def parse_ocr_text(text: str, *, game: str = "539") -> ParsedBet:
    """Parse a canonical bet line from the OCR/GT pipeline."""
    original = (text or "").strip()
    notes: list[str] = []
    errors: list[str] = []
    has_equals = "=" in original
    if "=" in original:
        notes.append("LEGACY_OR_HALLUCINATED_EQUALS")

    car = _parse_car_bet(original, game=game)
    if car is not None:
        if has_equals:
            car.parse_notes.append("LEGACY_OR_HALLUCINATED_EQUALS")
            car.parse_errors.append("LEGACY_OR_HALLUCINATED_EQUALS")
        return car

    body, parts = _peel_multiplier(original.split("=", 1)[0].strip() if has_equals else original)

    # declaration-only (shared multiplier) line (body may hold only 各)
    clean_body = re.sub(r"各", "", body).strip() if body else ""
    if not clean_body and parts:
        cats = sorted({c for pair in parts for c in pair[0]})
        value = parts[0][1]
        return ParsedBet(
            game=_game_key(game),
            type="shared_multiplier",
            numbers=[],
            stars=cats,
            unit=_to_json(value),
            money=int(multiplier_to_amount(str(value))),
            original_text=original,
            normalized_text=original,
            parse_notes=notes + (["scope=all_groups_in_region"] if "各" in original else ["scope=current_group"]),
            executable=False,
            supported=True,
            parse_errors=["LEGACY_OR_HALLUCINATED_EQUALS"] if has_equals else [],
        )

    columns = _split_columns(body, game=game) if body else None
    if columns is not None and len(columns) >= 2:
        flat = [n for col in columns for n in col]
        stars = sorted({c for pair in parts for c in pair[0]})
        value = parts[0][1] if parts else None
        return ParsedBet(
            game=_game_key(game),
            type="column",
            numbers=flat,
            stars=stars,
            unit=_to_json(value) if value is not None else None,
            money=int(multiplier_to_amount(str(value))) if value is not None else None,
            columns=columns,
            original_text=original,
            normalized_text=original,
            parse_notes=notes + (["compound_multiplier"] if len(parts) > 1 else []),
            executable=not has_equals and bool(parts) and _all_numbers_valid(flat, game=game),
            supported=True,
            parse_errors=["LEGACY_OR_HALLUCINATED_EQUALS"] if has_equals else [],
        )

    numbers = _numbers_from_body(body, game=game) if body else []
    if not numbers:
        errors.append("missing numbers")
    stars = sorted({c for pair in parts for c in pair[0]})
    value = parts[0][1] if parts else None
    max_n = _max_number(game)
    for n in numbers:
        if not (1 <= n <= max_n):
            errors.append(f"number out of range 01-{max_n}: {n}")
    from .parser import ParseError

    if errors:
        raise ParseError("; ".join(errors))
    return ParsedBet(
        game=_game_key(game),
        type="normal",
        numbers=numbers,
        stars=stars,
        unit=_to_json(value) if value is not None else None,
        money=int(multiplier_to_amount(str(value))) if value is not None else None,
        original_text=original,
        normalized_text=original,
        parse_notes=notes + (["compound_multiplier"] if len(parts) > 1 else []),
        executable=not has_equals and bool(parts) and _all_numbers_valid(numbers, game=game),
        supported=True,
        parse_errors=["LEGACY_OR_HALLUCINATED_EQUALS"] if has_equals else [],
    )


def _all_numbers_valid(numbers: list[int], *, game: str) -> bool:
    max_n = _max_number(game)
    return all(1 <= n <= max_n for n in numbers)


def _to_json(value: Decimal) -> Any:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return int(normalized)
    return float(normalized)
