"""Semantic ROI annotation model — minimal compatible extension of the
existing betguard.vision.roi.v1 schema.

Adds, per group:
  - raw_transcription   (user-entered; NOT auto-generated)
  - layout              (normal_row | number_set | shared_multiplier)
  - scope               (current_group | all_groups_in_region | unresolved_region)
  - semantics           (deterministic Closed Set V2 parse of raw_transcription)
  - applies_to_group_ids(list of group ids for shared multipliers)
  - verification_status (needs_human_review | verified)

Ground truth is NEVER inferred by OCR or vision models — verified requires an
explicit human action in the annotator UI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .closed_set import (
    ClosedSetIssue,
    normalize_text,
    parse_multi_category_shared,
    parse_multiplier_text,
    parse_paren_number_set,
    parse_shared_multiplier,
    validate_line,
)
from .roi_annotations import ROI_SCHEMA_VERSION

SEMANTIC_SCHEMA_VERSION = "betguard.vision.semantic-roi.v1"

VERIFICATION_NEEDS_REVIEW = "needs_human_review"
VERIFICATION_VERIFIED = "verified"

LAYOUT_VALUES = frozenset({"normal_row", "number_set", "shared_multiplier"})
SCOPE_VALUES = frozenset({"current_group", "all_groups_in_region", "unresolved_region"})


@dataclass
class SemanticRoiGroup:
    id: str
    bbox: list[int]  # [x, y, w, h] in ORIGINAL image pixels
    raw_transcription: str = ""
    layout: str = ""
    scope: str = "current_group"
    semantics: dict[str, Any] | None = None
    applies_to_group_ids: list[str] = field(default_factory=list)
    verification_status: str = VERIFICATION_NEEDS_REVIEW
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
            "raw_transcription": self.raw_transcription,
            "layout": self.layout,
            "scope": self.scope,
            "semantics": self.semantics,
            "applies_to_group_ids": list(self.applies_to_group_ids),
            "verification_status": self.verification_status,
            "label": self.label,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticRoiGroup:
        return cls(
            id=str(d.get("id", "")),
            bbox=[int(v) for v in d.get("bbox", [])],
            raw_transcription=str(d.get("raw_transcription", "")),
            layout=str(d.get("layout", "")),
            scope=str(d.get("scope", "current_group")),
            semantics=d.get("semantics"),
            applies_to_group_ids=[str(x) for x in d.get("applies_to_group_ids", [])],
            verification_status=str(d.get("verification_status", VERIFICATION_NEEDS_REVIEW)),
            label=str(d.get("label", "")),
            notes=str(d.get("notes", "")),
        )


@dataclass
class SemanticRoiAnnotationSet:
    schema_version: str = SEMANTIC_SCHEMA_VERSION
    image: str = ""
    groups: list[SemanticRoiGroup] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "image": self.image,
            "notes": self.notes,
            "groups": [g.to_dict() for g in self.groups],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticRoiAnnotationSet:
        return cls(
            schema_version=str(d.get("schema_version", SEMANTIC_SCHEMA_VERSION)),
            image=str(d.get("image", "")),
            notes=str(d.get("notes", "")),
            groups=[SemanticRoiGroup.from_dict(g) for g in d.get("groups", [])],
        )


def parse_column_matrix(lines: list[str]) -> list[list[str]] | None:
    """Parse a column matrix (zhu-peng arrangement) into per-column groups.

    Example:
        01 X 10 X 17
        02   11   18
        03   12   27
    → [["01", "02", "03"], ["10", "11", "12"], ["17", "18", "27"]]

    The first row defines the column count (numbers separated by × or
    spaces); following rows fill each column top-to-bottom. Returns None
    when rows disagree on column count or contain non-number tokens.

    IMPORTANT: a matrix is only recognized when at least one line contains
    an explicit × separator (e.g. "01 X 10 X 17"). A block of plain
    space-separated rows (e.g. ten 4-number rows) is NOT a matrix — it is
    ten independent number lines. This prevents ambiguous flattening.
    """
    rows: list[list[str]] = []
    has_x_separator = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if "×" in normalize_text(line):
            has_x_separator = True
        cells = [c for c in re.split(r"[×Xx\s]+", normalize_text(line)) if c]
        if not all(re.fullmatch(r"[0-9?]{2}", c) for c in cells):
            return None
        rows.append(cells)

    if not rows:
        return None
    if not has_x_separator:
        return None  # plain rows are independent lines, not a matrix
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        return None  # ragged matrix — ambiguous
    columns: list[list[str]] = []
    for col in range(width):
        columns.append([rows[row][col] for row in range(len(rows))])
    return columns


def flatten_matrix_for_ui(columns: list[list[str]]) -> str:
    """Render matrix columns as slash-separated column groups for display."""
    return " / ".join(" ".join(col) for col in columns)


def parse_semantics(raw_transcription: str, *, region_bound: bool = False) -> dict[str, Any]:
    """Run the production Closed Set V2 pipeline on raw transcription.

    Splits the raw transcription into number tokens + multiplier text, then
    delegates to validate_line (the production entry point) so ALL syntax
    checks (parens, decimals, shared scope, bare dots) apply identically.
    The front-end NEVER parses semantics itself.
    """
    raw = raw_transcription.strip()

    # Shared multiplier (各=三×0.3 OR 二三×0.3) is its own layout —
    # validate_line handles it.
    if parse_shared_multiplier(raw) is not None or parse_multi_category_shared(raw) is not None:
        return validate_line(
            number_groups=[], multiplier_text=None, raw_text=raw,
            layout_hint="unknown", uncertain=False, region_bound=region_bound,
        )

    # Split parenthesized number set (if any) from trailing multiplier text.
    paren = parse_paren_number_set(raw)
    if paren is not None:
        numbers = paren
        rest = re.sub(r"\([^()]*\)", "", raw).strip()
        multiplier_text = rest or None
        return validate_line(
            number_groups=[numbers], multiplier_text=multiplier_text, raw_text=raw,
            layout_hint="normal_like", uncertain=False, region_bound=region_bound,
        )

    # No parens: separate bare number tokens from multiplier-like fragments.
    numbers: list[str] = []
    multiplier_parts: list[str] = []
    for token in re.split(r"\s+", raw):
        if re.fullmatch(r"[0-9?]{1,2}", token):
            numbers.append(token)
        else:
            multiplier_parts.append(token)
    multiplier_text = " ".join(multiplier_parts) if multiplier_parts else None
    validation = validate_line(
        number_groups=[numbers] if numbers else [], multiplier_text=multiplier_text,
        raw_text=raw, layout_hint="normal_like", uncertain=False,
        region_bound=region_bound,
    )

    # Pure number rows without any multiplier get a synthesized normal_row
    # semantic (deterministic, server-side only).
    if validation.get("semantics") is None and numbers:
        from .closed_set import validate_number_token

        canonical: list[str] = []
        issues: list[str] = list(validation.get("issues", []))
        for token in numbers:
            tv = validate_number_token(token)
            if tv.canonical is not None:
                canonical.append(tv.canonical)
            if tv.requires_human_confirmation:
                issues.extend(tv.issues)
        validation["semantics"] = {
            "layout": "normal_row",
            "numbers": canonical,
            "multipliers": [],
            "scope": "current_group",
            "needs_human_confirmation": True,
        }
        if issues:
            validation["issues"] = sorted(set(issues))
    return validation


def blocking_issues(validation: dict[str, Any]) -> list[str]:
    """Issues that block verification (syntax-level blockers only)."""
    issues = list(validation.get("issues", []))
    # group_structure_uncertain is expected for shared multipliers until they
    # are bound; it is NOT a blocking issue by itself.
    return [i for i in issues if i != ClosedSetIssue.GROUP_STRUCTURE_UNCERTAIN.value]


def can_verify_group(
    group: SemanticRoiGroup,
    *,
    all_group_ids: list[str],
    img_w: int,
    img_h: int,
) -> list[str]:
    """Return blockers preventing verification. Empty list = verified allowed.

    Human confirmation in the UI is still required on top of this — verified
    only means the dataset was human-checked, never auto-betting.
    """
    blockers: list[str] = []

    # NOTE: verification_status is NOT exempt from re-checking — a dataset
    # with verified groups that later violate rules must be rejected too.

    if len(group.bbox) != 4 or group.w <= 0 or group.h <= 0:
        blockers.append("bbox_invalid_or_empty")
    if group.x < 0 or group.y < 0 or group.x + group.w > img_w or group.y + group.h > img_h:
        blockers.append("bbox_out_of_image")

    if not group.raw_transcription.strip():
        blockers.append("raw_transcription_empty")

    validation = parse_semantics(group.raw_transcription, region_bound=bool(group.applies_to_group_ids))
    if blocking_issues(validation):
        blockers.append(f"validation_issues:{','.join(sorted(blocking_issues(validation)))}")
    sem = validation.get("semantics") or {}
    if sem.get("scope") == "unresolved_region":
        blockers.append("scope_unresolved_region")

    numbers = sem.get("numbers") or []
    if any("?" in n for n in numbers):
        blockers.append("unresolved_question_mark")
    if "?" in group.raw_transcription:
        blockers.append("unresolved_question_mark")

    if sem.get("layout") == "shared_multiplier":
        if not group.applies_to_group_ids:
            blockers.append("shared_multiplier_unbound")
        else:
            seen: set[str] = set()
            for gid in group.applies_to_group_ids:
                if not gid or not gid.strip():
                    blockers.append("applies_to_blank_id")
                elif gid == group.id:
                    blockers.append("applies_to_self")
                elif gid in seen:
                    blockers.append("applies_to_duplicate")
                elif not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", gid):
                    blockers.append("applies_to_invalid_format")
                elif gid not in all_group_ids:
                    blockers.append("applies_to_missing_group")
                seen.add(gid)

    return blockers


def validate_annotation_set(
    ann: SemanticRoiAnnotationSet,
    *,
    img_w: int = 0,
    img_h: int = 0,
) -> list[str]:
    """Full dataset validation. Empty = valid."""
    errors: list[str] = []

    if ann.schema_version != SEMANTIC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SEMANTIC_SCHEMA_VERSION}")

    if not ann.image:
        errors.append("image path is required")
    elif not os.path.isfile(ann.image):
        errors.append(f"image not found: {ann.image}")
    elif not img_w or not img_h:
        try:
            img_w, img_h = image_size(ann.image)
        except ValueError:
            errors.append(f"cannot read image size: {ann.image}")

    if not ann.groups:
        errors.append("at least one group is required")

    seen_ids: set[str] = set()
    all_ids = [g.id for g in ann.groups]
    for i, g in enumerate(ann.groups):
        if not g.id:
            errors.append(f"group[{i}]: id is required")
        elif g.id in seen_ids:
            errors.append(f"group[{i}]: duplicate id {g.id!r}")
        seen_ids.add(g.id)

        if g.verification_status not in {VERIFICATION_NEEDS_REVIEW, VERIFICATION_VERIFIED}:
            errors.append(f"group {g.id or i}: invalid verification_status {g.verification_status!r}")
        if g.layout and g.layout not in LAYOUT_VALUES:
            errors.append(f"group {g.id or i}: invalid layout {g.layout!r}")
        if g.scope not in SCOPE_VALUES:
            errors.append(f"group {g.id or i}: invalid scope {g.scope!r}")

        for target in g.applies_to_group_ids:
            if target not in all_ids:
                errors.append(f"group {g.id or i}: applies_to_group_ids references missing group {target!r}")

        if img_w and img_h:
            blockers = can_verify_group(g, all_group_ids=all_ids, img_w=img_w, img_h=img_h)
            if g.verification_status == VERIFICATION_VERIFIED and blockers:
                errors.append(f"group {g.id or i}: verified but blockers remain: {blockers}")

    return errors


def atomic_save(ann: SemanticRoiAnnotationSet, path: str) -> None:
    """Safe write: temp file → parse+validate → atomic replace → keep backup."""
    path = str(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    data = json.dumps(ann.to_dict(), ensure_ascii=False, indent=2)
    # parse + schema validation BEFORE replacing the real file
    parsed = SemanticRoiAnnotationSet.from_dict(json.loads(data))
    errors = validate_annotation_set(parsed)
    if errors:
        raise ValueError(f"refusing to save invalid annotation: {errors}")

    fd, tmp = tempfile.mkstemp(prefix=".roi-annot-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def image_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def image_size(path: str) -> tuple[int, int]:
    """Return (width, height) for PNG/JPEG via header parsing (no cv2)."""
    with open(path, "rb") as f:
        head = f.read(32)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        import struct
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    if head[:2] == b"\xff\xd8":
        import struct
        with open(path, "rb") as f:
            data = f.read(64 * 1024)
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
    raise ValueError("unsupported image format")
