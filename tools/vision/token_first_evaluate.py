"""Evaluate sealed token-first predictions using only post-seal truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betguard.vision.cell_first import (  # noqa: E402
    DECIMAL_LITERAL,
    NUMBER_01_39,
    OPERATOR_X,
    SPECIAL_LITERAL,
)
from betguard.vision.token_first import literal_tokens_from_regions  # noqa: E402


SCORED_CLASSES = frozenset({
    NUMBER_01_39,
    OPERATOR_X,
    DECIMAL_LITERAL,
    SPECIAL_LITERAL,
})


def _parse_truth(value: str) -> tuple[str, Path]:
    sample_id, separator, path = value.partition("=")
    if not separator or not sample_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("--truth must be SAMPLE_ID=PATH")
    return sample_id.strip(), Path(path).resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _score(predicted: Iterable[str], expected: Iterable[str]) -> dict[str, Any]:
    predicted_counter = Counter(predicted)
    expected_counter = Counter(expected)
    true_positive = sum((predicted_counter & expected_counter).values())
    predicted_count = sum(predicted_counter.values())
    expected_count = sum(expected_counter.values())
    return {
        "true_positive": true_positive,
        "predicted": predicted_count,
        "expected": expected_count,
        "precision": true_positive / predicted_count if predicted_count else None,
        "recall": true_positive / expected_count if expected_count else None,
        "invented": predicted_count - true_positive,
        "missed": expected_count - true_positive,
    }


def _truth_line_tokens(truth: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for index, line in enumerate(truth.get("lines", []), 1):
        text = str(line.get("raw_text") or "")
        if not text:
            continue
        result[str(line.get("line_id") or f"LINE-{index:04d}")] = literal_tokens_from_regions([{
            "region_id": f"TRUTH-{index:04d}",
            "text": text,
            "confidence": 1.0,
            "bbox": [0.0, 0.0, float(max(1, len(text))), 1.0],
        }])
    return result


def _signature(tokens: Iterable[dict[str, Any]]) -> Counter[tuple[str, str]]:
    return Counter(
        (str(token["classification"]), str(token["text_raw"]).replace("．", "."))
        for token in tokens
        if token.get("classification") in SCORED_CLASSES
        and not token.get("decimal_group_id")
    )


def _semantic_grouping_proxy(
    groups: list[dict[str, Any]],
    truth_lines: dict[str, list[dict[str, Any]]],
    reference_region_count: int,
) -> dict[str, Any]:
    group_signatures = {
        group["group_id"]: _signature(group.get("assigned_tokens", []))
        for group in groups
    }
    truth_signatures = {
        line_id: _signature(tokens) for line_id, tokens in truth_lines.items()
    }
    pairs = []
    for group_id, predicted in group_signatures.items():
        predicted_count = sum(predicted.values())
        for line_id, expected in truth_signatures.items():
            expected_count = sum(expected.values())
            intersection = sum((predicted & expected).values())
            if not intersection or not expected_count or not predicted_count:
                continue
            precision = intersection / predicted_count
            recall = intersection / expected_count
            f1 = 2 * precision * recall / (precision + recall)
            pairs.append({
                "group_id": group_id,
                "line_id": line_id,
                "intersection": intersection,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            })
    accepted = [
        pair for pair in pairs
        if pair["recall"] >= 0.35 and pair["precision"] >= 0.25
    ]
    accepted.sort(key=lambda pair: (pair["f1"], pair["intersection"]), reverse=True)
    matched_groups: set[str] = set()
    matched_lines: set[str] = set()
    matches = []
    for pair in accepted:
        if pair["group_id"] in matched_groups or pair["line_id"] in matched_lines:
            continue
        matched_groups.add(pair["group_id"])
        matched_lines.add(pair["line_id"])
        matches.append(pair)

    over_grouping = 0
    for group_id in group_signatures:
        substantially_covered = {
            pair["line_id"] for pair in pairs
            if pair["group_id"] == group_id
            and pair["recall"] >= 0.50
            and pair["intersection"] >= 2
        }
        over_grouping += int(len(substantially_covered) >= 2)

    under_grouping = 0
    for line_id, expected in truth_signatures.items():
        if line_id in matched_lines or not expected:
            continue
        contributors = [
            group_signatures[pair["group_id"]]
            for pair in pairs
            if pair["line_id"] == line_id and pair["intersection"] > 0
        ]
        if len(contributors) < 2:
            continue
        combined = Counter()
        for signature in contributors:
            combined |= signature
        coverage = sum((combined & expected).values()) / sum(expected.values())
        under_grouping += int(coverage >= 0.50)
    return {
        "reference_region_count": reference_region_count,
        "matched_semantic_regions": len(matched_lines),
        "coverage": len(matched_lines) / reference_region_count if reference_region_count else None,
        "over_grouping": over_grouping,
        "under_grouping": under_grouping,
        "matching_method": "literal-signature proxy; truth has no physical bboxes",
        "matches": matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prediction-dir", required=True, type=Path)
    parser.add_argument("--truth", action="append", required=True, type=_parse_truth)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    prediction_dir = args.prediction_dir.resolve()
    seal_path = prediction_dir / "prediction-seal.json"
    predictions_path = prediction_dir / "predictions.json"
    if not seal_path.is_file() or not predictions_path.is_file():
        parser.error("sealed predictions are required")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if seal.get("truth_loaded_before_seal") is not False:
        parser.error("prediction seal does not prove blind execution")
    for relative, expected_hash in seal.get("artifacts", {}).items():
        artifact = prediction_dir / relative
        if not artifact.is_file() or _sha256(artifact) != expected_hash:
            parser.error(f"sealed artifact hash mismatch: {relative}")
    prediction_payload = json.loads(predictions_path.read_text(encoding="utf-8"))
    predictions = {
        item["sample_id"]: item["result"]
        for item in prediction_payload.get("samples", [])
    }

    evaluations = []
    for sample_id, truth_path in args.truth:
        if sample_id not in predictions or not truth_path.is_file():
            parser.error(f"missing prediction or truth: {sample_id}")
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        prediction = predictions[sample_id]
        grouping = prediction["grouping"]
        tokens = prediction["global_tokens"]
        assigned_tokens = [
            token
            for group in grouping["groups"]
            for token in group.get("assigned_tokens", [])
        ]
        row_token_ids = {
            token["token_id"]
            for group_rows in grouping["rows"]
            for row in group_rows.get("visible_rows", [])
            for token in row
        }
        base = {
            "sample_id": sample_id,
            "truth_sha256": _sha256(truth_path),
            "truth_source": truth.get("source"),
            "global_ocr_token_count": prediction["global_ocr_token_count"],
            "token_retention_rate": prediction["token_retention_rate"],
            "group_hypothesis_count": prediction["group_hypothesis_count"],
            "cross_separator_merge_count": grouping["cross_separator_merge_count"],
            "token_group_conflict_count": len(grouping["token_group_conflicts"]),
            "visible_row_coverage": (
                len(row_token_ids) / len(tokens) if tokens else None
            ),
            "output_token_row_assignment_rate": (
                len(row_token_ids) / len(tokens) if tokens else None
            ),
            "metric_definitions": {
                "visible_row_coverage": "Legacy alias of output_token_row_assignment_rate; not image-row accuracy.",
                "output_token_row_assignment_rate": "Distinct output token IDs assigned to a reconstructed row / output OCR token count. Measures internal assignment only; missed image tokens are outside this denominator.",
                "token_bbox_scope": "Character-position partitions of parent OCR line boxes are estimates, not token-detector localization ground truth.",
            },
            "image_row_accuracy": None,
            "token_localization_accuracy": None,
        }
        if truth.get("source") != "human_verified_image_ground_truth":
            base.update({"scored": False, "score_reason": "no_human_truth_qualitative_only"})
            evaluations.append(base)
            continue

        truth_lines = _truth_line_tokens(truth)
        truth_tokens = [token for values in truth_lines.values() for token in values]
        predicted_decimals = [
            token["text_raw"].replace("．", ".")
            for token in assigned_tokens
            if token["classification"] == DECIMAL_LITERAL
            and not token.get("decimal_group_id")
        ] + [
            span["literal"]
            for group_rows in grouping["rows"]
            for span in group_rows.get("decimal_spans", [])
        ]
        truth_decimals = [
            token["text_raw"].replace("．", ".")
            for token in truth_tokens
            if token["classification"] == DECIMAL_LITERAL
        ]
        base.update({
            "scored": True,
            "physical_semantic_coverage": _semantic_grouping_proxy(
                grouping["groups"], truth_lines, len(truth.get("regions", []))
            ),
            "numbers": _score(
                [
                    token["text_raw"] for token in assigned_tokens
                    if token["classification"] == NUMBER_01_39
                ],
                [token["text_raw"] for token in truth_tokens if token["classification"] == NUMBER_01_39],
            ),
            "operators": _score(
                [
                    OPERATOR_X for token in assigned_tokens
                    if token["classification"] == OPERATOR_X
                ],
                [
                    OPERATOR_X for token in truth_tokens
                    if token["classification"] == OPERATOR_X
                ],
            ),
            "decimals": _score(predicted_decimals, truth_decimals),
            "multiplier_to_number_contamination": sum(
                1 for token in assigned_tokens
                if token.get("decimal_group_id")
                and token.get("classification") == NUMBER_01_39
            ),
            "special_literals": _score(
                [
                    token["text_raw"] for token in assigned_tokens
                    if token["classification"] == SPECIAL_LITERAL
                ],
                [token["text_raw"] for token in truth_tokens if token["classification"] == SPECIAL_LITERAL],
            ),
        })
        evaluations.append(base)

    report = {
        "phase": str(seal.get("phase") or "TOKEN_FIRST_GROUPING_UNSPECIFIED_PHASE"),
        "prediction_seal_sha256": _sha256(seal_path),
        "truth_loaded_only_after_prediction_seal": True,
        "samples": evaluations,
        "machine_evidence_only": True,
        "human_confirmed": False,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    args.output.resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
