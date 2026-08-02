"""Closed character-set recognition: normalizer + validator.

The bet slip domain has a tiny alphabet: digits 0-9, Chinese 二/三/四,
the fixed symbol ×, and whitespace. English letters, other Chinese
characters, and general punctuation do NOT exist on these slips.

Rules:
  - X / x / × are the same token and normalize to ×.
  - Fullwidth digits normalize to halfwidth.
  - Anything outside the closed set is NEVER silently corrected or deleted.
    It is flagged needs_human_confirmation with candidates kept for the UI.
  - Numbers must match the existing parser range (01-39).
  - UNKNOWN is a legal result; the model is never forced to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Closed character set ─────────────────────────────────────────────────────

ALLOWED_DIGITS = frozenset("0123456789")
ALLOWED_CHINESE = frozenset("二三四")
ALLOWED_SYMBOLS = frozenset("×")
ALLOWED_TOKEN_CHARS = ALLOWED_DIGITS | ALLOWED_CHINESE | ALLOWED_SYMBOLS
ALLOWED_RAW_TEXT_CHARS = ALLOWED_TOKEN_CHARS | frozenset("? ")  # ? = unknown marker

LOTTERY_NUMBER_MIN = 1
LOTTERY_NUMBER_MAX = 39  # matches parser.py (1-39); never widen/narrow independently

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_MULTIPLIER_MARKS = {"X": "×", "x": "×", "×": "×"}

# Letter→digit candidate suggestions ONLY for human review, never auto-applied
_LETTER_CANDIDATES = {
    "O": "0", "o": "0",
    "B": "8", "b": "8",
    "l": "1", "I": "1", "|": "1",
    "S": "5", "s": "5",
    "Z": "2", "z": "2",
    "T": "7", "t": "7",
}


class ClosedSetIssue(str, Enum):
    DISALLOWED_CHARACTER = "disallowed_character"
    INVALID_NUMBER_RANGE = "invalid_number_range"
    INCOMPLETE_NUMBER = "incomplete_number"
    AMBIGUOUS_SYMBOL = "ambiguous_symbol"
    UNKNOWN_TOKEN = "unknown_token"
    LAYOUT_UNCERTAIN = "layout_uncertain"
    GROUP_STRUCTURE_UNCERTAIN = "group_structure_uncertain"


# ── Normalizer ───────────────────────────────────────────────────────────────


@dataclass
class NormalizedToken:
    raw: str = ""
    normalized: str | None = None
    candidates: list[str] = field(default_factory=list)
    reason: str = ""
    requires_human_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "candidates": self.candidates,
            "reason": self.reason,
            "requires_human_confirmation": self.requires_human_confirmation,
        }


def normalize_symbols(text: str) -> str:
    """X, x, × → × (the fixed multiplier/separator token)."""
    return "".join(_MULTIPLIER_MARKS.get(ch, ch) for ch in text)


def normalize_fullwidth_digits(text: str) -> str:
    return text.translate(_FULLWIDTH_DIGITS)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_token(raw: str) -> NormalizedToken:
    """Normalize a single token; flag anything outside the closed set.

    Dangerous auto-guesses (O7→07, 3B→38) are NEVER applied — they are only
    offered as candidates for human review.
    """
    normalized = normalize_whitespace(normalize_fullwidth_digits(normalize_symbols(raw)))
    if not normalized:
        return NormalizedToken(raw=raw, reason="unknown_token", requires_human_confirmation=True)

    chars = set(normalized)
    disallowed = chars - ALLOWED_RAW_TEXT_CHARS
    if "?" in chars:
        reason = ClosedSetIssue.INCOMPLETE_NUMBER.value if re.search(r"\d", normalized) else ClosedSetIssue.UNKNOWN_TOKEN.value
        return NormalizedToken(raw=raw, reason=reason, requires_human_confirmation=True)
    if not disallowed:
        # Only symbols may still be ambiguous (e.g. stray ×)
        if "×" in chars and normalized not in {"×"} and not re.search(r"\d|二|三|四", normalized):
            return NormalizedToken(
                raw=raw, reason="ambiguous_symbol", requires_human_confirmation=True,
            )
        return NormalizedToken(raw=raw, normalized=normalized)

    # Contains characters outside the closed set → candidates for review only
    candidates: list[str] = []
    if re.fullmatch(r"[0-9A-Za-z?]{1,2}", normalized):
        rebuilt = "".join(_LETTER_CANDIDATES.get(ch, ch) for ch in normalized)
        if re.fullmatch(r"\d{1,2}", rebuilt) and LOTTERY_NUMBER_MIN <= int(rebuilt) <= LOTTERY_NUMBER_MAX:
            candidates.append(rebuilt)
    return NormalizedToken(
        raw=raw,
        candidates=sorted(set(candidates)),
        reason=ClosedSetIssue.DISALLOWED_CHARACTER.value,
        requires_human_confirmation=True,
    )


# ── Validator ────────────────────────────────────────────────────────────────


@dataclass
class TokenValidation:
    token: str = ""
    canonical: str | None = None
    issues: list[str] = field(default_factory=list)
    requires_human_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "canonical": self.canonical,
            "issues": self.issues,
            "requires_human_confirmation": self.requires_human_confirmation,
        }


def _canonical_two_digit(token: str) -> str:
    n = int(token)
    return f"{n:02d}"


def validate_number_token(token: str) -> TokenValidation:
    """Validate one number token against the closed set + parser range 01-39."""
    t = normalize_whitespace(token)
    if not t:
        return TokenValidation(token=token, issues=[ClosedSetIssue.UNKNOWN_TOKEN.value],
                               requires_human_confirmation=True)

    if "?" in t:
        if t == "?":
            return TokenValidation(token=token, issues=[ClosedSetIssue.UNKNOWN_TOKEN.value],
                                   requires_human_confirmation=True)
        return TokenValidation(token=token, issues=[ClosedSetIssue.INCOMPLETE_NUMBER.value],
                               requires_human_confirmation=True)

    if not t.isdigit():
        return TokenValidation(token=token, issues=[ClosedSetIssue.DISALLOWED_CHARACTER.value],
                               requires_human_confirmation=True)

    n = int(t)
    if n < LOTTERY_NUMBER_MIN or n > LOTTERY_NUMBER_MAX:
        return TokenValidation(token=token, issues=[ClosedSetIssue.INVALID_NUMBER_RANGE.value],
                               requires_human_confirmation=True)

    return TokenValidation(token=token, canonical=_canonical_two_digit(t))


def validate_layout(value: Any) -> list[str]:
    if value in {"auto", "normal", "column", "mixed"}:
        return []
    return [ClosedSetIssue.LAYOUT_UNCERTAIN.value]


def validate_label(value: Any) -> list[str]:
    if value is None or value in ALLOWED_CHINESE:
        return []
    return [ClosedSetIssue.DISALLOWED_CHARACTER.value]


def validate_multiplier(text: str | None) -> list[str]:
    """Multiplier must be an integer or null; anything else → human confirmation."""
    if text is None:
        return []
    t = normalize_whitespace(normalize_fullwidth_digits(normalize_symbols(text)))
    if not t:
        return []
    # Forms accepted: ×1, 1, 二三×1 (star text + integer multiplier)
    if re.fullmatch(r"(?:[二三四]{1,3})?×?\d{1,2}", t):
        return []
    if "?" in t:
        return [ClosedSetIssue.UNKNOWN_TOKEN.value]
    if re.fullmatch(r"[二三]?×?\d+\.\d+", t):
        return [ClosedSetIssue.AMBIGUOUS_SYMBOL.value]  # decimal multiplier
    if re.search(r"[A-Za-z]", t):
        return [ClosedSetIssue.DISALLOWED_CHARACTER.value]
    return [ClosedSetIssue.AMBIGUOUS_SYMBOL.value]


def validate_group_structure(number_groups: list[list[str]], multiplier_text: str | None) -> list[str]:
    """A group with numbers but no multiplier is fine; a group with only a
    multiplier and no numbers is structurally uncertain."""
    issues: list[str] = []
    has_numbers = any(bool(g) for g in number_groups)
    if not has_numbers:
        issues.append(ClosedSetIssue.GROUP_STRUCTURE_UNCERTAIN.value)
    return issues


def validate_line(
    *,
    number_groups: list[list[str]],
    multiplier_text: str | None,
    raw_text: str,
    layout_hint: str,
    uncertain: bool,
) -> dict[str, Any]:
    """Full closed-set validation of one model line.

    Returns {issues: [...], needs_human_confirmation: bool, token_validations: [...]}.
    """
    issues: list[str] = []
    token_validations: list[dict[str, Any]] = []

    for group in number_groups:
        for token in group:
            tv = validate_number_token(token)
            token_validations.append(tv.to_dict())
            if tv.requires_human_confirmation:
                issues.extend(tv.issues)

    issues.extend(validate_multiplier(multiplier_text))
    issues.extend(validate_group_structure(number_groups, multiplier_text))
    if raw_text and not re.fullmatch(r"[0-9?二三四× ]+", raw_text):
        issues.append(ClosedSetIssue.DISALLOWED_CHARACTER.value)

    needs_human = bool(issues) or uncertain or bool(layout_hint == "unknown")
    return {
        "issues": sorted(set(issues)),
        "needs_human_confirmation": needs_human,
        "token_validations": token_validations,
    }
