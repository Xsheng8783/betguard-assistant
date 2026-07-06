from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from betguard.game_rules import GAME_RULES
from betguard.models import BetReport, ParsedBet, ValidationResult
from betguard.parser import ParseError, parse_line
from betguard.validator import validate_bet


@dataclass(frozen=True)
class ReviewItem:
    line_no: int
    raw: str
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_no": self.line_no,
            "raw": self.raw,
            "result": self.result,
        }


@dataclass(frozen=True)
class ReviewSummary:
    total: int
    ok: int
    warning: int
    error: int
    items: list[ReviewItem]

    @property
    def can_continue(self) -> bool:
        return self.error == 0 and self.warning == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ok": self.ok,
            "warning": self.warning,
            "error": self.error,
            "items": [item.to_dict() for item in self.items],
            "can_continue": self.can_continue,
        }


def build_report(text: str, *, game: str = "539") -> BetReport:
    try:
        bet = parse_line(text, default_game=game)
        validation = validate_bet(bet)
    except ParseError as exc:
        errors = getattr(exc, "errors", [str(exc)])
        bet = ParsedBet(
            game=game if game in GAME_RULES else "539",
            type="normal",
            numbers=[],
            stars=[],
            unit=None,
            money=None,
        )
        validation = ValidationResult(errors=errors)
    return BetReport(bet=bet, validation=validation)


def review_lines(lines: list[str], *, game: str = "539") -> ReviewSummary:
    items: list[ReviewItem] = []
    counts = {"ok": 0, "warning": 0, "error": 0}

    for source_line_no, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue

        result = build_report(raw, game=game).to_dict()
        status = result["status"]
        counts[status] += 1
        items.append(
            ReviewItem(
                line_no=source_line_no,
                raw=raw,
                result=result,
            )
        )

    return ReviewSummary(
        total=len(items),
        ok=counts["ok"],
        warning=counts["warning"],
        error=counts["error"],
        items=items,
    )


def review_text(text: str, *, game: str = "539") -> ReviewSummary:
    return review_lines(text.splitlines(), game=game)


def format_pretty_review(summary: ReviewSummary) -> str:
    lines: list[str] = []
    for item in summary.items:
        result = item.result
        status = result["status"].upper()
        bet_type = result.get("type", "unknown")
        lines.append(f"[{item.line_no}] {status} {bet_type} {item.raw}")
        for error in result.get("errors", []):
            lines.append(f"    - {error}")
        for warning in result.get("warnings", []):
            lines.append(f"    - {warning}")

    if lines:
        lines.append("")
    lines.append("Summary:")
    lines.append(
        "total={total} ok={ok} warning={warning} error={error} can_continue={can_continue}".format(
            total=summary.total,
            ok=summary.ok,
            warning=summary.warning,
            error=summary.error,
            can_continue=str(summary.can_continue).lower(),
        )
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Input Format Diagnostic Report (v1)
#
# A read-only, line-by-line diagnostic for verifying what build_report() /
# review_text() decide about each line of pasted betting text. Designed to
# be used BEFORE market open (or before any --new-batch-from-file) so the
# caller can confirm that no Needs-Review / Invalid / Watchlist fragment
# is silently slipping into the approved fill queue.
#
# Hard rules (do not relax without discussion):
#   1. The function never mutates parser / validator / queue / fill state.
#      It only inspects already-produced ReviewSummary data.
#   2. Every line always carries its raw parser status (ok / warning /
#      error) plus a human-friendly display label (VALID / REVIEW /
#      BLOCKED).  The raw status is never dropped or overwritten.
#   3. The "Reason" line is a best-effort summary.  If no specific rule
#      matches, the report falls back to the first raw error / warning
#      message -- the raw errors / warnings lists are always included
#      below so no information is lost.
# ---------------------------------------------------------------------------


_DISPLAY_STATUS = {
    "ok": "VALID",
    "warning": "REVIEW",
    "error": "BLOCKED",
}


def _format_numbers(numbers: list[int] | None) -> str:
    if not numbers:
        return "-"
    return ",".join(f"{n:02d}" for n in numbers)


def _format_stars(stars: list[int] | None) -> str:
    if not stars:
        return "-"
    return ",".join(str(s) for s in stars)


def _format_unit(unit: float | int | None) -> str:
    if unit is None:
        return "-"
    if unit == int(unit):
        return str(int(unit))
    return f"{unit:g}"


def _format_money(money: int | float | None) -> str:
    if money is None:
        return "-"
    return str(money)


def _reason_for_item(item: ReviewItem) -> str:
    """Return a short human-readable reason for the item.

    Tries specific rules first (substring match against the first error or
    warning).  Falls back to the first raw error / warning so the report
    always carries actionable text.
    """
    errors = list(item.result.get("errors", []))
    warnings = list(item.result.get("warnings", []))
    primary = errors[0] if errors else (warnings[0] if warnings else "")

    text = primary.lower()

    # Specific rule matches (substring on the raw parser/validator message).
    if not errors and not warnings:
        if "normal" in (item.result.get("type") or ""):
            unit = item.result.get("unit")
            money = item.result.get("money")
            if unit is not None and money is not None and unit != 1:
                return f"decimal hyphen amount, {unit:g} unit = {int(money)} 元"
            return "normal bet, all fields parsed"
        return "ok"
    if "customer-specific shorthand" in text:
        return "customer shorthand, requires human review"
    if "number out of range" in text:
        return "one or more numbers outside the 1-39 range"
    if "unsupported characters" in text:
        return "unsupported characters present"
    if "港" in (item.raw or "") or item.raw.lower().startswith("hk"):
        return "HK prefix requires manual review"
    if "duplicate number" in text:
        return "duplicate number in bet"
    if "missing money" in text:
        return "missing money"
    if "missing numbers" in text:
        return "missing numbers (no valid 2-digit numbers found)"
    if "missing stars" in text:
        return "missing stars"
    if "standalone amount" in text:
        return "standalone amount line needs manual review"
    # Fallback: return the first raw error / warning verbatim.
    return primary


def format_input_diagnostic_report(summary: ReviewSummary) -> str:
    lines: list[str] = []
    for item in summary.items:
        result = item.result
        raw_status = str(result.get("status", ""))
        display_status = _DISPLAY_STATUS.get(raw_status, raw_status.upper())
        bet_type = result.get("type") or "-"

        lines.append(f"Line {item.line_no}: {item.raw}")
        lines.append(f"  Raw status: {raw_status}")
        lines.append(f"  Display status: {display_status}")
        lines.append(f"  Type: {bet_type}")
        lines.append(f"  Numbers: {_format_numbers(result.get('numbers'))}")
        lines.append(f"  Stars: {_format_stars(result.get('stars'))}")
        lines.append(f"  Unit: {_format_unit(result.get('unit'))}")
        lines.append(f"  Money: {_format_money(result.get('money'))}")
        lines.append(f"  Reason: {_reason_for_item(item)}")
        raw_errors = list(result.get("errors", []))
        raw_warnings = list(result.get("warnings", []))
        lines.append(f"  Raw errors: {raw_errors if raw_errors else '[]'}")
        lines.append(f"  Raw warnings: {raw_warnings if raw_warnings else '[]'}")
        lines.append("")

    lines.append("Summary:")
    lines.append(
        f"  total={summary.total} ok={summary.ok} warning={summary.warning} "
        f"error={summary.error} can_continue={str(summary.can_continue).lower()}"
    )
    return "\n".join(lines)
