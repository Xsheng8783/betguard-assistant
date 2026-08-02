"""ROI annotation schema for the manual ROI diagnostic benchmark.

Format: betguard.vision.roi.v1
Each group is one manually-annotated full bet group (e.g. one red-box group
on the slip) with its own ground truth. Bboxes are in ORIGINAL image pixels.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

ROI_SCHEMA_VERSION = "betguard.vision.roi.v1"


@dataclass
class RoiGroup:
    id: str
    bbox: list[int]  # [x, y, w, h] in original image pixels
    ground_truth: str = ""
    label: str = ""
    notes: str = ""

    @property
    def x(self) -> int:
        return int(self.bbox[0])

    @property
    def y(self) -> int:
        return int(self.bbox[1])

    @property
    def w(self) -> int:
        return int(self.bbox[2])

    @property
    def h(self) -> int:
        return int(self.bbox[3])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bbox": [self.x, self.y, self.w, self.h],
            "ground_truth": self.ground_truth,
            "label": self.label,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoiGroup:
        return cls(
            id=str(d.get("id", "")),
            bbox=[int(v) for v in d.get("bbox", [])],
            ground_truth=str(d.get("ground_truth", "")),
            label=str(d.get("label", "")),
            notes=str(d.get("notes", "")),
        )


@dataclass
class RoiAnnotationSet:
    schema_version: str = ROI_SCHEMA_VERSION
    image: str = ""
    groups: list[RoiGroup] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "image": self.image,
            "notes": self.notes,
            "groups": [g.to_dict() for g in self.groups],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoiAnnotationSet:
        return cls(
            schema_version=str(d.get("schema_version", ROI_SCHEMA_VERSION)),
            image=str(d.get("image", "")),
            notes=str(d.get("notes", "")),
            groups=[RoiGroup.from_dict(g) for g in d.get("groups", [])],
        )


def validate_annotation_set(ann: RoiAnnotationSet) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []

    if ann.schema_version != ROI_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ROI_SCHEMA_VERSION}")

    if not ann.image:
        errors.append("image path is required")
    elif "://" in ann.image:
        errors.append("image must be a local path, not a URL")
    elif not os.path.isfile(ann.image):
        errors.append(f"image not found: {ann.image}")
    elif os.path.islink(ann.image):
        errors.append("image must not be a symlink")

    if not ann.groups:
        errors.append("at least one group is required")

    seen_ids: set[str] = set()
    for i, g in enumerate(ann.groups):
        if not g.id:
            errors.append(f"group[{i}]: id is required")
        elif g.id in seen_ids:
            errors.append(f"group[{i}]: duplicate id {g.id!r}")
        seen_ids.add(g.id)

        if len(g.bbox) != 4:
            errors.append(f"group {g.id or i}: bbox must be [x, y, w, h]")
            continue
        if g.w <= 0 or g.h <= 0:
            errors.append(f"group {g.id or i}: bbox width/height must be > 0")
        if any(v < 0 for v in g.bbox):
            errors.append(f"group {g.id or i}: bbox coordinates must be >= 0")

        if not g.ground_truth.strip():
            errors.append(f"group {g.id or i}: ground_truth is required")

    return errors


def load_annotation_set(path: str) -> RoiAnnotationSet:
    with open(path, "r", encoding="utf-8") as f:
        return RoiAnnotationSet.from_dict(json.load(f))


def save_annotation_set(ann: RoiAnnotationSet, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ann.to_dict(), f, ensure_ascii=False, indent=2)


def clamp_bboxes_to_image(ann: RoiAnnotationSet, img_w: int, img_h: int) -> None:
    """Clamp all bboxes to image bounds (mutates in place)."""
    for g in ann.groups:
        x = max(0, g.x)
        y = max(0, g.y)
        x2 = min(img_w, g.x + g.w)
        y2 = min(img_h, g.y + g.h)
        g.bbox = [x, y, max(1, x2 - x), max(1, y2 - y)]
