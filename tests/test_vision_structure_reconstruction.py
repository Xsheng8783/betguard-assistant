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
    source = _result([_token(category, 800, 10)], numbers=[[category]])
    evidence = _one(source)
    assert evidence["reconstructed_candidate"]["number_groups"] == []
    token = evidence["evidence"]["tokens"][0]
    assert token["zone"] == "play"
    assert token["classification"] == "play_category_evidence"
    assert evidence["status"] == "incomplete"


def test_right_play_zone_34_plus_x1_forms_rule_without_becoming_a_number() -> None:
    source = _result(
        [_token("34", 760, 10), _token("X1", 810, 10)],
        numbers=[["34"]],
        multiplier="34X1",
    )
    evidence = _one(source)
    reconstructed = evidence["reconstructed_candidate"]
    assert reconstructed["number_groups"] == []
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
