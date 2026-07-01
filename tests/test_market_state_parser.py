from betguard.webfill.market_state import (
    UNKNOWN,
    build_market_state_summary,
    market_open_error,
    parse_market_state_from_html,
)


TIANTIAN = "\u5929\u5929\u6a02"
DALETOU = "\u5927\u6a02"
HK = "\u516d\u5408"


def parsed_market_state() -> dict:
    html = f"""
    <script>
      $Global.GameID = 13;
      $Global.GameState = {{"13":1,"22":1,"12":0,"11":0}};
      $Global.GameList = [
        {{"ID":12,"Name":"{DALETOU}"}},
        {{"ID":11,"Name":"{HK}"}},
        {{"ID":13,"Name":"539"}},
        {{"ID":22,"Name":"{TIANTIAN}"}}
      ];
    </script>
    """
    return parse_market_state_from_html(html)["market_state"]


def test_game_13_open_maps_to_539() -> None:
    state = parsed_market_state()

    assert state["games"]["539"] == {"game_id": 13, "is_open": True}


def test_game_22_open_maps_to_tiantian() -> None:
    state = parsed_market_state()

    assert state["games"][TIANTIAN] == {"game_id": 22, "is_open": True}


def test_game_12_closed_maps_to_daletou() -> None:
    state = parsed_market_state()

    assert state["games"][DALETOU] == {"game_id": 12, "is_open": False}


def test_game_11_closed_maps_to_hk() -> None:
    state = parsed_market_state()

    assert state["games"][HK] == {"game_id": 11, "is_open": False}


def test_missing_state_is_unknown() -> None:
    state = build_market_state_summary({"game_state": {"13": 1}})["games"]

    assert state["539"]["is_open"] is True
    assert state[TIANTIAN]["is_open"] == UNKNOWN


def test_market_open_error_blocks_closed() -> None:
    selector_report = {"market_state": {"games": {"539": {"game_id": 13, "is_open": False}}}}

    assert market_open_error(selector_report, "539") == "market closed for 539"


def test_market_open_error_blocks_unknown() -> None:
    selector_report = {"market_state": {"games": {"539": {"game_id": 13, "is_open": UNKNOWN}}}}

    assert market_open_error(selector_report, "539") == "market state unknown for 539"


def test_market_open_error_allows_dry_run_unknown() -> None:
    selector_report = {"market_state": {"games": {"539": {"game_id": 13, "is_open": UNKNOWN}}}}

    assert market_open_error(selector_report, "539", dry_run=True) is None

