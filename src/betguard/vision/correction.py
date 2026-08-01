"""Manual correction records and PaddleOCR training data export.

Only HUMAN-CONFIRMED corrections become training data. OCR's own guesses are
never used as labels.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CorrectionStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"


@dataclass
class CorrectionRecord:
    """A single human correction for one row crop."""

    crop_path: str
    source_image: str
    region: str
    bbox: list[int]  # [x, y, w, h] in original image coords
    raw_ocr_text: str = ""
    corrected_text: str = ""
    confidence: float = 0.0
    status: CorrectionStatus | str = CorrectionStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    trainable: bool = True
    unusable_reason: str = ""

    def is_training_eligible(self) -> bool:
        """Only trainable, CONFIRMED corrections with non-empty text are training data."""
        return (
            self.trainable
            and self.status == CorrectionStatus.CONFIRMED
            and bool(self.corrected_text.strip())
            and bool(self.crop_path)
            and os.path.isfile(self.crop_path)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "crop_path": self.crop_path,
            "source_image": self.source_image,
            "region": self.region,
            "bbox": self.bbox,
            "raw_ocr_text": self.raw_ocr_text,
            "corrected_text": self.corrected_text,
            "confidence": round(self.confidence, 4),
            "status": self.status.value if isinstance(self.status, CorrectionStatus) else self.status,
            "created_at": self.created_at,
            "trainable": self.trainable,
            "unusable_reason": self.unusable_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorrectionRecord:
        status = d.get("status", "pending")
        try:
            status = CorrectionStatus(status)
        except ValueError:
            pass
        return cls(
            record_id=d.get("record_id", uuid.uuid4().hex[:12]),
            crop_path=d.get("crop_path", ""),
            source_image=d.get("source_image", ""),
            region=d.get("region", ""),
            bbox=d.get("bbox", []),
            raw_ocr_text=d.get("raw_ocr_text", ""),
            corrected_text=d.get("corrected_text", ""),
            confidence=d.get("confidence", 0.0),
            status=status,
            created_at=d.get("created_at", ""),
            trainable=d.get("trainable", True),
            unusable_reason=d.get("unusable_reason", ""),
        )


# ── Persistence ──────────────────────────────────────────────────────────────

CORRECTIONS_FILE = "corrections.json"


def save_correction(record: CorrectionRecord, workspace_dir: str) -> None:
    """Append a correction record to the workspace's corrections.json."""
    os.makedirs(workspace_dir, exist_ok=True)
    path = os.path.join(workspace_dir, CORRECTIONS_FILE)
    records = load_corrections(workspace_dir)
    # Replace existing record with same id, else append
    records = [r for r in records if r.record_id != record.record_id]
    records.append(record)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in records], f, ensure_ascii=False, indent=2)


def load_corrections(workspace_dir: str) -> list[CorrectionRecord]:
    path = os.path.join(workspace_dir, CORRECTIONS_FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [CorrectionRecord.from_dict(d) for d in data]
    except (json.JSONDecodeError, TypeError):
        return []


def find_pending_crops(crop_dir: str, workspace_dir: str) -> list[dict]:
    """List TRAINABLE crop files not yet confirmed/skipped, with OCR text.

    Returns list of {crop_path, region, bbox, raw_ocr_text, confidence, status,
    trainable, unusable_reason}. Non-trainable crops are excluded (they are
    never offered for human confirmation).
    """
    done = load_corrections(workspace_dir)
    done_paths = {r.crop_path for r in done}
    pending = []
    if not os.path.isdir(crop_dir):
        return pending
    for root, _dirs, files in os.walk(crop_dir):
        for fn in sorted(files):
            if not fn.endswith((".png", ".jpg", ".jpeg")):
                continue
            crop_path = os.path.join(root, fn)
            if crop_path in done_paths:
                continue
            meta = _load_crop_meta(crop_path)
            if not meta.get("trainable", True):
                continue  # never offer unusable crops
            pending.append({
                "crop_path": crop_path,
                "region": meta.get("region", os.path.basename(root)),
                "bbox": meta.get("bbox", []),
                "raw_ocr_text": meta.get("raw_ocr_text", ""),
                "confidence": meta.get("confidence", 0.0),
                "status": "pending",
                "trainable": True,
                "unusable_reason": meta.get("unusable_reason", ""),
            })
    return pending


def scan_crop_meta(crop_dir: str) -> dict[str, int]:
    """Count trainable vs excluded crops (for reporting only)."""
    trainable = 0
    excluded = 0
    reasons: dict[str, int] = {}
    if not os.path.isdir(crop_dir):
        return {"trainable": 0, "excluded": 0, "reasons": {}}
    for root, _dirs, files in os.walk(crop_dir):
        for fn in sorted(files):
            if not fn.endswith((".png", ".jpg", ".jpeg")):
                continue
            meta = _load_crop_meta(os.path.join(root, fn))
            if meta.get("trainable", True):
                trainable += 1
            else:
                excluded += 1
                reason = (meta.get("unusable_reason") or "unknown").split(";")[0].strip()
                reasons[reason] = reasons.get(reason, 0) + 1
    return {"trainable": trainable, "excluded": excluded, "reasons": reasons}


def _load_crop_meta(crop_path: str) -> dict[str, Any]:
    meta_path = crop_path + ".json"
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ── Training data export ─────────────────────────────────────────────────────


def export_training_list(workspace_dir: str, output_path: str) -> int:
    """Export confirmed corrections as crop_path<TAB>corrected_text lines.

    Returns number of exported rows. Only CONFIRMED + non-empty text rows.
    """
    records = load_corrections(workspace_dir)
    eligible = [r for r in records if r.is_training_eligible()]
    with open(output_path, "w", encoding="utf-8") as f:
        for r in sorted(eligible, key=lambda r: r.created_at):
            # Normalize path separators for portability
            path = r.crop_path.replace("\\", "/")
            f.write(f"{path}\t{r.corrected_text.strip()}\n")
    return len(eligible)


def summary(workspace_dir: str) -> dict[str, int]:
    records = load_corrections(workspace_dir)
    return {
        "total": len(records),
        "confirmed": sum(1 for r in records if r.status == CorrectionStatus.CONFIRMED),
        "skipped": sum(1 for r in records if r.status == CorrectionStatus.SKIPPED),
        "pending": sum(1 for r in records if r.status == CorrectionStatus.PENDING),
        "training_eligible": sum(1 for r in records if r.is_training_eligible()),
    }
