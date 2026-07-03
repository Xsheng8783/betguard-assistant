from betguard.webfill.selector_discovery import (
    build_candidate_selectors,
    build_market_state,
    build_route_probe_report,
    build_selector_discovery_report,
    build_route_url,
    detect_login_page,
    detect_market_closed,
    detect_amount_field_candidates,
    detect_amount_fields,
    detect_danger_candidates,
    detect_danger_elements,
    extract_global_routes_from_html,
    detect_number_candidates,
    detect_number_elements,
    mark_visited_url,
    resolve_frame_src,
    should_scan_frame_src,
)


AUTHENTICATED_B03_HTML = (
    "<html><head><script>"
    "$Global.GameID = 13;"
    "$Global.Auth = 1;"
    "</script></head><body>"
    "-539 已開盤- "
    "帳號 密碼 下載Chrome"
    "</body></html>"
)


def _b03_number_board_elements() -> list[dict]:
    numbers = [element(tag="td", text=f"{n:02d}") for n in range(1, 40)]
    inputs = [
        element(tag="input", name="twoStar"),
        element(tag="input", name="threeStar"),
        element(tag="input", name="fourStar"),
    ]
    danger = [element(tag="button", text="送出")]
    return numbers + inputs + danger


def _authenticated_b03_route_page_scan(*, gated_elements: list[dict]) -> dict:
    raw_elements = _b03_number_board_elements()
    return {
        "resolved_url": "https://w1.gts362.com/token/Front/B/B03",
        "actual_url": "https://w1.gts362.com/token/Front/B/B03",
        "title": "539",
        "origin_source": "frame_elements.frame_url",
        "gid_source": "active_url",
        "probe_method": "mainFrame",
        "status_code": 200,
        "scan": {
            "text_sources": ["-539 已開盤- 帳號 密碼 下載Chrome"],
            "html_sources": [AUTHENTICATED_B03_HTML],
            "elements": list(gated_elements),
            "raw_elements": raw_elements,
            "live_frames": [
                {
                    "name": "mainFrame",
                    "url": "https://w1.gts362.com/token/Front/B/B03",
                    "element_count": len(raw_elements),
                    "appears_login_page": True,
                }
            ],
            "elements_sample_by_frame": {"mainFrame | b03": raw_elements[:20]},
        },
    }


def test_authenticated_b03_with_login_words_is_not_login_page() -> None:
    board = _b03_number_board_elements()
    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="https://w1.gts362.com/token/Front/Shared/Index",
        route_name="二三四星",
        global_config={"routes": {"二三四星": "/Front/B/B03"}, "game_id": 12},
        route_page_scan=_authenticated_b03_route_page_scan(gated_elements=board),
    )

    assert report["route_probe"]["appears_login_page"] is False
    assert report["number_candidates_count"] == 39
    assert report["route_probe_elements"] != []
    assert all(report["amount_field_candidates"][marker] for marker in ("二星", "三星", "四星"))
    assert report["danger_candidates"] != []
    assert "safe_to_continue" not in report
    assert "download Chrome banner detected; not treated as login page" in report["warnings"]

    gate_before = report["route_probe_gate_before"]
    assert gate_before["raw_elements_count"] == 43
    assert gate_before["raw_number_candidates_count"] == 39
    assert gate_before["route_probe_global_game_id_from_b03"] == 13
    assert gate_before["route_probe_auth_from_b03"] == 1
    assert gate_before["appears_login_page_before_gate"] is False


def _real_login_page_scan() -> dict:
    login_html = (
        "<html><body><form>"
        "帳號 <input type=\"text\" name=\"user\">"
        "密碼 <input type=\"password\" name=\"pass\">"
        "<div>登入</div><div>下載Chrome</div>"
        "</form></body></html>"
    )
    login_elements = [
        element(tag="input", type="text", name="user"),
        element(tag="input", type="password", name="pass"),
    ]
    return {
        "resolved_url": "https://www.gts362.com/Front/Shared/Login",
        "actual_url": "https://www.gts362.com/Front/Shared/Login",
        "title": "登入",
        "origin_source": "active_page.url",
        "gid_source": "",
        "probe_method": "mainFrame",
        "status_code": 200,
        "scan": {
            "text_sources": ["帳號 密碼 登入 下載Chrome"],
            "html_sources": [login_html],
            "elements": list(login_elements),
            "raw_elements": list(login_elements),
            "live_frames": [
                {
                    "name": "mainFrame",
                    "url": "https://www.gts362.com/Front/Shared/Login",
                    "element_count": len(login_elements),
                    "appears_login_page": True,
                }
            ],
            "elements_sample_by_frame": {},
        },
    }


def test_real_login_page_with_password_input_is_still_login() -> None:
    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="https://www.gts362.com/Front/Shared/Index",
        route_name="二三四星",
        global_config={"routes": {"二三四星": "/Front/B/B03"}, "game_id": 12},
        route_page_scan=_real_login_page_scan(),
    )

    assert report["route_probe"]["appears_login_page"] is True
    assert report["number_candidates_count"] == 0
    assert report["route_probe_elements"] == []
    assert not any(report["amount_field_candidates"].values())
    assert report["danger_candidates"] == []
    assert report["route_probe_gate_before"]["appears_login_page_before_gate"] is True


def test_gate_before_game_id_and_auth_come_from_b03_frame_not_index() -> None:
    scan = _authenticated_b03_route_page_scan(gated_elements=_b03_number_board_elements())
    index_html = "<html><script>$Global.GameID = 12;$Global.Auth = 0;</script></html>"
    scan["scan"]["html_sources"] = [index_html, AUTHENTICATED_B03_HTML]
    scan["scan"]["frame_html_sources"] = [
        {
            "frame_url": "https://w1.gts362.com/token/Front/Shared/Index",
            "frame_name": "",
            "html": index_html,
        },
        {
            "frame_url": "https://w1.gts362.com/token/Front/B/B03",
            "frame_name": "mainFrame",
            "html": AUTHENTICATED_B03_HTML,
        },
    ]

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="https://w1.gts362.com/token/Front/Shared/Index",
        route_name="二三四星",
        global_config={"routes": {"二三四星": "/Front/B/B03"}, "game_id": 12},
        route_page_scan=scan,
    )

    gate_before = report["route_probe_gate_before"]
    assert gate_before["route_probe_global_game_id_from_b03"] == 13
    assert gate_before["route_probe_auth_from_b03"] == 1


def test_route_probe_gate_before_does_not_flip_safe_to_continue_or_safety() -> None:
    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="https://w1.gts362.com/token/Front/Shared/Index",
        route_name="二三四星",
        global_config={"routes": {"二三四星": "/Front/B/B03"}, "game_id": 12},
        route_page_scan=_authenticated_b03_route_page_scan(gated_elements=[]),
    )

    # Diagnostics-only: it must not introduce any go-ahead / real-site signal.
    assert "safe_to_continue" not in report
    assert report["route_probe_gate_before"]["route_probe_auth_from_b03"] == 1
    assert "read-only diagnostics" in report["route_probe_gate_before"]["note"]


def test_route_probe_gate_before_matches_primary_when_not_login() -> None:
    scan = _authenticated_b03_route_page_scan(gated_elements=_b03_number_board_elements())
    # Remove login markers so the page is not classified as login.
    scan["scan"]["text_sources"] = ["-539 已開盤-"]
    scan["scan"]["html_sources"] = ["<html><script>$Global.GameID = 13;$Global.Auth = 1;</script></html>"]
    scan["scan"]["live_frames"][0]["appears_login_page"] = False

    report = build_route_probe_report(
        url="http://www.gts362.com",
        active_url="https://w1.gts362.com/token/Front/Shared/Index",
        route_name="二三四星",
        global_config={"routes": {"二三四星": "/Front/B/B03"}, "game_id": 12},
        route_page_scan=scan,
    )

    assert report["route_probe"]["appears_login_page"] is False
    assert report["number_candidates_count"] == 39
    gate_before = report["route_probe_gate_before"]
    assert gate_before["raw_number_candidates_count"] == report["number_candidates_count"]
    assert gate_before["appears_login_page_before_gate"] is False


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
        "frame_url": "http://example.test/frame",
        "source_url": "http://example.test/source",
        "source_kind": "active_page",
        "frame_name": "",
        "index": 1,
    }
    data.update(overrides)
    return data


def test_build_candidate_selectors_uses_id_name_and_text() -> None:
    selectors = build_candidate_selectors(element(tag="input", text="01", id="num01", name="n01"))

    assert "#num01" in selectors
    assert 'input[name="n01"]' in selectors
    assert "text=01" in selectors


def test_detect_number_elements_finds_text_01() -> None:
    numbers = detect_number_elements([element(text="01")])

    assert "01" in numbers
    assert numbers["01"][0]["tag"] == "button"
    assert "text=01" in numbers["01"][0]["candidate_selectors"]


def test_detect_amount_fields_finds_twostar_name() -> None:
    fields = detect_amount_fields([element(tag="input", name="twoStar")])

    assert fields["二星"]
    assert fields["二星"][0]["name"] == "twoStar"


def test_detect_amount_fields_finds_nearby_chinese_label() -> None:
    fields = detect_amount_fields([
        element(tag="label", text="二星"),
        element(tag="input", name="amount2"),
    ])

    assert fields["二星"]
    assert fields["二星"][0]["name"] == "amount2"


def test_detect_danger_elements_finds_submit_value() -> None:
    danger = detect_danger_elements([element(tag="button", value="送出注單")])

    assert danger
    assert danger[0]["text"] == "送出注單"
    assert 'button[value="送出注單"]' in danger[0]["candidate_selectors"]


def test_star_tab_is_not_danger() -> None:
    danger = detect_danger_elements([element(tag="button", text="二三四星")])

    assert danger == []


def test_detect_number_candidates_finds_onclick_01() -> None:
    numbers = detect_number_candidates([element(tag="img", onclick="pickNumber('01')")])

    assert "01" in numbers
    assert numbers["01"][0]["matched_field"] == "onclick"


def test_detect_number_candidates_finds_outer_html_39() -> None:
    numbers = detect_number_candidates([element(tag="td", outerHTML='<td onclick="n(39)">39</td>')])

    assert "39" in numbers
    assert numbers["39"][0]["matched_field"] == "outerHTML"


def test_detect_danger_candidates_finds_submit_value() -> None:
    danger = detect_danger_candidates([element(tag="input", value="送出注單")])

    assert danger
    assert danger[0]["matched_field"] == "value"


def test_detect_amount_field_candidates_finds_parent_label() -> None:
    fields = detect_amount_field_candidates([element(tag="input", name="two_amount", parentText="二星 金額")])

    assert fields["二星"]
    assert fields["二星"][0]["name"] == "two_amount"



def test_should_scan_frame_src_skips_unsafe_or_empty_values() -> None:
    assert should_scan_frame_src("") is False
    assert should_scan_frame_src("about:blank") is False
    assert should_scan_frame_src("javascript:alert(1)") is False
    assert should_scan_frame_src("#main") is False
    assert should_scan_frame_src("/Front/Shared/Lobby?p=1") is True


def test_resolve_frame_src_uses_active_url_origin() -> None:
    active_url = "http://w0.gts362.com/1i9HzH16HE6mhxD6Twza7g/Front/Shared/Index"
    frame_src = "/1i9HzH16HE6mhxD6Twza7g/Front/Shared/Lobby?p=1782833258407"

    resolved = resolve_frame_src(active_url, frame_src)

    assert resolved == "http://w0.gts362.com/1i9HzH16HE6mhxD6Twza7g/Front/Shared/Lobby?p=1782833258407"


def test_mark_visited_url_prevents_duplicates_and_ignores_fragments() -> None:
    visited: set[str] = set()

    assert mark_visited_url(visited, "http://w0.gts362.com/Front/Shared/Lobby?p=1#top") is True
    assert mark_visited_url(visited, "http://w0.gts362.com/Front/Shared/Lobby?p=1") is False
    assert len(visited) == 1


def test_frame_src_page_elements_are_merged_into_detection() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[
            element(
                tag="button",
                text="01",
                source_url="http://w0.gts362.com/Front/Shared/Lobby?p=1",
                source_kind="frame_src_page",
                frame_name="mainFrame",
            ),
        ],
        frame_src_pages_scanned=["http://w0.gts362.com/Front/Shared/Lobby?p=1"],
        visited_urls={
            "http://w0.gts362.com/Front/Shared/Index",
            "http://w0.gts362.com/Front/Shared/Lobby?p=1",
        },
    )

    assert report["number_selectors_count"] == 1
    assert "01" in report["number_selectors"]
    assert report["number_selectors"]["01"][0]["source_kind"] == "frame_src_page"
    assert report["number_selectors"]["01"][0]["frame_name"] == "mainFrame"
    assert report["diagnostics"]["frame_src_pages_scanned"] == ["http://w0.gts362.com/Front/Shared/Lobby?p=1"]
    assert report["diagnostics"]["visited_urls_count"] == 2



def test_frame_src_labels_are_exposed_when_numbers_missing() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[],
        frame_elements=[
            {
                "tag": "frame",
                "name": "gmenu",
                "id": "",
                "src": "/token/Front/Shared/Menu?p=1",
                "source_url": "http://w0.gts362.com/token/Front/Shared/Index",
            },
            {
                "tag": "frame",
                "name": "mainFrame",
                "id": "",
                "src": "/token/Front/Shared/Lobby?p=1",
                "source_url": "http://w0.gts362.com/token/Front/Shared/Index",
            },
        ],
    )

    assert "gmenu" in report["available_labels"]
    assert "mainFrame" in report["available_labels"]
    assert "Menu" in report["available_labels"]
    assert "Lobby" in report["available_labels"]


def test_frame_src_login_page_elements_are_ignored() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[
            element(
                tag="div",
                text="\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome 01",
                source_kind="frame_src_page",
                frame_url="http://w0.gts362.com/login",
            ),
            element(
                tag="input",
                type="password",
                name="pass",
                source_kind="frame_src_page",
                frame_url="http://w0.gts362.com/login",
            ),
        ],
        live_frames=[
            {
                "name": "mainFrame",
                "url": "http://w0.gts362.com/login",
                "text_length": 20,
                "html_length": 100,
                "sample_text": "\u5e33\u865f \u5bc6\u78bc \u767b\u5165",
                "appears_login_page": True,
            }
        ],
    )

    assert report["number_candidates_count"] == 0
    assert "frame src page appears to be login page; ignored" in report["warnings"]
    assert "not logged in or redirected to login page" in report["warnings"]


def test_login_live_frame_warning_is_reported() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[],
        live_frames=[
            {
                "name": "mainFrame",
                "url": "http://w0.gts362.com/login",
                "text_length": 20,
                "html_length": 100,
                "sample_text": "\u5e33\u865f \u5bc6\u78bc \u767b\u5165",
                "appears_login_page": True,
            }
        ],
    )

    assert "not logged in or redirected to login page" in report["warnings"]
    assert report["diagnostics"]["live_frames"][0]["appears_login_page"] is True


def test_extract_global_routes_from_html_parses_menu_and_game_config() -> None:
    html = """
    <script>
      $Global.GameID = 13;
      $Global.AllBetState = 2;
      $Global.AllGame = {"13":{"Name":"539"},"22":{"Name":"\u5929\u5929\u6a02"}};
      $Global.GameState = {"13":1,"22":1};
      $Global.DefaultPage = "/Front/B/B02";
      $Global.GameList = [{"ID":13,"Name":"539"},{"ID":22,"Name":"天天樂"}];
      $Global.Menu = [
        {"Name":"\u5168\u8eca","Url":"/Front/B/B02"},
        {"Name":"\u4e8c\u4e09\u56db\u661f","Url":"/Front/B/B03"},
        {"Name":"\u5feb\u901f\u8f38\u5165","Url":"/Front/B/B09"}
      ];
    </script>
    """

    config = extract_global_routes_from_html(html)

    assert config["game_id"] == 13
    assert config["all_bet_state"] == 2
    assert config["game_state"] == {"13": 1, "22": 1}
    assert config["default_page"] == "/Front/B/B02"
    assert config["routes"]["\u4e8c\u4e09\u56db\u661f"] == "/Front/B/B03"
    assert config["routes"]["\u5168\u8eca"] == "/Front/B/B02"
    assert config["routes"]["\u5feb\u901f\u8f38\u5165"] == "/Front/B/B09"


def test_market_state_maps_game_state_to_game_names() -> None:
    config = {
        "game_id": 13,
        "all_bet_state": 2,
        "game_state": {"13": 1, "22": 1, "99": 0},
        "all_game": {"13": {"Name": "539"}, "22": {"Name": "\u5929\u5929\u6a02"}},
        "game_list": None,
        "routes": {"\u4e8c\u4e09\u56db\u661f": "/Front/B/B03"},
    }

    market_state = build_market_state(
        global_config=config,
        route_probe={
            "label": "\u4e8c\u4e09\u56db\u661f",
            "route": "/Front/B/B03",
            "status": "ok",
            "has_bet_page_body": True,
        },
        detection_elements=[],
    )

    assert market_state["current_game_id"] == 13
    assert market_state["current_game_name"] == "539"
    assert market_state["all_bet_state"] == 2
    assert market_state["game_state"]["539"] == 1
    assert market_state["game_state"]["\u5929\u5929\u6a02"] == 1
    assert market_state["selected_route"] == "\u4e8c\u4e09\u56db\u661f"
    assert market_state["selected_route_url"] == "/Front/B/B03"
    assert market_state["can_probe_bet_page"] is True


def test_extract_global_routes_from_html_finds_route_near_label() -> None:
    html = """
    <script>
      $Global.Menu = "menu text \u4e8c\u4e09\u56db\u661f something /Front/B/B03";
    </script>
    """

    config = extract_global_routes_from_html(html)

    assert config["routes"]["\u4e8c\u4e09\u56db\u661f"] == "/Front/B/B03"


def test_build_route_url_preserves_login_token_path() -> None:
    active_url = "http://w1.gts362.com/xxxx/Front/Shared/Index"

    route_url = build_route_url(active_url, "/Front/B/B03")

    assert route_url == "http://w1.gts362.com/xxxx/Front/B/B03"


def test_build_route_url_keeps_w0_host_and_token_for_car_route() -> None:
    active_url = "http://w0.gts362.com/token123/Front/Shared/Index"

    route_url = build_route_url(active_url, "/Front/B/B02")

    assert route_url == "http://w0.gts362.com/token123/Front/B/B02"


def test_build_route_url_ignores_active_query_and_keeps_token() -> None:
    active_url = "http://w0.gts362.com/token123/Front/Shared/Lobby?p=123"

    route_url = build_route_url(active_url, "/Front/B/B09")

    assert route_url == "http://w0.gts362.com/token123/Front/B/B09"


def test_build_route_url_does_not_use_www_when_active_url_is_worker_host() -> None:
    active_url = "http://w1.gts362.com/abc123/Front/Shared/Index"

    route_url = build_route_url(active_url, "/Front/B/B03")

    assert route_url == "http://w1.gts362.com/abc123/Front/B/B03"
    assert "www.gts362.com" not in route_url


def test_selector_report_keeps_route_probe_built_url() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[],
        route_probe={
            "label": "\u4e8c\u4e09\u56db\u661f",
            "route": "/Front/B/B03",
            "built_url": "http://w1.gts362.com/abc123/Front/B/B03",
            "status": "ok",
            "appears_404": False,
            "appears_login_page": False,
        },
    )

    assert report["route_probe"]["built_url"] == "http://w1.gts362.com/abc123/Front/B/B03"


def test_route_404_sets_market_state_unavailable_without_number_warning() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[],
        global_config={
            "game_id": 13,
            "all_bet_state": 2,
            "game_state": {"13": 1},
            "all_game": {"13": {"Name": "539"}},
            "game_list": None,
            "routes": {"\u4e8c\u4e09\u56db\u661f": "/Front/B/B03"},
        },
        route_probe={
            "label": "\u4e8c\u4e09\u56db\u661f",
            "route": "/Front/B/B03",
            "built_url": "http://w1.gts362.com/abc123/Front/B/B03",
            "status": "http_404",
            "appears_404": True,
            "appears_login_page": False,
            "has_bet_page_body": False,
        },
    )

    assert report["market_state"]["can_probe_bet_page"] is False
    assert report["safe_to_continue"] is False
    assert "expected 39 number selectors" not in "\n".join(report["warnings"])
    assert "bet page unavailable because market appears closed; selector discovery postponed" in report["warnings"]


def test_market_closed_text_sets_market_state_unavailable() -> None:
    assert detect_market_closed("\u5df2\u95dc\u76e4") is True

    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[],
        route_probe={
            "label": "\u4e8c\u4e09\u56db\u661f",
            "route": "/Front/B/B03",
            "status": "market_closed",
            "appears_404": False,
            "appears_login_page": False,
            "appears_market_closed": True,
            "has_bet_page_body": False,
        },
    )

    assert report["market_state"]["can_probe_bet_page"] is False
    assert "expected 39 number selectors" not in "\n".join(report["warnings"])


def test_can_probe_true_missing_numbers_emits_expected_39_warning() -> None:
    report = build_selector_discovery_report(
        url="http://www.gts362.com",
        elements=[element(tag="input", name="twoStar")],
        route_probe={
            "label": "\u4e8c\u4e09\u56db\u661f",
            "route": "/Front/B/B03",
            "status": "ok",
            "appears_404": False,
            "appears_login_page": False,
            "has_bet_page_body": True,
        },
    )

    assert report["market_state"]["can_probe_bet_page"] is True
    assert "expected 39 number selectors, found 0" in report["warnings"]


def test_detect_login_page_markers() -> None:
    # Text markers alone (menus, change-password links, Chrome banner) are no
    # longer enough; a real password input is required.
    assert detect_login_page("\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome") is False
    assert (
        detect_login_page(
            "\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome",
            '<form><input type="password"></form>',
        )
        is True
    )
    assert detect_login_page("539 \u4e8c\u4e09\u56db\u661f 01 02 03") is False


def test_authenticated_bet_page_signals_override_login_markers() -> None:
    # Auth=1 wins even when a password input exists (e.g. change-password form
    # inside the logged-in shell).
    assert (
        detect_login_page(
            "\u5e33\u865f \u5bc6\u78bc \u4e0b\u8f09Chrome",
            '<script>$Global.Auth = 1;</script><input type="password">',
        )
        is False
    )
    # /Front/B/B03 marker also wins.
    assert (
        detect_login_page(
            "\u5e33\u865f \u5bc6\u78bc",
            '<frame src="/token/Front/B/B03"><input type="password">',
        )
        is False
    )
