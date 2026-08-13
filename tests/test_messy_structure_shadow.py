"""Offline regressions for messy structure audit policy."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.vision.messy_structure_shadow import (
    ShadowAmbiguity,
    attach_continuation_by_geometry,
    canonical_multiplier,
    category_scope_fallback,
    deterministic_column_anchors,
    evaluate_sample007,
    shadow_reconstruct_suggestion,
)


TRUTH = Path(r"C:\BetguardOCRDataset\ground-truth-draft\sample-007.json")
REVIEW = Path(r"C:\BetguardOCRBench\analysis\sample-007-messy-stress-test\human-review-answer.json")


def _suggestion() -> dict:
    return {
        "number_groups": [["36", "38"], ["07", "17"], ["04", "05", "03"], ["23"]],
        "suggested_multiplier": "?X0.5",
        "suggested_layout": "column",
        "suggested_continuation": False,
        "suggested_special_play": "",
        "suggested_scope": "normal",
        "suggested_cancelled": False,
        "physical_linkage": "unresolved",
    }


def test_canonical_multiplier_preserves_question_category_and_normalizes_unicode_multiply() -> None:
    assert canonical_multiplier("?×0.5") == "?X0.5"
    assert canonical_multiplier("23×0.5") == "2/3X0.5"


def test_category_23_creates_review_only_fallback_not_auto_apply() -> None:
    fallback = category_scope_fallback(_suggestion())
    assert fallback is not None
    assert fallback["number_groups"] == [["36", "38"], ["07", "17"], ["04", "05", "03"]]
    assert fallback["multiplier_rules"] == ["2/3X0.5"]
    assert fallback["needs_review"] is True
    assert fallback["auto_apply"] is False
    assert fallback["executable"] is False


def test_category_fallback_requires_exactly_one_terminal_23() -> None:
    value = _suggestion()
    value["number_groups"][-1] = ["23", "24"]
    assert category_scope_fallback(value) is None
    value = _suggestion()
    value["number_groups"].insert(0, ["23"])
    assert category_scope_fallback(value) is None


def test_shadow_reconstruction_never_mutates_source() -> None:
    source = _suggestion()
    before = copy.deepcopy(source)
    result = shadow_reconstruct_suggestion(source)
    assert source == before
    assert result["fallback_candidate"]["status"] == "partial"


def test_column_anchors_use_bbox_not_input_order() -> None:
    tokens = [
        {"text": "04", "bbox": [102, 60, 122, 80]},
        {"text": "01", "bbox": [10, 10, 30, 30]},
        {"text": "03", "bbox": [12, 60, 32, 80]},
        {"text": "02", "bbox": [100, 10, 120, 30]},
    ]
    result = deterministic_column_anchors(tokens)
    assert result["number_groups"] == [["01", "03"], ["02", "04"]]
    assert result["provenance"] == "literal_token_bbox_center_x"


def test_column_anchor_does_not_fabricate_subbbox_for_fused_token() -> None:
    with pytest.raises(ShadowAmbiguity, match="number_geometry_missing"):
        deterministic_column_anchors([{"text": "05x08", "bbox": [10, 10, 90, 30]}])


def test_continuation_attaches_only_to_unique_nearest_anchor() -> None:
    result = attach_continuation_by_geometry(
        [20, 120, 220],
        [{"text": "04", "bbox": [211, 50, 231, 70]}],
        tolerance=30,
    )
    assert result["groups"] == [[], [], ["04"]]


def test_continuation_tie_and_out_of_range_fail_closed() -> None:
    with pytest.raises(ShadowAmbiguity, match="tie"):
        attach_continuation_by_geometry(
            [20, 120], [{"text": "04", "bbox": [60, 50, 80, 70]}], tolerance=60
        )
    with pytest.raises(ShadowAmbiguity, match="outside"):
        attach_continuation_by_geometry(
            [20, 120], [{"text": "04", "bbox": [300, 50, 320, 70]}], tolerance=30
        )


@pytest.mark.skipif(not (TRUTH.exists() and REVIEW.exists()), reason="sample-007 audit fixtures unavailable")
def test_sample007_truth_links_cancelled_record_by_human_bet_id_and_counts() -> None:
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    result = evaluate_sample007(truth, review)
    assert result["metrics"]["records"] == 25
    assert result["metrics"]["active"] == 24
    assert result["metrics"]["cancelled"] == 1
    cancelled = next(row for row in result["records"] if row["record_id"] == "H-015")
    assert cancelled["truth"]["cancelled"] is True
    assert cancelled["baseline_exact"] is True


@pytest.mark.skipif(not (TRUTH.exists() and REVIEW.exists()), reason="sample-007 audit fixtures unavailable")
def test_sample007_only_five_category_scope_fallbacks_and_no_safe_auto_gain() -> None:
    result = evaluate_sample007(
        json.loads(TRUTH.read_text(encoding="utf-8")),
        json.loads(REVIEW.read_text(encoding="utf-8")),
    )
    metrics = result["metrics"]
    assert metrics["category_scope_fallbacks"] == 5
    assert metrics["safe_auto_exact"] == metrics["baseline_exact"]
    assert all(row["shadow"]["needs_review"] for row in result["records"])
    error_codes = {code for row in result["records"] for code in row["baseline_errors"]}
    assert "CATEGORY_AS_NUMBER_ERROR" in error_codes
    assert "MULTIPLIER_SCOPE_ERROR" in error_codes
    assert "CONTINUATION_ERROR" in error_codes
    assert "SPECIAL_PLAY_SCOPE_ERROR" in error_codes

