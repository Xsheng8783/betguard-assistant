"""Closed character-set recognition V2: normalizer + validator.

The bet slip domain has a small alphabet: digits 0-9, Chinese 二/三/四/各,
the multiplier symbol ×, the number-set separators . ( ), the shared
multiplier marker =, and whitespace. English letters and other characters
do NOT exist on these slips.

V2 additions (confirmed real-slip semantics):
  - Decimal multipliers (×0.2, ×0.3, ×0.5) are legal.
  - Parenthesized number sets (12.18.20.23) use '.' as number separator.
  - 各=三×0.3 is a shared multiplier applying to every group in the current
    region (scope=all_groups_in_region) — never silently applied to the
    whole image; unresolved region → scope=unresolved_region.

Rules:
  - X / x / × are the same token and normalize to ×.
  - Fullwidth digits/parens/equals/dots normalize to halfwidth.
  - * or ＊ are NEVER auto-converted to ×.
  - Anything outside the closed set is NEVER silently corrected or deleted.
    It is flagged needs_human_confirmation with candidates kept for the UI.
  - Numbers must match the existing parser range (01-39).
  - Multiplier canonical value is stored as string (value_text), never as
    the primary binary float.
  - UNKNOWN is a legal result; the model is never forced to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Closed character set (V2) ────────────────────────────────────────────────

ALLOWED_DIGITS = frozenset("0123456789")
ALLOWED_CHINESE = frozenset("二三四各")
ALLOWED_SYMBOLS = frozenset("×.=()")
ALLOWED_TOKEN_CHARS = ALLOWED_DIGITS | ALLOWED_CHINESE | ALLOWED_SYMBOLS
ALLOWED_RAW_TEXT_CHARS = ALLOWED_TOKEN_CHARS | frozenset("? ")  # ? = unknown marker

LOTTERY_NUMBER_MIN = 1
LOTTERY_NUMBER_MAX = 39  # matches parser.py (1-39); never widen/narrow independently

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_FULLWIDTH_PARENS = str.maketrans("（）", "()")
_FULLWIDTH_EQUALS = str.maketrans("＝", "=")
_FULLWIDTH_DOTS = str.maketrans("．。", "..")
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

# Multiplier: optional 二/三/四 category + × + integer or decimal value.
# value_text keeps the canonical STRING (e.g. "0.5"), never binary float.
MULTIPLIER_PATTERN = re.compile(
    r"(?P<category>[二三四]{1,3})?\s*×\s*(?P<value>\d+(?:\.\d+)?)"
)

# Valid decimal formats: integer digits, or integer + '.' + >=1 decimal digit.
VALID_MULTIPLIER_VALUE = re.compile(r"\d+(?:\.\d+)?")

# Scope enum (closed)
SCOPES = frozenset({"current_group", "all_groups_in_region", "unresolved_region"})

# Layout enum (V2 minimal)
LAYOUTS_V2 = frozenset({"normal_row", "number_set", "shared_multiplier"})


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


def normalize_fullwidth_punct(text: str) -> str:
    """Fullwidth parens/equals/dots → halfwidth (never touches digits)."""
    return text.translate(_FULLWIDTH_PARENS).translate(_FULLWIDTH_EQUALS).translate(_FULLWIDTH_DOTS)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(raw: str) -> str:
    """Full pipeline: symbols → ×, fullwidth → halfwidth, whitespace collapse."""
    return normalize_whitespace(normalize_fullwidth_punct(normalize_fullwidth_digits(normalize_symbols(raw))))


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
    if value in {"auto", "normal", "column", "mixed"} or value in LAYOUTS_V2:
        return []
    return [ClosedSetIssue.LAYOUT_UNCERTAIN.value]


def validate_label(value: Any) -> list[str]:
    if value is None or value in ALLOWED_CHINESE:
        return []
    return [ClosedSetIssue.DISALLOWED_CHARACTER.value]


def validate_scope(value: Any) -> list[str]:
    if value in SCOPES:
        return []
    return [ClosedSetIssue.LAYOUT_UNCERTAIN.value]


def validate_multiplier(text: str | None) -> list[str]:
    """Multiplier forms (V2): ×1, ×2, ×10, ×0.2, ×0.3, ×0.5, 1, 二三×1.

    Decimal multipliers are LEGAL. Rejects: .5, 0., 0.5.2, -0.5, 5e-1,
    letters, and anything else outside the grammar.
    """
    if text is None:
        return []
    t = normalize_text(text)
    if not t:
        return []
    if "?" in t:
        return [ClosedSetIssue.UNKNOWN_TOKEN.value]
    # category? × value  (value = integer or integer.decimal)
    if re.fullmatch(r"(?:[二三四]{1,3})?\s*×?\s*\d+(?:\.\d+)?", t):
        return []
    if re.fullmatch(r"[二三四]{1,3}\s*×\s*\d+(?:\.\d+)?(?:\s+[二三四]{1,3}\s*×\s*\d+(?:\.\d+)?)*", t):
        return []  # multiple multipliers on one number set
    if re.search(r"[A-Za-z]", t):
        return [ClosedSetIssue.DISALLOWED_CHARACTER.value]
    return [ClosedSetIssue.AMBIGUOUS_SYMBOL.value]


def parse_multiplier_text(text: str | None) -> list[dict[str, Any]]:
    """Parse multiplier text into structured [{category, value_text}, ...].

    Examples:
      三×0.5        → [{"category": "三", "value_text": "0.5"}]
      四×3          → [{"category": "四", "value_text": "3"}]
      二三×1        → [{"category": "二三", "value_text": "1"}]
      三×0.5 四×3   → two entries
      ×1            → [{"category": None, "value_text": "1"}]
    Unparseable → [] (caller decides uncertainty).
    """
    if not text:
        return []
    t = normalize_text(text)
    if not t or "?" in t:
        return []
    parts = re.split(r"\s+", t.strip())
    result: list[dict[str, Any]] = []
    for part in parts:
        m = MULTIPLIER_PATTERN.fullmatch(part) or re.fullmatch(r"×?\s*(?P<value>\d+(?:\.\d+)?)", part)
        if m is None:
            return []
        category = m.group("category") if "category" in m.groupdict() and m.group("category") else None
        value_text = m.group("value")
        if not VALID_MULTIPLIER_VALUE.fullmatch(value_text):
            return []
        result.append({"category": category, "value_text": value_text})
    return result


def parse_paren_number_set(text: str) -> list[str] | None:
    """Parse (12.18.20.23) into ["12", "18", "20", "23"].

    Dots inside parens are number separators. The paren group may be part
    of a longer transcription (e.g. "(12.18.20.23) 三×0.5 四×3"). Returns
    None when parens are unbalanced, nested, empty, or contain non-number
    tokens.
    """
    t = normalize_text(text)
    m = re.search(r"\((?P<body>[^()]*)\)", t)
    if m is None:
        return None
    # Reject unbalanced extra parens: exactly one '(' and one ')' allowed
    if t.count("(") != 1 or t.count(")") != 1:
        return None
    body = m.group("body")
    tokens = [x for x in re.split(r"[.\s]+", body) if x]
    if not tokens:
        return None
    for token in tokens:
        if not re.fullmatch(r"[0-9?]{1,2}", token):
            return None
    return tokens


def parse_shared_multiplier(text: str) -> dict[str, Any] | None:
    """Parse 各=三×0.3 into a shared multiplier semantic.

    The '=' marker is REQUIRED (各三×0.3 without '=' is rejected as
    ambiguous). Returns {"category", "value_text", "scope":
    "all_groups_in_region"} or None when not a valid shared-multiplier
    marker. Scope is all_groups_in_region only when the region is actually
    known; the caller must downgrade to unresolved_region when the region
    boundary is not yet established.
    """
    t = normalize_text(text)
    m = re.fullmatch(r"各\s*=\s*(?P<category>[二三四]{1,3})\s*×\s*(?P<value>\d+(?:\.\d+)?)", t)
    if m is None:
        return None
    return {
        "category": m.group("category"),
        "value_text": m.group("value"),
        "scope": "all_groups_in_region",
    }


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
    scope: str | None = None,
    region_bound: bool = False,
) -> dict[str, Any]:
    """Full closed-set validation of one model line (V2).

    Returns {issues, needs_human_confirmation, token_validations, semantics}.
    semantics is the structured parse (multipliers, paren number set,
    shared multiplier) — produced by the deterministic V2 parser, never by
    the model itself. Shared multipliers without an explicit region binding
    get scope=unresolved_region and always require human confirmation.
    """
    issues: list[str] = []
    token_validations: list[dict[str, Any]] = []
    semantics: dict[str, Any] | None = None

    for group in number_groups:
        for token in group:
            tv = validate_number_token(token)
            token_validations.append(tv.to_dict())
            if tv.requires_human_confirmation:
                issues.extend(tv.issues)

    issues.extend(validate_multiplier(multiplier_text))
    issues.extend(validate_group_structure(number_groups, multiplier_text))
    if raw_text and not re.fullmatch(r"[0-9?二三四各×.=() ]+", raw_text):
        issues.append(ClosedSetIssue.DISALLOWED_CHARACTER.value)

    # ── V2 structured semantics (deterministic parser, production path) ──────
    multiplier_semantics = parse_multiplier_text(multiplier_text) if multiplier_text else []
    if multiplier_text and not multiplier_semantics:
        issues.append(ClosedSetIssue.AMBIGUOUS_SYMBOL.value)

    shared = parse_shared_multiplier(raw_text or "")
    if shared is not None:
        # 各=三×0.3: legal only with an explicit region binding
        if not region_bound:
            shared["scope"] = "unresolved_region"
            issues.append(ClosedSetIssue.GROUP_STRUCTURE_UNCERTAIN.value)
        semantics = {
            "layout": "shared_multiplier",
            "numbers": [],
            "multipliers": [{"category": shared["category"], "value_text": shared["value_text"]}],
            "scope": shared["scope"],
            "needs_human_confirmation": True,
        }

    if semantics is None and multiplier_semantics:
        paren = parse_paren_number_set(raw_text or "")
        numbers: list[str] = []
        if paren is not None:
            numbers = paren
            layout = "number_set"
        else:
            layout = "normal_row"
            for group in number_groups:
                numbers.extend(group)
        semantics = {
            "layout": layout,
            "numbers": numbers,
            "multipliers": multiplier_semantics,
            "scope": scope if scope in SCOPES else "current_group",
            # Structured semantics always require human confirmation
            # (closed set never auto-confirms; PENDING_HUMAN_CONFIRMATION)
            "needs_human_confirmation": True,
        }

    # Bare dot-separated numbers without parens have no declared purpose
    if (
        raw_text
        and "." in raw_text
        and parse_paren_number_set(raw_text) is None
        and not any(parse_multiplier_text(m) for m in (multiplier_text,) if m)
    ):
        issues.append(ClosedSetIssue.AMBIGUOUS_SYMBOL.value)

    # Policy: every OCR/vision line requires human confirmation, regardless of
    # syntax validity. This is the single canonical source of truth — never
    # re-derived per layer, so top-level and semantics can never diverge.
    # `issues` still records syntax problems for the UI, but never flips the
    # confirmation flag to False.
    needs_human = True
    return {
        "issues": sorted(set(issues)),
        "needs_human_confirmation": needs_human,
        "token_validations": token_validations,
        "semantics": semantics,
    }
