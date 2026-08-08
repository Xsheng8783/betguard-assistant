"""Gate 2A deterministic Qwen structure reconstruction tests."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from betguard.vision import service
from betguard.vision.contracts import (
    BoundingBox,
    Confidence,
    Line,
    ProviderMetadata,
    RecognitionResult,
    SourceImage,
    Token,
)
from betguard.vision.structure_reconstruction import reconstruct_structure


def _bbox(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "coordinate_space": "pixel",
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }


def _token(text: str, x: float, y: float, *, token_id: str = "") -> dict:
    return {
        "token_id": token_id,
        "text": text,
        "bounding_box": _bbox(x, y, x + 20, y + 16),
    }


def _result(
    tokens: list[dict],
    *,
    numbers: list[list[str]],
    layout: str = "normal_row",
    multiplier: str | None = None,
    line_id: str = "S01-L01",
    shared_multiplier=None,
    extra_row: dict | None = None,
) -> dict:
    for index, token in enumerate(tokens, start=1):
        token.setdefault("token_id", f"{line_id}-T{index:02d}")
    row = {
        "tokens": [
            {
                "text": token["text"],
                "bbox": _polygon_to_xyxy(token.get("bounding_box")),
            }
            for token in tokens
        ],
        "numbers": numbers,
        "multiplier": multiplier,
        "layout_hint": layout,
    }
    if extra_row:
        row.update(extra_row)
    return {
        "status": "completed",
        "provider": {"id": "qwen-dashscope"},
        "source_image": {"width": 1000},
        "raw_text": " ".join(token["text"] for token in tokens),
        "lines": [{"line_id": line_id, "text": "", "tokens": tokens}],
        "preprocessing": {
            "qwen_response": {
                "sections": [{"rows": [row], "shared_multiplier": shared_multiplier}]
            }
        },
    }


def _polygon_to_xyxy(value: dict | None) -> list[float]:
    if not value:
        return [0, 0, 1, 1]
    points = value["polygon"]
    return [
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    ]


def _one(result: dict, *, game: str = "539") -> dict:
    evidence = reconstruct_structure(result, game=game)
    assert len(evidence) == 1
    return evidence[0]


def test_normal_row_reconstructs_by_center_x_not_token_order() -> None:
    ordered = _result(
        [_token("03", 210, 10), _token("01", 10, 10), _token("02", 110, 10)],
        numbers=[["01", "02", "03"]],
    )
    reconstructed = _one(ordered)
    assert reconstructed["reconstructed_candidate"]["number_groups"] == [["01", "02", "03"]]
    assert reconstructed["status"] == "consistent"


def test_scrambled_input_order_is_geometry_stable() -> None:
    tokens = [_token("01", 10, 10), _token("02", 110, 10), _token("03", 210, 10)]
    first = _one(_result(tokens, numbers=[["01", "02", "03"]]))
    second = _one(_result(list(reversed(tokens)), numbers=[["01", "02", "03"]]))
    assert first["reconstructed_candidate"] == second["reconstructed_candidate"]


def test_column_bet_uses_bbox_and_preserves_top_to_bottom_order() -> None:
    tokens = [
        _token("04", 112, 52),
        _token("01", 10, 10),
        _token("03", 12, 50),
        _token("02", 110, 12),
    ]
    result = _one(_result(tokens, numbers=[["01", "03"], ["02", "04"]], layout="column_bet"))
    assert result["reconstructed_candidate"]["number_groups"] == [["01", "03"], ["02", "04"]]
    assert result["status"] == "consistent"


def test_column_reconstruction_is_stable_under_small_xy_jitter() -> None:
    tokens = [
        _token("01", 9, 9),
        _token("02", 112, 12),
        _token("03", 13, 48),
        _token("04", 108, 52),
    ]
    result = _one(_result(tokens, numbers=[["01", "03"], ["02", "04"]], layout="column_bet"))
    assert result["reconstructed_candidate"]["number_groups"] == [["01", "03"], ["02", "04"]]
    assert result["status"] == "consistent"


def test_partial_row_is_right_aligned() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("02", 110, 10),
        _token("03", 210, 10),
        _token("04", 112, 50),
        _token("05", 212, 50),
    ]
    expected = [["01"], ["02", "04"], ["03", "05"]]
    result = _one(_result(tokens, numbers=expected, layout="column_bet"))
    assert result["reconstructed_candidate"]["number_groups"] == expected
    assert result["status"] == "consistent"


def test_x_separator_is_not_a_multiplier() -> None:
    tokens = [_token("01", 10, 10), _token("x", 65, 10), _token("02", 120, 10)]
    result = _one(_result(tokens, numbers=[["01"], ["02"]], layout="column_bet"))
    reconstructed = result["reconstructed_candidate"]
    assert reconstructed["number_groups"] == [["01"], ["02"]]
    assert reconstructed["multiplier_rules"] == []
    separator = next(item for item in result["evidence"]["tokens"] if item["text"] == "x")
    assert separator["classification"] == "column_separator"


def test_complete_multiplier_stays_out_of_numbers_and_enters_rules() -> None:
    tokens = [_token("01", 10, 10), _token("02", 55, 10), _token("2X1", 150, 10)]
    result = _one(_result(tokens, numbers=[["01", "02"]], multiplier="2X1"))
    reconstructed = result["reconstructed_candidate"]
    assert reconstructed["number_groups"] == [["01", "02"]]
    assert reconstructed["multiplier_rules"] == ["2X1"]
    assert result["status"] == "consistent"


def test_stacked_three_four_remains_collision_play_evidence() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("x", 65, 10),
        _token("02", 120, 10),
        _token("3", 220, 10),
        _token("4", 220, 50),
    ]
    result = _one(_result(tokens, numbers=[["01"], ["02"]], layout="column_bet"))
    assert result["reconstructed_candidate"]["collision"] == "3/4"
    assert result["reconstructed_candidate"]["multiplier_rules"] == []
    assert any(warning.startswith("collision_play_evidence:") for warning in result["warnings"])
    assert result["status"] == "incomplete"


@pytest.mark.parametrize(
    ("category_tokens", "value", "expected_rule"),
    [
        (("3", "4"), "X1", "3/4X1"),
        (("2", "3"), "X0.1", "2/3X0.1"),
    ],
)
def test_stacked_collision_and_single_nearby_value_form_complete_rule(
    category_tokens: tuple[str, str],
    value: str,
    expected_rule: str,
) -> None:
    first, second = category_tokens
    tokens = [
        _token("01", 10, 10),
        _token("x", 65, 10),
        _token("02", 120, 10),
        _token(first, 760, 10),
        _token(second, 760, 35),
        _token(value, 800, 35),
    ]
    collision = "/".join(category_tokens)
    result = _one(_result(
        tokens,
        numbers=[["01"], ["02"]],
        layout="column_bet",
        multiplier=expected_rule,
        extra_row={"collision": collision},
    ))

    reconstructed = result["reconstructed_candidate"]
    assert reconstructed["number_groups"] == [["01"], ["02"]]
    assert reconstructed["collision"] == collision
    assert reconstructed["multiplier_rules"] == [expected_rule]
    assert result["status"] == "consistent"
    stacked = result["evidence"]["bbox_debug"]["stacked_collision_categories"]
    assert len(stacked) == 1
    assert stacked[0]["text"] == collision
    assert stacked[0]["geometry"] == "stacked_bbox"
    classifications = {
        item["text"]: item["classification"]
        for item in result["evidence"]["tokens"]
    }
    assert classifications[first] == "multiplier_fragment_candidate"
    assert classifications[second] == "multiplier_fragment_candidate"


def test_stacked_collision_with_two_nearby_values_is_incomplete() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("x", 65, 10),
        _token("02", 120, 10),
        _token("3", 760, 10),
        _token("4", 760, 35),
        _token("X1", 800, 35),
        _token("X2", 830, 35),
    ]
    result = _one(_result(
        tokens,
        numbers=[["01"], ["02"]],
        layout="column_bet",
        multiplier="3/4X1",
        extra_row={"collision": "3/4"},
    ))

    assert result["reconstructed_candidate"]["multiplier_rules"] == []
    assert result["status"] == "incomplete"
    assert any(
        warning.startswith("collision_multiplier_value_ambiguous:")
        for warning in result["warnings"]
    )
    partial_values = [
        item for item in result["evidence"]["tokens"]
        if item["text"] in {"X1", "X2"}
    ]
    assert all(
        item["classification"] == "partial_multiplier_evidence"
        for item in partial_values
    )


def test_stacked_collision_value_too_far_is_incomplete() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("x", 65, 10),
        _token("02", 120, 10),
        _token("3", 760, 10),
        _token("4", 760, 35),
        _token("X1", 900, 35),
    ]
    result = _one(_result(
        tokens,
        numbers=[["01"], ["02"]],
        layout="column_bet",
        multiplier="3/4X1",
        extra_row={"collision": "3/4"},
    ))

    assert result["reconstructed_candidate"]["multiplier_rules"] == []
    assert result["status"] == "incomplete"
    assert "fragment_multiplier_incomplete" in result["warnings"]


def test_stacked_collision_never_pairs_value_from_different_line() -> None:
    source = _result(
        [
            _token("01", 10, 10),
            _token("x", 65, 10),
            _token("02", 120, 10),
            _token("3", 760, 10),
            _token("4", 760, 35),
        ],
        numbers=[["01"], ["02"]],
        layout="column_bet",
        extra_row={"collision": "3/4"},
    )
    value = _token("X1", 800, 35, token_id="S01-L02-T01")
    source["lines"].append({
        "line_id": "S01-L02",
        "text": "X1",
        "tokens": [value],
    })
    source["preprocessing"]["qwen_response"]["sections"][0]["rows"].append({
        "tokens": [{"text": "X1", "bbox": _polygon_to_xyxy(value["bounding_box"])}],
        "numbers": [],
        "multiplier": "X1",
        "layout_hint": "column_bet",
    })

    primary = next(
        item for item in reconstruct_structure(source, game="539")
        if item["line_role"] == "primary"
    )
    assert primary["reconstructed_candidate"]["multiplier_rules"] == []
    assert primary["status"] == "incomplete"
    assert "fragment_multiplier_incomplete" in primary["warnings"]


def test_collision_comparison_is_structural_not_raw_category_order() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("x", 65, 10),
        _token("02", 120, 10),
        _token("4/3", 220, 10),
    ]
    result = _one(_result(
        tokens,
        numbers=[["01"], ["02"]],
        layout="column_bet",
        extra_row={"collision": "4/3"},
    ))
    assert result["reconstructed_candidate"]["collision"] == "3/4"
    assert result["status"] == "consistent"


@pytest.mark.parametrize("partial", ["2", "2/3", "X1", "2X", "23X35"])
def test_partial_or_invalid_multiplier_never_enters_rules(partial: str) -> None:
    tokens = [_token("01", 10, 10), _token(partial, 120, 10)]
    result = _one(_result(tokens, numbers=[["01"]], multiplier=partial))
    assert result["reconstructed_candidate"]["multiplier_rules"] == []
    assert result["status"] == "incomplete"
    token = next(item for item in result["evidence"]["tokens"] if item["text"] == partial)
    assert token.get("multiplier_classification") in {
        "partial_multiplier_evidence",
        "invalid_multiplier_token",
    }


def test_same_multiplier_values_merge_but_different_values_do_not() -> None:
    merged = _one(_result(
        [_token("01", 10, 10), _token("2X1", 100, 10), _token("3X1", 180, 10)],
        numbers=[["01"]],
        multiplier="2X1 3X1",
    ))
    separate = _one(_result(
        [_token("01", 10, 10), _token("2X1", 100, 10), _token("3X0.2", 180, 10)],
        numbers=[["01"]],
        multiplier="2X1 3X0.2",
    ))
    assert merged["reconstructed_candidate"]["multiplier_rules"] == ["2/3X1"]
    assert separate["reconstructed_candidate"]["multiplier_rules"] == ["2X1", "3X0.2"]


def test_same_numbers_but_different_columns_are_divergent() -> None:
    tokens = [_token("01", 10, 10), _token("02", 110, 10), _token("03", 210, 10)]
    result = _one(_result(tokens, numbers=[["01"], ["02", "03"]], layout="column_bet"))
    assert result["reconstructed_candidate"]["layout"] == "normal_row"
    assert result["status"] == "divergent"


def test_same_numbers_but_different_complete_multiplier_is_divergent() -> None:
    tokens = [_token("01", 10, 10), _token("3X1", 120, 10)]
    result = _one(_result(tokens, numbers=[["01"]], multiplier="2X1"))
    assert result["reconstructed_candidate"]["multiplier_rules"] == ["3X1"]
    assert result["status"] == "divergent"


def test_missing_or_nonfinite_bbox_is_incomplete() -> None:
    missing = _result([_token("01", 10, 10)], numbers=[["01"]])
    missing["lines"][0]["tokens"][0].pop("bounding_box")
    assert _one(missing)["status"] == "incomplete"

    nonfinite = _result([_token("01", 10, 10)], numbers=[["01"]])
    nonfinite["lines"][0]["tokens"][0]["bounding_box"]["polygon"][0][0] = float("nan")
    assert _one(nonfinite)["status"] == "incomplete"


def test_unsupported_bbox_coordinate_space_fails_closed() -> None:
    source = _result([_token("01", 10, 10)], numbers=[["01"]])
    source["lines"][0]["tokens"][0]["bounding_box"]["coordinate_space"] = "banana"

    evidence = _one(source)
    assert evidence["status"] == "incomplete"
    assert "bbox_coordinate_space_unsupported" in evidence["warnings"]
    assert "bbox_coordinate_space_unsupported:S01-L01-T01" in evidence["warnings"]
    assert evidence["reconstructed_candidate"]["number_groups"] == []


def test_normalized_bbox_out_of_range_fails_closed() -> None:
    source = _result([_token("01", 10, 10)], numbers=[["01"]])
    source["lines"][0]["tokens"][0]["bounding_box"] = {
        "coordinate_space": "normalized",
        "polygon": [[0.1, 0.1], [1.5, 0.1], [1.5, 0.2], [0.1, 0.2]],
    }

    evidence = _one(source)
    assert evidence["status"] == "incomplete"
    assert "bbox_normalized_out_of_range" in evidence["warnings"]
    assert "bbox_normalized_out_of_range:S01-L01-T01" in evidence["warnings"]
    assert evidence["reconstructed_candidate"]["number_groups"] == []


def test_valid_pixel_bbox_records_zone_provenance() -> None:
    evidence = _one(_result([_token("01", 10, 10)], numbers=[["01"]]))
    debug = evidence["evidence"]["bbox_debug"]

    assert evidence["status"] == "consistent"
    assert debug["zone_rule"] == "token_bbox_x1_gt_structure_rightmost_number_x2"
    assert debug["play_zone_ratio"] == 0.68
    assert debug["play_zone_boundary_x"] == 30.0
    assert debug["play_zone_boundary_source"] == "structure_relative_rightmost_number_x2"
    assert debug["global_play_zone_boundary_x"] == 680.0
    assert debug["rightmost_number_x2"] == 30.0
    assert debug["rightmost_number_token_ids"] == ["S01-L01-T01"]
    assert debug["image_width"] == 1000.0


def test_valid_normalized_bbox_records_zone_provenance() -> None:
    source = _result([_token("01", 10, 10)], numbers=[["01"]])
    source["lines"][0]["tokens"][0]["bounding_box"] = {
        "coordinate_space": "normalized",
        "polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]],
    }

    evidence = _one(source)
    debug = evidence["evidence"]["bbox_debug"]
    assert evidence["status"] == "consistent"
    assert debug["coordinate_spaces"] == ["normalized"]
    assert debug["play_zone_boundary_x"] == 0.2
    assert debug["play_zone_boundary_source"] == "structure_relative_rightmost_number_x2"
    assert debug["global_play_zone_boundary_x"] == 0.68
    assert debug["rightmost_number_x2"] == 0.2
    assert debug["play_zone_ratio"] == 0.68


def test_right_side_column_structure_uses_model_number_multiset_before_global_zone() -> None:
    tokens = [
        _token("12", 720, 10),
        _token("x", 755, 10),
        _token("15", 790, 10),
        _token("x", 825, 10),
        _token("06", 860, 10),
        _token("2", 900, 10),
        _token("x", 925, 10),
        _token("3", 950, 10),
    ]

    evidence = _one(_result(
        tokens,
        numbers=[["12"], ["15"], ["06"]],
        layout="column_bet",
        multiplier="2X3",
    ))

    assert evidence["reconstructed_candidate"]["number_groups"] == [
        ["12"],
        ["15"],
        ["06"],
    ]
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == ["2X3"]
    assert evidence["reconstructed_candidate"]["layout"] == "column_bet"
    debug = evidence["evidence"]["bbox_debug"]
    assert debug["number_boundary_source"] == "model_number_multiset"
    assert debug["matched_number_token_ids"] == [
        "S01-L01-T01",
        "S01-L01-T03",
        "S01-L01-T05",
    ]
    assert debug["unmatched_model_number_values"] == []
    assert debug["local_play_boundary_x"] == 880.0
    assert debug["global_play_boundary_x"] == 680.0
    assert debug["fallback_reason"] is None
    assert debug["ambiguous_number_play_token_ids"] == []


def test_column_structure_crossing_global_boundary_keeps_all_model_numbers() -> None:
    tokens = [
        _token("12", 620, 10),
        _token("x", 655, 10),
        _token("15", 690, 10),
        _token("x", 725, 10),
        _token("06", 760, 10),
    ]

    evidence = _one(_result(
        tokens,
        numbers=[["12"], ["15"], ["06"]],
        layout="column_bet",
    ))

    assert evidence["reconstructed_candidate"]["number_groups"] == [
        ["12"],
        ["15"],
        ["06"],
    ]
    debug = evidence["evidence"]["bbox_debug"]
    assert debug["number_boundary_source"] == "model_number_multiset"
    assert debug["matched_number_token_ids"] == [
        "S01-L01-T01",
        "S01-L01-T03",
        "S01-L01-T05",
    ]
    assert debug["local_play_boundary_x"] == 780.0
    assert debug["global_play_boundary_x"] == 680.0


def test_left_of_global_boundary_shorthand_stays_out_of_model_number_multiset() -> None:
    tokens = [
        _token("01", 100, 10),
        _token("02", 200, 10),
        _token("34", 350, 10),
        _token("X1", 390, 10),
    ]

    evidence = _one(_result(
        tokens,
        numbers=[["01", "02"]],
        multiplier="34X1",
    ))

    assert evidence["reconstructed_candidate"]["number_groups"] == [["01", "02"]]
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == ["3/4X1"]
    debug = evidence["evidence"]["bbox_debug"]
    assert debug["number_boundary_source"] == "model_number_multiset"
    assert debug["matched_number_token_ids"] == ["S01-L01-T01", "S01-L01-T02"]
    assert debug["local_play_boundary_x"] == 220.0
    assert debug["global_play_boundary_x"] == 680.0
    assert debug["ambiguous_number_play_token_ids"] == []


def test_model_number_that_also_forms_shorthand_fails_closed_as_ambiguous() -> None:
    tokens = [
        _token("01", 100, 10),
        _token("34", 350, 10),
        _token("X1", 390, 10),
    ]

    evidence = _one(_result(
        tokens,
        numbers=[["01", "34"]],
        multiplier="34X1",
    ))

    assert evidence["status"] == "incomplete"
    assert "local_number_play_scope_ambiguous" in evidence["warnings"]
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == []
    assert evidence["evidence"]["bbox_debug"]["ambiguous_number_play_token_ids"] == [
        "S01-L01-T02"
    ]


@pytest.mark.parametrize("line_id_mode", ["blank", "missing"])
def test_blank_or_missing_line_id_produces_explicit_safe_incomplete_evidence(
    line_id_mode: str,
) -> None:
    source = _result([_token("01", 10, 10)], numbers=[["01"]], line_id="")
    if line_id_mode == "missing":
        source["lines"][0].pop("line_id")

    evidence = _one(source)
    assert evidence["line_id"] == ""
    assert evidence["line_role"] == "unlinked"
    assert evidence["status"] == "incomplete"
    assert evidence["warnings"] == ["line_id_evidence_missing"]
    assert "model_candidate" not in evidence
    assert "reconstructed_candidate" not in evidence
    assert evidence["human_confirmation_required"] is True
    assert evidence["auto_apply"] is False
    assert evidence["auto_confirm"] is False
    assert evidence["auto_submit"] is False


def test_missing_exact_line_id_is_incomplete_and_never_borrows_other_row() -> None:
    result = _result([_token("01", 10, 10)], numbers=[["01"]], line_id="S01-L03")
    evidence = _one(result)
    assert evidence["line_id"] == "S01-L03"
    assert evidence["model_candidate"]["numbers"] == []
    assert evidence["status"] == "incomplete"
    assert "line_id_evidence_missing" in evidence["warnings"]


def test_game_ranges_are_explicit_and_never_pad_single_digits() -> None:
    forty = _result([_token("40", 10, 10)], numbers=[["40"]])
    assert _one(forty, game="539")["status"] == "incomplete"
    assert _one(forty, game="六合")["status"] == "consistent"

    single = _result([_token("9", 10, 10)], numbers=[["09"]])
    evidence = _one(single)
    assert evidence["reconstructed_candidate"]["number_groups"] == []
    assert any(warning.startswith("partial_number_token:") for warning in evidence["warnings"])


def test_unsupported_layout_is_reported_without_promoting_a_candidate() -> None:
    source = _result(
        [_token("尾", 10, 10)],
        numbers=[["01"]],
        layout="tail_expansion",
    )
    evidence = _one(source)
    assert evidence["status"] == "unsupported"
    assert evidence["reconstructed_candidate"]["layout"] == "unknown"
    assert evidence["auto_apply"] is False


def test_reconstruction_is_immutable_review_only_evidence() -> None:
    source = _result([_token("01", 10, 10)], numbers=[["01"]])
    before = copy.deepcopy(source)
    evidence = reconstruct_structure(source, game="539")
    assert source == before
    assert "structure_evidence" not in source
    serialized = repr(evidence)
    for forbidden in (
        "accepted_by_human",
        "executable",
        "exportable",
        "manual_candidate_id",
        "approved_fill_queue",
    ):
        assert forbidden not in serialized
    assert evidence[0]["human_confirmation_required"] is True
    assert evidence[0]["auto_apply"] is False
    assert evidence[0]["auto_confirm"] is False
    assert evidence[0]["auto_submit"] is False


def test_recognition_result_to_dict_is_deep_equal_after_reconstruction() -> None:
    source = _recognition_result()
    before = copy.deepcopy(source.to_dict())

    reconstruct_structure(source, game="539")

    assert source.to_dict() == before


def test_service_attaches_evidence_outside_result_without_extra_provider_call(monkeypatch) -> None:
    qwen_result = _recognition_result()
    before = qwen_result.to_dict()
    calls = 0

    class StubProvider:
        def recognize(self, request):
            nonlocal calls
            calls += 1
            return qwen_result

    meta = SimpleNamespace(
        storage_path=Path("unused.png"),
        mime_type="image/png",
        sha256="image-sha",
        width=40,
        height=30,
        byte_size=100,
    )
    monkeypatch.setattr(service, "get_metadata", lambda image_id: meta)
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)

    response = service.run_job("image-id", "qwen-dashscope", game="539")

    assert response["ok"] is True
    assert calls == 1
    assert response["result"] == before
    assert response["structure_evidence"][0]["status"] == "consistent"
    assert response["structure_evidence"][0]["game"] == "539"
    assert "structure_evidence" not in response["result"]


def test_multi_row_section_produces_one_primary_structure_and_continuations() -> None:
    source = _multi_row_column_result()
    evidence = reconstruct_structure(source, game="539")

    primary = next(item for item in evidence if item["line_role"] == "primary")
    continuations = [item for item in evidence if item["line_role"] == "continuation"]
    assert primary["structure_id"] == "S01"
    assert primary["primary_line_id"] == "S01-L01"
    assert primary["member_line_ids"] == ["S01-L01", "S01-L02", "S01-L03"]
    assert primary["reconstructed_candidate"]["number_groups"] == [
        ["24", "34"],
        ["03", "23"],
        ["17", "27", "37"],
        ["20", "30", "35"],
    ]
    assert primary["status"] == "consistent"
    assert [item["line_id"] for item in continuations] == ["S01-L02", "S01-L03"]
    assert sum("reconstructed_candidate" in item for item in evidence) == 1
    assert all("model_candidate" not in item for item in continuations)


def test_multi_row_structure_is_invariant_when_recognition_lines_are_scrambled() -> None:
    ordered = _multi_row_column_result()
    scrambled = copy.deepcopy(ordered)
    scrambled["lines"] = [
        scrambled["lines"][2],
        scrambled["lines"][0],
        scrambled["lines"][1],
    ]
    assert reconstruct_structure(ordered, game="539") == reconstruct_structure(
        scrambled,
        game="539",
    )


def test_left_number_zone_keeps_twenty_three_as_a_number() -> None:
    source = _result([_token("23", 100, 10)], numbers=[["23"]])
    evidence = _one(source)
    assert evidence["reconstructed_candidate"]["number_groups"] == [["23"]]
    assert evidence["evidence"]["tokens"][0]["zone"] == "main"


@pytest.mark.parametrize("category", ["23", "24"])
def test_right_play_zone_category_never_enters_number_groups(category: str) -> None:
    source = _result(
        [_token("01", 100, 10), _token(category, 800, 10)],
        numbers=[["01"]],
    )
    evidence = _one(source)
    assert evidence["reconstructed_candidate"]["number_groups"] == [["01"]]
    token = evidence["evidence"]["tokens"][1]
    assert token["zone"] == "play"
    assert token["classification"] == "play_category_evidence"
    assert evidence["status"] == "incomplete"


def test_right_play_zone_34_plus_x1_forms_rule_without_becoming_a_number() -> None:
    source = _result(
        [_token("01", 100, 10), _token("34", 760, 10), _token("X1", 810, 10)],
        numbers=[["01"]],
        multiplier="34X1",
    )
    evidence = _one(source)
    reconstructed = evidence["reconstructed_candidate"]
    assert reconstructed["number_groups"] == [["01"]]
    assert reconstructed["multiplier_rules"] == ["3/4X1"]
    assert any(warning.startswith("play_rule_reconstructed:") for warning in evidence["warnings"])


def test_left_number_and_right_complete_play_rule_remain_separate() -> None:
    source = _result(
        [_token("23", 100, 10), _token("23X1", 800, 10)],
        numbers=[["23"]],
        multiplier="23X1",
    )
    evidence = _one(source)
    reconstructed = evidence["reconstructed_candidate"]
    assert reconstructed["number_groups"] == [["23"]]
    assert reconstructed["multiplier_rules"] == ["2/3X1"]
    assert evidence["status"] == "consistent"


def test_missing_or_invalid_game_never_calls_qwen_provider(monkeypatch) -> None:
    calls = 0

    class ForbiddenProvider:
        def __init__(self):
            nonlocal calls
            calls += 1

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _service_meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", ForbiddenProvider)

    for game in (None, "auto", "六合彩", []):
        response = service.run_job("image-id", "qwen-dashscope", game=game)
        assert response["ok"] is False
        assert response["error"]["code"] == "INVALID_GAME"
        assert response["auto_confirm"] is False
        assert response["auto_submit"] is False
    assert calls == 0


def test_service_passes_selected_game_to_reconstruction(monkeypatch) -> None:
    qwen_result = _recognition_result()
    seen: list[str] = []

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    def reconstruct(result, *, game):
        seen.append(game)
        return [{"game": game, "status": "incomplete"}]

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _service_meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", reconstruct)

    response = service.run_job("image-id", "qwen-dashscope", game="六合")
    assert response["ok"] is True
    assert seen == ["六合"]
    assert response["structure_evidence"][0]["game"] == "六合"


def test_reconstruction_exception_isolated_from_successful_provider_result(monkeypatch) -> None:
    qwen_result = _recognition_result()
    before = qwen_result.to_dict()

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _service_meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(
        service,
        "reconstruct_structure",
        lambda result, *, game: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    response = service.run_job("image-id", "qwen-dashscope", game="539")
    assert response["ok"] is True
    assert response["result"] == before
    assert qwen_result.to_dict() == before
    failed = response["structure_evidence"][0]
    assert failed["status"] == "incomplete"
    assert failed["warnings"] == ["structure_reconstruction_failed"]
    assert failed["human_confirmation_required"] is True
    assert failed["auto_apply"] is False
    assert failed["auto_confirm"] is False
    assert failed["auto_submit"] is False
    for forbidden in ("candidate", "queue", "draft", "webfill"):
        assert forbidden not in repr(response["structure_evidence"]).lower()


def _service_meta() -> SimpleNamespace:
    return SimpleNamespace(
        storage_path=Path("unused.png"),
        mime_type="image/png",
        sha256="image-sha",
        width=1000,
        height=600,
        byte_size=100,
    )


def _multi_row_column_result() -> dict:
    specs = [
        (
            "S01-L01",
            [
                _token("24", 10, 10), _token("x", 65, 10),
                _token("03", 110, 10), _token("x", 165, 10),
                _token("17", 210, 10), _token("x", 265, 10),
                _token("20", 310, 10),
            ],
            [["24"], ["03"], ["17"], ["20"]],
        ),
        (
            "S01-L02",
            [_token("34", 10, 50), _token("23", 110, 50), _token("27", 210, 50), _token("30", 310, 50)],
            [["34"], ["23"], ["27"], ["30"]],
        ),
        (
            "S01-L03",
            [_token("37", 210, 90), _token("35", 310, 90)],
            [["37"], ["35"]],
        ),
    ]
    lines: list[dict] = []
    rows: list[dict] = []
    for line_id, tokens, numbers in specs:
        for index, token in enumerate(tokens, start=1):
            token["token_id"] = f"{line_id}-T{index:02d}"
        lines.append({
            "line_id": line_id,
            "text": " ".join(token["text"] for token in tokens),
            "tokens": tokens,
        })
        rows.append({
            "tokens": [
                {"text": token["text"], "bbox": _polygon_to_xyxy(token["bounding_box"])}
                for token in tokens
            ],
            "numbers": numbers,
            "multiplier": None,
            "layout_hint": "column_bet",
        })
    return {
        "status": "completed",
        "provider": {"id": "qwen-dashscope"},
        "source_image": {"width": 1000},
        "raw_text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "preprocessing": {
            "qwen_response": {
                "sections": [{"rows": rows, "shared_multiplier": None}]
            }
        },
    }


def _recognition_result() -> RecognitionResult:
    line_text = "01"
    token = Token(
        token_id="S01-L01-T01",
        text=line_text,
        start=0,
        end=2,
        confidence=Confidence(source="qwen-dashscope"),
        bounding_box=BoundingBox(
            coordinate_space="pixel",
            polygon=[[10, 10], [30, 10], [30, 26], [10, 26]],
        ),
    )
    return RecognitionResult(
        recognition_id="recognition-test",
        request_id="request-test",
        provider=ProviderMetadata(id="qwen-dashscope", model_name="qwen3-vl-plus"),
        source_image=SourceImage(image_id="image-id", sha256="image-sha"),
        raw_text=line_text,
        lines=[Line(line_id="S01-L01", order=1, text=line_text, tokens=[token])],
        preprocessing={
            "qwen_response": {
                "sections": [{
                    "rows": [{
                        "tokens": [{"text": "01", "bbox": [10, 10, 30, 26]}],
                        "numbers": [["01"]],
                        "multiplier": None,
                        "layout_hint": "normal_row",
                    }],
                    "shared_multiplier": None,
                }]
            },
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
    )


def _exact_token(
    line_id: str,
    index: int,
    text: str,
    bbox: tuple[float, float, float, float],
) -> dict:
    return {
        "token_id": f"{line_id}-T{index:02d}",
        "text": text,
        "bounding_box": _bbox(*bbox),
    }


def _sample014_s05_result() -> dict:
    specs = [
        (
            "S05-L01",
            [
                ("24", (54, 471, 98, 499)),
                ("x", (102, 471, 122, 499)),
                ("08", (126, 471, 170, 499)),
                ("x", (174, 471, 194, 499)),
                ("16", (198, 471, 242, 499)),
                ("x", (246, 471, 266, 499)),
                ("03", (270, 471, 314, 499)),
                (" ", (318, 471, 334, 499)),
                ("2", (338, 478, 360, 504)),
                ("/", (364, 478, 388, 504)),
                ("3", (392, 478, 414, 504)),
                ("x", (418, 478, 438, 504)),
                ("0", (442, 478, 462, 504)),
                (".", (466, 478, 486, 504)),
                ("1", (490, 478, 510, 504)),
            ],
            [["24", "08", "16", "03"]],
            "2/3x0.1",
        ),
        (
            "S05-L02",
            [
                ("34", (54, 502, 98, 530)),
                ("x", (102, 502, 122, 530)),
                ("38", (126, 502, 170, 530)),
                ("x", (174, 502, 194, 530)),
                ("36", (198, 502, 242, 530)),
                (" ", (246, 502, 266, 530)),
                ("13", (270, 502, 314, 530)),
                (" ", (318, 502, 334, 530)),
                ("4", (338, 509, 360, 535)),
            ],
            [["34", "38", "36", "13"]],
            None,
        ),
    ]
    return _sample014_section_result(5, specs)


def _sample014_s06_result() -> dict:
    specs = [
        (
            "S06-L01",
            [
                ("34", (54, 580, 98, 608)),
                ("x", (102, 580, 122, 608)),
                ("03", (126, 580, 170, 608)),
                ("x", (174, 580, 194, 608)),
                ("16", (198, 580, 242, 608)),
                (" ", (246, 580, 266, 608)),
                ("2", (270, 587, 292, 613)),
                ("x", (296, 587, 316, 613)),
                ("1", (320, 587, 342, 613)),
            ],
            [["34", "03", "16"]],
            "2x1",
        ),
        (
            "S06-L02",
            [
                ("13", (126, 611, 170, 639)),
                (" ", (174, 611, 194, 639)),
                ("36", (198, 611, 242, 639)),
            ],
            [["13", "36"]],
            None,
        ),
    ]
    return _sample014_section_result(6, specs)


def _sample014_s11_result() -> dict:
    specs = [
        (
            "S11-L01",
            [
                ("12", (590, 240, 634, 268)),
                ("x", (638, 240, 658, 268)),
                ("15", (662, 240, 706, 268)),
                ("x", (710, 240, 730, 268)),
                ("34", (734, 240, 778, 268)),
                ("x", (782, 240, 802, 268)),
                ("13", (806, 240, 850, 268)),
                (" ", (854, 240, 870, 268)),
                ("2", (874, 247, 896, 273)),
                ("x", (900, 247, 920, 273)),
                ("3", (924, 247, 946, 273)),
            ],
            [["12", "15", "34", "13"]],
            "2x3",
        ),
        (
            "S11-L02",
            [
                ("20", (806, 278, 850, 306)),
                (" ", (854, 278, 870, 306)),
                ("3", (874, 285, 896, 311)),
                ("x", (900, 285, 920, 311)),
                ("1", (924, 285, 946, 311)),
            ],
            [["20"]],
            "3x1",
        ),
    ]
    return _sample014_section_result(11, specs)


def _sample014_section_result(section_number: int, specs: list[tuple]) -> dict:
    lines: list[dict] = []
    rows: list[dict] = []
    for line_id, token_specs, numbers, multiplier in specs:
        tokens = [
            _exact_token(line_id, index, text, bbox)
            for index, (text, bbox) in enumerate(token_specs, start=1)
        ]
        lines.append({
            "line_id": line_id,
            "text": " ".join(token["text"] for token in tokens),
            "tokens": tokens,
        })
        rows.append({
            "tokens": [
                {
                    "text": token["text"],
                    "bbox": _polygon_to_xyxy(token["bounding_box"]),
                }
                for token in tokens
            ],
            "numbers": numbers,
            "multiplier": multiplier,
            "layout_hint": "row_bet",
        })
    sections = [{"rows": []} for _ in range(section_number - 1)]
    sections.append({"rows": rows, "shared_multiplier": None})
    return {
        "status": "completed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "source_image": {"image_id": "sample-014", "width": 1024, "height": 768},
        "raw_text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "preprocessing": {
            "qwen_response": {"sections": sections},
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
    }


def test_sample014_s05_real_bbox_composes_stacked_fragmented_multiplier() -> None:
    source = _sample014_s05_result()
    before = copy.deepcopy(source)

    evidence = reconstruct_structure(source, game="539")
    primary = next(item for item in evidence if item["line_role"] == "primary")

    assert primary["structure_id"] == "S05"
    assert primary["member_line_ids"] == ["S05-L01", "S05-L02"]
    assert primary["reconstructed_candidate"]["number_groups"] == [
        ["24", "34"],
        ["08", "38"],
        ["16", "36"],
        ["03", "13"],
    ]
    assert primary["reconstructed_candidate"]["multiplier_rules"] == ["2/3/4X0.1"]
    assert primary["model_candidate"]["multiplier"] == "2/3x0.1"
    assert primary["status"] == "divergent"
    assert primary["human_confirmation_required"] is True
    assert primary["auto_apply"] is False
    assert primary["auto_confirm"] is False
    assert primary["auto_submit"] is False

    debug = primary["evidence"]["bbox_debug"]
    assert debug["play_zone_boundary_x"] == 314.0
    assert debug["play_zone_boundary_source"] == "structure_relative_rightmost_number_x2"
    assert debug["rightmost_number_token_ids"] == ["S05-L01-T07", "S05-L02-T07"]
    classifications = {
        token["token_id"]: token["classification"]
        for token in primary["evidence"]["tokens"]
    }
    assert classifications["S05-L01-T09"] == "multiplier_fragment_candidate"
    assert classifications["S05-L01-T10"] == "multiplier_fragment"
    assert classifications["S05-L01-T12"] == "multiplier_operator_candidate"
    assert classifications["S05-L01-T13"] == "multiplier_fragment_candidate"
    assert classifications["S05-L01-T14"] == "multiplier_fragment"
    assert classifications["S05-L01-T15"] == "multiplier_fragment_candidate"
    assert classifications["S05-L02-T09"] == "multiplier_fragment_candidate"
    assert source == before


def test_sample014_s06_real_bbox_composes_consistent_fragmented_multiplier() -> None:
    source = _sample014_s06_result()
    before = copy.deepcopy(source)

    evidence = reconstruct_structure(source, game="539")
    primary = next(item for item in evidence if item["line_role"] == "primary")

    assert primary["structure_id"] == "S06"
    assert primary["reconstructed_candidate"]["number_groups"] == [
        ["34"],
        ["03", "13"],
        ["16", "36"],
    ]
    assert primary["reconstructed_candidate"]["multiplier_rules"] == ["2X1"]
    assert primary["model_candidate"]["multiplier"] == "2x1"
    assert primary["status"] == "consistent"
    assert primary["human_confirmation_required"] is True
    assert primary["auto_apply"] is False
    assert primary["auto_confirm"] is False
    assert primary["auto_submit"] is False
    assert primary["evidence"]["bbox_debug"]["play_zone_boundary_x"] == 242.0
    assert source == before


def test_sample014_s11_real_bbox_keeps_independent_member_line_rules() -> None:
    source = _sample014_s11_result()
    before = copy.deepcopy(source)

    evidence = reconstruct_structure(source, game="539")
    primary = next(item for item in evidence if item["line_role"] == "primary")
    continuation = next(
        item for item in evidence if item["line_role"] == "continuation"
    )

    assert primary["structure_id"] == "S11"
    assert primary["primary_line_id"] == "S11-L01"
    assert primary["member_line_ids"] == ["S11-L01", "S11-L02"]
    assert continuation["line_id"] == "S11-L02"
    assert continuation["primary_line_id"] == "S11-L01"
    assert "reconstructed_candidate" not in continuation
    assert primary["reconstructed_candidate"] == {
        "number_groups": [["12"], ["15"], ["34"], ["13", "20"]],
        "multiplier_rules": ["2X3", "3X1"],
        "layout": "column_bet",
        "collision": None,
        "shared_multiplier": None,
    }
    assert primary["status"] == "consistent"
    assert "fragment_multiplier_ambiguous" not in primary["warnings"]
    assert not any("collision" in warning for warning in primary["warnings"])
    assert "model_collision_evidence_missing" not in primary["warnings"]
    assert primary["needs_review"] is True
    assert primary["human_confirmation_required"] is True
    assert primary["auto_apply"] is False
    assert primary["auto_confirm"] is False
    assert primary["auto_submit"] is False
    assert source == before


def _two_line_fragment_rules(
    first_parts: list[str],
    second_parts: list[str],
    *,
    first_multiplier: str,
    second_multiplier: str,
) -> dict:
    specs = []
    for line_index, (parts, multiplier) in enumerate(
        (
            (first_parts, first_multiplier),
            (second_parts, second_multiplier),
        ),
        start=1,
    ):
        line_id = f"S01-L{line_index:02d}"
        y = 10 + (line_index - 1) * 40
        token_specs = [(f"0{line_index}", (10, y, 30, y + 16))]
        token_specs.extend(
            (part, (60 + index * 24, y, 80 + index * 24, y + 16))
            for index, part in enumerate(parts)
        )
        specs.append((line_id, token_specs, [[f"0{line_index}"]], multiplier))
    return _sample014_section_result(1, specs)


def test_two_member_lines_keep_independent_fragmented_rules() -> None:
    source = _two_line_fragment_rules(
        ["2", "x", "3"],
        ["3", "x", "1"],
        first_multiplier="2X3",
        second_multiplier="3X1",
    )
    primary = next(
        item for item in reconstruct_structure(source, game="539")
        if item["line_role"] == "primary"
    )

    assert primary["reconstructed_candidate"]["multiplier_rules"] == ["2X3", "3X1"]
    assert primary["reconstructed_candidate"]["collision"] is None
    assert "fragment_multiplier_ambiguous" not in primary["warnings"]


def test_shared_fragment_token_id_fails_closed_as_ambiguous() -> None:
    source = _two_line_fragment_rules(
        ["2", "x", "3"],
        ["2", "x", "1"],
        first_multiplier="2X3",
        second_multiplier="2X1",
    )
    source["lines"][0]["tokens"][1]["token_id"] = "shared-category-token"
    source["lines"][1]["tokens"][1]["token_id"] = "shared-category-token"

    primary = next(
        item for item in reconstruct_structure(source, game="539")
        if item["line_role"] == "primary"
    )
    assert primary["reconstructed_candidate"]["multiplier_rules"] == []
    assert primary["status"] == "incomplete"
    assert "fragment_multiplier_ambiguous" in primary["warnings"]


def test_fragmented_rules_with_same_value_follow_existing_merge_policy() -> None:
    source = _two_line_fragment_rules(
        ["2", "x", "1"],
        ["3", "x", "1"],
        first_multiplier="2X1",
        second_multiplier="3X1",
    )
    primary = next(
        item for item in reconstruct_structure(source, game="539")
        if item["line_role"] == "primary"
    )

    assert primary["reconstructed_candidate"]["multiplier_rules"] == ["2/3X1"]


def test_only_unconsumed_stacked_categories_remain_collision_evidence() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("x", 45, 10),
        _token("02", 80, 10),
        _token("2", 130, 10),
        _token("x", 154, 10),
        _token("1", 178, 10),
        _token("3", 260, 10),
        _token("4", 260, 50),
    ]
    evidence = _one(_result(
        tokens,
        numbers=[["01"], ["02"]],
        layout="column_bet",
        multiplier="2X1",
        extra_row={"collision": "3/4"},
    ))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == ["2X1"]
    assert evidence["reconstructed_candidate"]["collision"] == "3/4"
    collision_warnings = [
        warning for warning in evidence["warnings"]
        if warning.startswith("collision_play_evidence:")
    ]
    assert collision_warnings == ["collision_play_evidence:3/4"]


@pytest.mark.parametrize(
    ("parts", "expected_rule"),
    [
        (["2", "x", "1"], "2X1"),
        (["3", "x", "5"], "3X5"),
        (["2", "/", "3", "x", "0", ".", "1"], "2/3X0.1"),
        (["2", "/", "3", "/", "4", "x", "0", ".", "1"], "2/3/4X0.1"),
    ],
)
def test_adjacent_fragment_sequences_compose_one_valid_rule(
    parts: list[str],
    expected_rule: str,
) -> None:
    tokens = [_token("01", 10, 10)]
    tokens.extend(_token(part, 60 + index * 24, 10) for index, part in enumerate(parts))
    evidence = _one(_result(tokens, numbers=[["01"]], multiplier=expected_rule))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == [expected_rule]
    assert evidence["status"] == "consistent"


def test_two_independent_fragmented_rules_remain_two_rules() -> None:
    parts = ["2", "x", "2", "/", "3", "x", "5"]
    tokens = [_token("01", 10, 10)]
    tokens.extend(_token(part, 60 + index * 24, 10) for index, part in enumerate(parts))
    evidence = _one(_result(tokens, numbers=[["01"]], multiplier="2X2 3X5"))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == ["2X2", "3X5"]
    assert evidence["status"] == "consistent"


def test_two_possible_fragment_values_fail_closed_without_a_rule() -> None:
    parts = ["2", "x", "1", "x", "2"]
    tokens = [_token("01", 10, 10)]
    tokens.extend(_token(part, 60 + index * 24, 10) for index, part in enumerate(parts))
    evidence = _one(_result(tokens, numbers=[["01"]], multiplier="2X1"))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == []
    assert evidence["status"] == "incomplete"
    assert "fragment_multiplier_ambiguous" in evidence["warnings"]


def test_fragmented_decimal_without_tail_fails_closed() -> None:
    parts = ["2", "x", "0", "."]
    tokens = [_token("01", 10, 10)]
    tokens.extend(_token(part, 60 + index * 24, 10) for index, part in enumerate(parts))
    evidence = _one(_result(tokens, numbers=[["01"]], multiplier="2X0."))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == []
    assert evidence["status"] == "incomplete"
    assert "fragment_multiplier_incomplete" in evidence["warnings"]


def test_fragmented_tokens_too_far_apart_fail_closed() -> None:
    tokens = [
        _token("01", 10, 10),
        _token("2", 60, 10),
        _token("x", 84, 10),
        _token("1", 180, 10),
    ]
    evidence = _one(_result(tokens, numbers=[["01"]], multiplier="2X1"))

    assert evidence["reconstructed_candidate"]["multiplier_rules"] == []
    assert evidence["status"] == "incomplete"
    assert "fragment_multiplier_incomplete" in evidence["warnings"]


def test_number_column_separator_never_becomes_multiplier_operator() -> None:
    tokens = [_token("01", 10, 10), _token("x", 65, 10), _token("02", 120, 10)]
    evidence = _one(_result(tokens, numbers=[["01"], ["02"]], layout="column_bet"))

    separator = next(token for token in evidence["evidence"]["tokens"] if token["text"] == "x")
    assert separator["zone"] == "main"
    assert separator["classification"] == "column_separator"
    assert evidence["reconstructed_candidate"]["multiplier_rules"] == []


def test_fragmented_tokens_never_cross_structure_identity() -> None:
    line_one_tokens = [_token("01", 10, 10), _token("2", 60, 10)]
    line_two_tokens = [_token("02", 10, 50), _token("x", 60, 50), _token("1", 84, 50)]
    for line_id, tokens in (("S01-L01", line_one_tokens), ("S02-L01", line_two_tokens)):
        for index, token in enumerate(tokens, start=1):
            token["token_id"] = f"{line_id}-T{index:02d}"
    source = {
        "status": "completed",
        "provider": {"id": "qwen-dashscope"},
        "source_image": {"width": 1000},
        "raw_text": "01 2\n02 x 1",
        "lines": [
            {"line_id": "S01-L01", "text": "01 2", "tokens": line_one_tokens},
            {"line_id": "S02-L01", "text": "02 x 1", "tokens": line_two_tokens},
        ],
        "preprocessing": {
            "qwen_response": {
                "sections": [
                    {"rows": [{"numbers": [["01"]], "multiplier": "2", "layout_hint": "normal_row"}]},
                    {"rows": [{"numbers": [["02"]], "multiplier": "X1", "layout_hint": "normal_row"}]},
                ]
            }
        },
    }

    evidence = reconstruct_structure(source, game="539")
    assert len(evidence) == 2
    assert all(item["reconstructed_candidate"]["multiplier_rules"] == [] for item in evidence)
    assert all(item["status"] == "incomplete" for item in evidence)
