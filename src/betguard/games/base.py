from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameSpec:
    name: str
    game_id: int
    min_number: int
    max_number: int
    min_count: int
    max_count: int
    allowed_types: tuple[str, ...]
    allowed_stars: tuple[int, ...] = (2, 3, 4)

    def contains_number(self, number: int) -> bool:
        return self.min_number <= number <= self.max_number

    def default_stars(self, count: int) -> list[int]:
        if count == 2:
            return [2]
        if count == 3:
            return [2, 3]
        if count >= 4:
            return [2, 3, 4]
        return []
