"""Test vision contract validation rules."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

import pytest

from betguard.vision.contracts import (
    SCHEMA_VERSION,
    Alternative,
    BoundingBox,
    Confidence,
    ConfidenceLevel,
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
    Token,
)
from betguard.vision.errors import ContractValidationError, ErrorCode, ProviderError


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_result(**overrides) -> RecognitionResult:
    """Build a minimal valid RecognitionResult, overridden by kwargs."""
    defaults = {
        "schema_version": SCHEMA_VERSION,
        "recognition_id": f"rec-{uuid.uuid4().hex[:12]}",
        "request_id": f"req-{uuid.uuid4().hex[:8]}",
        "status": RecognitionStatus.COMPLETED,
        "provider": ProviderMetadata(id="test"),
        "source_image": SourceImage(image_id="img-001"),
    }
    defaults.update(overrides)
    return RecognitionResult(**defaults)


def _round_trip(result: RecognitionResult) -> RecognitionResult:
    d = result.to_dict()
    s = json.dumps(d, default=str)
    loaded = json.loads(s)
    return RecognitionResult.from_dict(loaded)


# ── JSON round-trip ──────────────────────────────────────────────────────────


class TestJsonRoundTrip:
    """RecognitionResult must survive json.dumps → json.loads → from_dict."""

    def test_minimal_result(self):
        r = _make_result()
        r2 = _round_trip(r)
        assert r2.recognition_id == r.recognition_id
        assert r2.request_id == r.request_id
        assert r2.status == r.status

    def test_full_result_with_lines(self):
        r = _make_result(
            raw_text="05 09\n15 19",
            lines=[
                Line(
                    line_id="l1",
                    order=1,
                    text="05 09",
                    confidence=Confidence(value=0.99, level=ConfidenceLevel.HIGH),
                    tokens=[
                        Token(
                            token_id="t1",
                            text="05",
                            start=0,
                            end=2,
                            confidence=Confidence(value=0.99, level=ConfidenceLevel.HIGH),
                            bounding_box=BoundingBox("pixel", [[0, 0], [50, 0], [50, 30], [0, 30]]),
                        ),
                    ],
                ),
            ],
        )
        r2 = _round_trip(r)
        assert r2.raw_text == r.raw_text
        assert len(r2.lines) == 1
        assert r2.lines[0].text == "05 09"
        assert r2.lines[0].tokens[0].text == "05"
        assert r2.lines[0].tokens[0].bounding_box is not None
        assert len(r2.lines[0].tokens[0].bounding_box.polygon) == 4

    def test_confidence_null_survives_round_trip(self):
        r = _make_result(
            lines=[
                Line(
                    line_id="l1",
                    order=1,
                    text="abc",
                    confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN),
                    tokens=[
                        Token(
                            token_id="t1",
                            text="abc",
                            start=0,
                            end=3,
                            confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN),
                        ),
                    ],
                ),
            ],
        )
        r2 = _round_trip(r)
        assert r2.lines[0].confidence.value is None
        assert r2.lines[0].tokens[0].confidence.value is None

    def test_provider_error_round_trip(self):
        r = _make_result(
            status=RecognitionStatus.FAILED,
            provider_error=ProviderError(
                code=ErrorCode.TIMEOUT,
                message="Timed out",
                retryable=True,
                http_status=None,
            ),
        )
        r2 = _round_trip(r)
        assert r2.status in (RecognitionStatus.FAILED, "failed")
        assert r2.provider_error is not None
        assert r2.provider_error.code == ErrorCode.TIMEOUT
        assert r2.provider_error.retryable is True
        assert r2.provider_error.http_status is None

    def test_alternatives_round_trip(self):
        r = _make_result(
            lines=[
                Line(
                    line_id="l1",
                    order=1,
                    text="X",
                    confidence=Confidence(value=0.5, level=ConfidenceLevel.LOW),
                    tokens=[
                        Token(
                            token_id="t1",
                            text="X",
                            start=0,
                            end=1,
                            confidence=Confidence(value=0.5, level=ConfidenceLevel.LOW),
                            alternatives=[
                                Alternative(text="×", score=0.4, source="fake"),
                                Alternative(text="-", score=0.1, source="fake"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        r2 = _round_trip(r)
        assert len(r2.lines[0].tokens[0].alternatives) == 2
        assert r2.lines[0].tokens[0].alternatives[0].text == "×"

    def test_source_image_round_trip(self):
        r = _make_result(
            source_image=SourceImage(
                image_id="img-002",
                sha256="abcdef",
                mime_type="image/jpeg",
                original_filename="bet.jpg",
                width=1024,
                height=768,
                byte_size=50000,
            ),
        )
        r2 = _round_trip(r)
        assert r2.source_image.image_id == "img-002"
        assert r2.source_image.width == 1024
        assert r2.source_image.sha256 == "abcdef"


# ── Confidence validation ────────────────────────────────────────────────────


class TestConfidenceValidation:
    def test_out_of_range_high(self):
        with pytest.raises(ValueError, match="0\\.0–1\\.0"):
            Confidence(value=1.5)

    def test_out_of_range_negative(self):
        with pytest.raises(ValueError, match="0\\.0–1\\.0"):
            Confidence(value=-0.1)

    def test_null_is_ok(self):
        c = Confidence(value=None)
        assert c.value is None

    def test_invalid_level(self):
        with pytest.raises(ValueError, match="Invalid confidence level"):
            Confidence(value=0.5, level="super_high")

    def test_all_valid_levels(self):
        for level in ("high", "medium", "low", "unknown"):
            c = Confidence(value=0.5, level=level)
            assert c.level.value == level


# ── Token range validation ───────────────────────────────────────────────────


class TestTokenRangeValidation:
    def test_valid_range(self):
        t = Token(token_id="t1", text="abc", start=0, end=3)
        assert t.start == 0
        assert t.end == 3

    def test_negative_start(self):
        with pytest.raises(ValueError, match="start must be >= 0"):
            Token(token_id="t1", text="abc", start=-1, end=3)

    def test_negative_end(self):
        with pytest.raises(ValueError, match="end must be >= 0"):
            Token(token_id="t1", text="abc", start=0, end=-1)

    def test_end_before_start(self):
        with pytest.raises(ValueError, match="end .* must be >= start"):
            Token(token_id="t1", text="abc", start=5, end=3)

    def test_outside_line_text(self):
        t = Token(token_id="t1", text="abc", start=0, end=4)
        with pytest.raises(ValueError, match="exceeds line text length"):
            t.validate_range("abc")  # length 3, range [0:4]

    def test_edge_at_line_end(self):
        t = Token(token_id="t1", text="abc", start=0, end=3)
        t.validate_range("abc")  # should not raise


# ── Polygon validation ───────────────────────────────────────────────────────


class TestPolygonValidation:
    def test_valid_polygon(self):
        b = BoundingBox("pixel", [[0, 0], [10, 0], [10, 10], [0, 10]])
        assert b.coordinate_space == "pixel"

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 3 points"):
            BoundingBox("pixel", [[0, 0], [10, 10]])

    def test_nan_coordinate(self):
        with pytest.raises(ValueError, match="NaN or infinite"):
            BoundingBox("pixel", [[0, 0], [10, float("nan")], [10, 10]])

    def test_inf_coordinate(self):
        with pytest.raises(ValueError, match="NaN or infinite"):
            BoundingBox("pixel", [[0, 0], [10, float("inf")], [10, 10]])

    def test_wrong_coordinate_count(self):
        with pytest.raises(ValueError, match="exactly 2 coordinates"):
            BoundingBox("pixel", [[0, 0, 0], [10, 0], [10, 10]])


# ── Line order validation ────────────────────────────────────────────────────


class TestLineOrderValidation:
    def test_positive_order(self):
        line = Line(line_id="l1", order=5, text="abc")
        assert line.order == 5

    def test_zero_order(self):
        with pytest.raises(ValueError, match="order must be positive"):
            Line(line_id="l1", order=0, text="abc")

    def test_negative_order(self):
        with pytest.raises(ValueError, match="order must be positive"):
            Line(line_id="l1", order=-1, text="abc")


# ── RecognitionResult cross-field validation ─────────────────────────────────


class TestResultValidation:
    def test_wrong_schema_version(self):
        with pytest.raises(ContractValidationError, match="schema_version"):
            _make_result(schema_version="wrong.v2")

    def test_blank_recognition_id(self):
        with pytest.raises(ContractValidationError, match="recognition_id"):
            _make_result(recognition_id="")

    def test_blank_request_id(self):
        with pytest.raises(ContractValidationError, match="request_id"):
            _make_result(request_id="")

    def test_negative_latency(self):
        with pytest.raises(ContractValidationError, match="latency_ms"):
            _make_result(latency_ms=-1.0)

    def test_completed_with_provider_error(self):
        with pytest.raises(ContractValidationError, match="must not have a provider_error"):
            _make_result(
                status=RecognitionStatus.COMPLETED,
                provider_error=ProviderError(code=ErrorCode.INTERNAL_ERROR, message="boom"),
            )

    def test_failed_without_provider_error(self):
        with pytest.raises(ContractValidationError, match="must have a provider_error"):
            _make_result(status=RecognitionStatus.FAILED)

    def test_failed_with_provider_error_is_valid(self):
        r = _make_result(
            status=RecognitionStatus.FAILED,
            provider_error=ProviderError(code=ErrorCode.TIMEOUT, message="timeout"),
        )
        assert r.status in (RecognitionStatus.FAILED, "failed")


# ── Request round-trip ───────────────────────────────────────────────────────


class TestRequest:
    def test_request_round_trip(self):
        req = RecognitionRequest(
            request_id="r1",
            image_id="img1",
            requested_language_hints=["zh-TW"],
            requested_features=["text", "bbox"],
            metadata={"source": "test"},
        )
        d = req.to_dict()
        req2 = RecognitionRequest.from_dict(d)
        assert req2.request_id == "r1"
        assert req2.requested_language_hints == ["zh-TW"]
        assert req2.metadata == {"source": "test"}
