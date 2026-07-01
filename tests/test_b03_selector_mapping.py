import inspect
import sys

from betguard.webfill import fill_mapping
from betguard.webfill import cli as webfill_cli
from betguard.webfill.fill_mapping import (
    build_b03_selector_mapping,
    collect_element_like_dicts,
    format_pretty_b03_selector_mapping,
    normalize_b03_route_elements,
)


STAR_ROUTE = "\u4e8c\u4e09\u56db\u661f"
TWO_STAR = "\u4e8c\u661f"
THREE_STAR = "\u4e09\u661f"
FOUR_STAR = "\u56db\u661f"


def element(**overrides) -> dict:
    data = {
        "tag": "span",
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
        "frame_url": "http://w0.gts362.com/gid/Front/B/B03",
        "source_url": "http://w0.gts362.com/gid/Front/B/B03",
        "source_kind": "route_probe",
        "frame_name": "mainFrame",
        "index": 1,
    }
    data.update(overrides)
    return data


def number_elements() -> list[dict]:
    return [
        element(
            text=f"{number:02d}",
            textContent=f"{number:02d}",
            outerHTML=f"<span data-bind=\"html: NO\">{number:02d}</span>",
            parentHTML="<td data-bind=\"click: Mo.OnSwitchSel\"></td>",
            index=number,
        )
        for number in range(1, 40)
    ]


def route_probe_report(extra_elements: list[dict] | None = None, *, login: bool = False) -> dict:
    elements = number_elements() + [
        element(
            tag="input",
            name="qk_nums",
            outerHTML="<input data-bind=\"value: QkNums, event: { change: Mo.OnChkNo }\">",
            parentText="\u5feb\u901f\u8f38\u5165 \u865f\u78bc",
            index=100,
        ),
        element(tag="button", text="\u9001\u51fa", parentText="\u5feb\u901f\u8f38\u5165", index=101),
        element(tag="input", name="two_amount", parentText=f"{TWO_STAR} \u672c\u91d1 \u6bcf\u78b0\u91d1\u984d", index=102),
        element(tag="input", name="three_amount", parentText=f"{THREE_STAR} \u672c\u91d1 \u6bcf\u78b0\u91d1\u984d", index=103),
        element(tag="input", name="four_amount", parentText=f"{FOUR_STAR} \u672c\u91d1 \u4e0b\u6ce8\u91d1\u984d", index=104),
        element(tag="button", text="\u9001\u51fa\u6ce8\u55ae", index=105),
        element(tag="button", text="\u78ba\u8a8d", index=106),
        element(tag="button", text="\u4e0b\u6ce8", index=107),
        element(tag="button", text="\u6e05\u9664", index=108),
        element(tag="button", text="\u522a\u9664", index=109),
        element(tag="button", text="\u52a0\u5165\u5e38\u7528\u724c\u7d44", index=110),
    ]
    if extra_elements:
        elements.extend(extra_elements)
    return {
        "mode": "selector_discovery_route_probe",
        "global_config": {
            "game_id": 13,
            "routes": {STAR_ROUTE: "/Front/B/B03"},
        },
        "route_probe": {
            "route_name": STAR_ROUTE,
            "route_path": "/Front/B/B03",
            "resolved_url": "http://w0.gts362.com/gid/Front/B/B03",
            "actual_url": "http://w0.gts362.com/gid/Front/B/B03",
            "appears_login_page": login,
            "is_404_page": False,
        },
        "route_probe_elements": elements,
        "elements_sample_by_frame": {"mainFrame | http://w0.gts362.com/gid/Front/B/B03": elements[:20]},
    }


def full_b03_text() -> str:
    numbers = " ".join(f"{number:02d}" for number in range(1, 40))
    return (
        f"539 - \u4e0b\u6ce8\u8cc7\u8a0a {TWO_STAR} {THREE_STAR} {FOUR_STAR} "
        f"\u672c\u91d1 \u6bcf\u78b0\u91d1\u984d \u4e0b\u6ce8\u91d1\u984d "
        f"\u5feb\u901f\u8f38\u5165 \u865f\u78bc \u9001\u51fa {numbers} "
        "\u6e05\u9664 \u9001\u51fa\u6ce8\u55ae \u78ba\u8a8d \u52a0\u5165\u5e38\u7528\u724c\u7d44"
    )


def sample_only_report() -> dict:
    elements = [
        element(
            tag="div",
            text="",
            grandparentText=full_b03_text(),
            outerHTML='<div><input class="btn1" type="text" data-bind="value: QkNums, event: { change: Mo.OnChkNo }"></div>',
            grandparentHTML='<button class="button1" type="button" data-bind="click: function (p0, p1) { Mo.OnAddSel(p0, p1) }">送出</button>',
        )
    ]
    return {
        "mode": "selector_discovery_route_probe",
        "global_config": {
            "game_id": 13,
            "routes": {STAR_ROUTE: "/Front/B/B03"},
        },
        "route_probe": {
            "route_name": STAR_ROUTE,
            "route_path": "/Front/B/B03",
            "resolved_url": "http://w0.gts362.com/gid/Front/B/B03",
            "actual_url": "http://w0.gts362.com/gid/Front/B/B03",
            "appears_login_page": False,
            "is_404_page": False,
        },
        "elements_sample_by_frame": {"mainFrame | http://w0.gts362.com/gid/Front/B/B03": elements},
    }


def sample_frames_dict_report() -> dict:
    return {
        "route_probe": {"route_path": "/Front/B/B03", "appears_login_page": False},
        "elements_sample_by_frame": {
            "frames": [
                {
                    "frame_name": "mainFrame",
                    "frame_url": "http://w0.gts362.com/gid/Front/B/B03",
                    "elements": [element(grandparentText=full_b03_text())],
                }
            ]
        },
    }


def sample_frames_list_report() -> dict:
    return {
        "route_probe": {"route_path": "/Front/B/B03", "appears_login_page": False},
        "elements_sample_by_frame": [
            {
                "frame_name": "mainFrame",
                "frame_url": "http://w0.gts362.com/gid/Front/B/B03",
                "elements": [element(grandparentText=full_b03_text())],
            }
        ],
    }


def frame_url_only_report() -> dict:
    item = element(frame_name="", source_kind="", grandparentText=full_b03_text())
    return {
        "route_probe": {"route_path": "/Front/B/B03", "appears_login_page": False},
        "elements_sample_by_frame": {
            "page_2_frame_4": [
                {
                    **item,
                    "frame_name": "",
                    "source_kind": "",
                    "frame_url": "http://w0.gts362.com/gid/Front/B/B03",
                    "source_url": "http://w0.gts362.com/gid/Front/B/B03",
                }
            ]
        },
    }


def nested_pages_report() -> dict:
    return {
        "route_probe": {
            "route_path": "/Front/B/B03",
            "actual_url": "http://w0.gts362.com/gid/Front/B/B03",
            "appears_login_page": False,
        },
        "pages": [
            {
                "frames": [
                    {
                        "name": "mainFrame",
                        "url": "http://w0.gts362.com/gid/Front/B/B03",
                        "elements": [element(grandparentText=full_b03_text())],
                    }
                ]
            }
        ],
    }


def unknown_nested_report() -> dict:
    item = element(
        frame_name="mainFrame",
        source_kind="route_probe",
        grandparentText=full_b03_text(),
        outerHTML=(
            '<section><input class="btn1" type="text" data-bind="value: QkNums, event: { change: Mo.OnChkNo }">'
            '<button class="button1" type="button" data-bind="click: function () { Mo.OnAddSel() }">送出</button></section>'
        ),
    )
    return {
        "mode": "selector_discovery",
        "elements_sample_by_frame": {
            "mystery": {
                "deep": {
                    "wrapper": [
                        {
                            "not_elements": {
                                "payload": item,
                            }
                        }
                    ]
                }
            }
        },
        "route_probe": {
            "route_path": "/Front/B/B03",
            "actual_url": "http://w0.gts362.com/gid/Front/B/B03",
            "appears_login_page": False,
        },
    }


def test_collect_element_like_dicts_recurses_arbitrary_dicts_and_lists() -> None:
    nested = {
        "a": [
            {"b": {"grandparentText": "539 - \u4e0b\u6ce8\u8cc7\u8a0a", "frame_name": "mainFrame"}},
            {"not_element": True},
        ]
    }

    found = collect_element_like_dicts(nested)

    assert len(found) == 1
    assert found[0]["frame_name"] == "mainFrame"


def test_normalize_b03_route_elements_reads_unknown_nested_sample_structure() -> None:
    elements = normalize_b03_route_elements(unknown_nested_report())

    assert len(elements) == 1
    assert elements[0]["source_kind"] == "route_probe"
    assert elements[0]["frame_name"] == "mainFrame"
    assert "01" in elements[0]["grandparentText"]


def test_normalize_b03_route_elements_reads_sample_by_frame_mainframe() -> None:
    elements = normalize_b03_route_elements(sample_only_report())

    assert len(elements) == 1
    assert elements[0]["source_kind"] == "route_probe"
    assert elements[0]["frame_name"] == "mainFrame"
    assert "539 - \u4e0b\u6ce8\u8cc7\u8a0a" in elements[0]["grandparentText"]


def test_normalize_b03_route_elements_reads_nested_pages_frames() -> None:
    elements = normalize_b03_route_elements(nested_pages_report())

    assert len(elements) == 1
    assert elements[0]["frame_name"] == "mainFrame"
    assert "01" in elements[0]["grandparentText"]


def test_normalize_b03_route_elements_reads_dict_frames_shape() -> None:
    elements = normalize_b03_route_elements(sample_frames_dict_report())

    assert len(elements) == 1
    assert elements[0]["frame_name"] == "mainFrame"
    assert "539 - \u4e0b\u6ce8\u8cc7\u8a0a" in elements[0]["grandparentText"]


def test_normalize_b03_route_elements_reads_list_frames_shape() -> None:
    elements = normalize_b03_route_elements(sample_frames_list_report())

    assert len(elements) == 1
    assert elements[0]["frame_name"] == "mainFrame"


def test_normalize_b03_route_elements_accepts_b03_frame_url_without_frame_name() -> None:
    elements = normalize_b03_route_elements(frame_url_only_report())

    assert len(elements) == 1
    assert elements[0]["source_kind"] == "route_probe"
    assert elements[0]["frame_url"].endswith("/Front/B/B03")


def test_cli_map_b03_selectors_uses_live_snapshot_runner(monkeypatch) -> None:
    seen: dict[str, bool] = {}

    def fake_run_selector_discovery(url: str, *, probe_route: str | None = None) -> dict:
        raise AssertionError("map-b03-selectors should use live DOM snapshot mapping")

    def fake_run_selector_route_probe(*_args, **_kwargs) -> dict:
        raise AssertionError("map-b03-selectors should not use route probe report mapping")

    def fake_run_live_b03_selector_mapping(url: str) -> dict:
        assert url == "http://www.gts362.com"
        seen["live_runner_called"] = True
        return {
            "mode": "b03_selector_mapping",
            "page": {},
            "number_selectors_count": 0,
            "quick_input": {},
            "amount_field_candidates": {},
            "danger_candidates": [],
            "safe_to_assisted_fill": False,
            "debug": {},
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(webfill_cli, "run_selector_discovery", fake_run_selector_discovery)
    monkeypatch.setattr(webfill_cli, "run_selector_route_probe", fake_run_selector_route_probe)
    monkeypatch.setattr(webfill_cli, "run_live_b03_selector_mapping", fake_run_live_b03_selector_mapping)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--url",
            "http://www.gts362.com",
            "--dry-run",
            "--map-b03-selectors",
        ],
    )

    webfill_cli.main()

    assert seen["live_runner_called"] is True


def test_b03_mapping_detects_page_game_and_frame() -> None:
    mapping = build_b03_selector_mapping(route_probe_report())

    assert mapping["page"]["game"] == "539"
    assert mapping["page"]["route"] == "/Front/B/B03"
    assert mapping["page"]["frame_name"] == "mainFrame"
    assert mapping["page"]["url"] == "http://w0.gts362.com/gid/Front/B/B03"


def test_b03_mapping_finds_01_to_39_number_selectors() -> None:
    mapping = build_b03_selector_mapping(route_probe_report())

    assert mapping["number_selectors_count"] == 39
    assert all(mapping["number_selectors"][f"{number:02d}"] for number in range(1, 40))


def test_b03_mapping_finds_quick_input_and_restricted_send_button() -> None:
    mapping = build_b03_selector_mapping(route_probe_report())

    assert mapping["quick_input_found"] is True
    assert mapping["quick_input"]["number_input_candidates"]
    send_candidates = mapping["quick_input"]["send_button_candidates"]
    assert send_candidates
    assert send_candidates[0]["restricted"] is True


def test_b03_mapping_finds_numbers_from_grandparent_text_fallback() -> None:
    mapping = build_b03_selector_mapping(sample_only_report())

    assert mapping["number_selectors_count"] == 39
    assert mapping["number_selectors"]["01"][0]["source"] == "page_text_fallback"
    assert mapping["number_selectors"]["39"][0]["confidence"] == "medium"
    assert mapping["debug"]["b03_text_detected"] is True
    assert mapping["debug"]["raw_element_like_dicts_found"] > 0
    assert mapping["debug"]["B03_candidate_elements"] > 0
    assert mapping["debug"]["first_b03_frame_url"].endswith("/Front/B/B03")


def test_b03_mapping_finds_quick_input_from_outer_html_and_send_restricted() -> None:
    mapping = build_b03_selector_mapping(sample_only_report())

    assert mapping["quick_input_found"] is True
    assert mapping["quick_input"]["number_input_candidates"][0]["selector_hint"] == "input[data-bind*='QkNums']"
    assert mapping["quick_input"]["send_button_candidates"][0]["restricted"] is True
    assert mapping["quick_input"]["send_button_candidates"][0]["reason"] == "quick input send button must not be auto-clicked"


def test_b03_mapping_from_unknown_nested_report_finds_expected_page_data() -> None:
    mapping = build_b03_selector_mapping(unknown_nested_report())
    danger_text = "\n".join(item["text"] for item in mapping["danger_candidates"])

    assert mapping["debug"]["route_elements"] > 0
    assert mapping["debug"]["mainFrame_elements"] > 0
    assert mapping["debug"]["b03_text_detected"] is True
    assert mapping["number_selectors_count"] == 39
    assert mapping["quick_input_found"] is True
    assert mapping["quick_input"]["send_button_candidates"][0]["restricted"] is True
    assert "\u9001\u51fa\u6ce8\u55ae" in danger_text
    assert "\u6e05\u9664" in danger_text
    assert "\u78ba\u8a8d" in danger_text


def test_b03_mapping_detects_amount_fields_and_danger_buttons() -> None:
    mapping = build_b03_selector_mapping(route_probe_report())
    danger_text = "\n".join(item["text"] for item in mapping["danger_candidates"])

    assert mapping["amount_fields_confident"] is True
    assert mapping["amount_field_candidates"][TWO_STAR]
    assert mapping["amount_field_candidates"][THREE_STAR]
    assert mapping["amount_field_candidates"][FOUR_STAR]
    for keyword in ["\u9001\u51fa\u6ce8\u55ae", "\u78ba\u8a8d", "\u4e0b\u6ce8", "\u6e05\u9664", "\u522a\u9664"]:
        assert keyword in danger_text


def test_b03_mapping_detects_text_dangers_and_amount_labels_from_page_text() -> None:
    mapping = build_b03_selector_mapping(sample_only_report())
    danger_text = "\n".join(item["text"] for item in mapping["danger_candidates"])

    assert "\u9001\u51fa\u6ce8\u55ae" in danger_text
    assert "\u6e05\u9664" in danger_text
    assert "\u78ba\u8a8d" in danger_text
    assert "\u52a0\u5165\u5e38\u7528\u724c\u7d44" in danger_text
    assert mapping["amount_fields_confident"] is False
    for star in [TWO_STAR, THREE_STAR, FOUR_STAR]:
        assert mapping["amount_field_candidates"][star][0]["labels_detected"] is True
        assert mapping["amount_field_candidates"][star][0]["selector_confidence"] == "low"


def test_b03_mapping_reports_empty_route_elements_error() -> None:
    mapping = build_b03_selector_mapping({"route_probe": {"appears_login_page": False}})

    assert mapping["debug"]["route_elements"] == 0
    assert "no route_probe mainFrame elements available for B03 mapping" in mapping["errors"]


def test_b03_mapping_safe_to_assisted_fill_is_always_false() -> None:
    mapping = build_b03_selector_mapping(route_probe_report())

    assert mapping["safe_to_assisted_fill"] is False
    assert "safe_to_assisted_fill: false" in format_pretty_b03_selector_mapping(mapping)


def test_b03_mapping_ignores_login_page_elements_for_candidates() -> None:
    mapping = build_b03_selector_mapping(route_probe_report(login=True))

    assert mapping["number_selectors_count"] == 0
    assert not mapping["quick_input"]["number_input_candidates"]
    assert not any(mapping["amount_field_candidates"].values())
    assert mapping["danger_candidates"] == []


def test_b03_mapping_source_has_no_site_operations() -> None:
    source = inspect.getsource(fill_mapping)

    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".press(" not in source
    assert "submit(" not in source
