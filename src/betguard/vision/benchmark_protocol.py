"""Benchmark protocol types — independent of production worker protocol.

Protocol version: betguard.vision.benchmark.v1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BENCHMARK_PROTOCOL_VERSION = "betguard.vision.benchmark.v1"
MAX_INFERENCES = 14


@dataclass
class BenchmarkRequest:
    protocol_version: str = BENCHMARK_PROTOCOL_VERSION
    request_id: str = ""
    image_path: str = ""
    rotations: list[int] = field(default_factory=lambda: [0, 90, 180, 270])
    preprocess_profiles: list[str] = field(default_factory=list)
    detection_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    ground_truth: str = ""


@dataclass
class PreprocessResult:
    profile_id: str
    width: int
    height: int
    elapsed_ms: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class OcrItem:
    text: str
    score: float
    processed_polygon: list[list[float]] = field(default_factory=list)
    original_polygon: list[list[float]] = field(default_factory=list)
    box: list[float] = field(default_factory=list)


@dataclass
class ProfileRun:
    rotation: int
    preprocess_profile: str
    detection_profile: str
    status: str = "completed"  # completed | failed
    elapsed_ms: float = 0.0
    items: list[OcrItem] = field(default_factory=list)
    raw_text: str = ""
    scores: list[float] = field(default_factory=list)
    detected_count: int = 0
    non_empty_count: int = 0
    digit_count: int = 0
    mean_confidence: float = 0.0
    max_confidence: float = 0.0
    heuristic_score: float = 0.0
    actual_text_det_params: dict[str, Any] = field(default_factory=dict)
    preprocess_result: PreprocessResult | None = None
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    protocol_version: str = BENCHMARK_PROTOCOL_VERSION
    request_id: str = ""
    image_path: str = ""
    engine: dict[str, Any] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    inference_count: int = 0
    profiles: list[ProfileRun] = field(default_factory=list)
    provisional_best_rotation: int = 0
    provisional_best_preprocess: str = ""
    provisional_best_detection: str = ""
    benchmark_score: float = 0.0
    warnings: list[str] = field(default_factory=list)


def compute_heuristic_score(
    detected_count: int,
    non_empty_count: int,
    mean_confidence: float,
    digit_count: int,
    has_single_low_conf_cjk: bool = False,
    full_image_box: bool = False,
) -> float:
    """Compute heuristic benchmark score (NOT accuracy)."""
    score = 0.0
    score += detected_count * 10.0
    score += non_empty_count * 15.0
    score += mean_confidence * 50.0
    score += digit_count * 2.0
    if has_single_low_conf_cjk:
        score -= 30.0
    if full_image_box:
        score -= 40.0
    return max(0.0, score)


_ROTATION_PRIORITY = {0: 0, 90: 1, 180: 2, 270: 3}


def select_best_rotation(profiles: list[ProfileRun]) -> int:
    """Select best rotation by heuristic score, tie-break by rotation order."""
    best = 0
    best_score = -1.0
    for p in profiles:
        if p.status != "completed":
            continue
        if p.heuristic_score > best_score:
            best_score = p.heuristic_score
            best = p.rotation
        elif p.heuristic_score == best_score:
            if _ROTATION_PRIORITY.get(p.rotation, 99) < _ROTATION_PRIORITY.get(best, 99):
                best = p.rotation
    return best


def transform_polygon(
    polygon: list[list[float]],
    rotation: int,
    src_w: int,
    src_h: int,
    scale: float = 1.0,
) -> list[list[float]]:
    """Transform polygon back to original image coordinates.

    Args:
        polygon: Points in processed image coordinates
        rotation: 0, 90, 180, or 270 degrees
        src_w, src_h: Original image dimensions
        scale: Upscale factor (1.0 = no upscale, 2.0 = 2x)

    Returns:
        Points in original image coordinates, clipped to bounds.
    """
    import math
    # Effective bounds after rotation
    if rotation in (90, 270):
        eff_w, eff_h = src_h, src_w
    else:
        eff_w, eff_h = src_w, src_h

    result = []
    for x, y in polygon:
        if math.isnan(x) or math.isinf(x) or math.isnan(y) or math.isinf(y):
            raise ValueError(f"NaN/Inf coordinate in polygon")
        # Reverse scale
        x, y = x / scale, y / scale
        # Reverse rotation
        if rotation == 90:
            x, y = src_h - y, x
        elif rotation == 180:
            x, y = src_w - x, src_h - y
        elif rotation == 270:
            x, y = y, src_w - x
        # Clip to effective bounds
        x = max(0.0, min(float(eff_w), float(x)))
        y = max(0.0, min(float(eff_h), float(y)))
        result.append([x, y])
    return result
