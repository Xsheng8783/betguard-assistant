import inspect

from betguard.webfill import live_b03_snapshot
from betguard.webfill.live_b03_snapshot import (
    build_b03_selector_mapping_from_live_snapshot,
    collect_live_b03_snapshot,
    format_pretty_live_b03_selector_mapping,
)


TWO_STAR = "\u4e8c\u661f"
THREE_STAR = "\u4e09\u661f"
FOUR_STAR = "\u56db\u661f"


def body_text() -> str:
    numbers = " ".join(f"{number:02d}" for number in range(1, 40))
    return (
        f"539 - \u4e0b\u6ce8\u8cc7\u8a0a \u4e8c\u4e09\u661f\u78b0\u6cd5 "
        f"{TWO_STAR} {THREE_STAR} {FOUR_STAR} \u672c\u91d1 \u6bcf\u78b0\u91d1\u984d \u4e0b\u6ce8\u91d1\u984d "
        f"\u5feb\u901f\u8f38\u5165 \u865f\u78bc {numbers} "
        "\u9001\u51fa \u9001\u51fa\u6ce8\u55ae \u6e05\u9664 \u78ba\u8a8d \u52a0\u5165\u5e38\u7528\u724c\u7d44"
    )


def tiantian_body_text() -> str:
    return body_text().replace("539 - \u4e0b\u6ce8\u8cc7\u8a0a", "\u5929\u5929\u6a02 - \u4e0b\u6ce8\u8cc7\u8a0a")


def live_elements() -> list[dict]:
    return [
        {
            "index": 1,
            "tag": "input",
            "text": "",
            "textContent": "",
            "value": "",
            "id": "",
            "name": "",
            "className": "btn1",
            "type": "text",
            "role": "",
            "href": "",
            "src": "",
            "alt": "",
            "placeholder": "",
            "title": "",
            "ariaLabel": "",
            "dataBind": "value: QkNums, event: { change: Mo.OnChkNo }",
            "onclick": "",
            "outerHTML": '<input class="btn1" type="text" data-bind="value: QkNums, event: { change: Mo.OnChkNo }">',
        },
        {
            "index": 2,
            "tag": "button",
            "text": "\u9001\u51fa",
            "textContent": "\u9001\u51fa",
            "value": "",
            "id": "",
            "name": "",
            "className": "button1",
            "type": "button",
            "role": "",
            "href": "",
            "src": "",
            "alt": "",
            "placeholder": "",
            "title": "",
            "ariaLabel": "",
            "dataBind": "click: function () { Mo.OnAddSel() }",
            "onclick": "",
            "outerHTML": '<button class="button1" type="button" data-bind="click: function () { Mo.OnAddSel() }">送出</button>',
        },
        {
            "index": 3,
            "tag": "button",
            "text": "\u78ba\u5b9a",
            "textContent": "\u78ba\u5b9a",
            "value": "",
            "id": "",
            "name": "",
            "className": "button1",
            "type": "button",
            "role": "",
            "href": "",
            "src": "",
            "alt": "",
            "placeholder": "",
            "title": "",
            "ariaLabel": "",
            "dataBind": "click: function () { close() }",
            "onclick": "",
            "outerHTML": '<button class="button1" type="button" data-bind="click: function () { close() }">\u78ba\u5b9a</button>',
        },
        {
            "index": 4,
            "tag": "button",
            "text": "\u522a\u9664",
            "textContent": "\u522a\u9664",
            "value": "",
            "id": "",
            "name": "",
            "className": "button1",
            "type": "button",
            "role": "",
            "href": "",
            "src": "",
            "alt": "",
            "placeholder": "",
            "title": "",
            "ariaLabel": "",
            "dataBind": "click: function () { close() }",
            "onclick": "",
            "outerHTML": '<button class="button1" type="button" data-bind="click: function () { close() }">\u522a\u9664</button>',
        },
    ]


class MockFrame:
    def __init__(self, name: str, url: str, text: str, elements: list[dict]):
        self.name = name
        self.url = url
        self._text = text
        self._elements = elements

    def evaluate(self, script: str):
        if "document.body" in script:
            return self._text
        if "document.documentElement.outerHTML" in script:
            return "<html>" + self._text + "</html>"
        if "querySelectorAll" in script:
            return self._elements
        return ""


class MockContext:
    def __init__(self, pages: list["MockPage"]):
        self.pages = pages


class MockPage:
    def __init__(
        self,
        *,
        url: str = "http://w0.gts362.com/gid/Front/Shared/Index",
        frames: list[MockFrame] | None = None,
        text: str = "",
        frame_tags: list[dict] | None = None,
    ):
        self.url = url
        self.frames = frames if frames is not None else [
            MockFrame("gmenu", "http://w0.gts362.com/gid/Front/Shared/Menu", "menu", []),
            MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", body_text(), live_elements()),
        ]
        self._text = text
        self._frame_tags = frame_tags or []
        self.context = MockContext([self])

    def evaluate(self, script: str):
        if "querySelectorAll(\"frame, iframe\")" in script:
            return self._frame_tags
        if "document.body" in script:
            return self._text
        if "document.documentElement.outerHTML" in script:
            tags = "".join(tag.get("outerHTML", "") for tag in self._frame_tags)
            return "<html><frameset>" + self._text + tags + "</frameset></html>"
        return ""

    def frame(self, *, name: str):
        for frame in self.frames:
            if frame.name == name:
                return frame
        return None


def snapshot() -> dict:
    return {
        "mode": "live_b03_snapshot",
        "frame_name": "mainFrame",
        "frame_url": "http://w0.gts362.com/gid/Front/B/B03",
        "body_text": body_text(),
        "html_length": 1000,
        "elements": [
            {**item, "frame_name": "mainFrame", "frame_url": "http://w0.gts362.com/gid/Front/B/B03"}
            for item in live_elements()
        ],
        "b03_detected": True,
        "live_frames_scanned": 2,
        "warnings": [],
        "errors": [],
    }


def test_collect_live_b03_snapshot_reads_mainframe_without_operations() -> None:
    result = collect_live_b03_snapshot(MockPage())

    assert result["frame_name"] == "mainFrame"
    assert result["frame_url"].endswith("/Front/B/B03")
    assert result["b03_detected"] is True
    assert len(result["elements"]) == 4
    assert result["selected_frame_score"] > 0
    assert result["frame_candidates"]


def test_frameset_page_lists_frame_tags_and_warns_when_frames_not_exposed() -> None:
    page = MockPage(
        frames=[MockFrame("", "http://w0.gts362.com/gid/Front/Shared/Index", "root", [])],
        frame_tags=[
            {"name": "gmenu", "id": "", "src": "/gid/Front/Shared/Menu", "outerHTML": "<frame name='gmenu'>"},
            {"name": "mainFrame", "id": "", "src": "/gid/Front/Shared/Lobby", "outerHTML": "<frame name='mainFrame'>"},
        ],
    )

    result = collect_live_b03_snapshot(page)

    assert len(result["frame_tags"]) == 2
    assert result["frame_tags"][1]["name"] == "mainFrame"
    assert any("Playwright page.frames did not expose child frames" in warning for warning in result["warnings"])


def test_context_pages_selects_authenticated_shared_index_page() -> None:
    entry = MockPage(url="http://www.gts362.com/", frames=[MockFrame("", "http://www.gts362.com/", "", [])])
    authenticated = MockPage(
        url="http://w0.gts362.com/gid/Front/Shared/Index",
        frames=[MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", tiantian_body_text(), live_elements())],
        frame_tags=[{"name": "mainFrame", "id": "", "src": "/gid/Front/B/B03", "outerHTML": "<frame name='mainFrame'>"}],
    )
    context = MockContext([entry, authenticated])
    entry.context = context
    authenticated.context = context

    result = collect_live_b03_snapshot(entry)

    assert result["active_page_url"] == "http://www.gts362.com/"
    assert result["selected_page_url"] == "http://w0.gts362.com/gid/Front/Shared/Index"
    assert result["frame_name"] == "mainFrame"
    assert result["b03_detected"] is True


def test_tiantian_b03_text_is_supported() -> None:
    page = MockPage(frames=[MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", tiantian_body_text(), live_elements())])

    result = collect_live_b03_snapshot(page)
    mapping = build_b03_selector_mapping_from_live_snapshot(result)

    assert result["b03_detected"] is True
    assert mapping["page"]["game"] == "\u5929\u5929\u6a02"
    assert mapping["number_selectors_count"] == 39


def test_empty_body_frame_is_not_selected_as_b03() -> None:
    page = type(
        "Page",
        (),
        {
            "frames": [
                MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", "", live_elements()),
            ]
        },
    )()

    result = collect_live_b03_snapshot(page)

    assert result["frame_name"] == ""
    assert result["b03_detected"] is False
    assert "no live B03 mainFrame detected" in result["errors"][0]
    assert any("empty body text" in " ".join(item["reasons"]) for item in result["frame_candidates"])


def test_www_entry_frame_is_not_selected_as_b03() -> None:
    page = type("Page", (), {"frames": [MockFrame("", "http://www.gts362.com/", body_text(), live_elements())]})()

    result = collect_live_b03_snapshot(page)

    assert result["frame_url"] == ""
    assert result["b03_detected"] is False
    assert "no live B03 mainFrame detected" in result["errors"][0]


def test_login_page_frame_is_not_selected_as_b03() -> None:
    login_text = "\u5e33\u865f \u5bc6\u78bc \u767b\u5165 \u4e0b\u8f09Chrome"
    page = type(
        "Page",
        (),
        {"frames": [MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", login_text, live_elements())]},
    )()

    result = collect_live_b03_snapshot(page)

    assert result["frame_name"] == ""
    assert "no live B03 mainFrame detected" in result["errors"][0]


def test_404_page_frame_is_not_selected_as_b03() -> None:
    text = "HTTP 404 \u627e\u4e0d\u5230\u8cc7\u6e90 \u8981\u6c42\u7684 URL"
    page = MockPage(frames=[MockFrame("mainFrame", "http://w0.gts362.com/gid/Front/B/B03", text, live_elements())])

    result = collect_live_b03_snapshot(page)

    assert result["frame_name"] == ""
    assert "no live B03 mainFrame detected" in result["errors"][0]


def test_live_snapshot_mapping_finds_39_numbers() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())

    assert mapping["number_selectors_count"] == 39
    assert mapping["number_selectors"]["01"][0]["source"] == "live_body_text"
    assert mapping["number_selectors"]["39"][0]["confidence"] == "medium"


def test_numbers_are_detected_only_from_selected_frame_body_text() -> None:
    bad_snapshot = {
        **snapshot(),
        "body_text": "539 - \u4e0b\u6ce8\u8cc7\u8a0a \u5feb\u901f\u8f38\u5165",
        "elements": [{"text": body_text(), "textContent": body_text(), "tag": "div"}],
        "b03_detected": True,
    }

    mapping = build_b03_selector_mapping_from_live_snapshot(bad_snapshot)

    assert mapping["number_selectors_count"] == 0


def test_quick_input_is_detected_only_from_selected_frame_elements() -> None:
    bad_snapshot = {**snapshot(), "elements": [], "b03_detected": True}

    mapping = build_b03_selector_mapping_from_live_snapshot(bad_snapshot)

    assert mapping["quick_input_found"] is False


def test_no_qualified_frame_does_not_fake_39_numbers() -> None:
    page = type("Page", (), {"frames": [MockFrame("", "http://www.gts362.com/", "", live_elements())]})()

    mapping = build_b03_selector_mapping_from_live_snapshot(collect_live_b03_snapshot(page))

    assert mapping["number_selectors_count"] == 0
    assert mapping["debug"]["b03_text_detected"] is False
    assert "no live B03 mainFrame detected" in mapping["errors"][0]


def test_live_snapshot_mapping_finds_quick_input_and_restricted_send() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())

    assert mapping["quick_input_found"] is True
    assert mapping["quick_input"]["number_input_candidates"][0]["selector_hint"] == 'input[data-bind*="QkNums"]'
    assert mapping["quick_input"]["send_button_candidates"][0]["restricted"] is True
    assert mapping["quick_input"]["send_button_candidates"][0]["selector_hint"] == 'button[data-bind*="Mo.OnAddSel"]'


def test_live_snapshot_mapping_detects_danger_candidates() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())
    danger_text = "\n".join(item["text"] for item in mapping["danger_candidates"])

    assert "\u9001\u51fa\u6ce8\u55ae" in danger_text
    assert "\u9001\u51fa" in danger_text
    assert "\u6e05\u9664" in danger_text
    assert "\u78ba\u8a8d" in danger_text
    assert "\u78ba\u5b9a" in danger_text
    assert "\u522a\u9664" in danger_text
    assert "close" in danger_text
    assert "\u52a0\u5165\u5e38\u7528\u724c\u7d44" in danger_text


def test_live_snapshot_json_keeps_full_danger_candidates_but_adds_unique_texts() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())

    assert len(mapping["danger_candidates"]) > len(mapping["unique_danger_texts"])
    assert mapping["unique_danger_texts"] == [
        "\u9001\u51fa\u6ce8\u55ae",
        "\u9001\u51fa",
        "\u78ba\u8a8d",
        "\u78ba\u5b9a",
        "\u6e05\u9664",
        "\u6e05",
        "\u522a\u9664",
        "\u52a0\u5165\u5e38\u7528\u724c\u7d44",
        "close",
        "\u4e0b\u6ce8\u8cc7\u8a0a",
    ]


def test_pretty_danger_report_is_unique_ordered_and_marks_text_only() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())
    pretty = format_pretty_live_b03_selector_mapping(mapping)
    danger_section = pretty.split("Danger:", 1)[1].split("Final:", 1)[0]

    expected_lines = [
        "- \u9001\u51fa\u6ce8\u55ae",
        "- \u9001\u51fa",
        "- \u78ba\u8a8d",
        "- \u78ba\u5b9a",
        "- \u6e05\u9664",
        "- \u6e05",
        "- \u522a\u9664",
        "- \u52a0\u5165\u5e38\u7528\u724c\u7d44",
        "- close",
        "- \u4e0b\u6ce8\u8cc7\u8a0a text only",
    ]
    actual_lines = [line.strip() for line in danger_section.splitlines() if line.strip()]

    assert actual_lines == expected_lines
    assert danger_section.count("\u9001\u51fa\u6ce8\u55ae") == 1
    assert danger_section.count("\u4e0b\u6ce8\u8cc7\u8a0a text only") == 1


def test_live_snapshot_mapping_detects_amount_labels() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())

    assert mapping["amount_fields_confident"] is False
    for star in [TWO_STAR, THREE_STAR, FOUR_STAR]:
        assert mapping["amount_field_candidates"][star][0]["labels_detected"] is True
        assert mapping["amount_field_candidates"][star][0]["selector_confidence"] == "low"


def test_live_snapshot_mapping_safe_to_assisted_fill_is_always_false() -> None:
    mapping = build_b03_selector_mapping_from_live_snapshot(snapshot())

    assert mapping["safe_to_assisted_fill"] is False


def test_live_b03_snapshot_source_has_no_site_operations() -> None:
    source = inspect.getsource(live_b03_snapshot)

    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".press(" not in source
    assert "submit(" not in source
