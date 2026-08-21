"""Generic red-grid cell segmentation and PP-OCRv6 token layout evidence.

The module is research-only and has no route into Betguard authority.  It uses
image pixels and OCR geometry only: no filenames, hashes, truth, expected bet
counts, or betting semantics are consulted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betguard.vision.cell_first import (  # noqa: E402
    CELL_BOUNDARY_UNCERTAIN,
    SCHEMA_VERSION,
    evidence_authority,
    make_token,
    reconstruct_visible_rows,
)


@dataclass(frozen=True)
class CellDetectionConfig:
    minimum_red: int = 50
    minimum_saturation: int = 30
    minimum_edge_support: float = 0.34
    minimum_average_support: float = 0.48
    minimum_component_fill: float = 0.72
    minimum_width_ratio: float = 0.055
    minimum_height_ratio: float = 0.022
    minimum_area_ratio: float = 0.0012
    maximum_area_ratio: float = 0.82
    maximum_overlap_ratio: float = 0.04
    maximum_competing_overlap_ratio: float = 0.50


def detect_physical_cells(
    image: np.ndarray,
    *,
    config: CellDetectionConfig | None = None,
) -> dict[str, Any]:
    """Detect enclosed physical cells from red grid pixels, failing closed."""
    config = config or CellDetectionConfig()
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("image must be a BGR color array")
    height, width = image.shape[:2]
    if width < 32 or height < 32:
        return _uncertain_detection(width, height, "image_too_small")

    red_mask = _red_pixel_mask(image, config)
    red_ratio = float(np.count_nonzero(red_mask)) / float(width * height)
    if red_ratio < 0.0005:
        return _uncertain_detection(width, height, "red_grid_missing", red_ratio=red_ratio)

    horizontal_length = max(9, int(round(width * 0.018)))
    vertical_length = max(9, int(round(height * 0.018)))
    horizontal = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_length, 1)),
    )
    vertical = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_length)),
    )
    grid = cv2.bitwise_or(horizontal, vertical)
    repair_x = max(3, int(round(width * 0.004)))
    repair_y = max(3, int(round(height * 0.004)))
    grid = cv2.bitwise_or(
        cv2.morphologyEx(
            grid,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (repair_x, 1)),
        ),
        cv2.morphologyEx(
            grid,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, repair_y)),
        ),
    )
    thickness = max(1, int(round(min(width, height) * 0.0015)))
    grid = cv2.dilate(
        grid,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2 * thickness + 1, 2 * thickness + 1)),
        iterations=1,
    )

    free_space = cv2.bitwise_not(grid)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        free_space, connectivity=4
    )
    min_width = max(18, int(round(width * config.minimum_width_ratio)))
    min_height = max(14, int(round(height * config.minimum_height_ratio)))
    min_area = max(180, int(round(width * height * config.minimum_area_ratio)))
    max_area = int(round(width * height * config.maximum_area_ratio))
    search_band = max(4, int(round(min(width, height) * 0.005)))

    candidates: list[dict[str, Any]] = []
    for label in range(1, component_count):
        x, y, component_width, component_height, area = [
            int(value) for value in stats[label]
        ]
        if x <= 0 or y <= 0 or x + component_width >= width or y + component_height >= height:
            continue
        if component_width < min_width or component_height < min_height:
            continue
        if area < min_area or area > max_area:
            continue
        bbox_area = component_width * component_height
        fill_ratio = float(area) / float(bbox_area) if bbox_area else 0.0
        if fill_ratio < config.minimum_component_fill:
            continue

        supports = _edge_supports(
            red_mask,
            x,
            y,
            x + component_width,
            y + component_height,
            search_band,
        )
        average_support = sum(supports.values()) / 4.0
        minimum_support = min(supports.values())
        bbox = _outer_boundary_bbox(
            red_mask,
            x,
            y,
            x + component_width,
            y + component_height,
            search_band,
        )
        confidence = max(0.0, min(1.0,
            0.55 * average_support + 0.25 * minimum_support + 0.20 * min(fill_ratio, 1.0)
        ))
        accepted = (
            minimum_support >= config.minimum_edge_support
            and average_support >= config.minimum_average_support
        )
        reasons = ["red_enclosed_component", "four_edge_support"]
        if not accepted:
            reasons.append(CELL_BOUNDARY_UNCERTAIN)
            if minimum_support < config.minimum_edge_support:
                reasons.append("weak_boundary_edge")
            if average_support < config.minimum_average_support:
                reasons.append("low_average_boundary_support")
        candidates.append({
            "bbox": bbox,
            "interior_bbox": [x, y, x + component_width, y + component_height],
            "confidence": round(confidence, 6),
            "detection_reason": ";".join(reasons),
            "boundary_status": "EXACT_CANDIDATE" if accepted else CELL_BOUNDARY_UNCERTAIN,
            "edge_support": {key: round(value, 6) for key, value in supports.items()},
            "component_fill": round(fill_ratio, 6),
        })

    hough_candidates = _hough_cell_candidates(image, red_mask, config)
    if hough_candidates:
        candidates = hough_candidates

    candidates.sort(key=lambda cell: (
        cell["bbox"][1], cell["bbox"][0], cell["bbox"][3], cell["bbox"][2]
    ))
    for index, cell in enumerate(candidates, 1):
        cell["cell_id"] = f"CELL-{index:04d}"

    accepted_cells = [cell for cell in candidates if cell["boundary_status"] != CELL_BOUNDARY_UNCERTAIN]
    overlap_pairs = _overlap_pairs(accepted_cells, config.maximum_overlap_ratio)
    if overlap_pairs:
        affected = {cell_id for pair in overlap_pairs for cell_id in pair}
        for cell in candidates:
            if cell["cell_id"] in affected:
                cell["boundary_status"] = CELL_BOUNDARY_UNCERTAIN
                cell["detection_reason"] += ";overlapping_cell_boundary;" + CELL_BOUNDARY_UNCERTAIN
        accepted_cells = [
            cell for cell in candidates if cell["boundary_status"] != CELL_BOUNDARY_UNCERTAIN
        ]

    competing_pairs = _competing_boundary_pairs(
        accepted_cells,
        [cell for cell in candidates if cell["boundary_status"] == CELL_BOUNDARY_UNCERTAIN],
        config.maximum_competing_overlap_ratio,
    )
    if competing_pairs:
        affected = {accepted_id for accepted_id, _uncertain_id in competing_pairs}
        for cell in candidates:
            if cell["cell_id"] in affected:
                cell["boundary_status"] = CELL_BOUNDARY_UNCERTAIN
                cell["detection_reason"] += (
                    ";competing_boundary_hypothesis;" + CELL_BOUNDARY_UNCERTAIN
                )
        accepted_cells = [
            cell for cell in candidates if cell["boundary_status"] != CELL_BOUNDARY_UNCERTAIN
        ]

    if not accepted_cells:
        status = CELL_BOUNDARY_UNCERTAIN
        failure_reasons = ["no_unique_closed_cells"]
    elif any(cell["boundary_status"] == CELL_BOUNDARY_UNCERTAIN for cell in candidates):
        status = "COMPLETED_WITH_UNCERTAIN_BOUNDARIES"
        failure_reasons = ["some_boundaries_uncertain"]
    else:
        status = "COMPLETED"
        failure_reasons = []

    return {
        "status": status,
        "image_size": [width, height],
        "red_pixel_ratio": round(red_ratio, 8),
        "cells": candidates,
        "accepted_cell_ids": [cell["cell_id"] for cell in accepted_cells],
        "uncertain_cell_ids": [
            cell["cell_id"] for cell in candidates
            if cell["boundary_status"] == CELL_BOUNDARY_UNCERTAIN
        ],
        "overlap_pairs": [list(pair) for pair in overlap_pairs],
        "competing_boundary_pairs": [list(pair) for pair in competing_pairs],
        "failure_reasons": failure_reasons,
        **evidence_authority(),
    }


def run_cell_first(
    image_path: str | os.PathLike[str],
    *,
    ocr_engine: Any,
    detection_config: CellDetectionConfig | None = None,
) -> dict[str, Any]:
    """Run physical segmentation, PP-OCR per accepted cell, and row geometry."""
    started = time.perf_counter()
    path = Path(image_path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("IMAGE_LOAD_FAILED")
    image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    detection = detect_physical_cells(image, config=detection_config)
    accepted = {
        cell["cell_id"]: cell
        for cell in detection["cells"]
        if cell["cell_id"] in set(detection["accepted_cell_ids"])
    }

    all_tokens: list[dict[str, Any]] = []
    rows_by_cell: list[dict[str, Any]] = []
    local_inference_calls = 0
    with tempfile.TemporaryDirectory(prefix="betguard-cell-first-") as temp_dir:
        for cell_index, cell in enumerate(accepted.values(), 1):
            x1, y1, x2, y2 = [int(value) for value in cell["bbox"]]
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                cell["boundary_status"] = CELL_BOUNDARY_UNCERTAIN
                cell["detection_reason"] += ";empty_crop;" + CELL_BOUNDARY_UNCERTAIN
                continue
            crop_path = Path(temp_dir) / f"cell-{cell_index:04d}.png"
            if not cv2.imwrite(str(crop_path), crop):
                raise OSError("CELL_CROP_WRITE_FAILED")
            local_inference_calls += 1
            results = list(ocr_engine.predict(str(crop_path)))
            regions = _normalize_ocr_regions(results)
            cell_tokens: list[dict[str, Any]] = []
            for region in regions:
                rx1, ry1, rx2, ry2 = region["bbox"]
                mapped = [
                    max(0.0, min(float(image.shape[1]), rx1 + x1)),
                    max(0.0, min(float(image.shape[0]), ry1 + y1)),
                    max(0.0, min(float(image.shape[1]), rx2 + x1)),
                    max(0.0, min(float(image.shape[0]), ry2 + y1)),
                ]
                if mapped[2] <= mapped[0] or mapped[3] <= mapped[1]:
                    continue
                token = make_token(
                    token_id=f"TOKEN-{len(all_tokens) + len(cell_tokens) + 1:05d}",
                    cell_id=cell["cell_id"],
                    text_raw=region["text"],
                    confidence=region["confidence"],
                    bbox=mapped,
                )
                token["source_region_polygon"] = [
                    [float(point[0]) + x1, float(point[1]) + y1]
                    for point in region["polygon"]
                ]
                cell_tokens.append(token)
            row_result = reconstruct_visible_rows(cell["cell_id"], cell_tokens)
            normalized_tokens = row_result.pop("tokens", cell_tokens)
            all_tokens.extend(normalized_tokens)
            rows_by_cell.append(row_result)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": detection["status"],
        "source_image": {
            "sha256": image_sha256,
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
        },
        "cell_detection": detection,
        "tokens": all_tokens,
        "rows": rows_by_cell,
        "local_inference_calls": local_inference_calls,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        **evidence_authority(),
    }


def write_debug_overlay(
    image_path: str | os.PathLike[str],
    result: dict[str, Any],
    output_path: str | os.PathLike[str],
) -> None:
    """Write a research-only overlay of cells, tokens, and reconstructed rows."""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("IMAGE_LOAD_FAILED")
    row_by_token: dict[str, int] = {}
    for cell_rows in result.get("rows", []):
        for row_index, row in enumerate(cell_rows.get("visible_rows", []), 1):
            for token in row:
                row_by_token[str(token["token_id"])] = row_index
    for cell in result.get("cell_detection", {}).get("cells", []):
        x1, y1, x2, y2 = [int(round(value)) for value in cell["bbox"]]
        color = (0, 170, 0) if cell["boundary_status"] != CELL_BOUNDARY_UNCERTAIN else (0, 140, 255)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(image, cell["cell_id"], (x1, max(14, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    for token in result.get("tokens", []):
        x1, y1, x2, y2 = [int(round(value)) for value in token["bbox"]]
        cv2.rectangle(image, (x1, y1), (x2, y2), (190, 0, 190), 1)
        label = f"R{row_by_token.get(token['token_id'], 0)}:{token['text_raw']}"
        cv2.putText(image, label, (x1, max(12, y1 - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (190, 0, 190), 1, cv2.LINE_AA)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), image):
        raise OSError("OVERLAY_WRITE_FAILED")


def _red_pixel_mask(image: np.ndarray, config: CellDetectionConfig) -> np.ndarray:
    b, g, r = cv2.split(image[:, :, :3])
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    hue_red = (hue <= 16) | (hue >= 164)
    rgb_red = (
        (r >= config.minimum_red)
        & (r.astype(np.int16) >= g.astype(np.int16))
        & (r.astype(np.int16) >= b.astype(np.int16))
    )
    mask = hue_red & (saturation >= config.minimum_saturation) & (value >= 45) & rgb_red
    binary = np.where(mask, 255, 0).astype(np.uint8)
    return cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )


def _hough_cell_candidates(
    image: np.ndarray,
    red_mask: np.ndarray,
    config: CellDetectionConfig,
) -> list[dict[str, Any]]:
    """Find skewed grid faces from two dominant red-edge line families."""
    height, width = red_mask.shape[:2]
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    red_neighborhood = cv2.dilate(
        red_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    )
    red_edges = cv2.bitwise_and(edges, red_neighborhood)
    minimum_line = max(32, int(round(min(width, height) * 0.055)))
    raw_lines = cv2.HoughLinesP(
        red_edges,
        1,
        np.pi / 360.0,
        threshold=max(24, int(round(minimum_line * 0.65))),
        minLineLength=minimum_line,
        maxLineGap=max(10, int(round(min(width, height) * 0.025))),
    )
    if raw_lines is None or len(raw_lines) < 4:
        return []

    segments = []
    for raw in raw_lines[:, 0]:
        x1, y1, x2, y2 = [float(value) for value in raw]
        length = float(np.hypot(x2 - x1, y2 - y1))
        if length < minimum_line:
            continue
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        angle = ((angle + 90.0) % 180.0) - 90.0
        segments.append({
            "points": [x1, y1, x2, y2],
            "length": length,
            "angle": angle,
        })
    vertical_segments = []
    all_edge_lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360.0,
        threshold=max(34, int(round(minimum_line * 0.8))),
        minLineLength=minimum_line,
        maxLineGap=max(8, int(round(min(width, height) * 0.018))),
    )
    if all_edge_lines is not None:
        for raw in all_edge_lines[:, 0]:
            x1, y1, x2, y2 = [float(value) for value in raw]
            length = float(np.hypot(x2 - x1, y2 - y1))
            angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            angle = ((angle + 90.0) % 180.0) - 90.0
            if length >= minimum_line and abs(angle) >= 55.0:
                vertical_segments.append({
                    "points": [x1, y1, x2, y2],
                    "length": length,
                    "angle": angle,
                })
    paired_candidates = _paired_segment_cell_candidates(
        segments + vertical_segments, red_edges, width, height, config
    )
    if paired_candidates:
        return paired_candidates
    families = _dominant_angle_families(segments)
    if families is None:
        return []
    first_angle, second_angle = families
    first_lines = _cluster_parallel_segments(
        segments, first_angle, width, height, minimum_line
    )
    second_lines = _cluster_parallel_segments(
        segments, second_angle, width, height, minimum_line
    )
    if len(first_lines) < 2 or len(second_lines) < 2:
        return []

    support_mask = cv2.dilate(
        red_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
        iterations=1,
    )
    candidates: list[dict[str, Any]] = []
    for first_index, first_top in enumerate(first_lines[:-1]):
        for first_bottom in first_lines[first_index + 1:first_index + 2]:
            for second_index, second_left in enumerate(second_lines[:-1]):
                for second_right in second_lines[second_index + 1:second_index + 2]:
                    polygon = _line_quad(
                        first_top, first_bottom, second_left, second_right
                    )
                    if polygon is None:
                        continue
                    array = np.asarray(polygon, dtype=np.float32)
                    if not cv2.isContourConvex(array.astype(np.int32)):
                        continue
                    area = abs(float(cv2.contourArea(array)))
                    if area < width * height * config.minimum_area_ratio:
                        continue
                    if area > width * height * config.maximum_area_ratio:
                        continue
                    if any(
                        point[0] < -4 or point[1] < -4
                        or point[0] > width + 4 or point[1] > height + 4
                        for point in polygon
                    ):
                        continue
                    xs = [point[0] for point in polygon]
                    ys = [point[1] for point in polygon]
                    bbox = [
                        max(0, int(np.floor(min(xs)))),
                        max(0, int(np.floor(min(ys)))),
                        min(width, int(np.ceil(max(xs))) + 1),
                        min(height, int(np.ceil(max(ys))) + 1),
                    ]
                    cell_width = bbox[2] - bbox[0]
                    cell_height = bbox[3] - bbox[1]
                    if cell_width < width * config.minimum_width_ratio:
                        continue
                    if cell_height < height * config.minimum_height_ratio:
                        continue
                    supports = _polygon_edge_supports(support_mask, polygon)
                    minimum_support = min(supports.values())
                    average_support = sum(supports.values()) / 4.0
                    span_support = min(
                        _line_span_support(first_top, polygon[0], polygon[1]),
                        _line_span_support(first_bottom, polygon[3], polygon[2]),
                        _line_span_support(second_left, polygon[0], polygon[3]),
                        _line_span_support(second_right, polygon[1], polygon[2]),
                    )
                    confidence = max(0.0, min(1.0,
                        0.55 * average_support
                        + 0.25 * minimum_support
                        + 0.20 * span_support
                    ))
                    accepted = (
                        minimum_support >= config.minimum_edge_support
                        and average_support >= config.minimum_average_support
                        and span_support >= 0.45
                    )
                    reasons = [
                        "red_edge_hough_intersections",
                        "two_line_families",
                        "four_edge_support",
                    ]
                    if not accepted:
                        reasons.extend([CELL_BOUNDARY_UNCERTAIN, "weak_skewed_boundary_support"])
                    candidates.append({
                        "bbox": bbox,
                        "polygon": [[round(float(x), 3), round(float(y), 3)] for x, y in polygon],
                        "interior_bbox": bbox,
                        "confidence": round(confidence, 6),
                        "detection_reason": ";".join(reasons),
                        "boundary_status": "EXACT_CANDIDATE" if accepted else CELL_BOUNDARY_UNCERTAIN,
                        "edge_support": {key: round(value, 6) for key, value in supports.items()},
                        "component_fill": None,
                        "line_family_angles": [round(first_angle, 3), round(second_angle, 3)],
                    })

    candidates.sort(key=lambda cell: cell["confidence"], reverse=True)
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(_bbox_iou(candidate["bbox"], kept["bbox"]) >= 0.72 for kept in unique):
            continue
        unique.append(candidate)
    return unique


def _paired_segment_cell_candidates(
    segments: list[dict[str, Any]],
    red_edges: np.ndarray,
    width: int,
    height: int,
    config: CellDetectionConfig,
) -> list[dict[str, Any]]:
    """Pair local red horizontal separators and verify their side edges."""
    horizontals = []
    for segment in segments:
        if abs(float(segment["angle"])) > 22.0:
            continue
        x1, y1, x2, y2 = [float(value) for value in segment["points"]]
        if x2 < x1:
            x1, x2, y1, y2 = x2, x1, y2, y1
        if x2 - x1 < max(28.0, width * config.minimum_width_ratio * 0.75):
            continue
        slope = (y2 - y1) / max(1.0, x2 - x1)
        horizontals.append({
            "x_min": x1,
            "x_max": x2,
            "slope": slope,
            "intercept": y1 - slope * x1,
            "length": float(segment["length"]),
        })
    merged = _merge_local_horizontal_segments(horizontals, width, height)
    if len(merged) < 2:
        return []

    support_mask = cv2.dilate(
        red_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
        iterations=1,
    )
    minimum_height = max(16.0, height * config.minimum_height_ratio)
    maximum_height = height * 0.24
    minimum_width = max(24.0, width * config.minimum_width_ratio)
    candidates: list[dict[str, Any]] = []
    merged.sort(key=lambda line: line["center_y"])
    for top_index, top in enumerate(merged[:-1]):
        for bottom in merged[top_index + 1:]:
            vertical_gap = bottom["center_y"] - top["center_y"]
            if vertical_gap < minimum_height:
                continue
            if vertical_gap > maximum_height:
                break
            overlap_left = max(top["x_min"], bottom["x_min"])
            overlap_right = min(top["x_max"], bottom["x_max"])
            overlap_width = overlap_right - overlap_left
            if overlap_width < minimum_width:
                continue
            smaller_span = min(
                top["x_max"] - top["x_min"], bottom["x_max"] - bottom["x_min"]
            )
            if overlap_width / max(1.0, smaller_span) < 0.58:
                continue
            verticals = _vertical_boundaries_for_span(
                segments,
                top,
                bottom,
                overlap_left,
                overlap_right,
                width,
                height,
            )
            polygons: list[tuple[list[list[float]], bool]] = []
            for boundary_index, left_boundary in enumerate(verticals[:-1]):
                right_boundary = verticals[boundary_index + 1]
                polygon = [
                    _intersect_local_lines(top, left_boundary),
                    _intersect_local_lines(top, right_boundary),
                    _intersect_local_lines(bottom, right_boundary),
                    _intersect_local_lines(bottom, left_boundary),
                ]
                if all(point is not None for point in polygon):
                    polygons.append(([point for point in polygon if point is not None], True))
            if not polygons:
                polygons.append(([
                    [overlap_left, _local_line_y(top, overlap_left)],
                    [overlap_right, _local_line_y(top, overlap_right)],
                    [overlap_right, _local_line_y(bottom, overlap_right)],
                    [overlap_left, _local_line_y(bottom, overlap_left)],
                ], False))

            for polygon, vertical_pair_confirmed in polygons:
                if any(
                    point[0] < 0 or point[1] < 0 or point[0] >= width or point[1] >= height
                    for point in polygon
                ):
                    continue
                xs = [point[0] for point in polygon]
                ys = [point[1] for point in polygon]
                if max(xs) - min(xs) < minimum_width:
                    continue
                supports = _polygon_edge_supports(support_mask, polygon)
                minimum_support = min(supports.values())
                average_support = sum(supports.values()) / 4.0
                side_support = min(supports["left"], supports["right"])
                span_support = min(
                    overlap_width / max(1.0, top["x_max"] - top["x_min"]),
                    overlap_width / max(1.0, bottom["x_max"] - bottom["x_min"]),
                )
                confidence = max(0.0, min(1.0,
                    0.45 * average_support
                    + 0.25 * minimum_support
                    + 0.15 * side_support
                    + 0.10 * span_support
                    + 0.05 * float(vertical_pair_confirmed)
                ))
                accepted = (
                    vertical_pair_confirmed
                    and minimum_support >= config.minimum_edge_support
                    and average_support >= config.minimum_average_support
                    and side_support >= 0.42
                )
                bbox = [
                    max(0, int(np.floor(min(xs)))),
                    max(0, int(np.floor(min(ys)))),
                    min(width, int(np.ceil(max(xs))) + 1),
                    min(height, int(np.ceil(max(ys))) + 1),
                ]
                reasons = [
                    "red_edge_local_separator_pair",
                    "vertical_line_pair" if vertical_pair_confirmed else "endpoint_fallback",
                    "four_edge_support",
                ]
                if not accepted:
                    reasons.extend([CELL_BOUNDARY_UNCERTAIN, "weak_local_boundary_support"])
                candidates.append({
                    "bbox": bbox,
                    "polygon": [[round(float(x), 3), round(float(y), 3)] for x, y in polygon],
                    "interior_bbox": bbox,
                    "confidence": round(confidence, 6),
                    "detection_reason": ";".join(reasons),
                    "boundary_status": "EXACT_CANDIDATE" if accepted else CELL_BOUNDARY_UNCERTAIN,
                    "edge_support": {key: round(value, 6) for key, value in supports.items()},
                    "component_fill": None,
                    "line_family_angles": [
                        round(float(np.degrees(np.arctan(top["slope"]))), 3),
                        round(float(np.degrees(np.arctan(bottom["slope"]))), 3),
                    ],
                })

    candidates.sort(key=lambda cell: cell["confidence"], reverse=True)
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(_bbox_iou(candidate["bbox"], kept["bbox"]) >= 0.62 for kept in unique):
            continue
        unique.append(candidate)
    return unique


def _merge_local_horizontal_segments(
    segments: list[dict[str, float]],
    width: int,
    height: int,
) -> list[dict[str, float]]:
    ordered = sorted(
        segments,
        key=lambda line: (
            _local_line_y(line, (line["x_min"] + line["x_max"]) / 2.0),
            line["x_min"],
        ),
    )
    clusters: list[list[dict[str, float]]] = []
    y_tolerance = max(5.0, min(width, height) * 0.009)
    x_gap = max(18.0, width * 0.12)
    for line in ordered:
        target_cluster = None
        for cluster in reversed(clusters[-8:]):
            representative = _combine_horizontal_cluster(cluster)
            overlap_left = max(line["x_min"], representative["x_min"])
            overlap_right = min(line["x_max"], representative["x_max"])
            gap = max(0.0, max(line["x_min"], representative["x_min"])
                      - min(line["x_max"], representative["x_max"]))
            probe_x = (
                (overlap_left + overlap_right) / 2.0
                if overlap_right >= overlap_left
                else (line["x_min"] + line["x_max"] + representative["x_min"] + representative["x_max"]) / 4.0
            )
            y_distance = abs(_local_line_y(line, probe_x) - _local_line_y(representative, probe_x))
            angle_distance = abs(
                np.degrees(np.arctan(line["slope"]))
                - np.degrees(np.arctan(representative["slope"]))
            )
            if y_distance <= y_tolerance and gap <= x_gap and angle_distance <= 8.0:
                target_cluster = cluster
                break
        if target_cluster is None:
            clusters.append([line])
        else:
            target_cluster.append(line)
    combined = [_combine_horizontal_cluster(cluster) for cluster in clusters]
    return [line for line in combined if line["x_max"] - line["x_min"] >= width * 0.045]


def _combine_horizontal_cluster(
    cluster: list[dict[str, float]],
) -> dict[str, float]:
    total = sum(line["length"] for line in cluster)
    slope = sum(line["slope"] * line["length"] for line in cluster) / total
    intercept = sum(line["intercept"] * line["length"] for line in cluster) / total
    x_min = min(line["x_min"] for line in cluster)
    x_max = max(line["x_max"] for line in cluster)
    return {
        "x_min": x_min,
        "x_max": x_max,
        "slope": float(slope),
        "intercept": float(intercept),
        "length": float(total),
        "center_y": _local_line_y(
            {"slope": slope, "intercept": intercept}, (x_min + x_max) / 2.0
        ),
    }


def _local_line_y(line: dict[str, float], x: float) -> float:
    return float(line["slope"] * x + line["intercept"])


def _vertical_boundaries_for_span(
    segments: list[dict[str, Any]],
    top: dict[str, float],
    bottom: dict[str, float],
    x_min: float,
    x_max: float,
    width: int,
    height: int,
) -> list[dict[str, float]]:
    probe_x = (x_min + x_max) / 2.0
    y_top = _local_line_y(top, probe_x)
    y_bottom = _local_line_y(bottom, probe_x)
    gap = max(1.0, y_bottom - y_top)
    raw = []
    for segment in segments:
        if abs(float(segment["angle"])) < 55.0:
            continue
        x1, y1, x2, y2 = [float(value) for value in segment["points"]]
        if y2 < y1:
            x1, x2, y1, y2 = x2, x1, y2, y1
        if y2 - y1 < gap * 0.42:
            continue
        overlap = max(0.0, min(y2, y_bottom) - max(y1, y_top))
        if overlap / gap < 0.42:
            continue
        slope = (x2 - x1) / max(1.0, y2 - y1)
        intercept = x1 - slope * y1
        x_probe = slope * ((y_top + y_bottom) / 2.0) + intercept
        if x_probe < x_min - width * 0.03 or x_probe > x_max + width * 0.03:
            continue
        raw.append({
            "slope": slope,
            "intercept": intercept,
            "x_probe": x_probe,
            "length": float(segment["length"]),
        })
    raw.sort(key=lambda line: line["x_probe"])
    tolerance = max(5.0, min(width, height) * 0.010)
    clusters: list[list[dict[str, float]]] = []
    for line in raw:
        if clusters:
            center = sum(item["x_probe"] * item["length"] for item in clusters[-1]) / sum(
                item["length"] for item in clusters[-1]
            )
            if abs(line["x_probe"] - center) <= tolerance:
                clusters[-1].append(line)
                continue
        clusters.append([line])
    result = []
    for cluster in clusters:
        total = sum(line["length"] for line in cluster)
        result.append({
            "slope": sum(line["slope"] * line["length"] for line in cluster) / total,
            "intercept": sum(line["intercept"] * line["length"] for line in cluster) / total,
            "x_probe": sum(line["x_probe"] * line["length"] for line in cluster) / total,
        })
    return sorted(result, key=lambda line: line["x_probe"])


def _intersect_local_lines(
    horizontal: dict[str, float],
    vertical: dict[str, float],
) -> list[float] | None:
    denominator = 1.0 - vertical["slope"] * horizontal["slope"]
    if abs(denominator) < 0.05:
        return None
    x = (
        vertical["slope"] * horizontal["intercept"] + vertical["intercept"]
    ) / denominator
    return [float(x), _local_line_y(horizontal, x)]


def _dominant_angle_families(
    segments: list[dict[str, float]],
) -> tuple[float, float] | None:
    if not segments:
        return None
    bins = {angle: 0.0 for angle in range(-90, 90)}
    for segment in segments:
        rounded = int(round(float(segment["angle"])))
        rounded = max(-90, min(89, rounded))
        for delta, weight in ((0, 1.0), (-1, 0.7), (1, 0.7), (-2, 0.35), (2, 0.35)):
            target = ((rounded + delta + 90) % 180) - 90
            bins[target] += float(segment["length"]) * weight
    near_horizontal = [angle for angle in bins if abs(angle) <= 30]
    if not near_horizontal:
        return None
    first_peak = max(near_horizontal, key=lambda angle: bins[angle])
    cross = [
        angle for angle in bins
        if 52 <= _angle_distance(float(first_peak), float(angle)) <= 105
    ]
    if not cross:
        return None
    second_peak = max(cross, key=lambda angle: bins[angle])
    if bins[first_peak] <= 0 or bins[second_peak] <= 0:
        return None
    return (
        _weighted_family_angle(segments, float(first_peak), 6.0),
        _weighted_family_angle(segments, float(second_peak), 12.0),
    )


def _weighted_family_angle(
    segments: list[dict[str, float]],
    peak: float,
    tolerance: float,
) -> float:
    members = [
        segment for segment in segments
        if _angle_distance(float(segment["angle"]), peak) <= tolerance
    ]
    if not members:
        return peak
    radians = [np.radians(float(segment["angle"]) * 2.0) for segment in members]
    x = sum(float(segment["length"]) * np.cos(value) for segment, value in zip(members, radians))
    y = sum(float(segment["length"]) * np.sin(value) for segment, value in zip(members, radians))
    return float(np.degrees(np.arctan2(y, x)) / 2.0)


def _angle_distance(left: float, right: float) -> float:
    return abs(((left - right + 90.0) % 180.0) - 90.0)


def _cluster_parallel_segments(
    segments: list[dict[str, Any]],
    family_angle: float,
    width: int,
    height: int,
    minimum_line: int,
) -> list[dict[str, Any]]:
    radians = np.radians(family_angle)
    direction = np.asarray([np.cos(radians), np.sin(radians)], dtype=np.float64)
    normal = np.asarray([-direction[1], direction[0]], dtype=np.float64)
    members = []
    for segment in segments:
        if _angle_distance(float(segment["angle"]), family_angle) > 12.0:
            continue
        x1, y1, x2, y2 = segment["points"]
        first = np.asarray([x1, y1], dtype=np.float64)
        second = np.asarray([x2, y2], dtype=np.float64)
        midpoint = (first + second) / 2.0
        members.append({
            "offset": float(midpoint @ normal),
            "t1": float(first @ direction),
            "t2": float(second @ direction),
            "length": float(segment["length"]),
        })
    members.sort(key=lambda item: item["offset"])
    tolerance = max(5.0, min(width, height) * 0.010)
    clusters: list[list[dict[str, float]]] = []
    for member in members:
        if not clusters:
            clusters.append([member])
            continue
        previous = clusters[-1]
        mean_offset = sum(item["offset"] * item["length"] for item in previous) / sum(
            item["length"] for item in previous
        )
        if abs(member["offset"] - mean_offset) <= tolerance:
            previous.append(member)
        else:
            clusters.append([member])
    lines = []
    for cluster in clusters:
        total = sum(item["length"] for item in cluster)
        t_min = min(min(item["t1"], item["t2"]) for item in cluster)
        t_max = max(max(item["t1"], item["t2"]) for item in cluster)
        if total < minimum_line or t_max - t_min < minimum_line * 0.75:
            continue
        offset = sum(item["offset"] * item["length"] for item in cluster) / total
        lines.append({
            "normal": normal.tolist(),
            "direction": direction.tolist(),
            "offset": float(offset),
            "t_min": float(t_min),
            "t_max": float(t_max),
            "support_length": float(total),
        })
    return sorted(lines, key=lambda line: line["offset"])


def _line_quad(
    first_top: dict[str, Any],
    first_bottom: dict[str, Any],
    second_left: dict[str, Any],
    second_right: dict[str, Any],
) -> list[list[float]] | None:
    points = [
        _line_intersection(first_top, second_left),
        _line_intersection(first_top, second_right),
        _line_intersection(first_bottom, second_right),
        _line_intersection(first_bottom, second_left),
    ]
    if any(point is None for point in points):
        return None
    return [point for point in points if point is not None]


def _line_intersection(
    left: dict[str, Any],
    right: dict[str, Any],
) -> list[float] | None:
    matrix = np.asarray([left["normal"], right["normal"]], dtype=np.float64)
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 0.05:
        return None
    values = np.asarray([left["offset"], right["offset"]], dtype=np.float64)
    point = np.linalg.solve(matrix, values)
    return [float(point[0]), float(point[1])]


def _polygon_edge_supports(
    support_mask: np.ndarray,
    polygon: list[list[float]],
) -> dict[str, float]:
    names = ("top", "right", "bottom", "left")
    values = {}
    for index, name in enumerate(names):
        values[name] = _sample_edge_support(
            support_mask, polygon[index], polygon[(index + 1) % 4]
        )
    return values


def _sample_edge_support(
    support_mask: np.ndarray,
    first: list[float],
    second: list[float],
) -> float:
    length = float(np.hypot(second[0] - first[0], second[1] - first[1]))
    sample_count = max(8, int(round(length / 3.0)))
    xs = np.linspace(first[0], second[0], sample_count)
    ys = np.linspace(first[1], second[1], sample_count)
    height, width = support_mask.shape[:2]
    supported = 0
    for x, y in zip(xs, ys):
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < width and 0 <= iy < height and support_mask[iy, ix] > 0:
            supported += 1
    return float(supported) / float(sample_count)


def _line_span_support(
    line: dict[str, Any],
    first: list[float],
    second: list[float],
) -> float:
    direction = np.asarray(line["direction"], dtype=np.float64)
    targets = [float(np.asarray(point, dtype=np.float64) @ direction) for point in (first, second)]
    target_min, target_max = min(targets), max(targets)
    target_length = max(1.0, target_max - target_min)
    overlap = max(0.0, min(target_max, line["t_max"]) - max(target_min, line["t_min"]))
    return min(1.0, overlap / target_length)


def _bbox_iou(left: list[int], right: list[int]) -> float:
    intersection = max(0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0, min(left[3], right[3]) - max(left[1], right[1])
    )
    left_area = max(1, (left[2] - left[0]) * (left[3] - left[1]))
    right_area = max(1, (right[2] - right[0]) * (right[3] - right[1]))
    return float(intersection) / float(left_area + right_area - intersection)


def _edge_supports(
    red_mask: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    band: int,
) -> dict[str, float]:
    height, width = red_mask.shape[:2]
    sx1, sx2 = max(0, x1), min(width, x2)
    sy1, sy2 = max(0, y1), min(height, y2)
    top = red_mask[max(0, y1 - band):min(height, y1 + band + 1), sx1:sx2]
    bottom = red_mask[max(0, y2 - band - 1):min(height, y2 + band), sx1:sx2]
    left = red_mask[sy1:sy2, max(0, x1 - band):min(width, x1 + band + 1)]
    right = red_mask[sy1:sy2, max(0, x2 - band - 1):min(width, x2 + band)]
    return {
        "top": _axis_support(top, axis=0),
        "bottom": _axis_support(bottom, axis=0),
        "left": _axis_support(left, axis=1),
        "right": _axis_support(right, axis=1),
    }


def _axis_support(region: np.ndarray, *, axis: int) -> float:
    if region.size == 0:
        return 0.0
    supported = np.any(region > 0, axis=axis)
    return float(np.count_nonzero(supported)) / float(supported.size) if supported.size else 0.0


def _outer_boundary_bbox(
    red_mask: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    band: int,
) -> list[int]:
    height, width = red_mask.shape[:2]
    top_region = red_mask[max(0, y1 - band):min(height, y1 + band + 1), x1:x2]
    bottom_origin = max(0, y2 - band - 1)
    bottom_region = red_mask[bottom_origin:min(height, y2 + band), x1:x2]
    left_region = red_mask[y1:y2, max(0, x1 - band):min(width, x1 + band + 1)]
    right_origin = max(0, x2 - band - 1)
    right_region = red_mask[y1:y2, right_origin:min(width, x2 + band)]

    top_points = np.argwhere(top_region > 0)
    bottom_points = np.argwhere(bottom_region > 0)
    left_points = np.argwhere(left_region > 0)
    right_points = np.argwhere(right_region > 0)
    outer_top = max(0, y1 - band) + int(top_points[:, 0].min()) if top_points.size else y1
    outer_bottom = bottom_origin + int(bottom_points[:, 0].max()) + 1 if bottom_points.size else y2
    outer_left = max(0, x1 - band) + int(left_points[:, 1].min()) if left_points.size else x1
    outer_right = right_origin + int(right_points[:, 1].max()) + 1 if right_points.size else x2
    return [outer_left, outer_top, outer_right, outer_bottom]


def _overlap_pairs(
    cells: list[dict[str, Any]],
    maximum_overlap_ratio: float,
) -> list[tuple[str, str]]:
    overlaps: list[tuple[str, str]] = []
    for left_index, left in enumerate(cells):
        lx1, ly1, lx2, ly2 = left["interior_bbox"]
        left_area = max(1.0, float((lx2 - lx1) * (ly2 - ly1)))
        for right in cells[left_index + 1:]:
            rx1, ry1, rx2, ry2 = right["interior_bbox"]
            intersection = max(0, min(lx2, rx2) - max(lx1, rx1)) * max(
                0, min(ly2, ry2) - max(ly1, ry1)
            )
            right_area = max(1.0, float((rx2 - rx1) * (ry2 - ry1)))
            if float(intersection) / min(left_area, right_area) > maximum_overlap_ratio:
                overlaps.append((left["cell_id"], right["cell_id"]))
    return overlaps


def _competing_boundary_pairs(
    accepted_cells: list[dict[str, Any]],
    uncertain_cells: list[dict[str, Any]],
    maximum_overlap_ratio: float,
) -> list[tuple[str, str]]:
    """Find alternative boundaries that make an accepted cell non-unique.

    The comparison uses overlap relative to the smaller candidate.  It is
    intentionally stricter than IoU: a larger alternative enclosing a smaller
    cell is exactly the ambiguity that must fail closed.
    """
    pairs: list[tuple[str, str]] = []
    for accepted in accepted_cells:
        ax1, ay1, ax2, ay2 = accepted["interior_bbox"]
        accepted_area = max(1.0, float((ax2 - ax1) * (ay2 - ay1)))
        for uncertain in uncertain_cells:
            ux1, uy1, ux2, uy2 = uncertain["interior_bbox"]
            intersection = max(0, min(ax2, ux2) - max(ax1, ux1)) * max(
                0, min(ay2, uy2) - max(ay1, uy1)
            )
            uncertain_area = max(1.0, float((ux2 - ux1) * (uy2 - uy1)))
            if float(intersection) / min(accepted_area, uncertain_area) >= maximum_overlap_ratio:
                pairs.append((accepted["cell_id"], uncertain["cell_id"]))
    return pairs


def _normalize_ocr_regions(results: list[Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for result in results:
        raw = _result_payload(result)
        payload = raw.get("res") if isinstance(raw.get("res"), dict) else raw
        texts = payload.get("rec_texts") if isinstance(payload.get("rec_texts"), list) else []
        scores = payload.get("rec_scores") if isinstance(payload.get("rec_scores"), list) else []
        polygons = payload.get("rec_polys") if isinstance(payload.get("rec_polys"), list) else []
        if not polygons and isinstance(payload.get("dt_polys"), list):
            polygons = payload["dt_polys"]
        for index in range(min(len(texts), len(scores), len(polygons))):
            text = str(texts[index]).strip()
            try:
                score = float(scores[index])
                polygon = [[float(point[0]), float(point[1])] for point in polygons[index]]
            except (TypeError, ValueError, IndexError):
                continue
            bbox = _polygon_bbox(polygon)
            if not text or bbox is None or not 0.0 <= score <= 1.0:
                continue
            regions.append({
                "text": text,
                "confidence": score,
                "polygon": polygon,
                "bbox": bbox,
            })
    regions.sort(key=lambda region: (
        (region["bbox"][1] + region["bbox"][3]) / 2.0,
        (region["bbox"][0] + region["bbox"][2]) / 2.0,
    ))
    return regions


def _result_payload(result: Any) -> dict[str, Any]:
    value = getattr(result, "json", None)
    if callable(value):
        value = value()
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        return _primitive(value)
    if hasattr(result, "to_dict"):
        value = result.to_dict()
        if isinstance(value, dict):
            return _primitive(value)
    return _primitive(dict(result))


def _primitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if hasattr(value, "tolist"):
        return _primitive(value.tolist())
    if hasattr(value, "item"):
        return value.item()
    return value


def _polygon_bbox(polygon: list[list[float]]) -> list[float] | None:
    if len(polygon) < 3:
        return None
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    if bbox[0] < 0 or bbox[1] < 0 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _uncertain_detection(
    width: int,
    height: int,
    reason: str,
    *,
    red_ratio: float = 0.0,
) -> dict[str, Any]:
    return {
        "status": CELL_BOUNDARY_UNCERTAIN,
        "image_size": [width, height],
        "red_pixel_ratio": round(red_ratio, 8),
        "cells": [],
        "accepted_cell_ids": [],
        "uncertain_cell_ids": [],
        "overlap_pairs": [],
        "competing_boundary_pairs": [],
        "failure_reasons": [reason],
        **evidence_authority(),
    }


def _build_ocr(args: argparse.Namespace) -> Any:
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(args.model_cache)
    from paddleocr import PaddleOCR

    return PaddleOCR(
        ocr_version="PP-OCRv6",
        text_detection_model_name="PP-OCRv6_medium_det",
        text_detection_model_dir=str(args.det_model_dir),
        text_recognition_model_name="PP-OCRv6_medium_rec",
        text_recognition_model_dir=str(args.rec_model_dir),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device=args.device,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overlay")
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--model-cache", required=True)
    parser.add_argument("--det-model-dir", required=True)
    parser.add_argument("--rec-model-dir", required=True)
    args = parser.parse_args()
    result = run_cell_first(args.image, ocr_engine=_build_ocr(args))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.overlay:
        write_debug_overlay(args.image, result, args.overlay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
