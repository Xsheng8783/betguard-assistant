"""Multiplier completeness classification and canonical merge policy.

Only COMPLETE rules may enter ``multiplier_text`` / ``multiplier_rules`` /
the standardized result. Partial fragments (``2``, ``2/3``, ``X1``) and
invalid tokens (``23 x 35``) may only enter evidence / fallback_candidate /
warnings, and must never make a row executable by themselves.

Category canonical order is always 2/3/4 (never 4/3, 3/2).
Categories are merged ONLY when the multiplier value is identical.
"""
from __future__ import annotations

import re

COMPLETE = "complete_multiplier_rule"
PARTIAL = "partial_multiplier_evidence"
INVALID = "invalid_multiplier_token"

_DIGIT_CN = {"二": "2", "三": "3", "四": "4"}

# Digit category token: single digit, stacked with / or ., two/three-digit
# shorthand (23/24/34/234) or Chinese chars. "4/3" parses as categories
# {3,4} and is canonicalized to 3/4.
_CATEGORY_RE = re.compile(r"^(?:[234](?:[/.][234])*|[23]4|23|234)$|^[二三四]{1,3}$")
_VALUE_RE = re.compile(r"^\d+(?:\.\d+)?$")


def normalize_rule(token: str) -> str:
    """Compact canonical rule text: collapse spaces, x/× -> X, ¾/⅔ -> 3/4/2/3."""
    return (
        re.sub(r"\s+", "", token or "")
        .replace("¾", "3/4")
        .replace("⅔", "2/3")
        .replace("×", "X")
        .replace("x", "X")
    )


def category_digits(category: str) -> list[str]:
    """Canonical ascending category digits for a category token.

    "3/4" -> ["3", "4"]; "4/3" -> ["3", "4"]; "三四" -> ["3", "4"];
    "34" -> ["3", "4"]; "2/3/4" -> ["2", "3", "4"]; "二" -> ["2"].
    """
    if not category:
        return []
    if re.fullmatch(r"[二三四]+", category):
        digits = [_DIGIT_CN[c] for c in category]
    else:
        digits = re.findall(r"[234]", category)
    seen: set[str] = set()
    return [d for d in digits if not (d in seen or seen.add(d))]


def canonical_category(category: str) -> str:
    return "/".join(sorted(category_digits(category)))


def _parse_rule(token: str) -> tuple[list[str], str] | None:
    t = normalize_rule(token)
    m = re.fullmatch(r"([^X]+)X(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    cat, value = m.group(1), m.group(2)
    if not _CATEGORY_RE.fullmatch(cat):
        return None
    if not _VALUE_RE.fullmatch(value):
        return None
    # Two 2-digit numbers joined by X (23 x 35) are a COLUMN separator, never
    # a multiplier (e.g. 24 x 22, 23 x 35).
    if len(cat) == 2 and cat.isdigit() and re.fullmatch(r"\d{2}", value):
        return None
    return category_digits(cat), value


def classify_multiplier_token(token: str) -> str:
    """Classify ONE multiplier token.

    complete: category + X + numeric value (e.g. 2X1, 3X0.2, 2/3/4X0.1,
    34X1, 三四X1).
    partial: category-only (2, 2/3, 4/3), value-only (X1, X0.1), or
    category+X without a value (2X, 3/4X).
    invalid: anything else (23 x 35, letters, empty).
    """
    t = normalize_rule(token)
    if not t:
        return INVALID
    if _parse_rule(t) is not None:
        return COMPLETE
    if re.fullmatch(r"\d{2}X\d{2}", t):
        return INVALID  # two 2-digit numbers = column separator, not multiplier
    if _CATEGORY_RE.fullmatch(t):
        return PARTIAL
    if re.fullmatch(r"X\d+(?:\.\d+)?", t):
        return PARTIAL
    if re.fullmatch(r"(?:[234](?:[/.][234])*|[23]4|23|234|[二三四]{1,3})X", t):
        return PARTIAL
    return INVALID


def is_complete_multiplier(text: str | None) -> bool:
    """True when EVERY whitespace-separated token is a complete rule."""
    if not text or not str(text).strip():
        return False
    tokens = _compact_rules(str(text)).split()
    return bool(tokens) and all(classify_multiplier_token(t) == COMPLETE for t in tokens)


def merge_complete_rules(rules: list[str]) -> list[str]:
    """Canonical merge policy for complete rules.

    - Categories are merged ONLY when the multiplier value is identical
      (e.g. 2/3X0.1 + 3/4X0.1 -> 2/3/4X0.1; 2X1 + 3X1 -> 2/3X1).
    - Different values NEVER merge (2X1, 3X0.2, 4X0.5 stay three rules).
    - Category order is always 2/3/4; 4/3 is canonicalized to 3/4.
    """
    by_value: dict[str, set[str]] = {}
    order: list[str] = []
    for rule in rules:
        parsed = _parse_rule(rule)
        if parsed is None:
            continue
        cats, value = parsed
        if value not in by_value:
            by_value[value] = set()
            order.append(value)
        by_value[value].update(cats)
    out: list[str] = []
    for value in order:
        cats = "/".join(sorted(by_value[value]))
        out.append(f"{cats}X{value}")
    return out


def split_complete_rules(text: str | None) -> list[str]:
    """Complete rules from a multiplier text (empty when any token is partial
    or invalid — fragments never become official rules)."""
    if not text or not str(text).strip():
        return []
    tokens = _compact_rules(str(text)).split()
    if not all(classify_multiplier_token(t) == COMPLETE for t in tokens):
        return []
    return merge_complete_rules(tokens)


def _compact_rules(text: str) -> str:
    """Collapse spacing around / and x/× so "3 / 4 x 1" -> "3/4X1"."""
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s*([xX×])\s*", "X", text.strip()))


def partial_tokens(text: str | None) -> list[str]:
    """Partial/invalid tokens AFTER normalization.

    "3/4 x 1" / "2 x 1" / "2 x 2 3 x 5" are COMPLETE and return [];
    "2", "2/3", "4/3", "X1", "23 x 35" return the offending token(s).
    """
    if not text or not str(text).strip():
        return []
    return [t for t in _compact_rules(str(text)).split() if classify_multiplier_token(t) != COMPLETE]
