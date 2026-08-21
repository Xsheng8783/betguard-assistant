"""Deterministic contracts for cell-first OCR evidence.

This module deliberately stops before betting semantics.  It accepts literal
PP-OCR regions that already belong to a physical cell, classifies only what is
visible, protects split decimal literals, and reconstructs visual rows from
geometry.  Its output is machine evidence and can never create a candidate.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from statistics import median
from typing import Any, Iterable


SCHEMA_VERSION = "betguard.vision.cell-first.v1"
TOKEN_SOURCE = "ppocrv6"
CELL_BOUNDARY_UNCERTAIN = "CELL_BOUNDARY_UNCERTAIN"

NUMBER_01_39 = "NUMBER_01_39"
OPERATOR_X = "OPERATOR_X"
DECIMAL_LITERAL = "DECIMAL_LITERAL"
MULTIPLIER_FRAGMENT = "MULTIPLIER_FRAGMENT"
SPECIAL_LITERAL = "SPECIAL_LITERAL"
TEXT_OTHER = "TEXT_OTHER"
UNCERTAIN = "UNCERTAIN"

CLASSIFICATIONS = frozenset({
    NUMBER_01_39,
    OPERATOR_X,
    DECIMAL_LITERAL,
    MULTIPLIER_FRAGMENT,
    SPECIAL_LITERAL,
    TEXT_OTHER,
    UNCERTAIN,
})

_DECIMAL_RE = re.compile(r"^[0-9]+[.．][0-9]+$")
_INTEGER_RE = re.compile(r"^[0-9]{1,2}$")
_MULTIPLIER_RE = re.compile(
    r"^(?:[0-9一二三四五六七八九十/]+\s*)?[xX×]\s*"
    r"(?:[0-9]+(?:[.．][0-9]+)?)?$"
)
_SPECIALS = ("半車", "尾", "車", "各")
_DOT_LITERALS = frozenset({".", "．"})


def evidence_authority() -> dict[str, bool]:
    """Return the non-authoritative flags required on every Phase 1 result."""
    return {
        "machine_evidence_only": True,
        "human_confirmed": False,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def classify_literal(
    text_raw: str,
    confidence: float,
    *,
    minimum_confidence: float = 0.5,
) -> str:
    """Classify a literal OCR region without correcting or interpreting it."""
    text = str(text_raw).strip()
    if not text or not math.isfinite(float(confidence)) or confidence < minimum_confidence:
        return UNCERTAIN
    if text in {"x", "X", "×"}:
        return OPERATOR_X
    if _DECIMAL_RE.fullmatch(text):
        return DECIMAL_LITERAL
    if any(literal in text for literal in _SPECIALS):
        return SPECIAL_LITERAL
    if _MULTIPLIER_RE.fullmatch(text) and any(operator in text for operator in "xX×"):
        return MULTIPLIER_FRAGMENT
    if _INTEGER_RE.fullmatch(text):
        value = int(text)
        if 1 <= value <= 39:
            return NUMBER_01_39
    return TEXT_OTHER


def make_token(
    *,
    token_id: str,
    cell_id: str,
    text_raw: str,
    confidence: float,
    bbox: Iterable[float],
) -> dict[str, Any]:
    """Build the normalized token contract from one PP-OCR region."""
    coords = [float(value) for value in bbox]
    if len(coords) != 4 or not all(math.isfinite(value) for value in coords):
        raise ValueError("token bbox must contain four finite values")
    x1, y1, x2, y2 = coords
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
        raise ValueError("token bbox must be positive and ordered")
    score = float(confidence)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("token confidence must be between zero and one")
    return {
        "token_id": str(token_id),
        "cell_id": str(cell_id),
        "text_raw": str(text_raw),
        "confidence": score,
        "bbox": [x1, y1, x2, y2],
        "center_x": (x1 + x2) / 2.0,
        "center_y": (y1 + y2) / 2.0,
        "width": x2 - x1,
        "height": y2 - y1,
        "source": TOKEN_SOURCE,
        "classification": classify_literal(str(text_raw), score),
    }


def reconstruct_visible_rows(
    cell_id: str,
    tokens: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Cluster tokens by visual y-center and sort each row by x-center."""
    selected = [deepcopy(token) for token in tokens if token.get("cell_id") == cell_id]
    if not selected:
        return {
            "cell_id": cell_id,
            "visible_rows": [],
            "decimal_spans": [],
            "row_reconstruction_uncertain": False,
            **evidence_authority(),
        }

    heights = [float(token["height"]) for token in selected if float(token["height"]) > 0]
    typical_height = median(heights) if heights else 1.0
    center_tolerance = max(2.0, typical_height * 0.55)
    selected.sort(key=lambda token: (float(token["center_y"]), float(token["center_x"])))

    rows: list[list[dict[str, Any]]] = []
    row_centers: list[float] = []
    for token in selected:
        matching: list[tuple[float, int]] = []
        ty1, ty2 = float(token["bbox"][1]), float(token["bbox"][3])
        for index, row in enumerate(rows):
            center_distance = abs(float(token["center_y"]) - row_centers[index])
            overlaps = []
            for member in row:
                my1, my2 = float(member["bbox"][1]), float(member["bbox"][3])
                overlap = min(ty2, my2) - max(ty1, my1)
                min_height = min(ty2 - ty1, my2 - my1)
                overlaps.append(overlap / min_height if min_height > 0 else 0.0)
            if center_distance <= center_tolerance or max(overlaps, default=0.0) >= 0.45:
                matching.append((center_distance, index))
        if matching:
            _, row_index = min(matching)
            rows[row_index].append(token)
            row_centers[row_index] = sum(
                float(member["center_y"]) for member in rows[row_index]
            ) / len(rows[row_index])
        else:
            rows.append([token])
            row_centers.append(float(token["center_y"]))

    ordered_rows = [
        row for _, row in sorted(zip(row_centers, rows), key=lambda pair: pair[0])
    ]
    for row in ordered_rows:
        row.sort(key=lambda token: (float(token["center_x"]), float(token["center_y"])))

    decimal_spans: list[dict[str, Any]] = []
    for row_index, row in enumerate(ordered_rows, 1):
        decimal_spans.extend(_protect_split_decimals(cell_id, row_index, row))
        for token in row:
            token["row_id"] = f"{cell_id}-R{row_index:03d}"

    visible_rows = []
    for row in ordered_rows:
        visible_rows.append([
            {
                "token_id": token["token_id"],
                "text": token["text_raw"],
                "x": token["center_x"],
                "bbox": token["bbox"],
                "classification": token["classification"],
                **({"decimal_group_id": token["decimal_group_id"]}
                   if token.get("decimal_group_id") else {}),
            }
            for token in row
        ])

    return {
        "cell_id": cell_id,
        "visible_rows": visible_rows,
        "tokens": [token for row in ordered_rows for token in row],
        "decimal_spans": decimal_spans,
        "row_reconstruction_uncertain": False,
        **evidence_authority(),
    }


def _protect_split_decimals(
    cell_id: str,
    row_index: int,
    row: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark literal ``digits dot digits`` spans so digits never leak as numbers."""
    spans: list[dict[str, Any]] = []
    cursor = 0
    while cursor + 2 < len(row):
        left = str(row[cursor]["text_raw"]).strip()
        dot = str(row[cursor + 1]["text_raw"]).strip()
        right = str(row[cursor + 2]["text_raw"]).strip()
        if left.isdigit() and dot in _DOT_LITERALS and right.isdigit():
            group_id = f"{cell_id}-R{row_index:03d}-D{len(spans) + 1:02d}"
            members = row[cursor:cursor + 3]
            for member in members:
                member["classification"] = DECIMAL_LITERAL
                member["decimal_group_id"] = group_id
            spans.append({
                "decimal_group_id": group_id,
                "token_ids": [member["token_id"] for member in members],
                "text_raw": " ".join(str(member["text_raw"]) for member in members),
                "literal": f"{left}.{right}",
                "bbox": _union_bbox(member["bbox"] for member in members),
            })
            cursor += 3
            continue
        cursor += 1
    return spans


def _union_bbox(values: Iterable[Iterable[float]]) -> list[float]:
    boxes = [list(value) for value in values]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]
