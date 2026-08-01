"""Vision / OCR module — provider-neutral recognition contracts and providers.

This module establishes the data contract between OCR/vision providers and the
betguard parsing pipeline. It does NOT import or depend on parser, normalizer,
validator, webfill, or Playwright.
"""

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
from betguard.vision.errors import (
    ContractValidationError,
    ErrorCode,
    ProviderError,
    VisionError,
)
from betguard.vision.providers.base import ImageRecognitionProvider
from betguard.vision.providers.fake import (
    FakeProvider,
    bet_slip_fixture,
    multi_line_fixture,
    no_confidence_fixture,
)

__all__ = [
    # Contracts
    "Alternative",
    "BoundingBox",
    "Confidence",
    "ConfidenceLevel",
    "Line",
    "ProviderMetadata",
    "RecognitionRequest",
    "RecognitionResult",
    "RecognitionStatus",
    "SourceImage",
    "Token",
    # Errors
    "ContractValidationError",
    "ErrorCode",
    "ProviderError",
    "VisionError",
    # Providers
    "ImageRecognitionProvider",
    "FakeProvider",
    # Fixtures
    "bet_slip_fixture",
    "multi_line_fixture",
    "no_confidence_fixture",
]
