from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

from betguard.expander import expand_tail
from betguard.games import ACTIVE_GAMES
from betguard.models import BetAmount, JsonNumber, ParsedBet
from betguard.normalizer import normalize_for_parser


COMMON_MONEY_VALUES = {50, 100, 200, 500, 1000}
CHINESE_TWO = "\u4e8c"
CHINESE_ALT_TWO = "\u5169"
CHINESE_THREE = "\u4e09"
CHINESE_FOUR = "\u56db"
STAR_WORD = "\u661f"
UNIT_WORD = "\u652f"
YUAN_WORD = "\u5143"
BLOCK_WORD = "\u584a"
COMMA_WORD = "\u3001"
FULL_COMMA = "\uff0c"
TOUCH_WORD = "\u78b0"
TAIL_WORD = "\u5c3e"
CAR_WORD = "\u8eca"
HALF_WORD = "\u534a"
FULL_WORD = "\u5168"
PING_WORD = "\u576a"
SUSPECT_WORD = "\u5acc"
FULL_OPEN_PAREN = "\uff08"
FULL_CLOSE_PAREN = "\uff09"
MULTIPLY_SIGN = "\u00d7"
ALLOWED_CHINESE_CHARS = set("二三四兩星元塊支車尾碰今彩天天樂港六合大两")
GAME_MARKER_PATTERN = re.compile(r"\u4eca\u5f69|(?<!\d)539(?!\d)")
# Tiantianle \u4f4f\u78b0 header: \u5929\u5929[\u6a02] followed by x-joined 3-4 digit groups such as
# 1019x2333x2637. This is not yet parseable, so it (and any continuation merged
# onto it) must stay Needs Review \u2014 never an independent Valid bet.
TIANTIAN_ZHUPENG_PATTERN = re.compile(
    r"\u5929\u5929(?:\u6a02)?\s*\d{3,4}(?:\s*[xX\u00d7\uff58]\s*\d{3,4})+"
)
NUMBER_DELIMITERS = set(f"./-{COMMA_WORD},{FULL_COMMA} \t\r\n")
COLUMN_INNER_DELIMITERS = set(f".-{COMMA_WORD},{FULL_COMMA} \t\r\n")
AMOUNT_KIND_PATTERN = f"{UNIT_WORD}|{YUAN_WORD}|{BLOCK_WORD}"
TAIL_OPERATOR_AMOUNT_PATTERN = re.compile(
    rf"(?P<op>[=/:])\s*(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*$"
)
X_AMOUNT_PATTERN = re.compile(
    rf"[xX]\s*(?P<value>\d+(?:\.\d+)?)(?:\s*{UNIT_WORD})?\s*$"
)
BARE_TAIL_AMOUNT_PATTERN = re.compile(
    rf"(?P<value>\d+)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*$"
)
PER_STAR_AMOUNT_PATTERN = re.compile(
    rf"(?P<star>{CHINESE_TWO}{STAR_WORD}|{CHINESE_ALT_TWO}{STAR_WORD}|"
    rf"{CHINESE_THREE}{STAR_WORD}|{CHINESE_FOUR}{STAR_WORD}|[234]{STAR_WORD}|"
    rf"{CHINESE_TWO}|{CHINESE_ALT_TWO}|{CHINESE_THREE}|{CHINESE_FOUR})"
    rf"\s*(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})"
)
COLUMN_SPLIT_PATTERN = re.compile(rf"\s*(?:/|{TOUCH_WORD}|{MULTIPLY_SIGN}|[xX])\s*")
TAIL_SHORTHAND_SPLIT_PATTERN = re.compile(rf"[{COMMA_WORD}{FULL_COMMA},\s\-]+")


class ParseError(ValueError):
    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def parse_line(text: str, *, default_game: str = "539") -> ParsedBet:
    normalized = normalize_for_parser(text)
    raw = normalized.normalized_text
    if TIANTIAN_ZHUPENG_PATTERN.search(raw):
        raise ParseError("Tiantianle 住碰 continuation requires manual review")
    if _is_standalone_star_amount_line(raw):
        raise ParseError("standalone star amount line requires manual review")
    diagnostics = _input_diagnostics(raw)
    value = _remove_game_markers(raw)
    game = ACTIVE_GAMES.get(default_game)
    if game is None:
        raise ParseError(f"unknown game: {default_game}")

    try:
        bet = _parse_line_without_diagnostics(value, game_name=game.name)
    except ParseError as exc:
        if not diagnostics:
            raise exc
        fallback_value = _sanitize_for_diagnostic_parse(value)
        try:
            bet = _parse_line_without_diagnostics(fallback_value, game_name=game.name)
        except ParseError:
            raise ParseError(diagnostics[0], errors=diagnostics) from exc

    return replace(
        bet,
        parse_errors=diagnostics if diagnostics else bet.parse_errors,
        original_text=normalized.original_text,
        normalized_text=normalized.normalized_text,
        parse_notes=normalized.parse_notes,
    )


def _parse_line_without_diagnostics(value: str, *, game_name: str) -> ParsedBet:
    if re.fullmatch(r"\s*0\.\d+\s*", value):
        raise ParseError("standalone amount line requires manual review")

    car_shorthand = _parse_confirmed_car_shorthand(value, game_name=game_name)
    if car_shorthand is not None:
        return car_shorthand

    if CAR_WORD in value:
        return _parse_car_line(value, game_name=game_name)

    shorthand = _parse_confirmed_shorthand_line(value, game_name=game_name)
    if shorthand is not None:
        return shorthand

    five_star_shorthand = _parse_five_number_thousand_shorthand(value, game_name=game_name)
    if five_star_shorthand is not None:
        return five_star_shorthand

    if not _should_parse_column_before_normal(value):
        return _parse_normal_line(value, game_name=game_name)

    column_bet = _parse_column_line(value, game_name=game_name)
    if column_bet is not None:
        return column_bet

    return _parse_normal_line(value, game_name=game_name)


def _should_parse_column_before_normal(value: str) -> bool:
    return (
        "/" in value
        or TOUCH_WORD in value
        or TAIL_WORD in value
        or bool(re.search(rf"(?<=\d)(?:[xX]|{MULTIPLY_SIGN})(?=\d)", value))
    )


def _input_diagnostics(text: str) -> list[str]:
    if _is_confirmed_car_shorthand_text(text):
        return []

    errors: list[str] = []
    unsupported = _unsupported_chinese_characters(text)
    if unsupported:
        errors.append(f"unsupported characters: {unsupported}")

    for number in _ambiguous_long_numbers(text):
        errors.append(f"ambiguous long number {number}")
    return _dedupe_strings(errors)


def _unsupported_chinese_characters(text: str) -> str:
    chars: list[str] = []
    for char in text:
        if "\u4e00" <= char <= "\u9fff" and char not in ALLOWED_CHINESE_CHARS:
            chars.append(char)
    return "".join(_dedupe_strings(chars))


def _ambiguous_long_numbers(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"\d{3,}", text):
        value = match.group(0)
        if _is_allowed_long_number(text, match):
            continue
        values.append(value)
    return _dedupe_strings(values)


def _is_allowed_long_number(text: str, match: re.Match[str]) -> bool:
    value = match.group(0)
    if value == "234":
        return True

    number = int(value)
    if number in COMMON_MONEY_VALUES:
        return True

    before = _previous_non_space(text, match.start())
    after = _next_non_space(text, match.end())
    if before in {CHINESE_TWO, CHINESE_ALT_TWO, CHINESE_THREE, CHINESE_FOUR, STAR_WORD} and number >= 50:
        return True
    if after in {YUAN_WORD, BLOCK_WORD, UNIT_WORD}:
        return True
    if before in {"/", "=", "x", "X", MULTIPLY_SIGN} and number >= 50:
        return True
    if match.end() == len(text.rstrip()) and number >= 50:
        return True
    return False


def _previous_non_space(text: str, index: int) -> str | None:
    pos = index - 1
    while pos >= 0 and text[pos].isspace():
        pos -= 1
    return text[pos] if pos >= 0 else None


def _next_non_space(text: str, index: int) -> str | None:
    pos = index
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return text[pos] if pos < len(text) else None


def _sanitize_for_diagnostic_parse(text: str) -> str:
    sanitized = "".join(
        " " if "\u4e00" <= char <= "\u9fff" and char not in ALLOWED_CHINESE_CHARS else char
        for char in text
    )
    for match in reversed(list(re.finditer(r"\d{3,}", sanitized))):
        if _is_allowed_long_number(sanitized, match):
            continue
        sanitized = sanitized[: match.start()] + " " + sanitized[match.end() :]
    return sanitized


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _parse_car_line(value: str, *, game_name: str) -> ParsedBet:
    parentheses = re.fullmatch(
        rf"\s*(?P<number>\d+)\s*[({FULL_OPEN_PAREN}]\s*"
        rf"(?P<units>\d+(?:\.\d+)?)\s*{CAR_WORD}\s*[){FULL_CLOSE_PAREN}]\s*",
        value,
    )
    if parentheses:
        number = int(parentheses.group("number"))
        car_units, money = _car_units_to_amount(parentheses.group("units"))
        return ParsedBet(
            game=game_name,
            type="car",
            numbers=[number],
            number=number,
            car_units=car_units,
            stars=[],
            unit=None,
            money=money,
        )

    before, after = value.split(CAR_WORD, 1)
    before_numbers = [int(token) for token in re.findall(r"\d+", before)]
    number = before_numbers[0] if len(before_numbers) == 1 and re.fullmatch(r"\s*\d+\s*", before) else None

    amount_match = re.fullmatch(
        rf"\s*(?P<value>\d+(?:\.\d+)?)?\s*(?P<kind>{AMOUNT_KIND_PATTERN}|{CAR_WORD})?\s*",
        after,
    )
    car_units: JsonNumber | None = None
    money: int | None = None
    if amount_match and amount_match.group("value") is not None:
        if amount_match.group("kind") in {YUAN_WORD, BLOCK_WORD}:
            car_units, money = _car_money_to_amount(amount_match.group("value"))
        elif amount_match.group("kind") in {UNIT_WORD, CAR_WORD}:
            car_units, money = _car_units_to_amount(amount_match.group("value"))
        elif _looks_like_bare_car_money(amount_match.group("value")):
            car_units, money = _car_money_to_amount(amount_match.group("value"))
        else:
            car_units, money = _car_units_to_amount(amount_match.group("value"))

    return ParsedBet(
        game=game_name,
        type="car",
        numbers=[number] if number is not None else before_numbers,
        number=number,
        car_units=car_units,
        stars=[],
        unit=None,
        money=money,
    )


def _car_units_to_amount(value: str) -> tuple[JsonNumber, int]:
    units = Decimal(value)
    return _number_for_json(units), _money_from_unit(units)


def _car_money_to_amount(value: str) -> tuple[JsonNumber, int]:
    money = _money_from_decimal(Decimal(value))
    return _number_for_json(Decimal(money) / Decimal("100")), money


def _looks_like_bare_car_money(value: str) -> bool:
    decimal_value = Decimal(value)
    return decimal_value == decimal_value.to_integral_value() and decimal_value >= Decimal("10")


def _parse_confirmed_car_shorthand(value: str, *, game_name: str) -> ParsedBet | None:
    text = value.strip()

    half_car = re.fullmatch(
        rf"(?P<number>\d{{1,2}})\s*{HALF_WORD}\s*{CAR_WORD}\s*(?:{PING_WORD})?",
        text,
    )
    if half_car:
        return _car_bet_from_unit(game_name, half_car.group("number"), Decimal("0.5"))

    full_car = re.fullmatch(
        rf"(?P<number>\d{{1,2}})\s*{FULL_WORD}\s*{CAR_WORD}\s*(?P<unit>\d+(?:\.\d+)?)\s*",
        text,
    )
    if full_car:
        return _car_bet_from_unit(game_name, full_car.group("number"), Decimal(full_car.group("unit")))

    slash_car = re.fullmatch(
        rf"(?P<number>\d{{1,2}})\s*/\s*(?P<unit>\d+(?:\.\d+)?)\s*{CAR_WORD}\s*(?:{SUSPECT_WORD})?",
        text,
    )
    if slash_car:
        return _car_bet_from_unit(game_name, slash_car.group("number"), Decimal(slash_car.group("unit")))

    dot_car = re.fullmatch(
        rf"(?P<number>\d{{1,2}})\.(?P<unit>0\.\d+|\d+)\s*{CAR_WORD}\s*",
        text,
    )
    if dot_car:
        return _car_bet_from_unit(game_name, dot_car.group("number"), Decimal(dot_car.group("unit")))

    operator_car = re.fullmatch(
        rf"(?P<number>\d{{1,2}})\s*(?P<op>-|[xX{MULTIPLY_SIGN}*])\s*(?P<unit>\d+(?:\.\d+)?)\s*",
        text,
    )
    if operator_car and _is_confirmed_single_number_car_operator(operator_car.group("op"), operator_car.group("unit")):
        return _car_bet_from_unit(game_name, operator_car.group("number"), Decimal(operator_car.group("unit")))

    return None


def _is_confirmed_car_shorthand_text(text: str) -> bool:
    value = text.strip()
    if re.fullmatch(rf"\d{{1,2}}\s*{HALF_WORD}\s*{CAR_WORD}\s*(?:{PING_WORD})?", value):
        return True
    if re.fullmatch(rf"\d{{1,2}}\s*{FULL_WORD}\s*{CAR_WORD}\s*\d+(?:\.\d+)?\s*", value):
        return True
    if re.fullmatch(rf"\d{{1,2}}\s*/\s*\d+(?:\.\d+)?\s*{CAR_WORD}\s*(?:{SUSPECT_WORD})?", value):
        return True
    operator = re.fullmatch(
        rf"\d{{1,2}}\s*(?P<op>-|[xX{MULTIPLY_SIGN}*])\s*(?P<unit>\d+(?:\.\d+)?)\s*",
        value,
    )
    return bool(operator and _is_confirmed_single_number_car_operator(operator.group("op"), operator.group("unit")))


def _is_confirmed_single_number_car_operator(op: str, unit_text: str) -> bool:
    unit = Decimal(unit_text)
    if op == "-":
        return unit < Decimal("1")
    return unit > Decimal("0")


def _car_bet_from_unit(game_name: str, number_text: str, unit: Decimal) -> ParsedBet:
    car_units, money = _car_units_to_amount(str(unit))
    number = int(number_text)
    return ParsedBet(
        game=game_name,
        type="car",
        numbers=[number],
        number=number,
        car_units=car_units,
        stars=[],
        unit=None,
        money=money,
    )


def _parse_normal_line(value: str, *, game_name: str) -> ParsedBet:
    if _is_customer_specific_two_number_shorthand(value):
        raise ParseError("customer-specific shorthand requires manual review")

    shorthand = _parse_confirmed_shorthand_line(value, game_name=game_name)
    if shorthand is not None:
        return shorthand

    game = ACTIVE_GAMES[game_name]
    has_per_star_bets = _has_per_star_bet_sequence(value)
    stars: list[int] = []
    amount: BetAmount | None = None
    if not has_per_star_bets:
        value, stars, amount = _peel_star_amount_suffix(value)
        if amount is None:
            value, amount = _peel_tail_amount(value)

    if not stars and not has_per_star_bets:
        value, stars = _peel_star_suffix(value, allow_numeric=amount is not None)

    numbers, remainder = _consume_numbers(value)
    if not numbers:
        raise ParseError("missing numbers")

    bets: dict[str, BetAmount] = {}
    remainder = remainder.strip()
    if stars and remainder:
        raise ParseError("unsupported or unclear betting format")

    if remainder:
        per_star = _parse_per_star_bets(remainder, allow_single_group=len(numbers) >= 5)
        if per_star is not None:
            stars, bets = per_star
            if amount is not None:
                raise ParseError("cannot mix total amount and per-star amounts")
        else:
            if amount is None and _starts_with_long_bare_amount(remainder):
                amount, remainder = _parse_amount_from_start(remainder, bare_small_as_money=True)
            else:
                parsed_stars, remainder = _parse_stars_from_start(remainder)
                if parsed_stars:
                    stars = parsed_stars
            remainder = remainder.strip()
            if amount is None and remainder:
                amount, remainder = _parse_amount_from_start(
                    remainder,
                    bare_small_as_money=len(numbers) >= 3 and not _single_digit_unit_after_explicit_stars(stars, remainder),
                )
            if remainder.strip():
                raise ParseError("unsupported or unclear betting format")

    if not stars:
        stars = game.default_stars(len(numbers))

    if amount is None and bets:
        amount = _shared_bet_amount(bets)

    unit = amount.unit if amount is not None else None
    money = amount.money if amount is not None else None

    return ParsedBet(
        game=game.name,
        type="normal",
        numbers=numbers,
        stars=stars,
        unit=unit,
        money=money,
        bets=bets,
    )


def _shared_bet_amount(bets: dict[str, BetAmount]) -> BetAmount | None:
    amounts = list(bets.values())
    if not amounts:
        return None
    first = amounts[0]
    if all(amount.unit == first.unit and amount.money == first.money for amount in amounts):
        return first
    return None


def _parse_confirmed_shorthand_line(value: str, *, game_name: str) -> ParsedBet | None:
    slash_x = re.fullmatch(r"\s*(?P<a>\d{1,2})-(?P<b>\d{1,2})\s*/\s*[xX]\s*(?P<unit>\d+(?:\.\d+)?)\s*", value)
    if slash_x:
        unit = Decimal(slash_x.group("unit"))
        amount = BetAmount(unit=_number_for_json(unit), money=_money_from_unit(unit))
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=[int(slash_x.group("a")), int(slash_x.group("b"))],
            stars=[2],
            unit=amount.unit,
            money=amount.money,
        )

    colon_x = re.fullmatch(r"\s*(?P<a>\d{1,2})-(?P<b>\d{1,2})\s*:\s*[xX]\s*(?P<unit>\d+(?:\.\d+)?)\s*", value)
    if colon_x:
        unit = Decimal(colon_x.group("unit"))
        amount = BetAmount(unit=_number_for_json(unit), money=_money_from_unit(unit))
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=[int(colon_x.group("a")), int(colon_x.group("b"))],
            stars=[2],
            unit=amount.unit,
            money=amount.money,
        )

    star_equals_amount = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}}){{2,}})"
        rf"\s*[({FULL_OPEN_PAREN}]?\s*(?P<stars>234|23|34)"
        rf"\s*=\s*(?P<amount>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*[){FULL_CLOSE_PAREN}]?\s*",
        value,
    )
    if star_equals_amount:
        numbers = [int(token) for token in re.findall(r"\d{1,2}", star_equals_amount.group("numbers"))]
        stars = _stars_from_token(star_equals_amount.group("stars"))
        amount = _amount_after_star(
            star_equals_amount.group("amount"),
            star_equals_amount.group("kind"),
            "=",
        )
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=numbers,
            stars=stars,
            unit=amount.unit,
            money=amount.money,
        )

    confirmed_equals_amount = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}}){{2,}})"
        rf"\s*=\s*(?P<amount>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*",
        value,
    )
    if confirmed_equals_amount:
        numbers = [int(token) for token in re.findall(r"\d{1,2}", confirmed_equals_amount.group("numbers"))]
        amount = _amount_from_parts(
            confirmed_equals_amount.group("amount"),
            confirmed_equals_amount.group("kind"),
            source="bare_money",
        )
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=numbers,
            stars=ACTIVE_GAMES[game_name].default_stars(len(numbers)),
            unit=amount.unit,
            money=amount.money,
        )

    explicit_star_amount = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}})+)"
        rf"\.?\s*=\s*(?P<stars>[234](?:[.{COMMA_WORD},{FULL_COMMA}]\s*[234]){{0,2}})"
        rf"\s*=\s*(?P<amount>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*",
        value,
    )
    if explicit_star_amount:
        numbers = [int(token) for token in re.findall(r"\d{1,2}", explicit_star_amount.group("numbers"))]
        stars = _stars_from_token(explicit_star_amount.group("stars"))
        amount = _amount_from_parts(
            explicit_star_amount.group("amount"),
            explicit_star_amount.group("kind"),
            source="=",
        )
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=numbers,
            stars=stars,
            unit=amount.unit,
            money=amount.money,
        )

    confirmed_hyphen_amount = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}})+)"
        rf"\s*-\s*(?P<amount>50|100|200|500|1000|1500)\s*",
        value,
    )
    if confirmed_hyphen_amount:
        numbers = [int(token) for token in re.findall(r"\d{1,2}", confirmed_hyphen_amount.group("numbers"))]
        amount = _amount_from_parts(confirmed_hyphen_amount.group("amount"), None, source="/")
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=numbers,
            stars=ACTIVE_GAMES[game_name].default_stars(len(numbers)),
            unit=amount.unit,
            money=amount.money,
        )

    confirmed_spaced_decimal_hyphen = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}})+)"
        rf"\s+-\s*(?P<amount>0?\.\d+)\s*",
        value,
    )
    if confirmed_spaced_decimal_hyphen:
        numbers = [int(token) for token in re.findall(r"\d{1,2}", confirmed_spaced_decimal_hyphen.group("numbers"))]
        amount = _amount_from_parts(confirmed_spaced_decimal_hyphen.group("amount"), UNIT_WORD, source="star")
        return ParsedBet(
            game=game_name,
            type="normal",
            numbers=numbers,
            stars=ACTIVE_GAMES[game_name].default_stars(len(numbers)),
            unit=amount.unit,
            money=amount.money,
        )

    x_code_shorthand = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}})+)"
        rf"\s*[xX]\s*(?P<code>320|640|440|880)\s*",
        value,
    )
    if x_code_shorthand:
        number_tokens = re.findall(r"\d{1,2}", x_code_shorthand.group("numbers"))
        code_map = {
            (3, "320"): ([2, 3], Decimal("1")),
            (3, "640"): ([2, 3], Decimal("2")),
            (4, "440"): ([2, 3, 4], Decimal("0.5")),
            (4, "880"): ([2, 3, 4], Decimal("1")),
        }
        code_match = code_map.get((len(number_tokens), x_code_shorthand.group("code")))
        if code_match is not None:
            stars, unit = code_match
            amount = BetAmount(unit=_number_for_json(unit), money=_money_from_unit(unit))
            return ParsedBet(
                game=game_name,
                type="normal",
                numbers=[int(token) for token in number_tokens],
                stars=stars,
                unit=amount.unit,
                money=amount.money,
            )

    separator_shorthand = re.fullmatch(
        rf"\s*(?P<numbers>\d{{1,2}}(?:[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}}){{2,3}})"
        rf"\s*:\s*(?P<code>440|880)\s*",
        value,
    )
    if separator_shorthand:
        number_tokens = re.findall(r"\d{1,2}", separator_shorthand.group("numbers"))
        if len(number_tokens) == 4:
            unit = Decimal("0.5") if separator_shorthand.group("code") == "440" else Decimal("1")
            amount = BetAmount(unit=_number_for_json(unit), money=_money_from_unit(unit))
            return ParsedBet(
                game=game_name,
                type="normal",
                numbers=[int(token) for token in number_tokens],
                stars=[2, 3, 4],
                unit=amount.unit,
                money=amount.money,
            )

    tokens = [token for token in re.split(rf"[.\s,{FULL_COMMA}{COMMA_WORD}-]+", value.strip()) if token]
    if len(tokens) not in {4, 5} or not all(token.isdigit() for token in tokens):
        return None

    code = tokens[-1]
    number_tokens = tokens[:-1]
    shorthand_map = {
        (3, "100"): ([2, 3], Decimal("1")),
        (3, "320"): ([2, 3], Decimal("1")),
        (3, "640"): ([2, 3], Decimal("2")),
        (4, "440"): ([2, 3, 4], Decimal("0.5")),
        (4, "880"): ([2, 3, 4], Decimal("1")),
    }
    match = shorthand_map.get((len(number_tokens), code))
    if match is None:
        return None

    if any(len(token) > 2 for token in number_tokens):
        return None

    stars, unit = match
    amount = BetAmount(unit=_number_for_json(unit), money=_money_from_unit(unit))
    return ParsedBet(
        game=game_name,
        type="normal",
        numbers=[int(token) for token in number_tokens],
        stars=stars,
        unit=amount.unit,
        money=amount.money,
    )


def _parse_five_number_thousand_shorthand(value: str, *, game_name: str) -> ParsedBet | None:
    """Detect 5-number + 1000/2000 shorthand: 09 15 22 27 33 1000 → 二三四星, unit=0.5."""
    m = re.fullmatch(
        r"\s*(?P<n1>\d{1,2})\s+(?P<n2>\d{1,2})\s+(?P<n3>\d{1,2})\s+(?P<n4>\d{1,2})\s+(?P<n5>\d{1,2})\s+(?P<th>1000|2000)\s*",
        value,
    )
    if not m:
        return None
    numbers = [int(m.group(f"n{i}")) for i in range(1, 6)]
    # All numbers must be valid (1-39), no duplicates
    if len(set(numbers)) != 5:
        return None
    for n in numbers:
        if n < 1 or n > 39:
            return None
    th = m.group("th")
    # 1000 → 0.5 unit, 50 per star; 2000 → 1 unit, 100 per star
    if th == "1000":
        unit_val = 0.5
        per_star_money = 50
    else:
        unit_val = 1
        per_star_money = 100
    unit = _number_for_json(Decimal(str(unit_val)))
    amount = BetAmount(unit=unit, money=_money_from_unit(Decimal(str(unit_val))))
    stars = [2, 3, 4]
    bets_per_star = BetAmount(unit=_number_for_json(Decimal(str(unit_val))), money=per_star_money)
    amounts = {str(s): bets_per_star for s in stars}
    return ParsedBet(
        game=game_name,
        type="normal",
        numbers=numbers,
        stars=stars,
        unit=amount.unit,
        money=amount.money,
        bets=amounts,
        parse_notes=[f"5-number {th} shorthand → 二三四星, unit={unit_val}"],
    )


def _parse_column_line(value: str, *, game_name: str) -> ParsedBet | None:
    working, stars, amount = _peel_attached_column_star_amount_suffix(value)
    if amount is None:
        working, stars, amount = _peel_star_amount_suffix(value)
    if amount is None:
        working, amount = _peel_tail_amount(working)
    if amount is None:
        working, stars, amount = _peel_column_tail_star_amount(value)

    if not stars:
        working, stars = _peel_star_suffix(working, allow_numeric=amount is not None)
    if not stars and amount is not None:
        working, stars = _peel_slash_star_suffix(working)

    columns = _parse_column_parts(working)
    if columns is None:
        return None

    numbers = [number for column in columns for number in column]
    unit = amount.unit if amount is not None else None
    money = amount.money if amount is not None else None

    return ParsedBet(
        game=game_name,
        type="column",
        numbers=numbers,
        columns=columns,
        stars=stars,
        unit=unit,
        money=money,
    )


def _peel_slash_star_suffix(text: str) -> tuple[str, list[int]]:
    match = re.fullmatch(rf"(?P<prefix>.+)/(?P<star>234|23|34|[234]{STAR_WORD}|[234]{{2,3}}{STAR_WORD})\s*", text)
    if not match:
        return text, []
    return match.group("prefix").strip(), _stars_from_token(match.group("star"))


def _parse_column_parts(text: str) -> list[list[int]] | None:
    value = text.strip()
    shorthand = _parse_tail_shorthand(value)
    if shorthand is not None:
        return shorthand

    if not _has_column_separator(value):
        return None

    raw_parts = COLUMN_SPLIT_PATTERN.split(value)
    if len(raw_parts) < 2:
        return None

    columns = [_parse_column_numbers(part) for part in raw_parts]
    return columns


def _has_column_separator(text: str) -> bool:
    return (
        "/" in text
        or TOUCH_WORD in text
        or MULTIPLY_SIGN in text
        or TAIL_WORD in text
        or bool(re.search(r"(?<=\d)\s*[xX]\s*(?=\d)", text))
    )


def _parse_flat_slash_dunhao_column_group(text: str) -> list[int] | None:
    if "/" not in text or COMMA_WORD not in text:
        return None
    if re.fullmatch(r"\d{1,2}(?:/\d{1,2})+、\d{1,2}(?:、\d{1,2})*", text.strip()):
        return [int(token) for token in re.findall(r"\d{1,2}", text)]
    return None


def _parse_tail_shorthand(text: str) -> list[list[int]] | None:
    value = text.strip()

    # Format: 尾2-9, 尾2,9, etc.
    if value.startswith(TAIL_WORD):
        body = value[len(TAIL_WORD):].strip()
        if body:
            parts = [part for part in TAIL_SHORTHAND_SPLIT_PATTERN.split(body) if part]
            if len(parts) >= 2:
                columns: list[list[int]] = []
                for part in parts:
                    if not re.fullmatch(r"\d", part):
                        raise ParseError("tail must be between 0 and 9")
                    columns.append(expand_tail(int(part)))
                return columns

    # Format: 2尾碰9尾, 2尾-9尾
    tail_match = re.fullmatch(
        rf"(\d){TAIL_WORD}\s*(?:碰|[-,\s])\s*(\d){TAIL_WORD}", value
    )
    if tail_match:
        return [expand_tail(int(tail_match.group(1))), expand_tail(int(tail_match.group(2)))]

    return None


def _parse_column_numbers(text: str) -> list[int]:
    value = text.strip()
    if not value:
        return []

    tail = _parse_tail_column(value)
    if tail is not None:
        return tail

    numbers: list[int] = []
    index = 0
    length = len(value)
    while index < length:
        while index < length and value[index] in COLUMN_INNER_DELIMITERS:
            index += 1
        if index >= length:
            break

        match = re.match(r"\d+", value[index:])
        if not match:
            raise ParseError("unsupported or unclear column format")
        token = match.group(0)
        if len(token) > 2:
            if len(token) % 2 != 0:
                raise ParseError("unsupported or unclear column format")
            for k in range(0, len(token), 2):
                numbers.append(int(token[k:k+2]))
            index += len(token)
            continue
        numbers.append(int(token))
        index += len(token)

        if index < length and value[index] not in COLUMN_INNER_DELIMITERS:
            raise ParseError("unsupported or unclear column format")

    return numbers


def _parse_tail_column(text: str) -> list[int] | None:
    match = re.fullmatch(rf"(?:(?P<prefix>{TAIL_WORD})(?P<prefix_tail>\d)|(?P<suffix_tail>\d){TAIL_WORD})", text)
    if not match:
        return None

    tail = match.group("prefix_tail") or match.group("suffix_tail")
    return expand_tail(int(tail))


def _remove_game_markers(text: str) -> str:
    return GAME_MARKER_PATTERN.sub(" ", text).strip()


def _is_standalone_star_amount_line(text: str) -> bool:
    return bool(
        re.fullmatch(
            rf"\s*(?:[234](?:\.[234]){{1,2}}|[234]{{2,3}}{STAR_WORD}?)"
            rf"\.?\s*[xX{MULTIPLY_SIGN}*]\s*\d+(?:\.\d+)?(?:{AMOUNT_KIND_PATTERN})?"
            rf"\s*(?:539)?\s*",
            text,
        )
    )


def _is_customer_specific_two_number_shorthand(text: str) -> bool:
    return bool(
        re.fullmatch(
            rf"\s*\d{{1,2}}[.\- {COMMA_WORD},{FULL_COMMA}]+\d{{1,2}}"
            rf"[.\- {COMMA_WORD},{FULL_COMMA}]+(?:1000|600|400)\s*",
            text,
        )
    )


def _peel_star_amount_suffix(text: str) -> tuple[str, list[int], BetAmount | None]:
    numeric_star_amount = re.fullmatch(
        rf"(?P<prefix>.+\d)(?P<before>\s+|[./]+)"
        rf"(?P<star>234|23|34|[234]{{2,3}}{STAR_WORD})"
        rf"\s*(?P<sep>[./=]|[*xX{MULTIPLY_SIGN}])\s*"
        rf"(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*",
        text,
    )
    if numeric_star_amount:
        if numeric_star_amount.group("sep") == "/" and _compact_star_token(numeric_star_amount.group("star")) != "234":
            return text, [], None
        return (
            numeric_star_amount.group("prefix").strip(),
            _stars_from_token(numeric_star_amount.group("star")),
            _amount_after_star(
                numeric_star_amount.group("value"),
                numeric_star_amount.group("kind"),
                numeric_star_amount.group("sep"),
                prefix=numeric_star_amount.group("prefix"),
            ),
        )

    pattern = re.compile(
        rf"^(?P<prefix>.+)(?P<before>\s+|[./]+|(?<=\d))(?P<star>{_star_token_source(include_numeric=True)})"
        rf"(?P<sep>[./=]|\s*[*xX{MULTIPLY_SIGN}]\s*|\s+)?"
        rf"(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*$"
    )
    match = pattern.match(text)
    if not match:
        return text, [], None

    if not _valid_star_amount_suffix(match):
        return text, [], None

    return (
        match.group("prefix").strip(),
        _stars_from_token(match.group("star")),
        _amount_after_star(
            match.group("value"),
            match.group("kind"),
            match.group("sep"),
            prefix=match.group("prefix"),
        ),
    )


def _valid_star_amount_suffix(match: re.Match[str]) -> bool:
    star = _compact_star_token(match.group("star"))
    sep = (match.group("sep") or "").strip()
    before = match.group("before") or ""
    token = match.group("star")

    if sep == "/" and star != "234":
        return False
    if sep == "/" and not match.group("value").isdigit():
        return False
    if (
        token.isdigit()
        and len(star) == 1
        and sep == ""
        and any(char in before for char in "./")
    ):
        return False
    if token.isdigit() and len(star) == 1 and sep == "" and before.isspace() and len(match.group("value")) >= 2:
        return False
    if before == "" and token[0].isdigit():
        return False
    return True


def _compact_star_token(token: str) -> str:
    cleaned = token.replace(STAR_WORD, "")
    cleaned = re.sub(rf"[.,{FULL_COMMA}{COMMA_WORD}\s]+", "", cleaned)
    return (
        cleaned.replace(CHINESE_TWO, "2")
        .replace("两", "2")
        .replace(CHINESE_ALT_TWO, "2")
        .replace(CHINESE_THREE, "3")
        .replace(CHINESE_FOUR, "4")
    )


def _amount_after_star(value: str, kind: str | None, sep: str | None, *, prefix: str | None = None) -> BetAmount:
    raw_sep = sep
    sep = sep.strip() if sep is not None else None
    if (
        kind is None
        and sep in {"x", "X", MULTIPLY_SIGN, "*"}
        and prefix is not None
        and _count_prefix_numbers(prefix) >= 3
        and Decimal(value) >= Decimal("10")
    ):
        return _amount_from_parts(value, None, source="bare_money")
    if kind == UNIT_WORD or sep in {"x", "X", MULTIPLY_SIGN, "*"} or "." in value:
        return _amount_from_parts(value, UNIT_WORD, source="star")
    if kind in {YUAN_WORD, BLOCK_WORD}:
        return _amount_from_parts(value, kind, source="star")
    if sep == "=":
        return _amount_from_parts(value, None, source="=")
    if raw_sep and raw_sep.isspace() and Decimal(value) > Decimal("10"):
        return _amount_from_parts(value, None, source="bare_money")
    if _is_common_money(Decimal(value)):
        return _amount_from_parts(value, None, source="/")
    return _amount_from_parts(value, UNIT_WORD, source="star")


def _count_prefix_numbers(prefix: str) -> int:
    return len(re.findall(r"\d{1,2}", prefix))


def _peel_attached_column_star_amount_suffix(text: str) -> tuple[str, list[int], BetAmount | None]:
    if "/" not in text:
        return text, [], None
    pattern = re.compile(
        rf"^(?P<prefix>.+\d)(?P<star>{_star_token_source(include_numeric=True)})"
        rf"\s*(?P<sep>[*xX{MULTIPLY_SIGN}])\s*"
        rf"(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?\s*$"
    )
    match = pattern.match(text)
    if not match:
        return text, [], None
    star_start = match.start("star")
    if star_start > 0 and text[star_start - 1].isspace():
        return text, [], None
    if match.group("star")[0].isdigit():
        return text, [], None
    if not all("." in part for part in match.group("prefix").split("/")):
        return text, [], None
    return (
        match.group("prefix").strip(),
        _stars_from_token(match.group("star")),
        _amount_after_star(match.group("value"), match.group("kind"), match.group("sep")),
    )


def _peel_tail_amount(text: str) -> tuple[str, BetAmount | None]:
    x_match = X_AMOUNT_PATTERN.search(text)
    if x_match:
        amount = _amount_from_parts(x_match.group("value"), UNIT_WORD, source="x")
        return text[: x_match.start()].strip(), amount

    match = TAIL_OPERATOR_AMOUNT_PATTERN.search(text)
    if match:
        op = match.group("op")
        value = match.group("value")
        kind = match.group("kind")
        if op != "/" or _looks_like_slash_amount(value, kind):
            amount = _amount_from_parts(value, kind, source="/" if op == ":" else op)
            return text[: match.start()].strip(), amount

    bare_match = BARE_TAIL_AMOUNT_PATTERN.search(text)
    if not bare_match:
        return text, None

    value = bare_match.group("value")
    kind = bare_match.group("kind")
    if _is_per_star_tail(text, bare_match.start()):
        return text, None
    if kind is None and not _looks_like_bare_tail_amount(value):
        return text, None

    amount = _amount_from_parts(value, kind, source="bare")
    return text[: bare_match.start()].strip(), amount


def _peel_column_tail_star_amount(text: str) -> tuple[str, list[int], BetAmount | None]:
    """Peel star+amount from column tail, e.g., 2尾碰9尾二50 → (2尾碰9尾, [2], BetAmount(50))."""
    match = re.fullmatch(
        rf"(?P<prefix>.+{TAIL_WORD}.+)"
        rf"(?P<star>二|三|四|兩|[234])"
        rf"\s*(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?",
        text,
    )
    if match:
        stars = _stars_from_token(match.group("star"))
        amount = _amount_from_parts(match.group("value"), match.group("kind"), source="star")
        return match.group("prefix").strip(), stars, amount
    return text, [], None


def _looks_like_slash_amount(value: str, kind: str | None) -> bool:
    if kind is not None or "." in value:
        return True
    number = int(value)
    return number > 39 or number in COMMON_MONEY_VALUES


def _looks_like_bare_tail_amount(value: str) -> bool:
    number = int(value)
    return number >= 50 or number in COMMON_MONEY_VALUES


def _is_per_star_tail(text: str, start: int) -> bool:
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return index >= 0 and text[index] in f"{STAR_WORD}{CHINESE_TWO}{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}"


def _peel_star_suffix(text: str, *, allow_numeric: bool) -> tuple[str, list[int]]:
    for pattern in _star_suffix_patterns(allow_numeric=allow_numeric):
        match = pattern.match(text)
        if match:
            return match.group("prefix").strip(), _stars_from_token(match.group("star"))
    return text, []


def _star_suffix_patterns(*, allow_numeric: bool) -> list[re.Pattern[str]]:
    star_sources = [_star_token_source(include_numeric=False)]
    if allow_numeric:
        star_sources.append(_star_token_source(include_numeric=True))
    return [
        re.compile(rf"^(?P<prefix>.+?)\s+(?P<star>{source})\s*$")
        for source in star_sources
    ]


def _star_token_source(*, include_numeric: bool) -> str:
    chinese = (
        rf"{CHINESE_TWO}{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}{COMMA_WORD}{CHINESE_THREE}{COMMA_WORD}{CHINESE_FOUR}|"
        rf"{CHINESE_ALT_TWO}{COMMA_WORD}{CHINESE_THREE}{COMMA_WORD}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}{COMMA_WORD}{CHINESE_THREE}|"
        rf"{CHINESE_ALT_TWO}{COMMA_WORD}{CHINESE_THREE}|"
        rf"{CHINESE_THREE}{COMMA_WORD}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}{CHINESE_THREE}|"
        rf"{CHINESE_ALT_TWO}{CHINESE_THREE}|"
        rf"{CHINESE_THREE}{CHINESE_FOUR}|"
        rf"{CHINESE_TWO}{STAR_WORD}|{CHINESE_ALT_TWO}{STAR_WORD}|"
        rf"{CHINESE_THREE}{STAR_WORD}|{CHINESE_FOUR}{STAR_WORD}|"
        rf"{CHINESE_TWO}|{CHINESE_ALT_TWO}|{CHINESE_THREE}|{CHINESE_FOUR}|[234]{STAR_WORD}"
    )
    if not include_numeric:
        return chinese
    return (
        rf"{chinese}|"
        rf"[234](?:\.[234])(?:\.[234])?|"
        rf"[234]{{2,3}}{STAR_WORD}|"
        rf"[234]\s*[,{FULL_COMMA}{COMMA_WORD}]\s*[234]\s*[,{FULL_COMMA}{COMMA_WORD}]\s*[234]|"
        rf"[234]\s+[234]\s+[234]|"
        rf"[234]\s*[,{FULL_COMMA}{COMMA_WORD}]\s*[234]|"
        rf"[234]\s+[234]|234|23|34|[234]"
    )


def _consume_numbers(text: str) -> tuple[list[int], str]:
    numbers: list[int] = []
    index = 0
    length = len(text)

    while index < length:
        while index < length and text[index] in NUMBER_DELIMITERS:
            index += 1

        match = re.match(r"\d+", text[index:])
        if not match:
            break

        token = match.group(0)
        end = index + len(token)
        if len(token) > 2:
            break
        if end < length and text[end] in f"{YUAN_WORD}{BLOCK_WORD}{UNIT_WORD}":
            break

        numbers.append(int(token))
        index = end

        if index >= length or text[index] not in NUMBER_DELIMITERS:
            break

    return numbers, text[index:]


def _parse_per_star_bets(
    remainder: str,
    *,
    allow_single_group: bool = False,
) -> tuple[list[int], dict[str, BetAmount]] | None:
    value = remainder.strip()
    if not value:
        return None

    stars: list[int] = []
    bets: dict[str, BetAmount] = {}
    group_count = 0
    single_group_had_space_before_amount = False
    index = 0
    length = len(value)

    while index < length:
        while index < length and value[index] in f".,{FULL_COMMA}{COMMA_WORD} \t":
            index += 1
        if index >= length:
            break

        token_start = index
        while index < length and not value[index].isdigit():
            index += 1
        raw_token = value[token_start:index]
        token = raw_token.strip(f".,{FULL_COMMA}{COMMA_WORD} \t")
        if not token:
            return None

        try:
            star_values = _stars_from_token(token)
        except ParseError:
            if group_count > 0:
                raise
            return None

        amount_match = re.match(rf"(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?", value[index:])
        if not amount_match:
            raise ParseError(f"missing money for star {token}")

        amount = _amount_from_parts(
            amount_match.group("value"),
            amount_match.group("kind"),
            source="per-star",
            include_unit_for_money=False,
        )
        for star in star_values:
            if str(star) in bets:
                raise ParseError(f"duplicate amount for star {star}")
            stars.append(star)
            bets[str(star)] = amount
        group_count += 1
        if group_count == 1:
            single_group_had_space_before_amount = bool(raw_token and raw_token[-1].isspace())
        index += amount_match.end()

    if group_count == 0:
        return None
    if group_count == 1 and not allow_single_group:
        return None
    if group_count == 1 and single_group_had_space_before_amount:
        return None

    return _dedupe_preserve_order(stars), bets


def _has_per_star_bet_sequence(text: str) -> bool:
    numbers, remainder = _consume_numbers(text)
    if not numbers:
        return len(list(PER_STAR_AMOUNT_PATTERN.finditer(text))) >= 2
    return _looks_like_star_amount_remainder(remainder.strip(), allow_single_group=len(numbers) >= 5)


def _looks_like_star_amount_remainder(remainder: str, *, allow_single_group: bool = True) -> bool:
    if not remainder:
        return False
    try:
        return _parse_per_star_bets(remainder, allow_single_group=allow_single_group) is not None
    except ParseError:
        return bool(re.search(rf"[{CHINESE_TWO}{CHINESE_ALT_TWO}{CHINESE_THREE}{CHINESE_FOUR}234].*\d", remainder))


def _parse_stars_from_start(remainder: str) -> tuple[list[int], str]:
    value = remainder.lstrip()
    for pattern in _star_start_patterns():
        match = pattern.match(value)
        if match:
            token = match.group(0)
            return _stars_from_token(token), value[match.end() :]
    return [], remainder


def _starts_with_long_bare_amount(remainder: str) -> bool:
    return bool(re.match(r"\s*\d{3,}(?:元|塊)?(?:\s*$)", remainder))


def _single_digit_unit_after_explicit_stars(stars: list[int], remainder: str) -> bool:
    if not stars:
        return False
    return bool(re.fullmatch(r"\s*[1-9]\s*", remainder))


def _star_start_patterns() -> list[re.Pattern[str]]:
    return [
        re.compile(_star_token_source(include_numeric=True)),
    ]


def _stars_from_token(token: str) -> list[int]:
    translated = _compact_star_token(token)
    stars = [int(char) for char in translated if char in "234"]
    if not stars or len(stars) != len(translated):
        raise ParseError(f"unknown star token: {token}")
    return _dedupe_preserve_order(stars)


def _parse_amount_from_start(
    remainder: str,
    *,
    bare_small_as_money: bool = False,
) -> tuple[BetAmount, str]:
    value = remainder.lstrip()
    x_match = re.match(rf"[xX]\s*(?P<value>\d+(?:\.\d+)?)(?:\s*{UNIT_WORD})?", value)
    if x_match:
        return _amount_from_parts(x_match.group("value"), UNIT_WORD, source="x"), value[x_match.end() :]

    match = re.match(rf"(?P<value>\d+(?:\.\d+)?)(?P<kind>{AMOUNT_KIND_PATTERN})?", value)
    if not match:
        raise ParseError("missing or unclear amount")
    source = "bare_money" if bare_small_as_money and match.group("kind") is None else "bare"
    return (
        _amount_from_parts(match.group("value"), match.group("kind"), source=source),
        value[match.end() :],
    )


def _amount_from_parts(
    value: str,
    kind: str | None,
    *,
    source: str,
    include_unit_for_money: bool = True,
) -> BetAmount:
    if "." in value and kind is None and source == "bare":
        raise ParseError("bare decimal amount is not allowed")

    decimal_value = Decimal(value)
    if decimal_value <= 0:
        return BetAmount(unit=_number_for_json(decimal_value), money=0)

    if kind == UNIT_WORD or source == "x":
        unit = _number_for_json(decimal_value)
        return BetAmount(unit=unit, money=_money_from_unit(decimal_value))

    if kind in {YUAN_WORD, BLOCK_WORD}:
        money = _money_from_decimal(decimal_value)
        unit = _number_for_json(decimal_value / Decimal("100")) if include_unit_for_money else None
        return BetAmount(unit=unit, money=money)

    if source == "/":
        money = _money_from_decimal(decimal_value)
        return BetAmount(unit=_number_for_json(decimal_value / Decimal("100")), money=money)

    if source == "bare_money":
        money = _money_from_decimal(decimal_value)
        return BetAmount(unit=_number_for_json(decimal_value / Decimal("100")), money=money)

    if source == "per-star" and kind is None and (decimal_value >= Decimal("10") or _is_common_money(decimal_value)):
        money = _money_from_decimal(decimal_value)
        return BetAmount(unit=_number_for_json(decimal_value / Decimal("100")), money=money)

    if source == "=":
        if _is_common_money(decimal_value):
            money = _money_from_decimal(decimal_value)
            return BetAmount(unit=_number_for_json(decimal_value / Decimal("100")), money=money)
        return BetAmount(
            unit=_number_for_json(decimal_value),
            money=_money_from_unit(decimal_value),
        )

    if _is_common_money(decimal_value):
        money = _money_from_decimal(decimal_value)
        return BetAmount(unit=_number_for_json(decimal_value / Decimal("100")), money=money)

    return BetAmount(
        unit=_number_for_json(decimal_value),
        money=_money_from_unit(decimal_value),
    )


def _is_common_money(value: Decimal) -> bool:
    if value != value.to_integral_value():
        return False
    integer = int(value)
    return integer in COMMON_MONEY_VALUES or integer >= 50


def _money_from_unit(unit: Decimal) -> int:
    return int((unit * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _money_from_decimal(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _number_for_json(value: Decimal) -> JsonNumber:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return int(normalized)
    return float(normalized)


def _dedupe_preserve_order(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
