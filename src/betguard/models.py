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
    parse_errors: list[str] = field(default_factory=list)


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
            return {
                "game": self.bet.game,
                "type": self.bet.type,
                "number": self.bet.number,
                "car_units": self.bet.car_units,
                "money": self.bet.money,
                "status": self.validation.status,
                "warnings": self.validation.warnings,
                "errors": self.validation.errors,
            }

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
        return data
