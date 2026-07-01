from betguard.webfill.cli import build_parser
from betguard.webfill.page_routes import parse_page_routes_from_html
from betguard.webfill.selector_discovery import extract_global_routes_from_html


CAR_PAGE = "\u5168\u8eca"
STAR_PAGE = "\u4e8c\u4e09\u56db\u661f"
QUICK_PAGE = "\u5feb\u901f\u8f38\u5165"
STATION_PAGE = "\u53f0\u865f"
SPECIAL_TAIL_PAGE = "\u7279\u5c3e\u4e09"


def parsed_routes() -> dict[str, str]:
    html = f"""
    <script>
      $Global.Menu = [
        {{"Name":"{CAR_PAGE}","Url":"/Front/B/B02"}},
        {{"Name":"{STAR_PAGE}","Url":"/Front/B/B03"}},
        {{"Name":"{QUICK_PAGE}","Url":"/Front/B/B09"}},
        {{"Name":"{STATION_PAGE}","Url":"/Front/B/B04"}},
        {{"Name":"{SPECIAL_TAIL_PAGE}","Url":"/Front/B/B06"}}
      ];
    </script>
    """
    return parse_page_routes_from_html(html)["routes"]


def test_star_page_route() -> None:
    assert parsed_routes()[STAR_PAGE] == "/Front/B/B03"


def test_car_page_route() -> None:
    assert parsed_routes()[CAR_PAGE] == "/Front/B/B02"


def test_quick_input_page_route() -> None:
    assert parsed_routes()[QUICK_PAGE] == "/Front/B/B09"


def test_station_page_route() -> None:
    assert parsed_routes()[STATION_PAGE] == "/Front/B/B04"


def test_special_tail_page_route() -> None:
    assert parsed_routes()[SPECIAL_TAIL_PAGE] == "/Front/B/B06"


def test_selector_discovery_global_config_uses_page_route_parser_labels() -> None:
    html = f"""
    <script>
      $Global.Menu = [
        {{"Name":"{CAR_PAGE}","Url":"/Front/B/B02"}},
        {{"Name":"{STAR_PAGE}","Url":"/Front/B/B03"}},
        {{"Name":"{QUICK_PAGE}","Url":"/Front/B/B09"}},
        {{"Name":"{STATION_PAGE}","Url":"/Front/B/B04"}},
        {{"Name":"{SPECIAL_TAIL_PAGE}","Url":"/Front/B/B06"}}
      ];
    </script>
    """

    routes = extract_global_routes_from_html(html)["routes"]

    assert routes[STATION_PAGE] == "/Front/B/B04"
    assert routes[SPECIAL_TAIL_PAGE] == "/Front/B/B06"


def test_webfill_cli_accepts_page_argument() -> None:
    args = build_parser().parse_args(
        [
            "--dry-run",
            "--discover-selectors",
            "--url",
            "http://www.gts362.com",
            "--page",
            STAR_PAGE,
        ]
    )

    assert args.page == STAR_PAGE
