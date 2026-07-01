from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameRule:
    name: str
    game_id: int
    number_min: int
    number_max: int


GAME_RULES: dict[str, GameRule] = {
    "539": GameRule(name="539", game_id=13, number_min=1, number_max=39),
    "天天樂": GameRule(name="天天樂", game_id=22, number_min=1, number_max=39),
    "大樂": GameRule(name="大樂", game_id=12, number_min=1, number_max=49),
    "六合": GameRule(name="六合", game_id=11, number_min=1, number_max=49),
}


def get_game_rule(name: str) -> GameRule | None:
    return GAME_RULES.get(name)

