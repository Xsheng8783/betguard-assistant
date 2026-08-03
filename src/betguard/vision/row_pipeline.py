"""Row pipeline: photo intake → perspective/rotation correction →
normalized-coordinate region split → text-line detection → unique IDs.

No models are trained or swapped here. OpenCV-only line detection (horizontal
projection). Every physical text line must either be detected or the page is
flagged NEEDS_MANUAL_ROW_REVIEW — lines never silently disappear.

Region split uses normalized coordinates (fractions of the corrected image):
  left   = x in [0.00, 0.33], y in [0.00, 0.85]
  center = x in [0.33, 0.66], y in [0.00, 0.85]
  right  = x in [0.66, 1.00], y in [0.00, 0.85]
  bottom = y in [0.85, 1.00] (full width)

Protocol/CLI:
  python -m betguard.vision.row_pipeline --image <path> --output-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover — OCR venv only
    cv2 = None
    np = None

PROTOCOL_VERSION = "betguard.vision.row-pipeline.v1"

# ── Region layout (normalized) ────────────────────────────────────────────────

REGION_DEFS: dict[str, tuple[float, float, float, float]] = {
    # name: (x0, y0, x1, y1) fractions of corrected image
    "left":   (0.00, 0.00, 0.33, 0.85),
    "center": (0.33, 0.00, 0.66, 0.85),
    "right":  (0.66, 0.00, 1.00, 0.85),
    "bottom": (0.00, 0.85, 1.00, 1.00),
}

REGION_ORDER = ("left", "center", "right", "bottom")


@dataclass
class DetectedLine:
    region_id: str          # e.g. "left-01"
    line_id: str            # globally unique, e.g. "L-left-01-<uuid8>"
    region: str             # left / center / right / bottom
    bounding_box: list[int]  # [x, y, w, h] in corrected-image pixels
    score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "line_id": self.line_id,
            "region": self.region,
            "bounding_box": self.bounding_box,
            "score": self.score,
        }


@dataclass
class RowPipelineResult:
    image_path: str
    image_size: list[int]          # [w, h] of source image
    corrected_size: list[int]      # [w, h] after correction
    rows: list[DetectedLine] = field(default_factory=list)
    page_status: str = "OK"        # OK | NEEDS_MANUAL_ROW_REVIEW
    review_reason: str | None = None
    correction_applied: str = "none"   # none | rotation | perspective
    warnings: list[str] = field(default_factory=list)
    splits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "image_path": self.image_path,
            "image_size": self.image_size,
            "corrected_size": self.corrected_size,
            "page_status": self.page_status,
            "review_reason": self.review_reason,
            "correction_applied": self.correction_applied,
            "warnings": self.warnings,
            "splits": self.splits,
            "rows": [r.to_dict() for r in self.rows],
        }


# ── Correction ────────────────────────────────────────────────────────────────

def _estimate_rotation_angle(gray: Any) -> float:
    """Estimate skew by scanning angles and measuring horizontal-projection
    sharpness (text rows align best at the true angle). Robust for text
    (Hough long-lines fails on text rows). Returns degrees."""
    # binarize
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape
    best_angle, best_score = 0.0, -1.0
    for angle_deg in np.arange(-10.0, 10.5, 0.5):
        if abs(angle_deg) < 0.25:
            rotated = binary
        else:
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
            rotated = cv2.warpAffine(binary, matrix, (w, h),
                                     flags=cv2.INTER_NEAREST, borderValue=0)
        row_sum = rotated.sum(axis=1).astype(np.float64)
        # sharpness: rows aligned → few tall peaks → high variance of sums
        # normalized: variance / mean (peaky distribution)
        mean = row_sum.mean()
        if mean <= 0:
            continue
        score = float(row_sum.var() / mean)
        if score > best_score:
            best_score, best_angle = score, angle_deg
    return best_angle


def _correct_rotation(image: Any) -> tuple[Any, str]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    angle = _estimate_rotation_angle(gray)
    if abs(angle) < 0.8:
        return image, "none"
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, matrix, (w, h),
                             flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated, f"rotation:{angle:.2f}deg"


def _correct_perspective(image: Any) -> tuple[Any, str]:
    """Find the largest quad (paper) and warp it to a rectangle."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image, "none"
    largest = max(contours, key=cv2.contourArea)
    area_ratio = cv2.contourArea(largest) / float(image.shape[0] * image.shape[1])
    if area_ratio < 0.2:
        return image, "none"  # no dominant paper quad
    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, 0.02 * peri, True)
    if len(approx) != 4:
        return image, "none"
    pts = approx.reshape(4, 2).astype(np.float32)
    # order: top-left, top-right, bottom-right, bottom-left
    pts = _order_points(pts)
    h, w = image.shape[:2]
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(image, matrix, (w, h),
                                 flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return warped, "perspective"


def _order_points(pts: Any) -> Any:
    """Order 4 points: TL, TR, BR, BL."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]      # TL
    rect[2] = pts[np.argmax(s)]      # BR
    diff = np.diff(pts, axis=1).ravel()
    rect[1] = pts[np.argmin(diff)]   # TR
    rect[3] = pts[np.argmax(diff)]   # BL
    return rect


# ── Line detection (horizontal projection, OpenCV only) ──────────────────────

def _detect_lines_in_gray(gray: Any) -> list[tuple[int, int, int, int]]:
    """Detect text lines via horizontal projection bands.

    Returns list of (x, y, w, h) in the input gray image coordinates.
    """
    h, w = gray.shape
    # binarize: text (dark ink) → 1
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # close horizontally to merge characters within a line
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, w // 60), 3))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    row_sum = closed.sum(axis=1)  # ink per row
    threshold = max(20.0, row_sum.max() * 0.05)
    bands: list[tuple[int, int]] = []  # (y_start, y_end)
    in_band = False
    start = 0
    for y in range(h):
        active = row_sum[y] >= threshold
        if active and not in_band:
            in_band, start = True, y
        elif not active and in_band:
            in_band = False
            if y - start >= 4:  # ignore 1-3px noise bands
                bands.append((start, y))
    if in_band and h - start >= 4:
        bands.append((start, h))

    # merge bands separated by tiny gaps (same visual line)
    merged: list[tuple[int, int]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= max(2, h // 200):
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)

    boxes: list[tuple[int, int, int, int]] = []
    for y0, y1 in merged:
        slice_bin = closed[y0:y1, :]
        col_sum = slice_bin.sum(axis=0)
        col_active = col_sum > 0
        xs = np.where(col_active)[0]
        if len(xs) == 0:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        boxes.append((x0, y0, x1 - x0 + 1, y1 - y0 + 1))
    return boxes


def _region_rect(region: str, w: int, h: int) -> tuple[int, int, int, int]:
    x0f, y0f, x1f, y1f = REGION_DEFS[region]
    x0, y0 = int(x0f * w), int(y0f * h)
    x1, y1 = int(x1f * w), int(y1f * h)
    return x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)


def _detect_region_lines(gray: Any, region: str) -> list[tuple[int, int, int, int]]:
    w, h = gray.shape[1], gray.shape[0]
    rx, ry, rw, rh = _region_rect(region, w, h)
    region_gray = gray[ry:ry + rh, rx:rx + rw]
    if region_gray.size == 0:
        return []
    boxes = _detect_lines_in_gray(region_gray)
    # map back to full-image coordinates
    return [(x + rx, y + ry, bw, bh) for (x, y, bw, bh) in boxes]


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _find_reliable_valleys(column: Any, *, min_gap: int = 2, depth_ratio: float = 0.15) -> list[int]:
    """Find reliable valley positions (row indices) in a projection column.

    A valley is a run of at least `min_gap` consecutive rows whose ink sum
    is below `depth_ratio` of the column peak. Returns split positions
    (middle of each valley run). Edge valleys (touching 0 or the end) are
    excluded — they are the band boundaries, not internal splits.
    """
    if len(column) == 0:
        return []
    peak = float(column.max())
    if peak <= 0:
        return []
    threshold = peak * depth_ratio
    valleys: list[int] = []
    in_valley = False
    start = 0
    n = len(column)
    for i in range(n):
        low = column[i] < threshold
        if low and not in_valley:
            in_valley, start = True, i
        elif not low and in_valley:
            in_valley = False
            if i - start >= min_gap and start > 0 and i < n - 1:
                valleys.append((start + i) // 2)
    if in_valley and n - start >= min_gap and start > 0:
        valleys.append((start + n) // 2)
    return valleys


def _split_tall_band(gray: Any, box: tuple[int, int, int, int],
                     median_h: float) -> list[tuple[int, int, int, int]] | None:
    """Conservatively split a tall band into sub-bands at reliable valleys.

    Returns list of sub-band boxes, or None when no reliable valley exists
    (caller keeps the merged band and flags review). Sub-bands never
    overlap and each must be at least half the median height.
    """
    x, y, w, h = box
    band = gray[y:y + h, x:x + w]
    _, binary = cv2.threshold(band, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    row_sum = binary.sum(axis=1).astype(np.float64)
    valleys = _find_reliable_valleys(row_sum)

    # keep only valleys that yield two sub-bands of reasonable height
    candidates: list[tuple[int, int, int, int]] = []
    positions = [0] + valleys + [h]
    for a, b in zip(positions, positions[1:]):
        if b - a >= max(4, median_h * 0.5):
            candidates.append((x, y + a, w, b - a))
    if len(candidates) < 2:
        return None  # no reliable split — keep merged band
    # every sub-band must be >= half median height and not degenerate
    if any(bh < max(4, median_h * 0.5) for (_, _, _, bh) in candidates):
        return None
    return candidates


def run_pipeline(image_path: str) -> RowPipelineResult:
    """Run the full row pipeline. Raises ValueError on unreadable image."""
    if cv2 is None or np is None:  # pragma: no cover
        raise RuntimeError("OpenCV not available (run with the OCR venv)")

    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"unable to read image: {image_path}")

    result = RowPipelineResult(
        image_path=image_path,
        image_size=[int(image.shape[1]), int(image.shape[0])],
        corrected_size=[int(image.shape[1]), int(image.shape[0])],
    )

    # 1) perspective (if a clear paper quad exists), then rotation
    corrected, persp = _correct_perspective(image)
    if persp != "none":
        corrected, rot = _correct_rotation(corrected)
        result.correction_applied = "perspective" + (f"+{rot}" if rot != "none" else "")
    else:
        corrected, rot = _correct_rotation(image)
        if rot != "none":
            result.correction_applied = rot

    result.corrected_size = [int(corrected.shape[1]), int(corrected.shape[0])]
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)

    # 2) detect lines per region
    all_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    for region in REGION_ORDER:
        for box in _detect_region_lines(gray, region):
            all_boxes.append((region, box))

    # 3) assign unique ids
    seen: dict[str, int] = {}
    for region, box in all_boxes:
        seen[region] = seen.get(region, 0) + 1
        seq = seen[region]
        region_id = f"{region}-{seq:02d}"
        line_id = f"L-{region}-{seq:02d}-{uuid.uuid4().hex[:8]}"
        result.rows.append(DetectedLine(
            region_id=region_id, line_id=line_id, region=region,
            bounding_box=[int(v) for v in box],
        ))

    # 3b) conservative secondary split of abnormally tall bands
    if result.rows:
        heights = [r.bounding_box[3] for r in result.rows]
        median_h = float(np.median(heights))
        split_rows: list[DetectedLine] = []
        for row in result.rows:
            if row.bounding_box[3] <= median_h * 2.2:
                split_rows.append(row)
                continue
            sub_boxes = _split_tall_band(
                gray, tuple(row.bounding_box), median_h)
            if sub_boxes is None:
                # no reliable valley — keep merged band, page will be flagged
                split_rows.append(row)
                result.warnings.append(
                    f"{row.line_id}: tall band kept merged (no reliable valley)")
                continue
            children: list[DetectedLine] = []
            for i, box in enumerate(sub_boxes, start=1):
                child = DetectedLine(
                    region_id=f"{row.region_id}-{i}",
                    line_id=f"{row.line_id}-{i}",
                    region=row.region,
                    bounding_box=[int(v) for v in box],
                )
                children.append(child)
            split_rows.extend(children)
            result.splits.append({
                "parent_band_id": row.line_id,
                "split_method": "projection_valley",
                "split_position": [b[1] - row.bounding_box[1] for b in sub_boxes[1:]],
                "split_score": 1.0,
                "child_band_ids": [c.line_id for c in children],
            })
        result.rows = split_rows

    # 4) row-count sanity: fewer than 3 bands anywhere → uncertain
    if len(result.rows) == 0:
        result.page_status = "NEEDS_MANUAL_ROW_REVIEW"
        result.review_reason = "no text lines detected"
    elif len(result.rows) < 3:
        result.page_status = "NEEDS_MANUAL_ROW_REVIEW"
        result.review_reason = f"only {len(result.rows)} line(s) detected — rows may be merged"
    else:
        # 4b) flat-projection check: a solid block (no ink peaks/valleys)
        # is NOT a text line — a band whose projection is nearly constant is
        # suspicious and must not silently count as rows.
        flat_bands: list[str] = []
        for row in result.rows:
            x, y, w, h = row.bounding_box
            if h <= 0 or w <= 0:
                continue
            band = gray[y:y + h, x:x + w]
            _, binary = cv2.threshold(band, 0, 255,
                                      cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            row_sum = binary.sum(axis=1).astype(np.float64)
            mean = row_sum.mean()
            peak = row_sum.max()
            # solid block: either all-ink (flat high) or all-background
            # (flat zero) — both are non-text and must not count as rows
            if mean <= 0 or peak <= 0:
                flat_bands.append(row.line_id)
                continue
            # ink coverage: text lines rarely exceed ~60% ink; a solid
            # block approaches 100%
            ink_ratio = float(np.count_nonzero(binary)) / float(binary.size)
            if ink_ratio > 0.85:
                flat_bands.append(row.line_id)
                continue
            cv_ratio = float(row_sum.std() / mean)
            if cv_ratio < 0.05:  # nearly flat projection = solid block
                flat_bands.append(row.line_id)
        if flat_bands:
            result.page_status = "NEEDS_MANUAL_ROW_REVIEW"
            result.review_reason = (
                f"{len(flat_bands)} band(s) have flat (non-text) projection: "
                + ", ".join(flat_bands[:5])
            )
            return result
        # 5) height consistency: an abnormally tall band is likely several
        # merged rows — rows would silently disappear if we kept it as one.
        heights = [r.bounding_box[3] for r in result.rows]
        median_h = float(np.median(heights))
        tall = [r for r in result.rows if r.bounding_box[3] > median_h * 2.2]
        if tall:
            result.page_status = "NEEDS_MANUAL_ROW_REVIEW"
            result.review_reason = (
                f"{len(tall)} band(s) {median_h * 2.2:.0f}px+ vs median "
                f"{median_h:.0f}px — rows may be merged: "
                + ", ".join(r.line_id for r in tall[:5])
            )

    return result


# ── Outputs ───────────────────────────────────────────────────────────────────

def write_outputs(result: RowPipelineResult, output_dir: str) -> dict[str, str]:
    """Write JSON result, debug overlay PNG, and per-line crops."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    crops_dir = out / "crops"
    crops_dir.mkdir(exist_ok=True)

    # overlay (row overlay)
    image = cv2.imread(result.image_path)
    if image is None:
        raise ValueError(f"unable to read image for overlay: {result.image_path}")
    overlay = image.copy()
    colors = {
        "left": (0, 255, 0), "center": (255, 200, 0),
        "right": (0, 160, 255), "bottom": (200, 0, 255),
    }
    for row in result.rows:
        x, y, w, h = row.bounding_box
        color = colors.get(row.region, (255, 255, 255))
        cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
        cv2.putText(overlay, row.line_id, (x, max(y - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
    overlay_path = out / "rows_overlay.png"
    cv2.imwrite(str(overlay_path), overlay)

    # region overlay (region rectangles)
    regions_overlay = image.copy()
    for region in REGION_ORDER:
        rx, ry, rw, rh = _region_rect(region, image.shape[1], image.shape[0])
        color = colors.get(region, (255, 255, 255))
        cv2.rectangle(regions_overlay, (rx, ry), (rx + rw, ry + rh), color, 2)
        cv2.putText(regions_overlay, region, (rx + 4, ry + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    regions_path = out / "regions_overlay.png"
    cv2.imwrite(str(regions_path), regions_overlay)

    # corrected image
    corrected_path = out / "corrected.png"
    if result.correction_applied != "none":
        corrected, _ = _correct_perspective(image)
        if result.correction_applied.startswith("perspective"):
            corrected, rot = _correct_rotation(corrected)
        else:
            corrected, _ = _correct_rotation(image)
        cv2.imwrite(str(corrected_path), corrected)
    else:
        cv2.imwrite(str(corrected_path), image)

    # crops
    crop_paths: list[str] = []
    for row in result.rows:
        x, y, w, h = row.bounding_box
        crop = image[y:y + h, x:x + w]
        crop_path = crops_dir / f"{row.line_id}.png"
        cv2.imwrite(str(crop_path), crop)
        crop_paths.append(str(crop_path))

    json_path = out / "row-pipeline.json"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # summary.txt
    region_counts: dict[str, int] = {}
    for row in result.rows:
        region_counts[row.region] = region_counts.get(row.region, 0) + 1
    summary_lines = [
        f"image_size: {result.image_size[0]}x{result.image_size[1]}",
        f"corrected_size: {result.corrected_size[0]}x{result.corrected_size[1]}",
        f"correction_applied: {result.correction_applied}",
        f"skew_angle_deg: {result.correction_applied}",
        f"regions: {json.dumps(region_counts, ensure_ascii=False)}",
        f"auto_splits: {len(result.splits)}",
        f"unresolved_merged_bands: {sum(1 for r in result.rows if r.bounding_box[3] > 0)}",
        f"page_status: {result.page_status}",
        f"review_reason: {result.review_reason or '-'}",
        f"warnings: {len(result.warnings)}",
    ]
    for w in result.warnings:
        summary_lines.append(f"  warning: {w}")
    summary_path = out / "summary.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {
        "json": str(json_path),
        "overlay": str(overlay_path),
        "regions_overlay": str(regions_path),
        "corrected": str(corrected_path),
        "summary": str(summary_path),
        "crops": crop_paths,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Betguard row pipeline (OpenCV only)")
    parser.add_argument("--image", required=True, help="photo of the bet slip")
    parser.add_argument("--output-dir", default="row-pipeline-out")
    args = parser.parse_args(argv)

    try:
        result = run_pipeline(args.image)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    outputs = write_outputs(result, args.output_dir)
    print(json.dumps({
        "page_status": result.page_status,
        "review_reason": result.review_reason,
        "rows": len(result.rows),
        "correction": result.correction_applied,
        "outputs": outputs,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
