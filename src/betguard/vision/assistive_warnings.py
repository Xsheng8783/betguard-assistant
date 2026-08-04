"""Conservative warning classification for assistive human review (MVP v1).

Warnings only HIGHLIGHT — they never modify text. Explicitly forbidden:
  - '>' -> '2'
  - '36' -> '26'
  - auto leading-zero padding
  - guessing numbers by game rules
  - picking the most likely digit within legal range

Severity levels:
  BLOCKER       — cannot confirm the line (invalid charset, out of range,
                  parser failure, truncated/empty model response)
  WARNING       — needs human attention (possible omission, bad multiplier,
                  duplicate number, bottom-special content, unparsed)
  RISK_HIGHLIGHT — ambiguous glyph highlight only (2/3, 6/8, 1/7,
                  leading-zero); does NOT block confirmation.

Per-line dedup: at most ONE warning per code per line (details lists
positions). POSSIBLE_ROW_OMISSION is session-level (needs
expected_physical_rows).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# closed-set charset (imported constants; values mirror closed_set.py —
# if closed_set exports its own, reuse those instead of duplicating).
try:
    from .closed_set import (  # type: ignore
        ALLOWED_RAW_TEXT_CHARS,
    )
    _CHARSET_SOURCE = "closed_set.ALLOWED_RAW_TEXT_CHARS"
except ImportError:  # pragma: no cover - fallback only
    ALLOWED_DIGITS = frozenset("0123456789")
    ALLOWED_CHINESE = frozenset("二三四各")
    ALLOWED_SYMBOLS = frozenset("×.=()")
    ALLOWED_RAW_TEXT_CHARS = (
        ALLOWED_DIGITS | ALLOWED_CHINESE | ALLOWED_SYMBOLS | frozenset("? ")
    )
    _CHARSET_SOURCE = "inline fallback"


class WarningSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    RISK_HIGHLIGHT = "RISK_HIGHLIGHT"


class WarningCode(str, Enum):
    # BLOCKER
    INVALID_CHARSET = "INVALID_CHARSET"
    NUMBER_OUT_OF_RANGE = "NUMBER_OUT_OF_RANGE"
    PARSER_FAILURE = "PARSER_FAILURE"
    TRUNCATED_RESPONSE = "TRUNCATED_RESPONSE"
    EMPTY_MODEL_RESPONSE = "EMPTY_MODEL_RESPONSE"
    UNKNOWN_MARKER = "UNKNOWN_MARKER"
    # WARNING
    POSSIBLE_ROW_OMISSION = "POSSIBLE_ROW_OMISSION"
    INVALID_MULTIPLIER = "INVALID_MULTIPLIER"
    DUPLICATE_NUMBER = "DUPLICATE_NUMBER"
    BOTTOM_SPECIAL_REVIEW_REQUIRED = "BOTTOM_SPECIAL_REVIEW_REQUIRED"
    UNPARSED_CONTENT = "UNPARSED_CONTENT"
    # RISK_HIGHLIGHT
    AMBIGUOUS_2_3 = "AMBIGUOUS_2_3"
    AMBIGUOUS_6_8 = "AMBIGUOUS_6_8"
    AMBIGUOUS_1_7 = "AMBIGUOUS_1_7"
    LEADING_ZERO_REVIEW = "LEADING_ZERO_REVIEW"


@dataclass
class AssistiveWarning:
    code: str
    severity: str
    message: str
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
        }


# ── helpers ────────────────────────────────────────────────────────────────

_NUMBER_RE = re.compile(r"\d{1,2}")
_MULTIPLIER_RE = re.compile(r"(?:[二三四]?[xX×]\d+(?:\.\d+)?)", re.I)
_BOTTOM_SPECIAL_RE = re.compile(r"[()（）、]|尾|三x10|x10|X10|x17|X17|X10|x10")
_CHINESE_MULT_RE = re.compile(r"[二三四][xX×]\d+(?:\.\d+)?")
_AMBIGUOUS_PAIRS = (
    (WarningCode.AMBIGUOUS_2_3, "2", "3"),
    (WarningCode.AMBIGUOUS_6_8, "6", "8"),
    (WarningCode.AMBIGUOUS_1_7, "1", "7"),
)


def _extract_tokens(text: str) -> list[str]:
    """Two-digit number tokens (01-39 context) + chinese multiplier tokens."""
    tokens = _NUMBER_RE.findall(text)
    tokens += _CHINESE_MULT_RE.findall(text)
    return tokens


def _strip_multipliers(text: str) -> str:
    """Remove multiplier segments so their digits are NOT treated as bet
    numbers. Handles: X1, 二X1, 三X0.5, 四X10, x0.2, ×1 ..."""
    # chinese-prefixed multipliers first (三X0.5), then bare (x1, X10)
    stripped = _CHINESE_MULT_RE.sub(" ", text)
    stripped = re.sub(r"(?<!\d)[xX×]\d+(?:\.\d+)?", " ", stripped)
    return stripped


def _bet_number_tokens(text: str) -> list[str]:
    """Two-digit tokens that are NOT part of a multiplier segment."""
    return _NUMBER_RE.findall(_strip_multipliers(text))


# ── line-level warnings ────────────────────────────────────────────────────

def warn_invalid_charset(text: str, allow_unknown: bool = True) -> list[AssistiveWarning]:
    """BLOCKER: any character outside the assistive charset.

    Assistive charset = closed-set raw-text chars + x/X (multiplier
    equivalent of ×, common in plain-text output) + '/' (matrix rows).
    Optionally '?' (unknown marker).
    """
    allowed = set(ALLOWED_RAW_TEXT_CHARS) | {"x", "X", "/"}
    if allow_unknown:
        allowed = allowed | {"?"}
    bad = sorted({ch for ch in text if ch not in allowed})
    if not bad:
        return []
    return [AssistiveWarning(
        code=WarningCode.INVALID_CHARSET.value,
        severity=WarningSeverity.BLOCKER.value,
        message=f"包含不在封閉字元集內的字元: {''.join(bad)}",
        details={"chars": "".join(bad)},
    )]


def warn_number_out_of_range(text: str) -> list[AssistiveWarning]:
    """BLOCKER: two-digit tokens (excluding multiplier segments) outside 01-39."""
    out = []
    for token in _bet_number_tokens(text):
        val = int(token)
        if not (1 <= val <= 39):
            out.append(token)
    if not out:
        return []
    return [AssistiveWarning(
        code=WarningCode.NUMBER_OUT_OF_RANGE.value,
        severity=WarningSeverity.BLOCKER.value,
        message=f"號碼超出 01-39 範圍: {', '.join(out)}",
        details={"tokens": out},
    )]


def warn_duplicate_number(text: str) -> list[AssistiveWarning]:
    """WARNING: same two-digit token repeated within the line
    (multiplier digits excluded)."""
    tokens = [t for t in _bet_number_tokens(text) if 1 <= int(t) <= 39]
    seen: set[str] = set()
    dup = sorted({t for t in tokens if t in seen or seen.add(t)})
    if not dup:
        return []
    return [AssistiveWarning(
        code=WarningCode.DUPLICATE_NUMBER.value,
        severity=WarningSeverity.WARNING.value,
        message=f"同行出現重複號碼: {', '.join(dup)}",
        details={"tokens": dup},
    )]


def warn_invalid_multiplier(text: str) -> list[AssistiveWarning]:
    """WARNING: a multiplier-looking token that is not valid 二X1/三X0.5 etc."""
    # any 'x'/'X'/'×' followed by non-numeric or missing number
    bad = []
    for m in re.finditer(r"[xX×]", text):
        rest = text[m.end():]
        num = re.match(r"\d+(?:\.\d+)?", rest)
        if not num:
            bad.append(text[m.start():m.end()])
            continue
        # chinese prefix must be 二/三/四 if present before the x
    if not bad:
        return []
    return [AssistiveWarning(
        code=WarningCode.INVALID_MULTIPLIER.value,
        severity=WarningSeverity.WARNING.value,
        message=f"倍率格式無法辨識: {' '.join(bad)}",
        details={"tokens": bad},
    )]


def warn_bottom_special(text: str) -> list[AssistiveWarning]:
    """WARNING: bottom-special content — parentheses, tail digits (尾),
    matrix ('/'), 各 marker, or multiple chinese multipliers in one line.
    Requires human review but never BLOCKs."""
    special = []
    if "/" in text:
        special.append("/")
    if re.search(r"[()（）、]", text):
        special.append("括號")
    if "各" in text:
        special.append("各")
    if "尾" in text:
        special.append("尾")
    if len(_CHINESE_MULT_RE.findall(text)) > 1:
        special.append("多組倍率")
    if not special:
        return []
    return [AssistiveWarning(
        code=WarningCode.BOTTOM_SPECIAL_REVIEW_REQUIRED.value,
        severity=WarningSeverity.WARNING.value,
        message="內容含底部特殊格式，請人工檢視: " + "、".join(special),
        details={"markers": special},
    )]


def warn_unparsed(text: str) -> list[AssistiveWarning]:
    """WARNING: line has content but no parseable numbers or multiplier."""
    if not text.strip():
        return []
    has_number = bool(_NUMBER_RE.search(text))
    has_mult = bool(_MULTIPLIER_RE.search(text))
    if has_number or has_mult:
        return []
    return [AssistiveWarning(
        code=WarningCode.UNPARSED_CONTENT.value,
        severity=WarningSeverity.WARNING.value,
        message="此行內容無法解析（無號碼或倍率）",
    )]


def warn_ambiguous_glyphs(text: str) -> list[AssistiveWarning]:
    """RISK_HIGHLIGHT: ambiguous glyph pairs, one warning per pair per line.
    Multiplier digits excluded (三X0.5's 3 is a multiplier, not a bet 3)."""
    out = []
    number_part = _strip_multipliers(text)
    for code, a, b in _AMBIGUOUS_PAIRS:
        if a in number_part or b in number_part:
            out.append(AssistiveWarning(
                code=code.value,
                severity=WarningSeverity.RISK_HIGHLIGHT.value,
                message=f"注意 {a}/{b} 可能易混淆，請確認",
                details={"pairs": f"{a}/{b}"},
            ))
    return out


def warn_leading_zero(text: str) -> list[AssistiveWarning]:
    """RISK_HIGHLIGHT: a bare single digit that might be a dropped 0.

    Multiplier suffix digits (x1, 三X0.5) are excluded — only standalone
    single digits in the number portion trigger this.
    """
    # remove multiplier tokens first (e.g. "x1", "二X1", "三X0.5")
    number_part = _strip_multipliers(text)
    singles = [t for t in _NUMBER_RE.findall(number_part) if len(t) == 1]
    if not singles:
        return []
    return [AssistiveWarning(
        code=WarningCode.LEADING_ZERO_REVIEW.value,
        severity=WarningSeverity.RISK_HIGHLIGHT.value,
        message="出現單一數字，可能遺失前導 0，請確認",
        details={"tokens": singles},
    )]


def warn_unknown_marker(text: str) -> list[AssistiveWarning]:
    """BLOCKER: '?' unknown marker — the line may be saved/shown/edited but
    cannot be CONFIRMED or CORRECTED until the user resolves it. The model
    output is never auto-guessed."""
    if "?" not in text:
        return []
    return [AssistiveWarning(
        code=WarningCode.UNKNOWN_MARKER.value,
        severity=WarningSeverity.BLOCKER.value,
        message="含有 ? 未知字元，需人工修正後才能確認",
        details={"count": text.count("?")},
    )]


# ── session-level ──────────────────────────────────────────────────────────

def warn_possible_row_omission(
    expected_physical_rows: int,
    returned_rows: int,
) -> list[AssistiveWarning]:
    """WARNING (session-level): model returned fewer non-blank rows than
    the known physical row count (e.g. from Row Pipeline)."""
    if expected_physical_rows <= 0:
        return []
    if returned_rows >= expected_physical_rows:
        return []
    return [AssistiveWarning(
        code=WarningCode.POSSIBLE_ROW_OMISSION.value,
        severity=WarningSeverity.WARNING.value,
        message=(f"辨識非空白行數 {returned_rows} 少於預期實體行數 "
                 f"{expected_physical_rows}，可能有漏行"),
        details={"expected": expected_physical_rows,
                 "returned": returned_rows},
    )]


# ── aggregator ─────────────────────────────────────────────────────────────

def analyze_line(text: str) -> list[AssistiveWarning]:
    """All line-level warnings for one line, deduplicated by code."""
    out: list[AssistiveWarning] = []
    if not text.strip():
        return []
    groups = [
        warn_invalid_charset(text),
        warn_number_out_of_range(text),
        warn_invalid_multiplier(text),
        warn_duplicate_number(text),
        warn_bottom_special(text),
        warn_unparsed(text),
        warn_ambiguous_glyphs(text),
        warn_leading_zero(text),
        warn_unknown_marker(text),
    ]
    seen: set[str] = set()
    for group in groups:
        for w in group:
            if w.code not in seen:
                seen.add(w.code)
                out.append(w)
    return out


def analyze_session_lines(lines: list[str],
                          expected_physical_rows: int = 0) -> dict[str, Any]:
    """Per-line warnings + session-level omission warning.

    Returns {"lines": {line_no: [warning_dict...]},
             "session": [warning_dict...]}.
    """
    per_line: dict[int, list[dict[str, Any]]] = {}
    for i, text in enumerate(lines):
        ws = analyze_line(text)
        if ws:
            per_line[i] = [w.to_dict() for w in ws]
    session_ws: list[dict[str, Any]] = []
    if expected_physical_rows > 0:
        non_blank = sum(1 for t in lines if t.strip())
        session_ws = [w.to_dict() for w in
                      warn_possible_row_omission(expected_physical_rows,
                                                 non_blank)]
    return {"lines": per_line, "session": session_ws}
