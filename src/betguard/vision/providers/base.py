"""Provider protocol and base abstractions for vision/OCR providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from betguard.vision.contracts import RecognitionRequest, RecognitionResult


@runtime_checkable
class ImageRecognitionProvider(Protocol):
    """Protocol that all vision/OCR providers must implement."""

    provider_id: str

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        """Process an image and return a RecognitionResult."""
        ...
