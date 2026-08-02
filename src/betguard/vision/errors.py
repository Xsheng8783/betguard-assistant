"""Vision module error codes and provider error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ErrorCode(str, Enum):
    """Provider-neutral error codes for OCR/vision failures."""

    TIMEOUT = "TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_IMAGE = "INVALID_IMAGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    NO_TEXT_DETECTED = "NO_TEXT_DETECTED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    BILLING_REQUIRED = "BILLING_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderError:
    """Error returned by a vision provider."""

    code: ErrorCode | str
    message: str
    retryable: bool = False
    http_status: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "code": self.code if isinstance(self.code, str) else self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            d["http_status"] = self.http_status
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ProviderError:
        code = d.get("code", "UNKNOWN")
        try:
            code = ErrorCode(code)
        except ValueError:
            pass  # keep as string
        return cls(
            code=code,
            message=d.get("message", ""),
            retryable=d.get("retryable", False),
            http_status=d.get("http_status"),
        )


class VisionError(Exception):
    """Base exception for vision module errors."""

    pass


class ContractValidationError(VisionError, ValueError):
    """Raised when a contract object fails validation."""

    pass
