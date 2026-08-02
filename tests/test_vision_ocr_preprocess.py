"""Test OCR preprocessing profiles and coordinate transforms.

Tests benchmark_protocol transform functions (no cv2/numpy needed).
Preprocessing functions are tested via fake subprocess in benchmark tests.
"""

from __future__ import annotations

import math

import pytest

from betguard.vision.benchmark_protocol import (
    BENCHMARK_PROTOCOL_VERSION,
    MAX_INFERENCES,
    BenchmarkRequest,
    ProfileRun,
    compute_heuristic_score,
    select_best_rotation,
    transform_polygon,
)


class TestBenchmarkProtocol:
    def test_protocol_version(self):
        assert BENCHMARK_PROTOCOL_VERSION == "betguard.vision.benchmark.v1"

    def test_request_defaults(self):
        req = BenchmarkRequest(request_id="r1", image_path="/tmp/x.png")
        assert req.rotations == [0, 90, 180, 270]
        assert req.protocol_version == BENCHMARK_PROTOCOL_VERSION


class TestHeuristicScore:
    def test_zero_items(self):
        s = compute_heuristic_score(0, 0, 0.0, 0)
        assert s == 0.0

    def test_many_items_high_confidence(self):
        s = compute_heuristic_score(detected_count=10, non_empty_count=10, mean_confidence=0.9, digit_count=50)
        assert s > 200

    def test_single_low_conf_cjk_penalized(self):
        s1 = compute_heuristic_score(1, 1, 0.2, 0, has_single_low_conf_cjk=False)
        s2 = compute_heuristic_score(1, 1, 0.2, 0, has_single_low_conf_cjk=True)
        assert s2 < s1

    def test_full_image_box_penalized(self):
        s1 = compute_heuristic_score(1, 1, 0.5, 0, full_image_box=False)
        s2 = compute_heuristic_score(1, 1, 0.5, 0, full_image_box=True)
        assert s2 < s1

    def test_digits_bonus(self):
        s1 = compute_heuristic_score(5, 5, 0.5, 0)
        s2 = compute_heuristic_score(5, 5, 0.5, 10)
        assert s2 > s1

    def test_never_negative(self):
        s = compute_heuristic_score(0, 0, 0.0, 0, has_single_low_conf_cjk=True, full_image_box=True)
        assert s == 0.0


class TestSelectBestRotation:
    def _profile(self, rotation: int, score: float, status: str = "completed") -> ProfileRun:
        return ProfileRun(
            rotation=rotation, preprocess_profile="orig", detection_profile="bal",
            heuristic_score=score, status=status,
        )

    def test_highest_score_wins(self):
        profiles = [self._profile(0, 10), self._profile(90, 50), self._profile(180, 30)]
        assert select_best_rotation(profiles) == 90

    def test_tie_break_order(self):
        profiles = [self._profile(180, 50), self._profile(0, 50), self._profile(90, 50)]
        assert select_best_rotation(profiles) == 0

    def test_failed_ignored(self):
        profiles = [self._profile(0, 100, "failed"), self._profile(90, 10)]
        assert select_best_rotation(profiles) == 90

    def test_all_failed_returns_zero(self):
        profiles = [self._profile(90, 50, "failed")]
        assert select_best_rotation(profiles) == 0


class TestTransformPolygon:
    def test_rotation_0_no_change(self):
        poly = [[10, 20], [30, 20], [30, 40], [10, 40]]
        result = transform_polygon(poly, 0, 100, 100)
        assert result == poly

    def test_rotation_90(self):
        poly = [[10, 0], [30, 0], [30, 20], [10, 20]]
        result = transform_polygon(poly, 90, 100, 50)
        # (x,y) at rot 90 → (h-y, x) = (50-y, x)
        assert result[0] == [50, 10]

    def test_rotation_180(self):
        poly = [[10, 10], [30, 30]]
        result = transform_polygon(poly, 180, 100, 50)
        assert result[0] == [90, 40]

    def test_rotation_270(self):
        poly = [[10, 0], [30, 0]]
        result = transform_polygon(poly, 270, 100, 50)
        assert result[0] == [0, 90]

    def test_upscale_2x(self):
        poly = [[20, 20], [40, 30]]
        result = transform_polygon(poly, 0, 100, 100, scale=2.0)
        assert result[0] == [10, 10]

    def test_clipping(self):
        poly = [[-10, -10], [200, 200]]
        result = transform_polygon(poly, 0, 100, 100)
        assert result[0] == [0, 0]
        assert result[1] == [100, 100]

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            transform_polygon([[float("nan"), 0], [1, 1]], 0, 100, 100)

    def test_inf_rejected(self):
        with pytest.raises(ValueError):
            transform_polygon([[float("inf"), 0], [1, 1]], 0, 100, 100)


class TestMaxInferences:
    def test_constant_exists(self):
        assert MAX_INFERENCES == 14
