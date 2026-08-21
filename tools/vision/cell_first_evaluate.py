"""Evaluate a sealed cell-first prediction set without altering predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betguard.vision.cell_first import (  # noqa: E402
    NUMBER_01_39,
    OPERATOR_X,
    SPECIAL_LITERAL,
    evidence_authority,
)


_OPERATOR_RE = re.compile(r"[xX×]")
_DECIMAL_RE = re.compile(r"(?<!\d)\d+[.．]\d+(?!\d)")
_SPECIAL_RE = re.compile(r"半車|尾|車|各")


def _parse_truth(value: str) -> tuple[str, Path]:
    sample_id, separator, path = value.partition("=")
    if not separator or not sample_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("--truth must be SAMPLE_ID=PATH")
    return sample_id.strip(), Path(path).resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counter_score(predicted: Iterable[str], expected: Iterable[str]) -> dict[str, Any]:
    predicted_counter = Counter(str(value) for value in predicted)
    expected_counter = Counter(str(value) for value in expected)
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


def _truth_numbers(truth: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    for line in truth.get("lines", []):
        for group in line.get("number_groups", []):
            numbers.extend(str(value) for value in group)
    return numbers


def _raw_texts(truth: dict[str, Any]) -> list[str]:
    return [str(line.get("raw_text") or "") for line in truth.get("lines", [])]


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
        parser.error("sealed predictions are required before truth evaluation")
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
    truth_ids = [sample_id for sample_id, _path in args.truth]
    if len(truth_ids) != len(set(truth_ids)):
        parser.error("truth sample ids must be unique")

    evaluated: list[dict[str, Any]] = []
    for sample_id, truth_path in args.truth:
        if sample_id not in predictions:
            parser.error(f"truth has no sealed prediction: {sample_id}")
        if not truth_path.is_file():
            parser.error(f"missing truth: {truth_path}")
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        prediction = predictions[sample_id]
        detection = prediction["cell_detection"]
        is_human_truth = truth.get("source") == "human_verified_image_ground_truth"
        item: dict[str, Any] = {
            "sample_id": sample_id,
            "truth_sha256": _sha256(truth_path),
            "truth_source": truth.get("source"),
            "scored": is_human_truth,
            "accepted_cell_count": len(detection.get("accepted_cell_ids", [])),
            "uncertain_boundary_hypothesis_count": len(detection.get("uncertain_cell_ids", [])),
            "status": detection.get("status"),
        }
        if not is_human_truth:
            item["score_reason"] = "no_human_truth_qualitative_only"
            evaluated.append(item)
            continue

        tokens = prediction.get("tokens", [])
        raw_texts = _raw_texts(truth)
        truth_regions = truth.get("regions", [])
        boundary_boxes = [region.get("bounding_box") for region in truth_regions if region.get("bounding_box")]
        number_score = _counter_score(
            [token["text_raw"] for token in tokens if token.get("classification") == NUMBER_01_39],
            _truth_numbers(truth),
        )
        operator_score = _counter_score(
            [token["text_raw"] for token in tokens if token.get("classification") == OPERATOR_X],
            [match.group(0) for raw in raw_texts for match in _OPERATOR_RE.finditer(raw)],
        )
        decimal_score = _counter_score(
            [span["literal"] for rows in prediction.get("rows", []) for span in rows.get("decimal_spans", [])],
            [match.group(0).replace("．", ".") for raw in raw_texts for match in _DECIMAL_RE.finditer(raw)],
        )
        special_score = _counter_score(
            [
                match.group(0)
                for token in tokens
                if token.get("classification") == SPECIAL_LITERAL
                for match in _SPECIAL_RE.finditer(str(token.get("text_raw") or ""))
            ],
            [
                match.group(0)
                for line in truth.get("lines", [])
                for match in _SPECIAL_RE.finditer(str(line.get("play_text") or ""))
            ],
        )
        item["physical_cells"] = {
            "reference_region_count": len(truth_regions),
            "boundary_exact": None,
            "over_segmentation": None,
            "under_segmentation": None,
            "score_reason": (
                "human truth has no physical boundary boxes"
                if not boundary_boxes
                else "prediction-to-boundary matching not implemented"
            ),
        }
        item["tokens"] = {
            "precision": None,
            "recall": None,
            "score_reason": "human truth has no token text+bbox annotations",
        }
        item["numbers"] = number_score
        item["operators"] = operator_score
        item["visible_rows"] = {
            "exact": None,
            "score_reason": "human truth has semantic lines, not visible-row annotations",
        }
        item["decimals"] = decimal_score
        item["special_literals"] = special_score
        evaluated.append(item)

    report = {
        "phase": "CELL_FIRST_RECOGNITION_PIPELINE_PHASE_1",
        "prediction_seal_sha256": _sha256(seal_path),
        "truth_loaded_only_after_prediction_seal": True,
        "samples": evaluated,
        **evidence_authority(),
    }
    args.output.resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
