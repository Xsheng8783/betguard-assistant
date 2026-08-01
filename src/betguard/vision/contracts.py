"""Provider-neutral RecognitionResult contract for OCR/vision providers.

All providers (PaddleOCR, OpenAI Vision, Google Cloud Vision, Azure, Fake)
must convert their output into these types before entering the betguard pipeline.

Schema version: betguard.vision.recognition.v1
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


SCHEMA_VERSION = "betguard.vision.recognition.v1"
_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low", "unknown"})


# ── Enums ────────────────────────────────────────────────────────────────────


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class RecognitionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# ── Shared helpers ───────────────────────────────────────────────────────────


def _validate_confidence(value: float | None) -> None:
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(
            f"Confidence value must be null or 0.0–1.0, got {value}"
        )


def _validate_polygon(polygon: list[list[float]]) -> None:
    if len(polygon) < 3:
        raise ValueError("Polygon must have at least 3 points")
    for i, point in enumerate(polygon):
        if len(point) != 2:
            raise ValueError(f"Polygon point {i} must have exactly 2 coordinates")
        for j, v in enumerate(point):
            if math.isnan(v) or math.isinf(v):
                raise ValueError(
                    f"Polygon point {i} coordinate {j} is NaN or infinite: {v}"
                )


def _json_serializable(obj: Any) -> Any:
    """Recursively convert to JSON-serializable types."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_serializable(v) for v in obj]
    return obj


# ── Sub-models ───────────────────────────────────────────────────────────────


@dataclass
class Alternative:
    """An alternative OCR reading for a token or line."""

    text: str
    score: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        d: dict = {"text": self.text, "source": self.source}
        if self.score is not None:
            d["score"] = self.score
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Alternative:
        return cls(
            text=d.get("text", ""),
            score=d.get("score"),
            source=d.get("source", ""),
        )


@dataclass
class Confidence:
    """Confidence score for a recognition result."""

    value: float | None = None
    source: str = ""
    calibrated: bool = False
    level: ConfidenceLevel | str = ConfidenceLevel.UNKNOWN

    def __post_init__(self) -> None:
        _validate_confidence(self.value)
        # Normalize string level to ConfidenceLevel enum
        if isinstance(self.level, str):
            try:
                object.__setattr__(self, "level", ConfidenceLevel(self.level))
            except ValueError:
                raise ValueError(
                    f"Invalid confidence level '{self.level}', must be one of {sorted(_CONFIDENCE_LEVELS)}"
                )
        level_str = self.level.value  # now always an enum after normalization
        if level_str not in _CONFIDENCE_LEVELS:
            raise ValueError(
                f"Invalid confidence level '{level_str}', must be one of {sorted(_CONFIDENCE_LEVELS)}"
            )

    def to_dict(self) -> dict:
        d: dict = {
            "source": self.source,
            "calibrated": self.calibrated,
            "level": self.level.value if isinstance(self.level, ConfidenceLevel) else self.level,
        }
        if self.value is not None:
            d["value"] = self.value
        else:
            d["value"] = None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Confidence:
        level = d.get("level", "unknown")
        try:
            level = ConfidenceLevel(level)
        except ValueError:
            pass
        return cls(
            value=d.get("value"),
            source=d.get("source", ""),
            calibrated=d.get("calibrated", False),
            level=level,
        )


@dataclass
class BoundingBox:
    """Polygon bounding box in a named coordinate space."""

    coordinate_space: str
    polygon: list[list[float]]

    def __post_init__(self) -> None:
        _validate_polygon(self.polygon)

    def to_dict(self) -> dict:
        return {
            "coordinate_space": self.coordinate_space,
            "polygon": self.polygon,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BoundingBox:
        return cls(
            coordinate_space=d.get("coordinate_space", "pixel"),
            polygon=d.get("polygon", []),
        )


@dataclass
class Token:
    """A single recognized token within a line."""

    token_id: str
    text: str
    start: int
    end: int
    confidence: Confidence = field(default_factory=Confidence)
    critical_type: str = ""
    bounding_box: BoundingBox | None = None
    alternatives: list[Alternative] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"Token start must be >= 0, got {self.start}")
        if self.end < 0:
            raise ValueError(f"Token end must be >= 0, got {self.end}")
        if self.end < self.start:
            raise ValueError(
                f"Token end ({self.end}) must be >= start ({self.start})"
            )

    def validate_range(self, line_text: str) -> None:
        """Validate token range is within line text bounds."""
        if self.start > len(line_text) or self.end > len(line_text):
            raise ValueError(
                f"Token range [{self.start}:{self.end}] exceeds line text length {len(line_text)}"
            )

    def to_dict(self) -> dict:
        d: dict = {
            "token_id": self.token_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence.to_dict(),
            "critical_type": self.critical_type,
            "alternatives": [a.to_dict() for a in self.alternatives],
            "warnings": list(self.warnings),
        }
        if self.bounding_box is not None:
            d["bounding_box"] = self.bounding_box.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Token:
        return cls(
            token_id=d.get("token_id", ""),
            text=d.get("text", ""),
            start=d.get("start", 0),
            end=d.get("end", 0),
            confidence=Confidence.from_dict(d.get("confidence", {})),
            critical_type=d.get("critical_type", ""),
            bounding_box=BoundingBox.from_dict(d["bounding_box"]) if d.get("bounding_box") else None,
            alternatives=[Alternative.from_dict(a) for a in d.get("alternatives", [])],
            warnings=d.get("warnings", []),
        )


@dataclass
class Line:
    """A single recognized line of text."""

    line_id: str
    order: int
    text: str
    confidence: Confidence = field(default_factory=Confidence)
    bounding_box: BoundingBox | None = None
    tokens: list[Token] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.order < 1:
            raise ValueError(f"Line order must be positive, got {self.order}")
        for token in self.tokens:
            token.validate_range(self.text)

    def to_dict(self) -> dict:
        d: dict = {
            "line_id": self.line_id,
            "order": self.order,
            "text": self.text,
            "confidence": self.confidence.to_dict(),
            "tokens": [t.to_dict() for t in self.tokens],
            "alternatives": [a.to_dict() for a in self.alternatives],
            "warnings": list(self.warnings),
        }
        if self.bounding_box is not None:
            d["bounding_box"] = self.bounding_box.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Line:
        return cls(
            line_id=d.get("line_id", ""),
            order=d.get("order", 1),
            text=d.get("text", ""),
            confidence=Confidence.from_dict(d.get("confidence", {})),
            bounding_box=BoundingBox.from_dict(d["bounding_box"]) if d.get("bounding_box") else None,
            tokens=[Token.from_dict(t) for t in d.get("tokens", [])],
            alternatives=[Alternative.from_dict(a) for a in d.get("alternatives", [])],
            warnings=d.get("warnings", []),
        )


# ── Source image metadata ────────────────────────────────────────────────────


@dataclass
class SourceImage:
    """Metadata about the source image that was recognized."""

    image_id: str
    sha256: str = ""
    mime_type: str = "image/png"
    original_filename: str = ""
    width: int = 0
    height: int = 0
    byte_size: int = 0
    retention: str = "transient"
    cloud_uploaded: bool = False

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "original_filename": self.original_filename,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
            "retention": self.retention,
            "cloud_uploaded": self.cloud_uploaded,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SourceImage:
        return cls(
            image_id=d.get("image_id", ""),
            sha256=d.get("sha256", ""),
            mime_type=d.get("mime_type", "image/png"),
            original_filename=d.get("original_filename", ""),
            width=d.get("width", 0),
            height=d.get("height", 0),
            byte_size=d.get("byte_size", 0),
            retention=d.get("retention", "transient"),
            cloud_uploaded=d.get("cloud_uploaded", False),
        )


# ── Provider metadata ────────────────────────────────────────────────────────


@dataclass
class ProviderMetadata:
    """Metadata about the provider that performed recognition."""

    id: str
    mode: str = "real"
    model_name: str = ""
    model_version: str = ""
    adapter_version: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProviderMetadata:
        return cls(
            id=d.get("id", ""),
            mode=d.get("mode", "real"),
            model_name=d.get("model_name", ""),
            model_version=d.get("model_version", ""),
            adapter_version=d.get("adapter_version", ""),
        )


# ── Request ──────────────────────────────────────────────────────────────────


@dataclass
class RecognitionRequest:
    """Input to a recognition provider."""

    request_id: str
    image_id: str
    image_path: str = ""
    mime_type: str = "image/png"
    preprocessing_variant: str = "none"
    requested_language_hints: list[str] = field(default_factory=list)
    requested_features: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "image_id": self.image_id,
            "image_path": self.image_path,
            "mime_type": self.mime_type,
            "preprocessing_variant": self.preprocessing_variant,
            "requested_language_hints": list(self.requested_language_hints),
            "requested_features": list(self.requested_features),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> RecognitionRequest:
        return cls(
            request_id=d.get("request_id", ""),
            image_id=d.get("image_id", ""),
            image_path=d.get("image_path", ""),
            mime_type=d.get("mime_type", "image/png"),
            preprocessing_variant=d.get("preprocessing_variant", "none"),
            requested_language_hints=d.get("requested_language_hints", []),
            requested_features=d.get("requested_features", []),
            metadata=d.get("metadata", {}),
        )


# ── Top-level result ─────────────────────────────────────────────────────────


@dataclass
class RecognitionResult:
    """Provider-neutral recognition result.

    All OCR/vision providers must return this shape.
    """

    schema_version: str = SCHEMA_VERSION
    recognition_id: str = ""
    request_id: str = ""
    status: RecognitionStatus | str = RecognitionStatus.COMPLETED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider: ProviderMetadata = field(default_factory=lambda: ProviderMetadata(id="unknown"))
    source_image: SourceImage = field(default_factory=lambda: SourceImage(image_id=""))
    preprocessing: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    provider_error: ProviderError | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate the result against the contract rules."""
        from betguard.vision.errors import ContractValidationError, ProviderError as _PE  # noqa: F811

        # 1. schema_version must be correct
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError(
                f"schema_version must be {SCHEMA_VERSION}, got {self.schema_version}"
            )

        # 2. IDs must not be blank
        if not self.recognition_id or not self.recognition_id.strip():
            raise ContractValidationError("recognition_id must not be blank")
        if not self.request_id or not self.request_id.strip():
            raise ContractValidationError("request_id must not be blank")

        status = self.status.value if isinstance(self.status, RecognitionStatus) else self.status

        # 3–6 validated in sub-object __post_init__

        # 10. latency must not be negative
        if self.latency_ms < 0:
            raise ContractValidationError(f"latency_ms must be >= 0, got {self.latency_ms}")

        # 11. completed must not have fatal provider_error
        if status == "completed":
            if self.provider_error is not None:
                raise ContractValidationError(
                    "status=completed must not have a provider_error"
                )

        # 12. failed must have provider_error
        if status == "failed":
            if self.provider_error is None:
                raise ContractValidationError(
                    "status=failed must have a provider_error"
                )

    def to_dict(self) -> dict:
        from betguard.vision.errors import ProviderError as _PE  # noqa: F811

        d: dict = {
            "schema_version": self.schema_version,
            "recognition_id": self.recognition_id,
            "request_id": self.request_id,
            "status": self.status.value if isinstance(self.status, RecognitionStatus) else self.status,
            "created_at": self.created_at.isoformat(),
            "provider": self.provider.to_dict(),
            "source_image": self.source_image.to_dict(),
            "preprocessing": dict(self.preprocessing),
            "raw_text": self.raw_text,
            "lines": [line.to_dict() for line in self.lines],
            "warnings": list(self.warnings),
            "latency_ms": self.latency_ms,
        }
        if self.provider_error is not None:
            d["provider_error"] = self.provider_error.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RecognitionResult:
        from betguard.vision.errors import ProviderError

        status = d.get("status", "completed")
        try:
            status = RecognitionStatus(status)
        except ValueError:
            pass

        created_at = d.get("created_at", "")
        if isinstance(created_at, str) and created_at:
            created_at = datetime.fromisoformat(created_at)
        else:
            created_at = datetime.now(timezone.utc)

        provider_error = None
        if d.get("provider_error"):
            provider_error = ProviderError.from_dict(d["provider_error"])

        return cls(
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            recognition_id=d.get("recognition_id", ""),
            request_id=d.get("request_id", ""),
            status=status,
            created_at=created_at,
            provider=ProviderMetadata.from_dict(d.get("provider", {})),
            source_image=SourceImage.from_dict(d.get("source_image", {})),
            preprocessing=d.get("preprocessing", {}),
            raw_text=d.get("raw_text", ""),
            lines=[Line.from_dict(line) for line in d.get("lines", [])],
            warnings=d.get("warnings", []),
            latency_ms=d.get("latency_ms", 0.0),
            provider_error=provider_error,
        )
