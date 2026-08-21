"""Whole-image PP-OCR followed by red-separator token grouping.

This Phase 1B research pipeline deliberately runs OCR before separator
detection.  Separator or grouping uncertainty can never remove OCR evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
VISION_TOOLS = REPO_ROOT / "tools" / "vision"
if str(VISION_TOOLS) not in sys.path:
    sys.path.insert(0, str(VISION_TOOLS))

from betguard.vision.cell_first import evidence_authority  # noqa: E402
from betguard.vision.token_first import (  # noqa: E402
    TOKEN_FIRST_SCHEMA_VERSION,
    group_regions_with_separators,
    literal_tokens_from_regions,
)
from cell_first_pipeline import _build_ocr, _normalize_ocr_regions  # noqa: E402


@dataclass(frozen=True)
class SeparatorDetectionConfig:
    minimum_red: int = 50
    minimum_saturation: int = 26
    minimum_segment_confidence: float = 0.32
    barrier_confidence: float = 0.52
    maximum_segments_per_orientation: int = 180


def run_token_first(
    image_path: str | os.PathLike[str],
    *,
    ocr_engine: Any,
    separator_config: SeparatorDetectionConfig | None = None,
) -> dict[str, Any]:
    """Run exactly one whole-image OCR call before any red-line processing."""
    started = time.perf_counter()
    path = Path(image_path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("IMAGE_LOAD_FAILED")
    image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    ocr_started = time.perf_counter()
    raw_results = list(ocr_engine.predict(str(path)))
    local_inference_calls = 1
    regions = _normalize_ocr_regions(raw_results)
    for index, region in enumerate(regions, 1):
        region["region_id"] = f"PPREGION-{index:05d}"
    tokens = literal_tokens_from_regions(regions)
    ocr_latency_ms = (time.perf_counter() - ocr_started) * 1000.0

    separator_started = time.perf_counter()
    separator_result = detect_red_separators(
        image, config=separator_config
    )
    separator_latency_ms = (time.perf_counter() - separator_started) * 1000.0
    grouping = group_regions_with_separators(
        regions,
        tokens,
        separator_result["segments"],
        image_size=(int(image.shape[1]), int(image.shape[0])),
    )
    retained_ids = set(grouping["retained_token_ids"])
    source_ids = {token["token_id"] for token in tokens}
    if retained_ids != source_ids:
        raise RuntimeError("GLOBAL_OCR_TOKEN_RETENTION_INVARIANT_BROKEN")

    return {
        "schema_version": TOKEN_FIRST_SCHEMA_VERSION,
        "status": "COMPLETED" if tokens else "GLOBAL_OCR_EMPTY",
        "architecture": "GLOBAL_PPOCR_THEN_RED_SEPARATOR_GROUPING",
        "source_image": {
            "path": str(path.resolve()),
            "sha256": image_sha256,
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
        },
        "ocr_regions": regions,
        "global_tokens": tokens,
        "red_separators": separator_result,
        "grouping": grouping,
        "global_ocr_region_count": len(regions),
        "global_ocr_token_count": len(tokens),
        "retained_token_count": len(retained_ids),
        "token_retention_rate": len(retained_ids) / len(tokens) if tokens else 1.0,
        "group_hypothesis_count": len(grouping["groups"]),
        "local_inference_calls": local_inference_calls,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "ocr_latency_ms": round(ocr_latency_ms, 3),
        "separator_latency_ms": round(separator_latency_ms, 3),
        **evidence_authority(),
    }


def detect_red_separators(
    image: np.ndarray,
    *,
    config: SeparatorDetectionConfig | None = None,
) -> dict[str, Any]:
    """Return broken horizontal/vertical red segments as barrier evidence."""
    config = config or SeparatorDetectionConfig()
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("image must be a BGR color array")
    height, width = image.shape[:2]
    red_mask = _red_pixel_mask(image, config)
    red_ratio = float(np.count_nonzero(red_mask)) / float(max(1, width * height))

    horizontal_seed = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, int(width * 0.018)), 1)),
    )
    vertical_seed = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, int(height * 0.018)))),
    )
    horizontal_seed = cv2.morphologyEx(
        horizontal_seed,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(width * 0.012)), 3)),
    )
    vertical_seed = cv2.morphologyEx(
        vertical_seed,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, max(5, int(height * 0.012)))),
    )
    horizontal = _hough_segments(
        horizontal_seed,
        red_mask,
        orientation="horizontal",
        config=config,
    )
    vertical = _hough_segments(
        vertical_seed,
        red_mask,
        orientation="vertical",
        config=config,
    )
    segments = []
    for index, segment in enumerate(horizontal + vertical, 1):
        segment["separator_id"] = f"SEP-{index:04d}"
        segments.append(segment)
    return {
        "red_pixel_ratio": round(red_ratio, 8),
        "segments": segments,
        "horizontal_segment_count": len(horizontal),
        "vertical_segment_count": len(vertical),
        "barrier_segment_ids": [
            segment["separator_id"]
            for segment in segments
            if segment["confidence"] >= config.barrier_confidence
        ],
        "complete_cells_required": False,
        **evidence_authority(),
    }


def write_debug_overlay(
    image_path: str | os.PathLike[str],
    result: dict[str, Any],
    output_path: str | os.PathLike[str],
) -> None:
    """Write research-only separators, OCR regions, and group hypotheses."""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("IMAGE_LOAD_FAILED")
    for separator in result["red_separators"]["segments"]:
        start = tuple(int(round(value)) for value in separator["start"])
        end = tuple(int(round(value)) for value in separator["end"])
        color = (0, 0, 255) if separator["confidence"] >= 0.52 else (0, 120, 255)
        cv2.line(image, start, end, color, 2)
    for region in result["ocr_regions"]:
        x1, y1, x2, y2 = [int(round(value)) for value in region["bbox"]]
        cv2.rectangle(image, (x1, y1), (x2, y2), (210, 0, 210), 1)
    for group in result["grouping"]["groups"]:
        x1, y1, x2, y2 = [int(round(value)) for value in group["bbox"]]
        color = (0, 150, 255) if group["grouping_uncertain"] else (0, 180, 0)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            image,
            group["group_id"],
            (x1, max(14, y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            color,
            1,
            cv2.LINE_AA,
        )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), image):
        raise OSError("OVERLAY_WRITE_FAILED")


def _red_pixel_mask(
    image: np.ndarray, config: SeparatorDetectionConfig
) -> np.ndarray:
    b, g, r = cv2.split(image[:, :, :3])
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    mask = (
        ((hue <= 17) | (hue >= 163))
        & (saturation >= config.minimum_saturation)
        & (value >= 42)
        & (r >= config.minimum_red)
        & (r.astype(np.int16) >= g.astype(np.int16))
        & (r.astype(np.int16) >= b.astype(np.int16))
    )
    binary = np.where(mask, 255, 0).astype(np.uint8)
    return cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )


def _hough_segments(
    seed_mask: np.ndarray,
    support_mask: np.ndarray,
    *,
    orientation: str,
    config: SeparatorDetectionConfig,
) -> list[dict[str, Any]]:
    height, width = seed_mask.shape[:2]
    relevant_size = width if orientation == "horizontal" else height
    minimum_length = max(24, int(relevant_size * 0.045))
    raw_lines = cv2.HoughLinesP(
        seed_mask,
        1,
        np.pi / 360.0,
        threshold=max(18, int(minimum_length * 0.55)),
        minLineLength=minimum_length,
        maxLineGap=max(8, int(relevant_size * 0.025)),
    )
    if raw_lines is None:
        return []
    candidates: list[dict[str, Any]] = []
    for raw in raw_lines[:, 0]:
        x1, y1, x2, y2 = [float(value) for value in raw]
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        angle = ((angle + 90.0) % 180.0) - 90.0
        if orientation == "horizontal" and abs(angle) > 24.0:
            continue
        if orientation == "vertical" and abs(angle) < 66.0:
            continue
        length = float(np.hypot(x2 - x1, y2 - y1))
        support = _sample_support(support_mask, [x1, y1], [x2, y2])
        length_score = min(1.0, length / max(1.0, relevant_size * 0.22))
        confidence = 0.68 * support + 0.32 * length_score
        if confidence < config.minimum_segment_confidence:
            continue
        candidates.append({
            "orientation": orientation,
            "start": [x1, y1],
            "end": [x2, y2],
            "bbox": [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)],
            "length": round(length, 3),
            "angle_degrees": round(angle, 3),
            "confidence": round(confidence, 6),
            "detection_reason": "red_directional_morphology+hough+pixel_support",
        })
    merged = _merge_continuations(candidates, orientation, width, height)
    merged.sort(key=lambda item: item["confidence"], reverse=True)
    return merged[:config.maximum_segments_per_orientation]


def _merge_continuations(
    candidates: list[dict[str, Any]],
    orientation: str,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    candidates = sorted(
        candidates,
        key=lambda item: (
            (item["start"][1] + item["end"][1]) / 2.0,
            (item["start"][0] + item["end"][0]) / 2.0,
        ) if orientation == "horizontal" else (
            (item["start"][0] + item["end"][0]) / 2.0,
            (item["start"][1] + item["end"][1]) / 2.0,
        ),
    )
    result: list[dict[str, Any]] = []
    position_tolerance = max(4.0, min(width, height) * 0.008)
    gap_limit = (width if orientation == "horizontal" else height) * 0.045
    for candidate in candidates:
        merged = False
        for kept in result:
            if abs(candidate["angle_degrees"] - kept["angle_degrees"]) > 7.0:
                continue
            if orientation == "horizontal":
                candidate_position = sum(point[1] for point in (candidate["start"], candidate["end"])) / 2.0
                kept_position = sum(point[1] for point in (kept["start"], kept["end"])) / 2.0
                candidate_span = sorted((candidate["start"][0], candidate["end"][0]))
                kept_span = sorted((kept["start"][0], kept["end"][0]))
            else:
                candidate_position = sum(point[0] for point in (candidate["start"], candidate["end"])) / 2.0
                kept_position = sum(point[0] for point in (kept["start"], kept["end"])) / 2.0
                candidate_span = sorted((candidate["start"][1], candidate["end"][1]))
                kept_span = sorted((kept["start"][1], kept["end"][1]))
            gap = max(0.0, max(candidate_span[0], kept_span[0]) - min(candidate_span[1], kept_span[1]))
            if abs(candidate_position - kept_position) > position_tolerance or gap > gap_limit:
                continue
            kept.update(_combined_segment(kept, candidate, orientation))
            merged = True
            break
        if not merged:
            result.append(dict(candidate))
    return result


def _combined_segment(
    left: dict[str, Any], right: dict[str, Any], orientation: str
) -> dict[str, Any]:
    points = [left["start"], left["end"], right["start"], right["end"]]
    if orientation == "horizontal":
        points.sort(key=lambda point: point[0])
    else:
        points.sort(key=lambda point: point[1])
    start, end = points[0], points[-1]
    length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
    return {
        "start": list(start),
        "end": list(end),
        "bbox": [min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])],
        "length": round(length, 3),
        "confidence": round(max(left["confidence"], right["confidence"]), 6),
        "detection_reason": "red_directional_morphology+hough+deterministic_continuation",
    }


def _sample_support(
    mask: np.ndarray, start: list[float], end: list[float]
) -> float:
    length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
    sample_count = max(8, int(length / 2.0))
    xs = np.linspace(start[0], end[0], sample_count)
    ys = np.linspace(start[1], end[1], sample_count)
    height, width = mask.shape[:2]
    supported = 0
    for x, y in zip(xs, ys):
        ix, iy = int(round(x)), int(round(y))
        neighborhood = mask[
            max(0, iy - 2):min(height, iy + 3),
            max(0, ix - 2):min(width, ix + 3),
        ]
        supported += int(np.any(neighborhood > 0))
    return supported / sample_count


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overlay")
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--model-cache", required=True, type=Path)
    parser.add_argument("--det-model-dir", required=True, type=Path)
    parser.add_argument("--rec-model-dir", required=True, type=Path)
    args = parser.parse_args()
    result = run_token_first(args.image, ocr_engine=_build_ocr(args))
    _write_json(Path(args.output), result)
    if args.overlay:
        write_debug_overlay(args.image, result, args.overlay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
