"""Image quality assessment and OCR strategy selection.

Pure logic (no cv2) — callers pass measured values. Blur score is computed
by the OCR-venv segmenter (Laplacian variance) and passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Quality thresholds
MIN_IMAGE_LONG_SIDE = 500        # original image long side (px)
MIN_CROP_LONG_SIDE = 160         # row crop long side (px)
MIN_TEXT_HEIGHT = 12             # estimated text height (px)
BLUR_SCORE_THRESHOLD = 80.0      # Laplacian variance; below = blurry


@dataclass
class QualityAssessment:
    image_width: int = 0
    image_height: int = 0
    crop_width: int = 0
    crop_height: int = 0
    estimated_text_height: float | None = None
    blur_score: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def low_resolution(self) -> bool:
        return max(self.image_width, self.image_height) < MIN_IMAGE_LONG_SIDE or (
            max(self.crop_width, self.crop_height) > 0
            and max(self.crop_width, self.crop_height) < MIN_CROP_LONG_SIDE
        )

    @property
    def blurry(self) -> bool:
        return self.blur_score is not None and self.blur_score < BLUR_SCORE_THRESHOLD

    @property
    def text_too_small(self) -> bool:
        return self.estimated_text_height is not None and self.estimated_text_height < MIN_TEXT_HEIGHT

    def assess(self) -> "QualityAssessment":
        """Populate warnings based on current values."""
        self.warnings = []
        long_side = max(self.image_width, self.image_height)
        if long_side > 0 and long_side < MIN_IMAGE_LONG_SIDE:
            self.warnings.append(
                f"LOW_RESOLUTION: image {self.image_width}x{self.image_height}px, "
                f"long side {long_side} < {MIN_IMAGE_LONG_SIDE}px"
            )
        if self.crop_width > 0 or self.crop_height > 0:
            crop_long = max(self.crop_width, self.crop_height)
            if crop_long < MIN_CROP_LONG_SIDE:
                self.warnings.append(
                    f"LOW_RESOLUTION: crop {self.crop_width}x{self.crop_height}px, "
                    f"long side {crop_long} < {MIN_CROP_LONG_SIDE}px"
                )
        if self.blurry:
            self.warnings.append(
                f"BLURRY: Laplacian variance {self.blur_score:.1f} < {BLUR_SCORE_THRESHOLD}"
            )
        if self.text_too_small:
            self.warnings.append(
                f"TEXT_TOO_SMALL: estimated text height {self.estimated_text_height:.1f}px "
                f"< {MIN_TEXT_HEIGHT}px"
            )
        return self

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        self.assess()
        return {
            "image_size": [self.image_width, self.image_height],
            "crop_size": [self.crop_width, self.crop_height],
            "estimated_text_height": self.estimated_text_height,
            "blur_score": self.blur_score,
            "low_resolution": self.low_resolution,
            "blurry": self.blurry,
            "warnings": self.warnings,
        }


def assess_image_quality(
    image_width: int,
    image_height: int,
    crop_width: int = 0,
    crop_height: int = 0,
    estimated_text_height: float | None = None,
    blur_score: float | None = None,
) -> QualityAssessment:
    """Build and assess a QualityAssessment."""
    q = QualityAssessment(
        image_width=image_width,
        image_height=image_height,
        crop_width=crop_width,
        crop_height=crop_height,
        estimated_text_height=estimated_text_height,
        blur_score=blur_score,
    )
    return q.assess()


def quality_gate(assessment: QualityAssessment) -> dict[str, Any]:
    """Quality gate: low-quality images must NOT auto-enter fill flow.

    Returns {"pass": bool, "reasons": [...]}.
    """
    assessment.assess()
    reasons = list(assessment.warnings)
    blocked = bool(reasons)
    return {"pass": not blocked, "blocked": blocked, "reasons": reasons}


def estimate_text_height_from_boxes(boxes: list[list[float]]) -> float | None:
    """Median box height from OCR boxes (original image coords)."""
    heights = [abs(b[3] - b[1]) for b in boxes if len(b) >= 4 and abs(b[3] - b[1]) > 0]
    if not heights:
        return None
    s = sorted(heights)
    return s[len(s) // 2]


def decide_ocr_strategy(
    whole_image_f1: float | None,
    layout_f1: float | None,
    whole_confidence: float | None = None,
    layout_confidence: float | None = None,
) -> dict[str, Any]:
    """Choose between whole-image and layout OCR.

    Layout is NOT assumed better. Fallback to whole-image when layout F1 is
    lower or missing. Returns {"strategy": ..., "reason": ...}.
    """
    if layout_f1 is None or layout_f1 < 0:
        return {"strategy": "whole-image", "reason": "layout result missing or failed"}
    if whole_image_f1 is None or whole_image_f1 < 0:
        return {"strategy": "layout", "reason": "whole-image result missing or failed"}
    if layout_f1 >= whole_image_f1:
        reason = f"layout F1 {layout_f1:.4f} >= whole-image F1 {whole_image_f1:.4f}"
        return {"strategy": "layout", "reason": reason}
    reason = (
        f"whole-image F1 {whole_image_f1:.4f} > layout F1 {layout_f1:.4f}; "
        "falling back to whole-image"
    )
    return {"strategy": "whole-image", "reason": reason}
