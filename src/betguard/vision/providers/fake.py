"""Fake OCR provider for testing — returns pre-configured RecognitionResult fixtures.

Does NOT: read real images, call network, write queue, import parser/validator/webfill.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from betguard.vision.contracts import (
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
from betguard.vision.errors import ErrorCode, ProviderError


PROVIDER_ID = "betguard.fake.v1"


def _make_confidence(
    value: float | None = None,
    level: str = "high",
    source: str = "fake",
) -> Confidence:
    return Confidence(value=value, source=source, calibrated=False, level=level)


def _make_bbox(points: list[list[float]], space: str = "pixel") -> BoundingBox:
    return BoundingBox(coordinate_space=space, polygon=points)


# ── Pre-built fixtures ───────────────────────────────────────────────────────


def bet_slip_fixture(
    image_id: str = "fixture-bet-slip-001",
    request_id: str = "",
) -> RecognitionResult:
    """Single fixture: 539 bet slip with low/medium confidence on specific tokens.

    Layout (simulated):

        ┌─────────────┬─────────────┐
        │ 05   09     │  L column   │
        │ 15   19     │             │
        │ 25   29     │             │
        │ 35   39     │             │
        │ 20   18     │             │
        ├─────────────┴─────────────┤
        │  右側: 2×5               │
        ├───────────────────────────┤
        │  18                      │
        │  25 各2車                │
        │  39                      │
        └───────────────────────────┘
    """
    if not request_id:
        request_id = f"req-{uuid.uuid4().hex[:8]}"

    return RecognitionResult(
        schema_version="betguard.vision.recognition.v1",
        recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            mode="fake",
            model_name="betguard-fake-v1",
            model_version="1.0.0",
            adapter_version="1.0.0",
        ),
        source_image=SourceImage(
            image_id=image_id,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            mime_type="image/png",
            original_filename="bet_slip_sample.png",
            width=800,
            height=600,
            byte_size=12345,
            retention="transient",
            cloud_uploaded=False,
        ),
        raw_text="05 09\n15 19\n25 29\n35 39\n20 18\n2×5\n18\n25 各2車\n39",
        lines=[
            # Line 0: 05    09  (high confidence)
            Line(
                line_id="line-001",
                order=1,
                text="05    09",
                confidence=_make_confidence(0.98),
                bounding_box=_make_bbox([[10, 10], [200, 10], [200, 30], [10, 30]]),
                tokens=[
                    Token(
                        token_id="tok-001",
                        text="05",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.99, "high"),
                        bounding_box=_make_bbox([[10, 10], [60, 10], [60, 30], [10, 30]]),
                    ),
                    Token(
                        token_id="tok-002",
                        text="09",
                        start=6,
                        end=8,
                        confidence=_make_confidence(0.97, "high"),
                        bounding_box=_make_bbox([[150, 10], [200, 10], [200, 30], [150, 30]]),
                    ),
                ],
            ),
            # Line 1: 15    19  (token 19 has low confidence + alternatives)
            Line(
                line_id="line-002",
                order=2,
                text="15    19",
                confidence=_make_confidence(0.70, "medium"),
                bounding_box=_make_bbox([[10, 35], [200, 35], [200, 55], [10, 55]]),
                tokens=[
                    Token(
                        token_id="tok-003",
                        text="15",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.96, "high"),
                        bounding_box=_make_bbox([[10, 35], [60, 35], [60, 55], [10, 55]]),
                    ),
                    Token(
                        token_id="tok-004",
                        text="19",
                        start=6,
                        end=8,
                        confidence=_make_confidence(0.45, "low"),
                        bounding_box=_make_bbox([[150, 35], [200, 35], [200, 55], [150, 55]]),
                        alternatives=[
                            Alternative(text="18", score=0.40, source="fake"),
                            Alternative(text="17", score=0.10, source="fake"),
                        ],
                        warnings=["Low confidence token: 19 — may be 18 or 17"],
                    ),
                ],
                warnings=["Line 2 has low confidence token(s)"],
            ),
            # Line 2: 25    29  (token 29 has low confidence + alternatives)
            Line(
                line_id="line-003",
                order=3,
                text="25    29",
                confidence=_make_confidence(0.65, "medium"),
                bounding_box=_make_bbox([[10, 60], [200, 60], [200, 80], [10, 80]]),
                tokens=[
                    Token(
                        token_id="tok-005",
                        text="25",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.95, "high"),
                        bounding_box=_make_bbox([[10, 60], [60, 60], [60, 80], [10, 80]]),
                    ),
                    Token(
                        token_id="tok-006",
                        text="29",
                        start=6,
                        end=8,
                        confidence=_make_confidence(0.40, "low"),
                        bounding_box=_make_bbox([[150, 60], [200, 60], [200, 80], [150, 80]]),
                        alternatives=[
                            Alternative(text="27", score=0.35, source="fake"),
                            Alternative(text="39", score=0.20, source="fake"),
                        ],
                        warnings=["Low confidence token: 29 — may be 27 or 39"],
                    ),
                ],
                warnings=["Line 3 has low confidence token(s)"],
            ),
            # Line 3: 35    39  (high confidence)
            Line(
                line_id="line-004",
                order=4,
                text="35    39",
                confidence=_make_confidence(0.99),
                bounding_box=_make_bbox([[10, 85], [200, 85], [200, 105], [10, 105]]),
                tokens=[
                    Token(
                        token_id="tok-007",
                        text="35",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.99, "high"),
                        bounding_box=_make_bbox([[10, 85], [60, 85], [60, 105], [10, 105]]),
                    ),
                    Token(
                        token_id="tok-008",
                        text="39",
                        start=6,
                        end=8,
                        confidence=_make_confidence(0.98, "high"),
                        bounding_box=_make_bbox([[150, 85], [200, 85], [200, 105], [150, 105]]),
                    ),
                ],
            ),
            # Line 4: 20    18  (high confidence)
            Line(
                line_id="line-005",
                order=5,
                text="20    18",
                confidence=_make_confidence(0.97),
                bounding_box=_make_bbox([[10, 110], [200, 110], [200, 130], [10, 130]]),
                tokens=[
                    Token(
                        token_id="tok-009",
                        text="20",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.98, "high"),
                        bounding_box=_make_bbox([[10, 110], [60, 110], [60, 130], [10, 130]]),
                    ),
                    Token(
                        token_id="tok-010",
                        text="18",
                        start=6,
                        end=8,
                        confidence=_make_confidence(0.96, "high"),
                        bounding_box=_make_bbox([[150, 110], [200, 110], [200, 130], [150, 110]]),
                    ),
                ],
            ),
            # Line 5: 2×5  (× has alternatives X, -)
            Line(
                line_id="line-006",
                order=6,
                text="2×5",
                confidence=_make_confidence(0.55, "low"),
                bounding_box=_make_bbox([[10, 140], [60, 140], [60, 160], [10, 160]]),
                tokens=[
                    Token(
                        token_id="tok-011",
                        text="×",
                        start=1,
                        end=2,
                        confidence=_make_confidence(0.50, "low"),
                        bounding_box=_make_bbox([[20, 140], [40, 140], [40, 160], [20, 160]]),
                        alternatives=[
                            Alternative(text="X", score=0.35, source="fake"),
                            Alternative(text="-", score=0.10, source="fake"),
                        ],
                        critical_type="multiply_operator",
                        warnings=["Ambiguous multiply sign — may be X or -"],
                    ),
                ],
                warnings=["Line 6 has ambiguous multiply operator"],
            ),
            # Line 6: 18  (high confidence)
            Line(
                line_id="line-007",
                order=7,
                text="18",
                confidence=_make_confidence(0.99),
                bounding_box=_make_bbox([[10, 170], [60, 170], [60, 190], [10, 190]]),
                tokens=[
                    Token(
                        token_id="tok-012",
                        text="18",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.99, "high"),
                        bounding_box=_make_bbox([[10, 170], [60, 170], [60, 190], [10, 190]]),
                    ),
                ],
            ),
            # Line 7: 25 各2車  (各2車 has alternatives)
            Line(
                line_id="line-008",
                order=8,
                text="25 各2車",
                confidence=_make_confidence(0.60, "low"),
                bounding_box=_make_bbox([[10, 200], [200, 200], [200, 220], [10, 220]]),
                tokens=[
                    Token(
                        token_id="tok-013",
                        text="25",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.95, "high"),
                        bounding_box=_make_bbox([[10, 200], [60, 200], [60, 220], [10, 220]]),
                    ),
                    Token(
                        token_id="tok-014",
                        text="各2車",
                        start=3,
                        end=6,
                        confidence=_make_confidence(0.30, "low"),
                        bounding_box=_make_bbox([[70, 200], [200, 200], [200, 220], [70, 220]]),
                        alternatives=[
                            Alternative(text="各2碰", score=0.30, source="fake"),
                            Alternative(text="各2連", score=0.25, source="fake"),
                        ],
                        warnings=["Ambiguous suffix: 各2車 — may be 各2碰 or 各2連"],
                    ),
                ],
                warnings=["Line 8 has ambiguous suffix"],
            ),
            # Line 8: 39  (high confidence)
            Line(
                line_id="line-009",
                order=9,
                text="39",
                confidence=_make_confidence(0.98),
                bounding_box=_make_bbox([[10, 230], [60, 230], [60, 250], [10, 250]]),
                tokens=[
                    Token(
                        token_id="tok-015",
                        text="39",
                        start=0,
                        end=2,
                        confidence=_make_confidence(0.98, "high"),
                        bounding_box=_make_bbox([[10, 230], [60, 230], [60, 250], [10, 250]]),
                    ),
                ],
            ),
        ],
        warnings=[
            "Some tokens have low confidence — manual review recommended",
        ],
        latency_ms=0.0,  # fake is instant
    )


# ── Pre-built edge-case fixtures ─────────────────────────────────────────────


def no_confidence_fixture(
    image_id: str = "fixture-no-conf-001",
    request_id: str = "",
) -> RecognitionResult:
    """Fixture: all confidence values are null."""
    if not request_id:
        request_id = f"req-{uuid.uuid4().hex[:8]}"

    return RecognitionResult(
        recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(id=PROVIDER_ID, mode="fake"),
        source_image=SourceImage(image_id=image_id),
        raw_text="01 02 03",
        lines=[
            Line(
                line_id="line-nc-001",
                order=1,
                text="01 02 03",
                confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN, source="fake"),
                tokens=[
                    Token(
                        token_id="tok-nc-001",
                        text="01",
                        start=0,
                        end=2,
                        confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN),
                    ),
                    Token(
                        token_id="tok-nc-002",
                        text="02",
                        start=3,
                        end=5,
                        confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN),
                    ),
                    Token(
                        token_id="tok-nc-003",
                        text="03",
                        start=6,
                        end=8,
                        confidence=Confidence(value=None, level=ConfidenceLevel.UNKNOWN),
                    ),
                ],
            ),
        ],
    )


def multi_line_fixture(
    image_id: str = "fixture-multi-001",
    request_id: str = "",
) -> RecognitionResult:
    """Fixture: multiple high-confidence lines with bounding boxes."""
    if not request_id:
        request_id = f"req-{uuid.uuid4().hex[:8]}"

    return RecognitionResult(
        recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(id=PROVIDER_ID, mode="fake"),
        source_image=SourceImage(image_id=image_id),
        raw_text="01 02 03 04\n05 06 07 08\n09 10 11 12",
        lines=[
            Line(
                line_id="ml-001",
                order=1,
                text="01 02 03 04",
                confidence=_make_confidence(0.99),
                bounding_box=_make_bbox([[0, 0], [300, 0], [300, 40], [0, 40]]),
            ),
            Line(
                line_id="ml-002",
                order=2,
                text="05 06 07 08",
                confidence=_make_confidence(0.98),
                bounding_box=_make_bbox([[0, 45], [300, 45], [300, 85], [0, 85]]),
            ),
            Line(
                line_id="ml-003",
                order=3,
                text="09 10 11 12",
                confidence=_make_confidence(0.97),
                bounding_box=_make_bbox([[0, 90], [300, 90], [300, 130], [0, 130]]),
            ),
        ],
    )


# ── Provider ─────────────────────────────────────────────────────────────────


class FakeProvider:
    """Fake OCR provider that returns pre-configured fixtures.

    Does NOT: read images, call network, write queue, import parser/validator/webfill.
    """

    provider_id: str = PROVIDER_ID

    # Fixture modes
    MODE_SUCCESS = "success"
    MODE_TIMEOUT = "timeout"
    MODE_PROVIDER_ERROR = "provider_error"
    MODE_NO_CONFIDENCE = "no_confidence"
    MODE_MULTI_LINE = "multi_line"
    MODE_BET_SLIP = "bet_slip"

    def __init__(self, mode: str = MODE_BET_SLIP) -> None:
        self._mode = mode

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        """Return a RecognitionResult based on the current mode."""
        req_id = request.request_id

        if self._mode == self.MODE_TIMEOUT:
            return RecognitionResult(
                recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
                request_id=req_id,
                status=RecognitionStatus.FAILED,
                provider=ProviderMetadata(id=PROVIDER_ID, mode="fake"),
                source_image=SourceImage(image_id=request.image_id),
                provider_error=ProviderError(
                    code=ErrorCode.TIMEOUT,
                    message="Fake timeout after 30.0s",
                    retryable=True,
                    http_status=None,
                ),
                latency_ms=30000.0,
            )

        if self._mode == self.MODE_PROVIDER_ERROR:
            return RecognitionResult(
                recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
                request_id=req_id,
                status=RecognitionStatus.FAILED,
                provider=ProviderMetadata(id=PROVIDER_ID, mode="fake"),
                source_image=SourceImage(image_id=request.image_id),
                provider_error=ProviderError(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="Fake internal error: simulated crash",
                    retryable=False,
                    http_status=500,
                ),
                latency_ms=1200.0,
            )

        if self._mode == self.MODE_BET_SLIP:
            return bet_slip_fixture(
                image_id=request.image_id,
                request_id=req_id,
            )

        if self._mode == self.MODE_NO_CONFIDENCE:
            return no_confidence_fixture(
                image_id=request.image_id,
                request_id=req_id,
            )

        if self._mode == self.MODE_MULTI_LINE:
            return multi_line_fixture(
                image_id=request.image_id,
                request_id=req_id,
            )

        # Default: success with simple text
        return RecognitionResult(
            recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
            request_id=req_id,
            status=RecognitionStatus.COMPLETED,
            provider=ProviderMetadata(id=PROVIDER_ID, mode="fake"),
            source_image=SourceImage(image_id=request.image_id),
            raw_text="01 02 03 04 05",
            lines=[
                Line(
                    line_id="succ-001",
                    order=1,
                    text="01 02 03 04 05",
                    confidence=_make_confidence(0.99),
                ),
            ],
        )
