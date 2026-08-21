"""Run and seal whole-image token-first predictions before truth access."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
VISION_TOOLS = REPO_ROOT / "tools" / "vision"
if str(VISION_TOOLS) not in sys.path:
    sys.path.insert(0, str(VISION_TOOLS))

from cell_first_pipeline import _build_ocr  # noqa: E402
from token_first_pipeline import run_token_first, write_debug_overlay  # noqa: E402


IMPLEMENTATION_PATHS = (
    REPO_ROOT / "src" / "betguard" / "vision" / "cell_first.py",
    REPO_ROOT / "src" / "betguard" / "vision" / "token_first.py",
    REPO_ROOT / "tools" / "vision" / "token_first_pipeline.py",
)


def _parse_image(value: str) -> tuple[str, Path]:
    sample_id, separator, path = value.partition("=")
    if not separator or not sample_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("--image must be SAMPLE_ID=PATH")
    return sample_id.strip(), Path(path).resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


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
        parser.error("output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    ocr_engine = _build_ocr(args)
    predictions: list[dict[str, Any]] = []
    artifacts: list[Path] = []
    for sample_id, image_path in args.image:
        result = run_token_first(image_path, ocr_engine=ocr_engine)
        if result["source_image"]["sha256"] != _sha256(image_path):
            raise RuntimeError("SOURCE_IMAGE_HASH_CHANGED_DURING_RUN")
        if result["retained_token_count"] != result["global_ocr_token_count"]:
            raise RuntimeError("GLOBAL_OCR_TOKEN_RETENTION_INVARIANT_BROKEN")
        sample_dir = output_dir / sample_id
        sample_dir.mkdir()
        payloads = {
            "ocr-regions.json": result["ocr_regions"],
            "global-tokens.json": result["global_tokens"],
            "red-separators.json": result["red_separators"],
            "group-hypotheses.json": result["grouping"]["groups"],
            "visible-rows.json": result["grouping"]["rows"],
        }
        for filename, payload in payloads.items():
            target = sample_dir / filename
            _write_json(target, payload)
            artifacts.append(target)
        overlay = sample_dir / "debug-overlay.png"
        write_debug_overlay(image_path, result, overlay)
        artifacts.append(overlay)
        predictions.append({
            "sample_id": sample_id,
            "source_path": str(image_path),
            "result": result,
        })

    predictions_path = output_dir / "predictions.json"
    _write_json(predictions_path, {
        "phase": "TOKEN_FIRST_RED_SEPARATOR_GROUPING_PHASE_1B",
        "truth_loaded": False,
        "samples": predictions,
    })
    artifacts.append(predictions_path)
    seal = {
        "phase": "TOKEN_FIRST_RED_SEPARATOR_GROUPING_PHASE_1B",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "truth_loaded_before_seal": False,
        "sample_count": len(predictions),
        "source_images": {
            sample_id: {"path": str(path), "sha256": _sha256(path)}
            for sample_id, path in args.image
        },
        "implementation": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in IMPLEMENTATION_PATHS
        },
        "artifacts": {
            str(path.relative_to(output_dir)): _sha256(path)
            for path in artifacts
        },
        "machine_evidence_only": True,
        "human_confirmed": False,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    seal_path = output_dir / "prediction-seal.json"
    _write_json(seal_path, seal)
    print(json.dumps({
        "output_dir": str(output_dir),
        "sample_count": len(predictions),
        "global_ocr_tokens": {
            item["sample_id"]: item["result"]["global_ocr_token_count"]
            for item in predictions
        },
        "groups": {
            item["sample_id"]: item["result"]["group_hypothesis_count"]
            for item in predictions
        },
        "retention": {
            item["sample_id"]: item["result"]["token_retention_rate"]
            for item in predictions
        },
        "local_inference_calls": sum(
            item["result"]["local_inference_calls"] for item in predictions
        ),
        "seal_sha256": _sha256(seal_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
