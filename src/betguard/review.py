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
