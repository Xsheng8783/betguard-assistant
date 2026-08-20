from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.gemma_crop_reread import (
    HARD_MAX_REGIONS,
    PROVIDER_ID,
    CropRereadConfig,
    build_contact_sheet,
    run_gemma_crop_reread,
    select_uncertain_crop_proposals,
)
from betguard.vision.runtime_reader_router import (
    AI_UNCERTAIN,
    SAFE_DRAFT,
    _reconcile_crop_reread,
)


IMAGE_SHA = "a" * 64


def _request(image_path: Path) -> RecognitionRequest:
    return RecognitionRequest(
        request_id="crop-test",
        image_id="image-test",
        image_path=str(image_path),
        mime_type="image/png",
        metadata={"sha256": IMAGE_SHA},
    )


def _grid_image(path: Path) -> Path:
    image = Image.new("RGB", (600, 420), "white")
    draw = ImageDraw.Draw(image)
    for x in (15, 300, 585):
        draw.line((x, 10, x, 410), fill=(180, 25, 25), width=4)
    for y in (10, 205, 410):
        draw.line((15, y, 585, y), fill=(180, 25, 25), width=4)
    draw.text((55, 55), "08 x 04", fill="black")
    draw.text((55, 115), "16 x 26", fill="black")
    draw.text((345, 55), "06 07 08 38", fill="black")
    image.save(path)
    return path


def _pp() -> dict[str, Any]:
    return {
        "status": "completed",
        "regions": [
            {"evidence_id": "PP-0001", "text": "08x04", "bbox": [45, 45, 250, 95]},
            {"evidence_id": "PP-0002", "text": "16x26", "bbox": [45, 105, 250, 155]},
            {"evidence_id": "PP-0003", "text": "06070838", "bbox": [335, 45, 550, 110]},
        ],
        "cache_hit": True,
        "local_inference_calls": 0,
    }


def _seed() -> dict[str, Any]:
    uncertain = {
        "draft_id": "draft-column",
        "raw_text": "08x04\n16x26",
        "number_groups_suggestion": [["08", "16"], ["04", "26"]],
        "layout_suggestion": "column",
        "continuation_suggestion": "unclear",
        "special_play_raw": "none",
        "draft_classification": AI_UNCERTAIN,
        "physical_boundary_evidence": "EQUAL_WIDTH_ADJACENT_OPERATOR_ROWS",
        "nested_group_evidence": "partial",
        "human_confirmed": False,
    }
    safe = {
        "draft_id": "draft-safe",
        "raw_text": "06 07 08 38",
        "number_groups_suggestion": [["06", "07", "08", "38"]],
        "layout_suggestion": "normal",
        "draft_classification": SAFE_DRAFT,
        "human_confirmed": False,
    }
    return {
        "review_cards": [uncertain, safe],
        "safe_bet_drafts": [safe],
        "provisional_bet_drafts": [uncertain],
        "bet_drafts": [uncertain, safe],
        "draft_items": [uncertain, safe],
        "machine_read_diagnostics": {},
        "human_confirmed": False,
        "value_authority": "human_confirmed_answer",
        "auto_confirm": False,
        "auto_submit": False,
    }


def _response(crop_id: str, *, layout: str = "column") -> dict[str, Any]:
    item = {
        "crop_id": crop_id,
        "raw_text": "08 x 04\n16 x 26\n2/3 x 1",
        "numbers": "08 x 04\n16 x 26",
        "number_groups": [["08", "16"], ["04", "26"]],
        "multiplier_text": "2/3 x 1",
        "multiplier_rules": ["2/3 x 1"],
        "visible_operators": ["x", "x", "x"],
        "layout_guess": layout,
        "continuation": "no",
        "special_text": "none",
        "cancelled": "no",
        "uncertain": False,
        "uncertain_reason": "none",
    }
    text = json.dumps({"version": "gemma-crop-reread-v2", "items": [item]})
    return {
        "responseId": "response-1",
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"text": "", "thought": True},
                        {"text": text},
                    ]
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
    }


def _config(cache_dir: Path) -> CropRereadConfig:
    return CropRereadConfig(
        enabled=True,
        endpoint="https://example.invalid/generate",
        timeout_seconds=5.0,
        cache_dir=cache_dir,
        max_regions=8,
    )


def test_selection_uses_only_uncertain_unique_single_cell_geometry(tmp_path: Path) -> None:
    path = _grid_image(tmp_path / "grid.png")
    proposals = select_uncertain_crop_proposals(_request(path), _seed(), _pp())

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["draft_id"] == "draft-column"
    assert proposal["linked_pp_evidence_ids"] == ["PP-0001", "PP-0002"]
    assert proposal["human_truth_used"] is False
    assert proposal["bbox"][2] <= 300
    assert proposal["bbox"][3] <= 205


def test_ambiguous_matching_physical_cells_fail_closed(tmp_path: Path) -> None:
    path = _grid_image(tmp_path / "grid.png")
    pp = _pp()
    pp["regions"][2] = {
        "evidence_id": "PP-0003",
        "text": "08x04 16x26",
        "bbox": [335, 45, 550, 155],
    }
    assert select_uncertain_crop_proposals(_request(path), _seed(), pp) == []


def test_two_first_pass_cards_competing_for_one_crop_both_fail_closed(
    tmp_path: Path,
) -> None:
    path = _grid_image(tmp_path / "grid.png")
    seed = _seed()
    duplicate = copy.deepcopy(seed["review_cards"][0])
    duplicate["draft_id"] = "draft-column-duplicate"
    seed["review_cards"].append(duplicate)
    assert select_uncertain_crop_proposals(_request(path), seed, _pp()) == []


def test_contact_sheet_is_bounded_and_contains_no_value_metadata(tmp_path: Path) -> None:
    path = _grid_image(tmp_path / "grid.png")
    proposals = select_uncertain_crop_proposals(_request(path), _seed(), _pp())
    encoded = build_contact_sheet(str(path), proposals)
    assert encoded.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(encoded)) as sheet:
        assert sheet.width <= 1320
        assert sheet.height <= 1200
    assert len(proposals) <= HARD_MAX_REGIONS


def test_run_uses_one_call_then_sha_identity_cache_and_never_persists_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _grid_image(tmp_path / "grid.png")
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-persist-this-key")

    def transport(payload: dict[str, Any], _key: str, _config: CropRereadConfig) -> dict[str, Any]:
        calls.append(payload)
        return _response("C01")

    config = _config(tmp_path / "cache")
    first = run_gemma_crop_reread(
        _request(path), _seed(), _pp(), config=config, transport=transport
    )
    second = run_gemma_crop_reread(
        _request(path), _seed(), _pp(), config=config, transport=transport
    )

    assert len(calls) == 1
    assert first["status"] == "completed"
    assert first["external_call_count"] == 1
    assert first["retry_count"] == 0
    assert second["cache_hit"] is True
    assert second["external_call_count"] == 0
    persisted = "\n".join(
        item.read_text(encoding="utf-8") for item in config.cache_dir.glob("*.json")
    )
    assert "do-not-persist-this-key" not in persisted
    assert first["cache_identity"]["source_image_sha256"] == IMAGE_SHA
    assert first["cache_identity"]["prompt_sha256"]
    assert first["cache_identity"]["request_schema_version"]


def test_timeout_is_a_value_with_no_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _grid_image(tmp_path / "grid.png")
    monkeypatch.setenv("GEMINI_API_KEY", "memory-only")

    def timeout(*_args: Any) -> dict[str, Any]:
        raise TimeoutError

    result = run_gemma_crop_reread(
        _request(path), _seed(), _pp(), config=_config(tmp_path / "cache"), transport=timeout
    )
    assert result["status"] == "timeout"
    assert result["error"]["code"] == "CROP_REREAD_TIMEOUT"
    assert result["external_call_count"] == 1
    assert result["retry_count"] == 0


def test_ambiguous_x05_can_never_contaminate_number_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _grid_image(tmp_path / "grid.png")
    monkeypatch.setenv("GEMINI_API_KEY", "memory-only")
    envelope = _response("C01")
    public_text = envelope["candidates"][0]["content"]["parts"][1]["text"]
    decoded = json.loads(public_text)
    decoded["items"][0].update(
        raw_text="08x04 16x26 2/3x05",
        numbers="08x04 16x26 05",
        number_groups=[["08", "16"], ["04", "26"], ["05"]],
        multiplier_text="2/3x05",
        multiplier_rules=[],
        uncertain=True,
        uncertain_reason="decimal point unclear",
    )
    envelope["candidates"][0]["content"]["parts"][1]["text"] = json.dumps(decoded)
    result = run_gemma_crop_reread(
        _request(path),
        _seed(),
        _pp(),
        config=_config(tmp_path / "cache"),
        transport=lambda *_args: envelope,
    )
    assert result["status"] == "completed"
    assert result["accepted_item_count"] == 0
    assert result["rejected_item_count"] == 1
    assert result["rejected_items"][0]["reason_code"] == (
        "CROP_ITEM_MULTIPLIER_NUMBER_CONTAMINATION"
    )


def test_reconciliation_never_overwrites_first_pass_or_confirms_human() -> None:
    seed = _seed()
    original = copy.deepcopy(seed)
    crop = {
        "provider_id": PROVIDER_ID,
        "status": "completed",
        "proposals": [
            {
                "crop_id": "C01",
                "draft_id": "draft-column",
                "bbox": [15, 10, 300, 205],
                "linked_pp_evidence_ids": ["PP-0001", "PP-0002"],
                "selection_reason": "UNIQUE_LITERAL_TO_PP_GEOMETRY",
            }
        ],
        "items": [
            {
                "crop_id": "C01",
                "raw_text": "08x04\n16x26\n2/3x1",
                "numbers": "08x04\n16x26",
                "number_groups": [["08", "16"], ["04", "26"]],
                "multiplier_text": "2/3x1",
                "multiplier_rules": ["2/3x1"],
                "visible_operators": ["x", "x", "x"],
                "layout_guess": "column",
                "continuation": "no",
                "special_text": "none",
                "cancelled": "no",
                "uncertain": False,
                "uncertain_reason": "none",
            }
        ],
        "selected_region_count": 1,
        "accepted_item_count": 1,
        "external_call_count": 1,
        "cache_hit": False,
        "latency_ms": 12.0,
    }
    result = _reconcile_crop_reread(seed, crop, pp_evidence=_pp())
    card = result["review_cards"][0]

    assert seed == original
    assert card["number_groups_suggestion"] == [["08", "16"], ["04", "26"]]
    assert card["crop_reread_suggestion"] is not None
    assert card["crop_reread_status"] in {"AI_REREAD_CONFIRMED", "AI_RECHECK_CONFLICT"}
    assert card["human_confirmed"] is False
    assert result["human_confirmed"] is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False


def test_single_digit_special_category_is_not_a_bet_number() -> None:
    seed = _seed()
    seed["review_cards"][0].update(
        raw_text="03x16x4",
        number_groups_suggestion=[["03", "16"]],
        layout_suggestion="unclear",
        special_play_raw="none",
    )
    crop = {
        "provider_id": PROVIDER_ID,
        "status": "completed",
        "proposals": [
            {
                "crop_id": "C01",
                "draft_id": "draft-column",
                "bbox": [15, 10, 300, 205],
                "linked_pp_evidence_ids": ["PP-0001"],
                "selection_reason": "UNIQUE_LITERAL_TO_PP_GEOMETRY",
            }
        ],
        "items": [
            {
                "crop_id": "C01",
                "raw_text": "03x16x4尾",
                "numbers": "03 16",
                "number_groups": [["03", "16"], ["4"]],
                "multiplier_text": "none",
                "multiplier_rules": [],
                "visible_operators": ["x", "x"],
                "layout_guess": "column",
                "continuation": "no",
                "special_text": "4尾",
                "cancelled": "no",
                "uncertain": False,
                "uncertain_reason": "none",
            }
        ],
        "selected_region_count": 1,
        "accepted_item_count": 1,
        "rejected_item_count": 0,
        "external_call_count": 0,
        "cache_hit": True,
        "latency_ms": 1.0,
    }
    result = _reconcile_crop_reread(seed, crop, pp_evidence=_pp())
    suggestion = result["review_cards"][0]["crop_reread_suggestion"]
    assert suggestion["number_groups_suggestion"] == [["03", "16"]]
    assert suggestion["special_play_raw"] == "4尾"
    assert "4" not in [
        number for group in suggestion["number_groups_suggestion"] for number in group
    ]


def test_safe_draft_never_enters_crop_batch(tmp_path: Path) -> None:
    path = _grid_image(tmp_path / "grid.png")
    seed = _seed()
    seed["review_cards"] = [seed["review_cards"][1]]
    assert select_uncertain_crop_proposals(_request(path), seed, _pp()) == []


@pytest.mark.parametrize(
    ("cell_left", "cell_right", "region_left", "region_right"),
    [
        (15, 300, 45, 250),
        (300, 585, 335, 550),
        (585, 870, 620, 835),
    ],
)
def test_generic_held_out_grid_cells_never_cross_a_physical_boundary(
    tmp_path: Path,
    cell_left: int,
    cell_right: int,
    region_left: int,
    region_right: int,
) -> None:
    path = tmp_path / f"held-out-{cell_left}.png"
    image = Image.new("RGB", (885, 430), "white")
    draw = ImageDraw.Draw(image)
    for x in (15, 300, 585, 870):
        draw.line((x, 10, x, 420), fill=(180, 25, 25), width=4)
    for y in (10, 210, 420):
        draw.line((15, y, 870, y), fill=(180, 25, 25), width=4)
    draw.text((region_left + 10, 55), "11 x 22", fill="black")
    draw.text((region_left + 10, 120), "13 x 24", fill="black")
    image.save(path)
    seed = _seed()
    seed["review_cards"] = [
        {
            **seed["review_cards"][0],
            "raw_text": "11x22\n13x24",
            "number_groups_suggestion": [["11", "13"], ["22", "24"]],
        }
    ]
    pp = {
        "status": "completed",
        "regions": [
            {
                "evidence_id": "PP-A",
                "text": "11x22",
                "bbox": [region_left, 45, region_right, 95],
            },
            {
                "evidence_id": "PP-B",
                "text": "13x24",
                "bbox": [region_left, 110, region_right, 160],
            },
        ],
    }
    proposals = select_uncertain_crop_proposals(_request(path), seed, pp)
    assert len(proposals) == 1
    assert proposals[0]["bbox"][0] >= cell_left
    assert proposals[0]["bbox"][2] <= cell_right


def test_crop_runtime_has_no_sample_identity_or_human_truth_special_case() -> None:
    source = Path(
        "src/betguard/vision/gemma_crop_reread.py"
    ).read_text(encoding="utf-8").lower()
    assert "sample-007" not in source
    assert "sample-008" not in source
    assert "sample-011" not in source
    assert "84b5133a49da" not in source
    assert "ground-truth" not in source
