"""Pure layout row logic — no cv2/numpy imports (testable in main venv).

Used by tools/vision/layout_segmenter.py (OCR venv).
"""

from __future__ import annotations


def group_by_rows(items: list[dict]) -> list[list[dict]]:
    """Cluster OCR items into physical rows.

    Uses vertical center distance + vertical overlap ratio + median text
    height. Boxes side-by-side on the same line overlap vertically → same row.
    Boxes on different lines have distant centers → different rows.
    """
    if not items:
        return []

    # Attach temp geometry
    for it in items:
        box = it.get("box") or []
        if len(box) >= 4:
            it["_y1"] = float(box[1])
            it["_y2"] = float(box[3])
        else:
            poly = it.get("polygon", [])
            ys = [p[1] for p in poly if len(p) >= 2] or [0.0]
            it["_y1"] = float(min(ys))
            it["_y2"] = float(max(ys))
        it["_yc"] = (it["_y1"] + it["_y2"]) / 2.0
        it["_h"] = max(it["_y2"] - it["_y1"], 0.1)

    heights = sorted(it["_h"] for it in items)
    median_h = heights[len(heights) // 2]

    sorted_items = sorted(items, key=lambda it: (it["_yc"], it["_y1"]))
    rows: list[list[dict]] = []
    current = [sorted_items[0]]
    for it in sorted_items[1:]:
        anchor = current[0]
        center_dist = abs(it["_yc"] - anchor["_yc"])
        overlap = min(it["_y2"], anchor["_y2"]) - max(it["_y1"], anchor["_y1"])
        min_h = min(it["_h"], anchor["_h"])
        overlap_ratio = overlap / min_h if min_h > 0 else 0.0
        # Same physical row: centers close OR strong vertical overlap
        same_row = center_dist <= 0.6 * median_h or overlap_ratio > 0.5
        if same_row:
            current.append(it)
        else:
            rows.append(current)
            current = [it]
    rows.append(current)

    for it in items:
        for k in ("_y1", "_y2", "_yc", "_h"):
            it.pop(k, None)
    return rows


def row_crop_ranges(
    rows: list[list[dict]], region_y1: int, region_y2: int, padding: int = 5,
) -> list[tuple[int, int]]:
    """Non-overlapping vertical crop ranges, one per physical row.

    Boundary between adjacent rows = midpoint of their vertical centers.
    Each crop spans the full region width (caller slices x separately).
    Padding is clamped to half the gap so crops never overlap or eat into
    the neighboring row.
    """
    if not rows:
        return []

    centers: list[float] = []
    for row in rows:
        ys = []
        for it in row:
            box = it.get("box") or []
            if len(box) >= 4:
                ys.extend([box[1], box[3]])
            else:
                poly = it.get("polygon", [])
                ys.extend(p[1] for p in poly if len(p) >= 2)
        centers.append((min(ys) + max(ys)) / 2.0 if ys else 0.0)

    n = len(centers)
    ranges: list[tuple[int, int]] = []
    for i, c in enumerate(centers):
        upper = region_y1 if i == 0 else (centers[i - 1] + c) / 2.0
        lower = region_y2 if i == n - 1 else (c + centers[i + 1]) / 2.0
        # Padding clamped: never push past the midpoint toward the neighbor
        gap_top = (c - upper) / 2.0 if i > 0 else 0.0
        gap_bot = (lower - c) / 2.0 if i < n - 1 else 0.0
        pad_top = min(float(padding), max(0.0, gap_top)) if i > 0 else 0
        pad_bot = min(float(padding), max(0.0, gap_bot)) if i < n - 1 else 0
        y1 = max(region_y1, int(upper + pad_top))
        y2 = min(region_y2, int(lower - pad_bot))
        if y2 <= y1:
            y2 = min(region_y2, y1 + 1)
        ranges.append((y1, y2))
    return ranges


def assess_crop_usability(
    row_items: list[dict],
    crop_w: int,
    crop_h: int,
    ocr_text: str,
    low_res: bool = False,
    min_w: int = 32,
    min_h: int = 16,
) -> dict:
    """Decide whether a row crop is usable for human correction / training.

    Returns {"trainable": bool, "unusable_reason": str}.
    """
    reasons: list[str] = []

    if not ocr_text.strip():
        reasons.append("blank_ocr")

    if crop_w < min_w or crop_h < min_h:
        reasons.append(f"crop_too_small:{crop_w}x{crop_h}")

    # Single tiny corner detection (e.g. stray dot, line fragment)
    if len(row_items) == 1:
        box = row_items[0].get("box") or []
        if len(box) >= 4:
            bw = abs(box[2] - box[0])
            bh = abs(box[3] - box[1])
            if bw < 20 and bh < 10:
                reasons.append("single_tiny_detection")

    # Suspected multi-line: vertical center spread >> typical text height
    centers = []
    heights = []
    for it in row_items:
        box = it.get("box") or []
        if len(box) >= 4:
            centers.append((box[1] + box[3]) / 2.0)
            heights.append(abs(box[3] - box[1]))
    if centers:
        median_h = sorted(heights)[len(heights) // 2] if heights else 0.0
        spread = max(centers) - min(centers)
        if median_h > 0 and spread > 1.2 * median_h:
            reasons.append("suspected_multi_line")

    if low_res:
        reasons.append("low_resolution_original")

    return {
        "trainable": len(reasons) == 0,
        "unusable_reason": "; ".join(reasons),
    }
