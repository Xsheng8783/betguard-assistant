from betguard.games.base import GameSpec
from betguard.games.game_539 import GAME_539
from betguard.game_rules import GAME_RULES

ACTIVE_GAMES: dict[str, GameSpec] = {
    GAME_539.name: GAME_539,
}

for rule in GAME_RULES.values():
    if rule.name in ACTIVE_GAMES:
        continue
    ACTIVE_GAMES[rule.name] = GameSpec(
        name=rule.name,
        game_id=rule.game_id,
        min_number=rule.number_min,
        max_number=rule.number_max,
        min_count=2,
        max_count=rule.number_max,
        allowed_types=("normal", "column", "car"),
        allowed_stars=(2, 3, 4),
    )

__all__ = ["ACTIVE_GAMES", "GameSpec"]
