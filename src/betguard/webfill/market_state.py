from __future__ import annotations

import json
import re
from typing import Any

from betguard.game_rules import GAME_RULES


MARKET_GAME_ORDER = ("大樂", "六合", "539", "天天樂")
UNKNOWN = "unknown"


def parse_market_state_from_html(source: str) -> dict[str, Any]:
    return {"market_state": build_market_state_summary(extract_market_globals_from_html(source))}


def extract_market_globals_from_html(source: str) -> dict[str, Any]:
    text = str(source or "")
    return {
        "game_id": _parse_int(_extract_global_assignment(text, "GameID")),
        "game_state": _parse_js_value(_extract_global_assignment(text, "GameState")) or {},
        "game_list": _parse_js_value(_extract_global_assignment(text, "GameList")),
        "all_game": _parse_js_value(_extract_global_assignment(text, "AllGame")),
    }


def build_market_state_summary(global_config: dict[str, Any]) -> dict[str, Any]:
    game_state = global_config.get("game_state") or {}
    if not isinstance(game_state, dict):
        game_state = {}

    games: dict[str, dict[str, Any]] = {}
    for name in MARKET_GAME_ORDER:
        rule = GAME_RULES[name]
        raw_state = _state_for_game_id(game_state, rule.game_id)
        games[name] = {
            "game_id": rule.game_id,
            "is_open": _open_state(raw_state),
        }

    for game_id, raw_state in game_state.items():
        name = _game_name_for_id(global_config, game_id)
        if not name or name in games:
            continue
        games[name] = {
            "game_id": _coerce_int(game_id),
            "is_open": _open_state(raw_state),
        }

    return {"games": games}


def market_open_error(selector_report: dict[str, Any], game_name: str, *, dry_run: bool = False) -> str | None:
    if dry_run:
        return None

    market_state = selector_report.get("market_state") or {}
    games = market_state.get("games")
    if not isinstance(games, dict):
        return f"market state unknown for {game_name}"

    game = games.get(game_name)
    if not isinstance(game, dict):
        game = _find_game_by_id(games, GAME_RULES.get(game_name).game_id if game_name in GAME_RULES else None)
    if not isinstance(game, dict):
        return f"market state unknown for {game_name}"

    is_open = game.get("is_open", UNKNOWN)
    if is_open is True:
        return None
    if is_open is False:
        return f"market closed for {game_name}"
    return f"market state unknown for {game_name}"


def _state_for_game_id(game_state: dict[Any, Any], game_id: int) -> Any:
    for key, value in game_state.items():
        if str(key) == str(game_id):
            return value
    return None


def _open_state(raw_state: Any) -> bool | str:
    try:
        state = int(str(raw_state))
    except (TypeError, ValueError):
        return UNKNOWN
    if state == 1:
        return True
    if state == 0:
        return False
    return UNKNOWN


def _find_game_by_id(games: dict[str, Any], game_id: int | None) -> dict[str, Any] | None:
    if game_id is None:
        return None
    for game in games.values():
        if isinstance(game, dict) and str(game.get("game_id")) == str(game_id):
            return game
    return None


def _game_name_for_id(global_config: dict[str, Any], game_id: Any) -> str | None:
    if game_id is None:
        return None
    game_id_text = str(game_id)
    for source_name in ("all_game", "game_list"):
        name = _find_game_name_in_source(global_config.get(source_name), game_id_text)
        if name:
            return name
    for name, rule in GAME_RULES.items():
        if str(rule.game_id) == game_id_text:
            return name
    return None


def _find_game_name_in_source(source: Any, game_id: str) -> str | None:
    if isinstance(source, dict):
        if game_id in source:
            return _game_name_from_value(source[game_id])
        for key, value in source.items():
            if str(key) == game_id:
                return _game_name_from_value(value)
            if isinstance(value, dict) and _dict_game_id(value) == game_id:
                return _game_name_from_value(value)
    if isinstance(source, list):
        for item in source:
            if isinstance(item, dict) and _dict_game_id(item) == game_id:
                return _game_name_from_value(item)
    return None


def _dict_game_id(item: dict[str, Any]) -> str | None:
    for key in ("ID", "Id", "id", "GameID", "game_id", "GameId"):
        if key in item:
            return str(item[key])
    return None


def _game_name_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("Name", "name", "GameName", "game_name", "Title", "title"):
            if value.get(key):
                return str(value[key])
    return None


def _extract_global_assignment(source: str, name: str) -> str | None:
    match = re.search(rf"\$Global\.{re.escape(name)}\s*=\s*", source)
    if not match:
        return None
    index = match.end()
    while index < len(source) and source[index].isspace():
        index += 1
    if index >= len(source):
        return None

    first = source[index]
    if first in "{[":
        return _read_balanced_js(source, index)
    if first in "\"'":
        return _read_quoted_js(source, index)

    end = index
    while end < len(source) and source[end] not in ";\r\n<":
        end += 1
    return source[index:end].strip()


def _read_balanced_js(source: str, start: int) -> str | None:
    opening = source[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return None


def _read_quoted_js(source: str, start: int) -> str | None:
    quote = source[start]
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return source[start : index + 1]
    return None


def _parse_js_value(raw: str | None) -> Any:
    if raw is None:
        return None
    raw = raw.strip().rstrip(";")
    if not raw:
        return None
    for candidate in (raw, _js_to_jsonish(raw)):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    if raw[0:1] in "\"'" and raw[-1:] == raw[0]:
        return raw[1:-1]
    return raw


def _js_to_jsonish(raw: str) -> str:
    text = raw.replace("'", '"')
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', text)
    return text


def _parse_int(raw: str | None) -> int | None:
    value = _parse_js_value(raw)
    return _coerce_int(value)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None

