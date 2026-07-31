from __future__ import annotations

from collections import Counter

from betguard.games import ACTIVE_GAMES
from betguard.models import ParsedBet, ValidationResult


STAR_NAMES = {
    2: "\u4e8c\u661f",
    3: "\u4e09\u661f",
    4: "\u56db\u661f",
}


def validate_bet(bet: ParsedBet) -> ValidationResult:
    errors: list[str] = list(bet.parse_errors)
    warnings: list[str] = []

    game = ACTIVE_GAMES.get(bet.game)
    if game is None:
        errors.append(f"unknown game: {bet.game}")
        return ValidationResult(warnings=warnings, errors=errors)

    if bet.type not in game.allowed_types:
        errors.append(f"unknown bet type for {bet.game}: {bet.type}")

    if bet.type == "car":
        _validate_car(bet, errors)
        return ValidationResult(warnings=warnings, errors=errors)

    if bet.type == "column":
        _validate_columns(bet, errors, warnings)
    else:
        _validate_normal(bet, errors, warnings)

    for star in bet.stars:
        if star not in game.allowed_stars:
            errors.append(f"unsupported star {star}")

    if not bet.bets:
        if bet.money is None or bet.unit is None:
            warnings.append("missing amount" if bet.type == "column" else "missing money")
        else:
            if bet.money <= 0:
                errors.append("money must be greater than 0")
            if bet.unit <= 0:
                errors.append("unit must be greater than 0")
    else:
        for star, amount in bet.bets.items():
            if int(star) not in bet.stars:
                errors.append(f"bet amount star {star} is not listed in stars")
            if amount.money is None:
                errors.append(f"missing money for star {star}")
            elif amount.money <= 0:
                errors.append(f"money for star {star} must be greater than 0")
            if amount.unit is not None and amount.unit <= 0:
                errors.append(f"unit for star {star} must be greater than 0")

    return ValidationResult(warnings=warnings, errors=errors)


def _validate_car(bet: ParsedBet, errors: list[str]) -> None:
    errors.append("車牌型尚未支援，請人工確認")
    game = ACTIVE_GAMES[bet.game]

    if bet.number is None:
        if len(bet.numbers) > 1:
            errors.append("ambiguous car number")
        else:
            errors.append("missing car number")
    elif not game.contains_number(bet.number):
        errors.append(_range_error(bet.game, bet.number))

    if len(bet.numbers) > 1 and bet.number is None:
        errors.append("multiple numbers found for car bet")

    if bet.car_units is None or bet.money is None:
        errors.append("missing car amount")
        return

    if bet.car_units <= 0:
        errors.append("invalid car units")
    if bet.money <= 0:
        errors.append("amount must be positive")


def _validate_normal(
    bet: ParsedBet,
    errors: list[str],
    warnings: list[str],
) -> None:
    game = ACTIVE_GAMES[bet.game]
    for number, count in Counter(bet.numbers).items():
        if count > 1:
            errors.append(f"duplicate number {number}")

    for message in _star_count_errors(len(bet.numbers), bet.stars):
        errors.append(message)

    for number in bet.numbers:
        if not game.contains_number(number):
            errors.append(_range_error(bet.game, number))


def _validate_columns(
    bet: ParsedBet,
    errors: list[str],
    warnings: list[str],
) -> None:
    game = ACTIVE_GAMES[bet.game]
    if len(bet.columns) < 2 and not _is_flat_column_group(bet):
        errors.append("column bet requires at least 2 columns")

    for index, column in enumerate(bet.columns, start=1):
        if not column:
            errors.append(f"column {index} is empty")
        for number in column:
            if not game.contains_number(number):
                errors.append(_range_error(bet.game, number))

    for number, count in Counter(bet.numbers).items():
        if count > 1:
            errors.append(f"duplicate number {number}")

    if not bet.stars:
        warnings.append("missing stars")

    # Column-count → stars guard: 2-col max 2-star, 3-col max 3-star
    if bet.stars and not errors:
        col_count = len(bet.columns)
        max_star = max(bet.stars)
        if col_count == 2 and max_star > 2:
            errors.append(
                f"2柱只支援二星，不可含{max_star}星 (cols={col_count})"
            )
        elif col_count == 3 and max_star > 3:
            errors.append(
                f"3柱只支援二三星，不可含{max_star}星 (cols={col_count})"
            )


def _star_count_errors(number_count: int, stars: list[int]) -> list[str]:
    errors: list[str] = []
    for star in stars:
        required = _required_numbers_for_star(star)
        if required is not None and number_count < required:
            star_name = STAR_NAMES.get(star) or f"{star}星"
            errors.append(f"{star_name} requires at least {required} numbers")
    return errors


def _required_numbers_for_star(star: int) -> int | None:
    if star in (2, 3, 4):
        return star
    return None


def _is_flat_column_group(bet: ParsedBet) -> bool:
    return len(bet.columns) == 1 and len(bet.columns[0]) >= 2 and bool(bet.stars)


def _range_error(game_name: str, number: int) -> str:
    game = ACTIVE_GAMES[game_name]
    return (
        f"number out of range {number}; "
        f"valid range is {game.min_number}-{game.max_number}"
    )
