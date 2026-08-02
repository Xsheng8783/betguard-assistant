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
from betguard.vision.providers.openai_paid import OpenAIPaidVisionProvider
from betguard.vision.image_intake import (
    ImageMetadata,
    delete_image,
    detect_mime_type,
    get_metadata,
    read_image_data,
    validate_and_store,
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
    "OpenAIPaidVisionProvider",
    # Fixtures
    "bet_slip_fixture",
    "multi_line_fixture",
    "no_confidence_fixture",
    # Image intake
    "ImageMetadata",
    "validate_and_store",
    "detect_mime_type",
    "delete_image",
    "get_metadata",
    "read_image_data",
]
