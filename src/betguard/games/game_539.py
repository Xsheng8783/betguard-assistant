from betguard.games.base import GameSpec


GAME_539 = GameSpec(
    name="539",
    game_id=13,
    min_number=1,
    max_number=39,
    min_count=2,
    max_count=39,
    allowed_types=("normal", "column", "car"),
    allowed_stars=(2, 3, 4),
)
