from __future__ import annotations

import hashlib
import json
from pathlib import Path

from betguard.vision.image_text_acceptance import (
    get_dataset_status,
    save_migrated_human_verified_sample,
)
from betguard.vision.image_text_truth_backfill import (
    discover_human_confirmed_sources,
    render_structured_human_truth,
    semantic_round_trip,
)


def _sample007_truth() -> dict:
    return {
        "sample_id": "sample-007",
        "review_status": "reviewed",
        "reviewed_by": "local-user",
        "reviewed_at": "2026-08-13T18:11:44.457Z",
        "annotated_by_human": True,
        "annotation_status": "confirmed",
        "lines": [
            {
                "line_id": "R01-L1",
                "review_action": "confirmed",
                "number_groups": [["05"], ["08", "09", "23"], ["10", "20", "29"]],
                "layout_hint": "column_bet",
                "multiplier_rules": [
                    {"categories": ["2", "3"], "value": "2", "rule_text": "2/3X2"}
                ],
                "continuation": False,
            },
            {
                "line_id": "R19-L1",
                "review_action": "confirmed",
                "number_groups": [["03"], ["16"]],
                "layout_hint": "column_bet",
                "multiplier_rules": [
                    {"categories": ["2", "3"], "value": "1", "rule_text": "2/3X1"}
                ],
                "special_play": "7尾",
                "continuation": True,
            },
        ],
        "cancelled_bets": [
            {
                "human_bet_id": "H-015",
                "cancelled": True,
                "human_answer": {"confirmed": True, "cancelled": True},
            }
        ],
        "truth_counts": {"human_records": 3, "active_bets": 2, "cancelled_bets": 1},
    }


def test_structured_truth_renders_parser_exact_tail_continuation_and_cancel_metadata() -> None:
    rendered = render_structured_human_truth(_sample007_truth())

    assert rendered["ok"] is True
    assert rendered["human_verified_betguard_text"].splitlines() == [
        "05 × 08 09 23 × 10 20 29 2,3 × 2",
        "03 × 16 × 7尾",
        "2,3 × 1",
    ]
    semantics = rendered["source_semantic_result"]
    assert semantics["physical_record_count"] == 3
    assert semantics["active_bet_count"] == 2
    assert semantics["cancelled_bet_count"] == 1
    assert semantics["cancelled_text_syntax_available"] is False

    proof = semantic_round_trip(rendered)
    assert proof["exact"] is True
    assert proof["differences"] == []
    second = proof["parser_normalized_result"]["bets"][1]
    assert second["original_lines"] == ["03 × 16 × 7尾", "2,3 × 1"]
    assert "merged continuation line" in second["preprocessing_notes"]


def test_shared_multiplier_scope_fails_closed() -> None:
    truth = _sample007_truth()
    truth["shared_multiplier_rules"] = {"raw_text": "各二三x0.5"}

    rendered = render_structured_human_truth(truth)

    assert rendered == {
        "ok": False,
        "reason": "SHARED_MULTIPLIER_SCOPE_NOT_ROUND_TRIPPABLE",
    }


def test_promoted_human_literal_rule_is_rendered_without_machine_fields() -> None:
    truth = _sample007_truth()
    truth["lines"][0]["multiplier_rules"] = [
        {
            "rule_text": "23X0.5",
            "categories": None,
            "value": None,
            "parse_status": "unresolved_human_literal",
        }
    ]

    rendered = render_structured_human_truth(truth)

    assert rendered["ok"] is True
    assert rendered["human_verified_betguard_text"].splitlines()[0].endswith("2,3 × 0.5")
    assert semantic_round_trip(rendered)["exact"] is True


def test_explicit_each_half_car_expands_semantically_without_losing_physical_count() -> None:
    truth = {
        "sample_id": "sample-013",
        "review_status": "reviewed",
        "reviewed_by": "local-user",
        "lines": [
            {
                "line_id": "R01-L1",
                "review_action": "confirmed",
                "number_groups": [["15", "34"]],
                "layout_hint": "normal_row",
                "play_type": "car_bet",
                "play_text": "15 34 各半車",
            },
            {
                "line_id": "R02-L1",
                "review_action": "confirmed",
                "number_groups": [["12"], ["15", "34"], ["08", "20"]],
                "layout_hint": "column_bet",
                "multiplier_rules": [
                    {"categories": ["2"], "value": "3", "rule_text": "2X3"},
                    {"categories": ["3"], "value": "1", "rule_text": "3X1"},
                ],
            },
        ],
        "cancelled_bets": [],
    }

    rendered = render_structured_human_truth(truth)
    proof = semantic_round_trip(rendered)

    assert rendered["ok"] is True
    assert rendered["human_verified_betguard_text"].splitlines()[0] == "15 34 各 0.5車"
    semantics = rendered["source_semantic_result"]
    assert semantics["physical_record_count"] == 2
    assert semantics["physical_active_record_count"] == 2
    assert semantics["active_bet_count"] == 3
    assert proof["exact"] is True
    assert [bet["result"]["type"] for bet in proof["parser_normalized_result"]["bets"]] == [
        "car",
        "car",
        "column",
    ]


def test_migrated_save_keeps_revision_history_and_sha_dedup(tmp_path: Path) -> None:
    rendered = render_structured_human_truth(_sample007_truth())
    proof = semantic_round_trip(rendered)
    image = tmp_path / "sample-007.jpg"
    image.write_bytes(b"human-truth-image")
    sha256 = hashlib.sha256(image.read_bytes()).hexdigest()
    dataset = tmp_path / "acceptance"
    provenance = {"authority_kind": "canonical_human_truth_promotion"}

    first = save_migrated_human_verified_sample(
        image,
        rendered["human_verified_betguard_text"],
        expected_image_sha256=sha256,
        source_sample_id="sample-007",
        source_truth_provenance=provenance,
        source_semantic_result=rendered["source_semantic_result"],
        semantic_round_trip=proof,
        dataset_root=dataset,
    )
    second = save_migrated_human_verified_sample(
        image,
        rendered["human_verified_betguard_text"],
        expected_image_sha256=sha256,
        source_sample_id="sample-007",
        source_truth_provenance=provenance,
        source_semantic_result=rendered["source_semantic_result"],
        semantic_round_trip=proof,
        dataset_root=dataset,
    )

    assert first["ok"] is True
    assert first["sample"]["revision_number"] == 1
    assert second["sample"]["revision_number"] == 2
    assert get_dataset_status(dataset)["unique_human_verified_images"] == 1
    revisions = sorted((dataset / "samples" / sha256 / "revisions").glob("*.json"))
    assert len(revisions) == 2
    latest = json.loads((dataset / "samples" / sha256 / "latest.json").read_text("utf-8"))
    assert latest["verification_source"] == "migrated_existing_human_truth"
    assert latest["ai_original_text_available"] is False
    assert latest["source_truth_provenance"] == provenance


def test_migrated_save_rejects_model_provenance(tmp_path: Path) -> None:
    rendered = render_structured_human_truth(_sample007_truth())
    proof = semantic_round_trip(rendered)
    image = tmp_path / "sample-007.jpg"
    image.write_bytes(b"machine-prelabel-image")
    sha256 = hashlib.sha256(image.read_bytes()).hexdigest()

    result = save_migrated_human_verified_sample(
        image,
        rendered["human_verified_betguard_text"],
        expected_image_sha256=sha256,
        source_sample_id="sample-007",
        source_truth_provenance={"authority_kind": "model_prelabel"},
        source_semantic_result=rendered["source_semantic_result"],
        semantic_round_trip=proof,
        dataset_root=tmp_path / "acceptance",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "MIGRATED_TRUTH_PROVENANCE_REQUIRED"


def test_discovery_requires_promotion_sha_and_does_not_promote_pending_prelabel(
    tmp_path: Path,
) -> None:
    (tmp_path / "audit").mkdir()
    (tmp_path / "ground-truth-draft").mkdir()
    (tmp_path / "raw").mkdir()
    truth = _sample007_truth()
    truth_path = tmp_path / "ground-truth-draft" / "sample-007.json"
    truth_path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    image_path = tmp_path / "raw" / "sample-007.jpg"
    image_path.write_bytes(b"sample-007")
    image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "sample-007",
                "image_path": "raw/sample-007.jpg",
                "ground_truth_path": "ground-truth/sample-007.txt",
                "sha256": image_sha,
                "gt_status": "pending_human_review",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "audit" / "gt-v1-freeze.json").write_text(
        json.dumps({"samples": []}), encoding="utf-8"
    )
    (tmp_path / "audit" / "sample-007-human-truth-promotion-r1.json").write_text(
        json.dumps(
            {
                "sample_id": "sample-007",
                "canonical_truth_sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest(),
                "authority_source_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    found = discover_human_confirmed_sources(tmp_path)

    assert [item["sample_id"] for item in found["eligible"]] == ["sample-007"]
    assert found["eligible"][0]["provenance"]["authority_kind"] == (
        "canonical_human_truth_promotion"
    )
