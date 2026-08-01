"""Worker protocol types for stdin/stdout JSON subprocess communication.

Protocol version: betguard.vision.worker.v1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORKER_PROTOCOL_VERSION = "betguard.vision.worker.v1"


@dataclass
class WorkerRequest:
    """Request sent to OCR worker via stdin JSON."""

    protocol_version: str = WORKER_PROTOCOL_VERSION
    request_id: str = ""
    image_path: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "image_path": self.image_path,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerRequest:
        return cls(
            protocol_version=d.get("protocol_version", ""),
            request_id=d.get("request_id", ""),
            image_path=d.get("image_path", ""),
            options=d.get("options", {}),
        )


@dataclass
class WorkerEngineInfo:
    """Engine metadata returned by worker."""

    name: str = ""
    paddle_version: str = ""
    paddleocr_version: str = ""
    device: str = "cpu"
    detection_model: str = ""
    recognition_model: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerEngineInfo:
        return cls(
            name=d.get("name", ""),
            paddle_version=d.get("paddle_version", ""),
            paddleocr_version=d.get("paddleocr_version", ""),
            device=d.get("device", "cpu"),
            detection_model=d.get("detection_model", ""),
            recognition_model=d.get("recognition_model", ""),
        )


@dataclass
class WorkerOcrItem:
    """Single OCR result item from worker."""

    text: str = ""
    score: float = 0.0
    polygon: list[list[float]] = field(default_factory=list)
    box: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerOcrItem:
        poly = d.get("polygon", [])
        # Validate polygon
        if not isinstance(poly, list) or not all(isinstance(p, list) and len(p) >= 2 for p in poly):
            raise ValueError(f"Invalid polygon format: {poly!r}")
        return cls(
            text=str(d.get("text", "")),
            score=float(d.get("score", 0.0)),
            polygon=[[float(x), float(y)] for x, y in poly],
            box=[float(v) for v in d.get("box", [])],
        )


@dataclass
class WorkerResponse:
    """Response from OCR worker via stdout JSON."""

    ok: bool = True
    protocol_version: str = WORKER_PROTOCOL_VERSION
    request_id: str = ""
    engine: WorkerEngineInfo = field(default_factory=WorkerEngineInfo)
    elapsed_ms: float = 0.0
    items: list[WorkerOcrItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerResponse:
        items = []
        for item in d.get("items", []):
            try:
                items.append(WorkerOcrItem.from_dict(item))
            except (ValueError, TypeError, KeyError):
                raise ValueError(f"Invalid OCR item format: {item!r}")

        return cls(
            ok=bool(d.get("ok", True)),
            protocol_version=d.get("protocol_version", ""),
            request_id=d.get("request_id", ""),
            engine=WorkerEngineInfo.from_dict(d.get("engine", {})),
            elapsed_ms=float(d.get("elapsed_ms", 0.0)),
            items=items,
            warnings=d.get("warnings", []),
            error=d.get("error", {}),
        )


def sort_ocr_items(items: list[WorkerOcrItem]) -> list[WorkerOcrItem]:
    """Stable sort OCR items by polygon min-y, then min-x, then original index."""
    indexed = list(enumerate(items))
    indexed.sort(key=lambda t: (
        min(p[1] for p in t[1].polygon) if t[1].polygon else 0,
        min(p[0] for p in t[1].polygon) if t[1].polygon else 0,
        t[0],
    ))
    return [item for _, item in indexed]
