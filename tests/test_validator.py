from betguard.models import BetAmount, ParsedBet
from betguard.validator import validate_bet


def test_validator_accepts_valid_539_bet() -> None:
    bet = ParsedBet(
        game="539",
        type="normal",
        numbers=[6, 13, 23, 22],
        stars=[2, 3, 4],
        unit=0.5,
        money=50,
    )

    result = validate_bet(bet)

    assert result.status == "ok"
    assert result.errors == []


def test_validator_blocks_duplicate_numbers() -> None:
    bet = ParsedBet(
        game="539",
        type="normal",
        numbers=[6, 13, 13, 22],
        stars=[2, 3, 4],
        unit=0.5,
        money=50,
    )

    result = validate_bet(bet)

    assert result.status == "error"
    assert "duplicate number 13" in result.errors


def test_validator_blocks_out_of_range_numbers() -> None:
    bet = ParsedBet(
        game="539",
        type="normal",
        numbers=[6, 13, 40, 22],
        stars=[2, 3, 4],
        unit=0.5,
        money=50,
    )

    result = validate_bet(bet)

    assert result.status == "error"
    assert "number out of range 40; valid range is 1-39" in result.errors


def test_validator_blocks_unknown_type() -> None:
    bet = ParsedBet(
        game="539",
        type="unknown",
        numbers=[6, 13, 23, 22],
        stars=[2, 3, 4],
        unit=0.5,
        money=50,
    )

    result = validate_bet(bet)

    assert result.status == "error"
    assert "unknown bet type for 539: unknown" in result.errors


def test_validator_warns_when_money_is_missing() -> None:
    bet = ParsedBet(
        game="539",
        type="normal",
        numbers=[6, 13, 23],
        stars=[2, 3],
        unit=None,
        money=None,
    )

    result = validate_bet(bet)

    assert result.status == "warning"
    assert result.warnings == ["missing money"]
    assert result.errors == []


def test_validator_accepts_per_star_amounts() -> None:
    bet = ParsedBet(
        game="539",
        type="normal",
        numbers=[7, 12, 20, 16, 27, 37, 19],
        stars=[3, 4],
        unit=None,
        money=None,
        bets={
            "3": BetAmount(money=10),
            "4": BetAmount(unit=0.05, money=5),
        },
    )

    result = validate_bet(bet)

    assert result.status == "ok"
    assert result.errors == []
