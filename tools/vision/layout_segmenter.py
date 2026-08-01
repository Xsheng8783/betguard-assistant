"""Bet slip layout segmenter — splits image into regions, OCR per-line.

Runs in OCR venv (imports cv2, numpy, paddleocr).
Does NOT import betguard modules.

Layout regions use NORMALIZED coordinates (0.0-1.0) so they scale to any
input resolution. Every region is reported even when it has zero detections.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

# Warn when the input's long side is below this (pixels)
MIN_LONG_SIDE = 500


@dataclass
class CropRegion:
    """A crop region in NORMALIZED coordinates (0.0-1.0 of image width/height)."""

    name: str
    nx: float
    ny: float
    nw: float
    nh: float
    description: str = ""

    def to_pixel(self, img_w: int, img_h: int) -> tuple[int, int, int, int]:
        x1 = max(0, min(img_w - 1, int(self.nx * img_w)))
        y1 = max(0, min(img_h - 1, int(self.ny * img_h)))
        x2 = max(x1 + 1, min(img_w, int((self.nx + self.nw) * img_w)))
        y2 = max(y1 + 1, min(img_h, int((self.ny + self.nh) * img_h)))
        return x1, y1, x2, y2


@dataclass
class LineOcr:
    line_id: str = ""
    region: str = ""
    row: int = 0
    y_min: float = 0.0
    y_max: float = 0.0
    crop_bbox: list[int] = field(default_factory=list)
    raw_text: str = ""
    tokens: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    uncertain: bool = False
    uncertain_reason: str = ""
    crop_path: str = ""
    trainable: bool = False
    unusable_reason: str = ""


# ── Default bet slip layout (normalized 0-1) ─────────────────────────────────

DEFAULT_LAYOUT: dict[str, list[CropRegion]] = {
    "columns": [
        CropRegion("left_numbers", nx=0.03, ny=0.08, nw=0.29, nh=0.55, description="左欄號碼區"),
        CropRegion("center_numbers", nx=0.35, ny=0.08, nw=0.29, nh=0.55, description="中欄號碼區"),
        CropRegion("right_numbers", nx=0.67, ny=0.08, nw=0.30, nh=0.55, description="右欄號碼區"),
    ],
    "bottom": [
        CropRegion("bottom", nx=0.03, ny=0.68, nw=0.94, nh=0.29, description="底部全車區"),
    ],
}


# ── Number splitting ─────────────────────────────────────────────────────────

_VALID_LOTTERY = set(range(1, 40))


def _split_connected_digits(text: str) -> list[int]:
    """Split '0515' → [5, 15]. Prefers unique two-digit split. [] if ambiguous."""
    text = text.strip()
    candidates: list[list[int]] = []
    for split_at in range(1, len(text)):
        left, right = text[:split_at], text[split_at:]
        try:
            lv, rv = int(left), int(right)
            if lv in _VALID_LOTTERY and rv in _VALID_LOTTERY:
                candidates.append([lv, rv])
        except ValueError:
            pass
    two_digit = [c for c in candidates if c[0] >= 10 and c[1] >= 10]
    if len(two_digit) == 1:
        return two_digit[0]
    if len(candidates) == 1:
        return candidates[0]
    return []


def _try_parse_number(text: str) -> list[int]:
    text = text.strip()
    try:
        n = int(text)
        if n in _VALID_LOTTERY:
            return [n]
    except ValueError:
        pass
    return _split_connected_digits(text)


# ── Row grouping (tolerance from median box height) ──────────────────────────


def _box_height(box: list) -> float:
    if len(box) >= 4:
        return float(abs(box[3] - box[1]))
    poly = box
    if isinstance(poly, list) and poly and all(len(p) >= 2 for p in poly):
        ys = [p[1] for p in poly]
        return float(max(ys) - min(ys))
    return 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def _group_by_rows(items: list[dict]) -> list[list[dict]]:
    """Alias — real implementation in layout_rows (pure, testable)."""
    try:
        from layout_rows import group_by_rows
    except ImportError:  # running with repo root on sys.path
        from tools.vision.layout_rows import group_by_rows
    return group_by_rows(items)


def _row_crop_ranges(
    rows: list[list[dict]], region_y1: int, region_y2: int, padding: int = 5,
) -> list[tuple[int, int]]:
    """Alias — real implementation in layout_rows (pure, testable)."""
    try:
        from layout_rows import row_crop_ranges
    except ImportError:  # running with repo root on sys.path
        from tools.vision.layout_rows import row_crop_ranges
    return row_crop_ranges(rows, region_y1, region_y2, padding)


def assess_crop_usability(
    row_items: list[dict],
    crop_w: int,
    crop_h: int,
    ocr_text: str,
    low_res: bool = False,
    min_w: int = 32,
    min_h: int = 16,
) -> dict:
    """Alias — real implementation in layout_rows (pure, testable)."""
    try:
        from layout_rows import assess_crop_usability as _assess
    except ImportError:  # running with repo root on sys.path
        from tools.vision.layout_rows import assess_crop_usability as _assess
    return _assess(row_items, crop_w, crop_h, ocr_text, low_res, min_w, min_h)


# ── Crop helpers ─────────────────────────────────────────────────────────────


def _crop_hash(img: np.ndarray) -> str:
    """Stable hash of crop bytes — used for unique temp filenames."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return hashlib.sha256(str(img.shape).encode()).hexdigest()[:16]
    return hashlib.sha256(buf.tobytes()).hexdigest()[:16]


def _crop_preview_data_uri(img: np.ndarray, max_side: int = 160) -> str:
    """Small base64 data URI preview of a crop (for offline HTML reports)."""
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


# ── Main segmentation ────────────────────────────────────────────────────────


def segment_and_ocr(
    image_path: str,
    layout: dict[str, list[CropRegion]] | None = None,
    ocr_engine=None,
    crop_output_dir: str | None = None,
) -> dict:
    """Segment image into regions, OCR per-line, return structured result.

    Every layout region is reported (even with zero detections).
    Region crops are saved to unique temp files named by crop hash.
    If crop_output_dir is set, raw row crops are persisted there (with
    sidecar .json metadata) for the human correction workflow.
    """
    if layout is None:
        layout = DEFAULT_LAYOUT

    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "IMAGE_LOAD_FAILED"}

    h, w = img.shape[:2]
    t0 = time.perf_counter()

    # Resolution check
    warnings: list[str] = []
    long_side = max(w, h)
    if long_side < MIN_LONG_SIDE:
        warnings.append(
            f"LOW_RESOLUTION: input {w}x{h} px, long side {long_side} < {MIN_LONG_SIDE}. "
            "Upscaling improves OCR input but does NOT recover detail lost at capture."
        )

    if ocr_engine is None:
        from paddleocr import PaddleOCR
        ocr_engine = PaddleOCR(
            device="cpu",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_hpi=False,
            enable_mkldnn=False,
            cpu_threads=4,
        )

    all_lines: list[LineOcr] = []
    regions: list[dict] = []
    temp_dir = tempfile.mkdtemp(prefix="layout_crops_")

    try:
        for group_name, crops in layout.items():
            for crop in crops:
                x1, y1, x2, y2 = crop.to_pixel(w, h)
                region_img = img[y1:y2, x1:x2]

                # Region quality: blur score (Laplacian variance on gray)
                gray_r = cv2.cvtColor(region_img, cv2.COLOR_BGR2GRAY) if len(region_img.shape) == 3 else region_img
                blur_score = float(cv2.Laplacian(gray_r, cv2.CV_64F).var())

                # Region-level enhancement: 2x upscale + CLAHE (same as best
                # whole-image benchmark profile; boosts detection on raw crops)
                gray_region = cv2.cvtColor(region_img, cv2.COLOR_BGR2GRAY) if len(region_img.shape) == 3 else region_img
                rh, rw = gray_region.shape[:2]
                region_enhanced = cv2.resize(gray_region, (rw * 2, rh * 2), interpolation=cv2.INTER_CUBIC)
                clahe_r = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                region_enhanced = clahe_r.apply(region_enhanced)

                # Unique temp file per crop (by hash — never overwritten)
                c_hash = _crop_hash(region_img)
                tmp_path = os.path.join(temp_dir, f"crop_{crop.name}_{c_hash}.png")
                cv2.imwrite(tmp_path, region_enhanced)

                items: list[dict] = []
                try:
                    result = ocr_engine.predict(tmp_path)
                    items = _extract_items(result)
                except Exception:
                    items = []

                # Adjust coordinates: OCR saw 2x-enhanced crop → divide by 2,
                # then offset by crop origin to land in original image coords
                region_scale = 2.0
                for item in items:
                    poly = item.get("polygon", [])
                    if poly:
                        item["polygon"] = [[p[0] / region_scale + x1, p[1] / region_scale + y1] for p in poly if len(p) >= 2]
                    box = item.get("box", [])
                    if len(box) >= 4:
                        item["box"] = [box[0] / region_scale + x1, box[1] / region_scale + y1,
                                       box[2] / region_scale + x1, box[3] / region_scale + y1]

                rows = _group_by_rows(items)
                # Non-overlapping vertical ranges: boundary = midpoint of
                # adjacent row centers; each crop spans the full region width
                row_ranges = _row_crop_ranges(rows, y1, y2)

                region_lines = []
                for row_idx, (row_items, (row_y1, row_y2)) in enumerate(zip(rows, row_ranges)):
                    # Merge row text from ORIGINAL (pre-re-OCR) items
                    row_text = " ".join(it.get("text", "") for it in row_items)
                    row_score = sum(it.get("score", 0) for it in row_items) / max(len(row_items), 1)

                    # Full region width × midpoint-based vertical range
                    row_x1 = x1
                    row_x2 = x2
                    y_min = min((min(p[1] for p in it.get("polygon", [])) for it in row_items if it.get("polygon")), default=float(row_y1))
                    y_max = max((max(p[1] for p in it.get("polygon", [])) for it in row_items if it.get("polygon")), default=float(row_y2))

                    line_crop = img[row_y1:row_y2, row_x1:row_x2]
                    line_text, line_conf, line_items = row_text, row_score, row_items
                    if line_crop.size > 0:
                        gray = cv2.cvtColor(line_crop, cv2.COLOR_BGR2GRAY) if len(line_crop.shape) == 3 else line_crop
                        lh, lw = gray.shape[:2]
                        upscaled = cv2.resize(gray, (lw * 2, lh * 2), interpolation=cv2.INTER_CUBIC)
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                        enhanced = clahe.apply(upscaled)

                        line_hash = _crop_hash(enhanced)
                        tmp2 = os.path.join(temp_dir, f"line_{crop.name}_r{row_idx}_{line_hash}.png")
                        cv2.imwrite(tmp2, enhanced)
                        try:
                            line_result = ocr_engine.predict(tmp2)
                            line_items = _extract_items(line_result)
                            line_text = " ".join(it.get("text", "") for it in line_items)
                            line_conf = sum(it.get("score", 0) for it in line_items) / max(len(line_items), 1)
                        except Exception:
                            pass

                    # Number tokens (no auto-guessing; uncertain when ambiguous)
                    tokens = []
                    uncertain = False
                    uncertain_reason = ""
                    for it in line_items:
                        txt = it.get("text", "").strip()
                        nums = _try_parse_number(txt)
                        if nums:
                            for n in nums:
                                tokens.append({"number": n, "raw_text": txt, "confidence": it.get("score", 0)})
                        elif re.search(r"\d", txt):
                            tokens.append({"number": None, "raw_text": txt, "confidence": it.get("score", 0)})
                            uncertain = True
                            uncertain_reason = f"unparsed digits: {txt}"

                    # Crop usability: trainable only if single clean line
                    low_res_original = max(w, h) < 500
                    usability = assess_crop_usability(
                        row_items,
                        crop_w=x2 - x1,
                        crop_h=row_y2 - row_y1,
                        ocr_text=line_text,
                        low_res=low_res_original,
                    )

                    line_obj = LineOcr(
                        line_id=f"{crop.name}-row{row_idx:02d}",
                        region=crop.name,
                        row=row_idx,
                        y_min=round(y_min, 1),
                        y_max=round(y_max, 1),
                        crop_bbox=[row_x1, row_y1, row_x2 - row_x1, row_y2 - row_y1],
                        raw_text=line_text,
                        tokens=tokens,
                        confidence=round(line_conf, 4),
                        uncertain=uncertain,
                        uncertain_reason=uncertain_reason,
                    )
                    # Persist raw row crop for human correction workflow
                    if crop_output_dir and line_crop.size > 0:
                        region_out = os.path.join(crop_output_dir, crop.name)
                        os.makedirs(region_out, exist_ok=True)
                        crop_file = os.path.join(region_out, f"{line_obj.line_id}.png")
                        cv2.imwrite(crop_file, line_crop)
                        with open(crop_file + ".json", "w", encoding="utf-8") as mf:
                            json.dump({
                                "crop_path": crop_file,
                                "source_image": image_path,
                                "region": crop.name,
                                "bbox": line_obj.crop_bbox,
                                "raw_ocr_text": line_text,
                                "confidence": line_obj.confidence,
                                "trainable": usability["trainable"],
                                "unusable_reason": usability["unusable_reason"],
                            }, mf, ensure_ascii=False, indent=2)
                        line_obj.crop_path = crop_file
                    line_obj.trainable = usability["trainable"]
                    line_obj.unusable_reason = usability["unusable_reason"]
                    region_lines.append(line_obj)
                    all_lines.append(line_obj)

                # Region quality assessment (inline — segmenter must stay
                # standalone; OCR venv does not import betguard package)
                text_heights = [
                    abs(it["box"][3] - it["box"][1])
                    for it in items if len(it.get("box", [])) >= 4 and abs(it["box"][3] - it["box"][1]) > 0
                ]
                est_text_height = (sorted(text_heights)[len(text_heights) // 2] if text_heights else None)
                quality_warnings = []
                if max(w, h) > 0 and max(w, h) < 500:
                    quality_warnings.append(f"LOW_RESOLUTION: image {w}x{h}")
                crop_long = max(x2 - x1, y2 - y1)
                if crop_long < 160:
                    quality_warnings.append(f"LOW_RESOLUTION: crop long side {crop_long}px < 160px")
                if blur_score < 80.0:
                    quality_warnings.append(f"BLURRY: Laplacian variance {blur_score:.1f} < 80")
                if est_text_height is not None and est_text_height < 12:
                    quality_warnings.append(f"TEXT_TOO_SMALL: est height {est_text_height:.1f}px < 12px")
                quality = {
                    "image_size": [w, h],
                    "crop_size": [x2 - x1, y2 - y1],
                    "estimated_text_height": est_text_height,
                    "blur_score": round(blur_score, 1),
                    "warnings": quality_warnings,
                }

                regions.append({
                    "group": group_name,
                    "name": crop.name,
                    "description": crop.description,
                    "normalized_bbox": [crop.nx, crop.ny, crop.nw, crop.nh],
                    "pixel_bbox": [x1, y1, x2 - x1, y2 - y1],
                    "crop_size": [x2 - x1, y2 - y1],
                    "crop_hash": c_hash,
                    "crop_preview": _crop_preview_data_uri(region_img),
                    "detection_count": len(items),
                    "row_count": len(region_lines),
                    "quality": quality,
                    "lines": [{
                        "line_id": l.line_id,
                        "row": l.row,
                        "y_min": l.y_min,
                        "y_max": l.y_max,
                        "crop_path": l.crop_path,
                        "crop_bbox": l.crop_bbox,
                        "raw_text": l.raw_text,
                        "tokens": l.tokens,
                        "confidence": l.confidence,
                        "uncertain": l.uncertain,
                        "uncertain_reason": l.uncertain_reason,
                        "trainable": l.trainable,
                        "unusable_reason": l.unusable_reason,
                    } for l in region_lines],
                })

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "ok": True,
        "image_size": [w, h],
        "resolution_warning": warnings[0] if warnings else "",
        "elapsed_ms": round(elapsed_ms, 1),
        "regions": regions,
        "all_numbers": [t for l in all_lines for t in l.tokens if t.get("number") is not None],
        "uncertain_lines": [l.line_id for l in all_lines if l.uncertain],
        "total_lines": len(all_lines),
    }


def _to_float(v) -> float:
    if hasattr(v, "item"):
        return float(v.item())
    return float(v)


def _extract_items(raw_result: list) -> list[dict]:
    items = []
    for res in raw_result:
        if not isinstance(res, dict):
            continue
        inner = res.get("res", res)
        texts = inner.get("rec_texts", [])
        scores = inner.get("rec_scores", [])
        polys = inner.get("rec_polys", [])
        boxes = inner.get("rec_boxes", [])
        n = len(texts)
        for i in range(n):
            poly = polys[i] if i < len(polys) else []
            if hasattr(poly, "tolist"):
                poly = poly.tolist()
            if isinstance(poly, (list, tuple)):
                flat = []
                for v in poly:
                    if hasattr(v, "tolist"):
                        v = v.tolist()
                    if hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
                        flat.append([_to_float(x) for x in v])
                    else:
                        flat.append(_to_float(v))
                if flat and not isinstance(flat[0], list):
                    flat = [[flat[j], flat[j + 1]] for j in range(0, len(flat) - 1, 2)]
                poly = flat
            b = boxes[i] if i < len(boxes) else []
            if hasattr(b, "tolist"):
                b = b.tolist()
            items.append({
                "text": str(texts[i]) if i < len(texts) else "",
                "score": _to_float(scores[i]) if i < len(scores) else 0.0,
                "polygon": poly if isinstance(poly, list) else [],
                "box": [_to_float(v) for v in b] if isinstance(b, (list, tuple)) and b else [],
            })
    return items
