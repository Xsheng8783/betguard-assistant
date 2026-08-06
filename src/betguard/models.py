from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


JsonNumber = int | float
BetStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class BetAmount:
    unit: JsonNumber | None = None
    money: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.unit is not None:
            data["unit"] = self.unit
        if self.money is not None:
            data["money"] = self.money
        return data


@dataclass(frozen=True)
class ParsedBet:
    game: str
    type: str
    numbers: list[int]
    stars: list[int]
    unit: JsonNumber | None
    money: int | None
    bets: dict[str, BetAmount] = field(default_factory=dict)
    columns: list[list[int]] = field(default_factory=list)
    number: int | None = None
    car_units: JsonNumber | None = None
    executable: bool = True
    supported: bool = True
    block_reason: str | None = None
    parse_errors: list[str] = field(default_factory=list)
    original_text: str = ""
    normalized_text: str = ""
    parse_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> BetStatus:
        if self.errors:
            return "error"
        if self.warnings:
            return "warning"
        return "ok"


@dataclass(frozen=True)
class BetReport:
    bet: ParsedBet
    validation: ValidationResult

    def to_dict(self) -> dict[str, Any]:
        if self.bet.type == "car":
            data = {
                "game": self.bet.game,
                "type": self.bet.type,
                "number": self.bet.number,
                "car_units": self.bet.car_units,
                "money": self.bet.money,
                "status": self.validation.status,
                "warnings": self.validation.warnings,
                "errors": self.validation.errors,
            }
            self._add_parser_metadata(data)
            return data

        data: dict[str, Any] = {
            "game": self.bet.game,
            "type": self.bet.type,
        }
        if self.bet.columns:
            data["columns"] = self.bet.columns
        else:
            data["numbers"] = self.bet.numbers
        data.update(
            {
                "stars": self.bet.stars,
                "unit": self.bet.unit,
                "money": self.bet.money,
                "status": self.validation.status,
                "warnings": self.validation.warnings,
                "errors": self.validation.errors,
            }
        )
        if self.bet.bets:
            data["bets"] = {
                star: amount.to_dict()
                for star, amount in self.bet.bets.items()
            }
        if not self.bet.supported or not self.bet.executable:
            data["supported"] = self.bet.supported
            data["executable"] = self.bet.executable
            if self.bet.block_reason:
                data["block_reason"] = self.bet.block_reason
        self._add_parser_metadata(data)
        return data

    def _add_parser_metadata(self, data: dict[str, Any]) -> None:
        if self.bet.original_text:
            data["original_text"] = self.bet.original_text
        if self.bet.normalized_text:
            data["normalized_text"] = self.bet.normalized_text
        if self.bet.parse_notes:
            data["parse_notes"] = self.bet.parse_notes
