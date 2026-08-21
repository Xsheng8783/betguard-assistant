"""Seal Phase 1 cell-first predictions before any truth is loaded.

This research CLI accepts only image paths.  It cannot read truth and writes
all outputs to a caller-selected evaluation directory outside the dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2


REPO_ROOT = Path(__file__).resolve().parents[2]
VISION_TOOLS = REPO_ROOT / "tools" / "vision"
if str(VISION_TOOLS) not in sys.path:
    sys.path.insert(0, str(VISION_TOOLS))

from cell_first_pipeline import (  # noqa: E402
    _build_ocr,
    detect_physical_cells,
    evidence_authority,
    run_cell_first,
    write_debug_overlay,
)


def _parse_image(value: str) -> tuple[str, Path]:
    sample_id, separator, path = value.partition("=")
    if not separator or not sample_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("--image must be SAMPLE_ID=PATH")
    return sample_id.strip(), Path(path).resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class _NoAcceptedCellOcr:
    def predict(self, _path: str) -> list[Any]:
        raise AssertionError("OCR must not run without an exact physical cell boundary")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", action="append", required=True, type=_parse_image)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--model-cache", required=True, type=Path)
    parser.add_argument("--det-model-dir", required=True, type=Path)
    parser.add_argument("--rec-model-dir", required=True, type=Path)
    args = parser.parse_args()

    sample_ids = [sample_id for sample_id, _path in args.image]
    if len(sample_ids) != len(set(sample_ids)):
        parser.error("sample ids must be unique")
    missing = [str(path) for _sample_id, path in args.image if not path.is_file()]
    if missing:
        parser.error("missing image(s): " + ", ".join(missing))
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error("output directory must be new or empty; sealed predictions are immutable")
    output_dir.mkdir(parents=True, exist_ok=True)

    preflight: dict[str, dict[str, Any]] = {}
    any_accepted = False
    for sample_id, image_path in args.image:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            parser.error(f"IMAGE_LOAD_FAILED: {image_path}")
        detection = detect_physical_cells(image)
        preflight[sample_id] = detection
        any_accepted = any_accepted or bool(detection["accepted_cell_ids"])

    ocr_engine = _build_ocr(args) if any_accepted else _NoAcceptedCellOcr()
    predictions: list[dict[str, Any]] = []
    artifact_paths: list[Path] = []
    for sample_id, image_path in args.image:
        result = run_cell_first(image_path, ocr_engine=ocr_engine)
        if result["source_image"]["sha256"] != _sha256(image_path):
            raise RuntimeError("SOURCE_IMAGE_HASH_CHANGED_DURING_RUN")
        sample_dir = output_dir / sample_id
        sample_dir.mkdir()
        cells_path = sample_dir / "detected-cells.json"
        tokens_path = sample_dir / "tokens.json"
        rows_path = sample_dir / "visible-rows.json"
        overlay_path = sample_dir / "debug-overlay.png"
        _write_json(cells_path, result["cell_detection"])
        _write_json(tokens_path, {
            "source_image": result["source_image"],
            "tokens": result["tokens"],
            **evidence_authority(),
        })
        _write_json(rows_path, {
            "source_image": result["source_image"],
            "rows": result["rows"],
            **evidence_authority(),
        })
        write_debug_overlay(image_path, result, overlay_path)
        artifact_paths.extend((cells_path, tokens_path, rows_path, overlay_path))
        predictions.append({
            "sample_id": sample_id,
            "source_path": str(image_path),
            "result": result,
        })

    predictions_path = output_dir / "predictions.json"
    _write_json(predictions_path, {
        "phase": "CELL_FIRST_RECOGNITION_PIPELINE_PHASE_1",
        "truth_loaded": False,
        "samples": predictions,
        **evidence_authority(),
    })
    artifact_paths.append(predictions_path)
    seal = {
        "phase": "CELL_FIRST_RECOGNITION_PIPELINE_PHASE_1",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "truth_loaded_before_seal": False,
        "sample_count": len(predictions),
        "source_images": {
            sample_id: {"path": str(path), "sha256": _sha256(path)}
            for sample_id, path in args.image
        },
        "artifacts": {
            str(path.relative_to(output_dir)): _sha256(path)
            for path in artifact_paths
        },
        **evidence_authority(),
    }
    seal_path = output_dir / "prediction-seal.json"
    _write_json(seal_path, seal)
    print(json.dumps({
        "output_dir": str(output_dir),
        "sample_count": len(predictions),
        "accepted_cells": sum(
            len(item["result"]["cell_detection"]["accepted_cell_ids"])
            for item in predictions
        ),
        "uncertain_cells": sum(
            len(item["result"]["cell_detection"]["uncertain_cell_ids"])
            for item in predictions
        ),
        "local_inference_calls": sum(item["result"]["local_inference_calls"] for item in predictions),
        "seal_sha256": _sha256(seal_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
