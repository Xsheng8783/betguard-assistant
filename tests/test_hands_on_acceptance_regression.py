"""Deterministic regressions from the hands-on acceptance audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from betguard.semantic_parser import parse_ocr_text
from betguard.vision.structure_reconstruction import reconstruct_structure
from betguard.webfill.web_assist_session import _assist_panel_popup_script
from betguard.webui import app as webui_app


def _bbox(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "coordinate_space": "pixel",
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }


def _token(line_id: str, index: int, text: str, bbox: tuple[float, ...]) -> dict:
    return {
        "token_id": f"{line_id}-T{index:02d}",
        "text": text,
        "bounding_box": _bbox(*bbox),
    }


def _source(
    sections: list[dict],
    line_specs: list[tuple[str, list[tuple[str, tuple[float, ...]]]]],
) -> dict:
    lines = []
    for line_id, specs in line_specs:
        tokens = [
            _token(line_id, index, text, bbox)
            for index, (text, bbox) in enumerate(specs, start=1)
        ]
        lines.append({
            "line_id": line_id,
            "text": " ".join(text for text, _bbox_value in specs),
            "tokens": tokens,
        })
    return {
        "status": "completed",
        "provider": {"id": "qwen-dashscope", "model": "qwen3-vl-plus"},
        "source_image": {"width": 1000},
        "raw_text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "preprocessing": {"qwen_response": {"sections": sections}},
    }


def _row(
    numbers: list[list[str]],
    *,
    multiplier: str | None = None,
    layout: str = "column_bet",
) -> dict:
    return {
        "numbers": numbers,
        "multiplier": multiplier,
        "layout_hint": layout,
        "tokens": [],
    }


def _primary(source: dict) -> dict:
    return next(
        item for item in reconstruct_structure(source, game="539")
        if item["line_role"] == "primary"
    )


def test_sample011_s02_acceptance_ledger_keeps_ocr_and_ui_causes_separate() -> None:
    ledger_path = (
        Path(__file__).resolve().parents[1]
        / "sample-034-debug"
        / "hands_on_acceptance_regression_ledger.json"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = {
        entry["bet_structure"]: entry
        for entry in ledger["entries"]
        if entry["sample"] == "sample-011" and entry["qwen_section"] == "S02"
    }

    number = entries["S02 first number 30 read as 34"]
    assert number["root_cause_classification"] == "OCR_SUBSTITUTION"
    assert number["action"] == "DEFER_TO_OCR_GATE_OR_HUMAN_EDIT"
    assert number["qwen_row"]["numbers"][0] == ["34"]
    assert "30" not in number["qwen_tokens"]

    multiplier = entries["S02 reconstructed multiplier hidden from review card"]
    assert multiplier["qwen_multiplier"] == "3/4x1"
    assert multiplier["reconstructed_candidate"]["multiplier_rules"] == ["3/4X1"]
    assert multiplier["old_ui_candidate"]["multiplier_rules"] == []
    assert multiplier["ui_candidate"]["multiplier_rules"] == ["3/4X1"]
    assert multiplier["root_cause_classification"] == "UI_SESSION_PRESENTATION_ERROR"
    assert multiplier["action"] == "FIXED"


def test_sample006_truncation_diagnostic_is_recorded_without_live_rerun() -> None:
    ledger_path = (
        Path(__file__).resolve().parents[1]
        / "sample-034-debug"
        / "hands_on_acceptance_regression_ledger.json"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    diagnostic = next(
        item for item in ledger["provider_failure_diagnostics"]
        if item["sample"] == "sample-006"
    )

    assert diagnostic == {
        "sample": "sample-006",
        "request_id": "job-e52e3fcff11a4ae2b278596cc0d5ed17",
        "latency_s": 110.48,
        "finish_reason": "length",
        "completion_tokens": 8000,
        "total_tokens": 9382,
        "image_tokens": 882,
        "response_content_length": 18019,
        "classification": "QWEN_OUTPUT_TRUNCATED",
        "secondary_classification": "NESTED_JSON_SALVAGE_MISLEADING_ERROR",
        "external_qwen_dashscope_calls": 0,
    }


def test_first_column_continuation_uses_bbox_not_right_alignment() -> None:
    source = _source(
        [{"rows": [
            _row([["21"], ["23"], ["34"], ["37"]]),
            _row([["35"]]),
        ]}],
        [
            ("S01-L01", [
                ("21", (92, 10, 122, 42)),
                ("23", (148, 10, 188, 42)),
                ("34", (215, 10, 255, 42)),
                ("37", (282, 10, 322, 42)),
            ]),
            ("S01-L02", [("35", (92, 50, 122, 82))]),
        ],
    )

    evidence = _primary(source)
    assert evidence["model_candidate"]["numbers"] == [
        ["21", "35"], ["23"], ["34"], ["37"],
    ]
    assert evidence["reconstructed_candidate"]["number_groups"] == [
        ["21", "35"], ["23"], ["34"], ["37"],
    ]
    assert evidence["status"] == "consistent"


def test_last_column_continuation_uses_bbox_not_row_length() -> None:
    source = _source(
        [{"rows": [
            _row([["34"], ["23"], ["35"], ["27"]]),
            _row([["37"]]),
        ]}],
        [
            ("S01-L01", [
                ("34", (100, 10, 130, 42)),
                ("23", (180, 10, 210, 42)),
                ("35", (260, 10, 290, 42)),
                ("27", (340, 10, 370, 42)),
            ]),
            ("S01-L02", [("37", (380, 50, 410, 82))]),
        ],
    )

    evidence = _primary(source)
    assert evidence["reconstructed_candidate"]["number_groups"] == [
        ["34"], ["23"], ["35"], ["27", "37"],
    ]
    assert evidence["status"] == "consistent"


def test_ambiguous_continuation_geometry_fails_closed_without_guessing() -> None:
    source = _source(
        [{"rows": [
            _row([["21"], ["23"], ["37"]]),
            _row([["35"]]),
        ]}],
        [
            ("S01-L01", [
                ("21", (90, 10, 110, 42)),
                ("23", (190, 10, 210, 42)),
                ("37", (290, 10, 310, 42)),
            ]),
            ("S01-L02", [("35", (240, 50, 260, 82))]),
        ],
    )

    evidence = _primary(source)
    assert evidence["status"] == "incomplete"
    assert "column_continuation_alignment_ambiguous" in evidence["warnings"]
    assert "35" not in sum(
        evidence["reconstructed_candidate"]["number_groups"],
        [],
    )


def test_two_multiplier_y_bands_on_one_source_line_remain_two_rules() -> None:
    line_id = "S01-L01"
    source = _source(
        [{"rows": [_row(
            [["11", "15", "24", "37"]],
            multiplier="2X5 3X2",
            layout="normal_row",
        )]}],
        [(line_id, [
            ("11", (40, 120, 80, 155)),
            ("15", (120, 120, 170, 155)),
            ("24", (210, 120, 260, 155)),
            ("37", (295, 120, 350, 155)),
            ("2", (420, 125, 445, 150)),
            ("x", (450, 125, 470, 150)),
            ("5", (480, 125, 510, 150)),
            ("3", (420, 165, 445, 190)),
            ("x", (450, 165, 470, 190)),
            ("2", (480, 165, 510, 190)),
        ])],
    )

    before = copy.deepcopy(source)
    evidence = _primary(source)
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == [
        "2X5", "3X2",
    ]
    assert evidence["status"] == "consistent"
    assert source == before


def test_identical_number_geometry_sections_coalesce_as_one_structure() -> None:
    number_specs = [
        ("32", (62, 315, 102, 347)),
        ("34", (120, 315, 160, 347)),
        ("35", (178, 315, 218, 347)),
    ]
    source = _source(
        [
            {"rows": [_row(
                [["32", "34", "35"]],
                multiplier="2X2",
                layout="normal_row",
            )]},
            {"rows": [_row(
                [["32", "34", "35"]],
                multiplier="3X5",
                layout="normal_row",
            )]},
        ],
        [
            ("S01-L01", [
                *number_specs,
                ("2", (235, 305, 265, 347)),
                ("x", (268, 305, 288, 347)),
                ("2", (292, 305, 322, 347)),
            ]),
            ("S02-L01", [
                *number_specs,
                ("3", (235, 335, 265, 367)),
                ("x", (268, 335, 288, 367)),
                ("5", (292, 335, 322, 367)),
            ]),
        ],
    )

    evidence = reconstruct_structure(source, game="539")
    primary = next(item for item in evidence if item["line_role"] == "primary")
    continuation = next(item for item in evidence if item["line_role"] == "continuation")
    assert primary["primary_line_id"] == "S01-L01"
    assert primary["member_line_ids"] == ["S01-L01", "S02-L01"]
    assert primary["reconstructed_candidate"] == {
        "number_groups": [["32", "34", "35"]],
        "multiplier_rules": ["2X2", "3X5"],
        "layout": "normal_row",
        "collision": None,
        "shared_multiplier": None,
    }
    assert primary["status"] == "consistent"
    assert "reconstructed_candidate" not in continuation


def test_slash_group_token_before_separate_multiplier_is_retained_as_number() -> None:
    source = _source(
        [{"rows": [_row(
            [["34"], ["23"], ["35"], ["27"]],
            multiplier="27/37x1",
        )]}],
        [("S01-L01", [
            ("34", (585, 115, 615, 147)),
            ("x", (618, 115, 638, 147)),
            ("23", (642, 115, 682, 147)),
            ("x", (685, 115, 705, 147)),
            ("35", (708, 115, 748, 147)),
            ("x", (752, 115, 772, 147)),
            ("27", (775, 105, 805, 147)),
            ("/", (805, 115, 822, 147)),
            ("37", (822, 115, 852, 147)),
            ("2", (869, 105, 899, 147)),
            ("x", (902, 105, 922, 147)),
            ("1", (925, 105, 955, 147)),
        ])],
    )

    evidence = _primary(source)
    assert evidence["reconstructed_candidate"]["number_groups"] == [
        ["34"], ["23"], ["35"], ["27", "37"],
    ]
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == ["2X1"]
    assert evidence["status"] == "incomplete"
    assert evidence["evidence"]["bbox_debug"]["recovered_number_token_ids"] == [
        "S01-L01-T09",
    ]


@pytest.mark.parametrize(
    ("shorthand", "stars"),
    [
        ("23X1", [2, 3]),
        ("34X1", [3, 4]),
        ("234X1", [2, 3, 4]),
        ("二三X1", [2, 3]),
        ("三四X1", [3, 4]),
        ("二三四X1", [2, 3, 4]),
    ],
)
def test_multiplier_shorthand_parser_regression(
    shorthand: str,
    stars: list[int],
) -> None:
    bet = parse_ocr_text(f"01 02 03 {shorthand}", game="539")
    assert bet.stars == stars
    assert bet.unit == 1.0


@pytest.mark.parametrize("port", [8765, 8766])
def test_assist_panel_url_uses_actual_server_port(port: int) -> None:
    assert webui_app._assist_panel_url_for_server(("127.0.0.1", port)) == (
        f"http://127.0.0.1:{port}/assist-panel"
    )


@pytest.mark.parametrize("port", [8765, 8766])
def test_open_site_handler_propagates_actual_assist_panel_port(
    monkeypatch: pytest.MonkeyPatch,
    port: int,
) -> None:
    captured: dict = {}

    class Worker:
        def dispatch(self, command: str, payload: dict) -> dict:
            captured.update({"command": command, "payload": payload})
            return {"ok": True}

    monkeypatch.setattr(
        "betguard.webfill.web_assist_session.get_assist_session",
        lambda: Worker(),
    )
    handler_type = webui_app.build_workbench_handler(
        project_version="test",
        git_commit="test",
    )
    sent: list[dict] = []
    fake_handler = SimpleNamespace(
        server=SimpleNamespace(server_address=("127.0.0.1", port)),
        _send_json=sent.append,
    )

    handler_type._handle_assist_fill_open_site(fake_handler)

    assert captured["payload"]["assist_panel_url"] == (
        f"http://127.0.0.1:{port}/assist-panel"
    )
    assert sent == [{"ok": True}]


def test_worker_popup_script_has_no_fixed_8765_when_given_8766() -> None:
    script = _assist_panel_popup_script("http://127.0.0.1:8766/assist-panel")
    assert "127.0.0.1:8766/assist-panel" in script
    assert "127.0.0.1:8765/assist-panel" not in script
