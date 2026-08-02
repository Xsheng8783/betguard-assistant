"""ROI preprocessing — full-group crops with 3 versions (cv2, OCR venv).

Each annotated bbox is cropped with padding on all sides. Three versions are
produced and saved as separate files (grid-suppressed never overwrites
original):
  original          : raw crop, unchanged
  contrast          : grayscale + CLAHE
  grid_suppressed   : red grid line suppression + grayscale + CLAHE
"""

from __future__ import annotations

import os

import cv2
import numpy as np

VERSION_NAMES = ("original", "contrast", "grid_suppressed")


def crop_with_padding(img, bbox, padding: int = 8):
    """Crop bbox [x,y,w,h] with clamped padding. Returns (crop, x1, y1)."""
    h, w = img.shape[:2]
    x, y, bw, bh = bbox
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(w, x + bw + padding)
    y2 = min(h, y + bh + padding)
    return img[y1:y2, x1:x2], x1, y1


def _grid_suppress_red(img, red_low=(0, 0, 120), red_high=(90, 90, 255)):
    """Suppress red-ish grid lines (typical printed bet-slip grid).

    Red mask pixels are replaced with the local white background estimate.
    Returns a BGR image with grid lines removed.
    """
    bgr = img if len(img.shape) == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    mask = cv2.inRange(bgr, np.array(red_low, dtype=np.uint8), np.array(red_high, dtype=np.uint8))
    # Slight dilation to catch thin lines
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.dilate(mask, kernel, iterations=1)
    # Background estimate: median of border pixels
    h, w = bgr.shape[:2]
    border = np.concatenate([
        bgr[0:2, :, :].reshape(-1, 3),
        bgr[h - 2:h, :, :].reshape(-1, 3),
        bgr[:, 0:2, :].reshape(-1, 3),
        bgr[:, w - 2:w, :].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0).astype(np.uint8)
    out = bgr.copy()
    out[mask > 0] = bg
    return out


def make_versions(img, bbox, out_dir: str, prefix: str, padding: int = 8) -> dict[str, str]:
    """Produce original / contrast / grid_suppressed crops on disk.

    Returns {version_name: file_path}. Never overwrites original with
    grid-suppressed output.
    """
    crop, _x1, _y1 = crop_with_padding(img, bbox, padding)
    os.makedirs(out_dir, exist_ok=True)

    paths: dict[str, str] = {}
    for v in VERSION_NAMES:
        out = os.path.join(out_dir, f"{prefix}__{v}.png")
        if v == "original":
            cv2.imwrite(out, crop)
        elif v == "contrast":
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            cv2.imwrite(out, clahe.apply(gray))
        else:  # grid_suppressed
            suppressed = _grid_suppress_red(crop)
            gray = cv2.cvtColor(suppressed, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            cv2.imwrite(out, clahe.apply(gray))
        paths[v] = out
    return paths
