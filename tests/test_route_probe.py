import inspect

from betguard.webfill import selector_discovery
from betguard.webfill.selector_discovery import (
    build_route_probe_report,
    build_route_probe_url,
    detect_404_page,
    detect_login_page,
    extract_global_config_from_html,
    get_authenticated_origin,
)


STAR_ROUTE = "\u4e8c\u4e09\u56db\u661f"
CAR_ROUTE = "\u5168\u8eca"
QUICK_ROUTE = "\u5feb\u901f\u8f38\u5165"
TWO_STAR = "\u4e8c\u661f"
THREE_STAR = "\u4e09\u661f"
FOUR_STAR = "\u56db\u661f"
SUBMIT_BET = "\u9001\u51fa\u6ce8\u55ae"
CONFIRM = "\u78ba\u8a8d"


def html_with_globals() -> str:
    return f"""
    <html>
      <script>
        $Global.GID = "globalHtmlToken";
        $Global.GameID = 13;
        $Global.GameState = {{"13":1,"22":1}};
        $Global.DefaultPage = "/Front/B/B02";
        $Global.Menu = [
          {{"Name":"{CAR_ROUTE}","Url":"/Front/B/B02"}},
          {{"Name":"{STAR_ROUTE}","Url":"/Front/B/B03"}},
          {{"Name":"{QUICK_ROUTE}","Url":"/Front/B/B09"}}
        ];
      </script>
    </html>
    """


def element(**overrides) -> dict:
    data = {
        "tag": "button",
        "text": "",
        "textContent": "",
        "innerText": "",
        "value": "",
        "id": "",
        "name": "",
        "className": "",
        "type": "",
        "role": "",
        "href": "",
        "src": "",
        "alt": "",
        "title": "",
        "onclick": "",
        "ariaLabel": "",
        "outerHTML": "",
        "parentText": "",
        "parentHTML": "",
        "grandparentText": "",
        "grandparentHTML": "",
        "frame_url": "http://w0.gts362.com/token/Front/B/B03",
        "source_url": "http://w0.gts362.com/token/Front/B/B03",
        "source_kind": "route_probe",
        "frame_name": STAR_ROUTE,
        "index": 1,
    }
    data.update(overrides)
    return data


def route_scan(elements: list[dict], text: str = "", html: str = "<html></html>") -> dict:
    return {
        "elements": elements,
        "text_sources": [text],
        "html_sources": [html],
        "elements_sample_by_frame": {
            f"{STAR_ROUTE} | http://w0.gts362.com/token/Front/B/B03": elements[:20],
        },
    }


def test_extract_global_config_from_html_parses_game_id() -> None:
    config = extract_global_config_from_html(html_with_globals())

    assert config["game_id"] == 13
    assert config["gid"] == "globalHtmlToken"
    assert config["game_state"] == {"13": 1, "22": 1}
    assert config["default_page"] == "/Front/B/B02"


def test_extract_global_config_from_html_parses_star_route() -> None:
    config = extract_global_config_from_html(html_with_globals())

    assert config["routes"][STAR_ROUTE] == "/Front/B/B03"
    assert config["routes"][CAR_ROUTE] == "/Front/B/B02"
    assert config["routes"][QUICK_ROUTE] == "/Front/B/B09"


def test_build_route_probe_url_keeps_active_origin_and_token() -> None:
    active_url = "http://w0.gts362.com/YT7C-5dmPEuDwX_RM2tkBw/Front/Shared/Index"

    resolved = build_route_probe_url(active_url, "/Front/B/B03")

    assert resolved == "http://w0.gts362.com/YT7C-5dmPEuDwX_RM2tkBw/Front/B/B03"
    assert "www.gts362.com/Front/B/B03" not in resolved


def test_build_route_probe_url_uses_frame_src_token() -> None:
    resolved = build_route_probe_url(
        "http://w1.gts362.com/Front/Shared/Index",
        "/Front/B/B03",
        frame_sources=[{"src": "/frameToken123/Front/Shared/Lobby?p=1"}],
    )

    assert resolved == "http://w1.gts362.com/frameToken123/Front/B/B03"


def test_build_route_probe_url_uses_global_gid_when_active_url_has_no_token() -> None:
    resolved = build_route_probe_url(
        "http://w0.gts362.com/Front/Shared/Index",
        "/Front/B/B03",
        global_config={"gid": "globalToken456"},
    )

    assert resolved == "http://w0.gts362.com/globalToken456/Front/B/B03"


def test_get_authenticated_origin_uses_frame_url_before_www() -> None:
    origin = get_authenticated_origin(
        "http://www.gts362.com/",
        frame_elements=[
            {
                "frame_url": "http://w0.gts362.com/d108lzqAWEeJMISFe2djdA/Front/Shared/Index",
                "src": "/d108lzqAWEeJMISFe2djdA/Front/Shared/Lobby?p=1",
            }
        ],
        live_frames=[],
    )

    assert origin == "http://w0.gts362.com"


def test_get_authenticated_origin_uses_live_frame_url_before_www() -> None:
    origin = get_authenticated_origin(
        "http://www.gts362.com/",
        frame_elements=[],
        live_frames=[{"url": "http://w1.gts362.com/tokenLive/Front/Shared/Index"}],
    )

    assert origin == "http://w1.gts362.com"


def test_build_route_probe_url_uses_authenticated_frame_origin_and_token() -> None:
    resolved = build_route_probe_url(
        "http://www.gts362.com/",
        "/Front/B/B03",
        frame_elements=[
            {
                "frame_url": "http://w0.gts362.com/d108lzqAWEeJMISFe2djdA/Front/Shared/Index",
                "src": "/d108lzqAWEeJMISFe2djdA/Front/Shared/Lobby?p=1",
            }
        ],
    )

    assert resolved == "http://w0.gts362.com/d108lzqAWEeJMISFe2djdA/Front/B/B03"
    assert not resolved.startswith("http://www.gts362.com/")


def test_detect_404_page_identifies_common_markers() -> None:
    assert detect_404_page("HTTP 404")
    assert detect_404_page("\u627e\u4e0d\u5230\u8cc7\u6e90")
    assert detect_404_page("\u8981\u6c42\u7684 URL")
    assert detect_404_page("539 \u4e8c\u4e09\u56db\u661f") is False


def test_detect_login_page_identifies_login_markers() -> None:
    assert detect_login_page("\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome") is True
    assert detect_login_page("539 二三四星 01 02 03") is False


def test_route_probe_report_records_candidates_without_clicking() -> None:
    config = extract_global_config_from_html(html_with_globals())
    elements = [
        element(text="01"),
        element(tag="input", name="two_amount", parentText=TWO_STAR),
        element(tag="input", name="three_amount", parentText=THREE_STAR),
        element(tag="input", name="four_amount", parentText=FOUR_STAR),
        element(tag="button", value=SUBMIT_BET),
        element(tag="button", text=CONFIRM),
    ]

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="http://w0.gts362.com/token/Front/Shared/Index",
        route_name=STAR_ROUTE,
        global_config=config,
        route_page_scan={
            "resolved_url": "http://w0.gts362.com/token/Front/B/B03",
            "actual_url": "http://w0.gts362.com/token/Front/B/B03",
            "title": "539 二三四星",
            "scan": route_scan(elements, text="539 二三四星 01"),
        },
    )

    assert report["mode"] == "selector_discovery_route_probe"
    assert report["route_probe"]["route_name"] == STAR_ROUTE
    assert report["route_probe"]["route_path"] == "/Front/B/B03"
    assert report["route_probe"]["resolved_url"] == "http://w0.gts362.com/token/Front/B/B03"
    assert report["route_probe"]["appears_login_page"] is False
    assert report["route_probe"]["is_404_page"] is False
    assert report["route_probe"]["origin_source"] == "active_page.url"
    assert report["route_probe"]["gid_source"] == "active_url"
    assert report["number_candidates_count"] == 1
    assert "01" in report["number_candidates"]
    assert report["amount_field_candidates"][TWO_STAR]
    assert report["amount_field_candidates"][THREE_STAR]
    assert report["amount_field_candidates"][FOUR_STAR]
    assert [item["text"] for item in report["danger_candidates"]] == [SUBMIT_BET, CONFIRM]

    source = inspect.getsource(selector_discovery)
    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".press(" not in source
    assert "submit(" not in source


def test_route_probe_report_marks_404_page() -> None:
    config = extract_global_config_from_html(html_with_globals())

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="http://w1.gts362.com/token/Front/Shared/Index",
        route_name=STAR_ROUTE,
        global_config=config,
        route_page_scan={
            "resolved_url": "http://w1.gts362.com/token/Front/B/B03",
            "actual_url": "http://w1.gts362.com/token/Front/B/B03",
            "title": "HTTP 404",
            "scan": route_scan([], text="\u627e\u4e0d\u5230\u8cc7\u6e90", html="<html>\u8981\u6c42\u7684 URL</html>"),
        },
    )

    assert report["route_probe"]["is_404_page"] is True


def test_route_probe_login_page_is_not_used_for_selector_detection() -> None:
    config = extract_global_config_from_html(html_with_globals())
    login_text = "\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome 01"

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="http://w0.gts362.com/token/Front/Shared/Index",
        route_name=STAR_ROUTE,
        global_config=config,
        route_page_scan={
            "resolved_url": "http://w0.gts362.com/token/Front/B/B03",
            "actual_url": "http://w0.gts362.com/Login",
            "title": "login",
            "scan": route_scan([element(text=login_text)], text=login_text, html="<html>login</html>"),
        },
    )

    assert report["route_probe"]["appears_login_page"] is True
    assert report["number_candidates_count"] == 0
    assert report["number_candidates"] == {}
    assert not any(report["amount_field_candidates"].values())
    assert report["danger_candidates"] == []
    assert "route probe redirected to login page" in report["warnings"]


def test_route_probe_html_without_numbers_exposes_elements_sample() -> None:
    config = extract_global_config_from_html(html_with_globals())
    elements = [element(tag="div", text="539 二三四星")]

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="http://w0.gts362.com/token/Front/Shared/Index",
        route_name=STAR_ROUTE,
        global_config=config,
        route_page_scan={
            "resolved_url": "http://w0.gts362.com/token/Front/B/B03",
            "actual_url": "http://w0.gts362.com/token/Front/B/B03",
            "title": "539 二三四星",
            "scan": route_scan(elements, text="539 二三四星", html="<html><div>539</div></html>"),
        },
    )

    assert report["number_candidates_count"] == 0
    assert report["elements_sample_by_frame"]
    assert "number candidates not found; inspect elements_sample_by_frame" in report["warnings"]
