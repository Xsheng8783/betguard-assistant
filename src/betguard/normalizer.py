from __future__ import annotations

from dataclasses import dataclass
import re


CHINESE_TWO = "\u4e8c"
CHINESE_ALT_TWO = "\u5169"
CHINESE_THREE = "\u4e09"
CHINESE_FOUR = "\u56db"
STAR_WORD = "\u661f"
UNIT_WORD = "\u652f"
FULL_OPEN_PAREN = "\uff08"
FULL_CLOSE_PAREN = "\uff09"
MULTIPLY_SIGN = "\u00d7"


@dataclass(frozen=True)
class NormalizationResult:
    original_text: str
    normalized_text: str
    parse_notes: list[str]


def normalize_input(text: str) -> str:
    """Return the parser-facing text while keeping normalization conservative."""
    return normalize_for_parser(text).normalized_text


def normalize_for_parser(text: str) -> NormalizationResult:
    original = text
    value = text.strip()
    notes: list[str] = []

    updated = value.replace(FULL_OPEN_PAREN, "(").replace(FULL_CLOSE_PAREN, ")")
    if updated != value:
        notes.append("normalized full-width parentheses")
    value = updated

    updated = _unwrap_star_parentheses(value)
    if updated != value:
        notes.append("unwrapped parenthesized star segment")
    value = updated

    updated = _normalize_star_phrases(value)
    if updated != value:
        notes.append("normalized star synonyms")
    value = updated

    updated = _normalize_star_multiplier(value)
    if updated != value:
        notes.append("normalized star multiplier")
    value = updated

    updated = re.sub(r"[ \t]+", " ", value).strip()
    if updated != value:
        notes.append("normalized whitespace")
    value = updated

    return NormalizationResult(
        original_text=original,
        normalized_text=value,
        parse_notes=_dedupe(notes),
    )


def _unwrap_star_parentheses(text: str) -> str:
    pattern = re.compile(r"\((?P<body>[^()]*)\)")

    def replace(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        if _looks_like_star_segment(body):
            return f" {body} "
        return match.group(0)

    return pattern.sub(replace, text)


def _looks_like_star_segment(value: str) -> bool:
    if not value:
        return False
    if "\u8eca" in value:
        return False
    compact = re.sub(r"[\s.,，、]+", "", value)
    compact = compact.replace(STAR_WORD, "")
    compact = (
        compact.replace(CHINESE_TWO, "2")
        .replace(CHINESE_ALT_TWO, "2")
        .replace(CHINESE_THREE, "3")
        .replace(CHINESE_FOUR, "4")
    )
    if re.fullmatch(r"[234]{1,3}(?:=\d+(?:\.\d+)?)?", compact):
        return True
    return False


def _normalize_star_phrases(text: str) -> str:
    value = text
    replacements = [
        (f"{CHINESE_TWO}{CHINESE_THREE}{STAR_WORD}{CHINESE_FOUR}{STAR_WORD}", f"{CHINESE_TWO}{CHINESE_THREE}{CHINESE_FOUR}"),
        (f"{CHINESE_ALT_TWO}{CHINESE_THREE}{STAR_WORD}{CHINESE_FOUR}{STAR_WORD}", f"{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}"),
        (f"{CHINESE_TWO}{CHINESE_THREE}{CHINESE_FOUR}{STAR_WORD}", f"{CHINESE_TWO}{CHINESE_THREE}{CHINESE_FOUR}"),
        (f"{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}{STAR_WORD}", f"{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}"),
        (f"{CHINESE_TWO}{CHINESE_THREE}{STAR_WORD}", f"{CHINESE_TWO}{CHINESE_THREE}"),
        (f"{CHINESE_ALT_TWO}{CHINESE_THREE}{STAR_WORD}", f"{CHINESE_ALT_TWO}{CHINESE_THREE}"),
        (f"{CHINESE_THREE}{CHINESE_FOUR}{STAR_WORD}", f"{CHINESE_THREE}{CHINESE_FOUR}"),
    ]
    for old, new in replacements:
        value = value.replace(old, new)

    value = re.sub(r"(?<!\d)2\s*[.]\s*3\s*[.]\s*4\s*星", "234", value)
    value = re.sub(r"(?<!\d)2\s*[.]\s*3\s*星", "23", value)
    value = re.sub(r"(?<!\d)3\s*[.]\s*4\s*星", "34", value)
    value = re.sub(r"(?<!\d)2\s+3\s+4\s*星", "234", value)
    value = re.sub(r"(?<!\d)2\s+3\s*星", "23", value)
    value = re.sub(r"(?<!\d)3\s+4\s*星", "34", value)
    return value


def _normalize_star_multiplier(text: str) -> str:
    star = _star_segment_source()
    pattern = re.compile(
        rf"(?<!\d)(?P<star>{star})\s*(?P<op>[*xX{MULTIPLY_SIGN}])\s*"
        rf"(?P<amount>\d+(?:\.\d+)?)(?P<unit>{UNIT_WORD})?\s*$"
    )
    return pattern.sub(
        lambda match: f"{match.group('star')}{MULTIPLY_SIGN}{match.group('amount')}{match.group('unit') or ''}",
        text,
    )


def _star_segment_source() -> str:
    return (
        rf"{CHINESE_TWO}{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}{CHINESE_THREE}|"
        rf"{CHINESE_ALT_TWO}{CHINESE_THREE}|"
        rf"{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}|{CHINESE_ALT_TWO}|{CHINESE_THREE}|{CHINESE_FOUR}|"
        rf"234|23|34|[234]"
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
