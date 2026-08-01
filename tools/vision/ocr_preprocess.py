"""Image preprocessing for OCR benchmark — runs in OCR venv (imports cv2, numpy).

Provides deterministic preprocessing profiles for benchmarking.
Does NOT modify original files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

PROFILES: dict[str, dict[str, Any]] = {
    "original": {"id": "original", "description": "No preprocessing"},
    "grayscale_clahe": {"id": "grayscale_clahe", "description": "Grayscale + CLAHE", "clahe_clip": 2.0, "clahe_grid": (8, 8)},
    "upscale_2x_clahe": {"id": "upscale_2x_clahe", "description": "2x upscale + CLAHE", "scale": 2.0, "clahe_clip": 2.0, "clahe_grid": (8, 8)},
    "otsu_2x": {"id": "otsu_2x", "description": "2x + Gaussian blur + Otsu", "scale": 2.0, "blur_ksize": 3, "blur_sigma": 0.5},
    "adaptive_2x": {"id": "adaptive_2x", "description": "2x + adaptive threshold", "scale": 2.0, "block_size": 21, "c_value": 8},
    "sharpen_clahe_2x": {"id": "sharpen_clahe_2x", "description": "2x + CLAHE + unsharp mask", "scale": 2.0, "clahe_clip": 2.0, "clahe_grid": (8, 8), "sharpen_amount": 1.0, "sharpen_radius": 1},
}


@dataclass
class PreprocessResult:
    profile_id: str
    image: np.ndarray
    width: int
    height: int
    elapsed_ms: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)


def load_image(path: str) -> np.ndarray:
    """Load image as BGR (OpenCV format)."""
    img = cv2.imread(path)
    if img is None or img.size == 0:
        raise ValueError("IMAGE_EMPTY")
    h, w = img.shape[:2]
    if w > 12000 or h > 12000 or w * h > 40_000_000:
        raise ValueError("IMAGE_TOO_LARGE")
    return img


def preprocess(img: np.ndarray, profile_id: str) -> PreprocessResult:
    """Apply a named preprocessing profile. All profiles are deterministic."""
    if profile_id not in PROFILES:
        raise ValueError(f"Unknown profile: {profile_id}")

    cfg = PROFILES[profile_id]
    t0 = time.perf_counter()

    if profile_id == "original":
        result_img = img.copy()
        params = {}

    elif profile_id == "grayscale_clahe":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=cfg["clahe_clip"], tileGridSize=cfg["clahe_grid"])
        result_img = clahe.apply(gray)
        params = {"clahe_clip": cfg["clahe_clip"], "clahe_grid": cfg["clahe_grid"]}

    elif profile_id in ("upscale_2x_clahe", "otsu_2x", "adaptive_2x", "sharpen_clahe_2x"):
        scale = int(cfg["scale"])
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
        params = {"scale": scale, "interpolation": "INTER_CUBIC"}

        if profile_id == "upscale_2x_clahe":
            clahe = cv2.createCLAHE(clipLimit=cfg["clahe_clip"], tileGridSize=cfg["clahe_grid"])
            result_img = clahe.apply(upscaled)
            params.update({"clahe_clip": cfg["clahe_clip"], "clahe_grid": cfg["clahe_grid"]})

        elif profile_id == "otsu_2x":
            k = cfg.get("blur_ksize", 3)
            s = cfg.get("blur_sigma", 0.5)
            blurred = cv2.GaussianBlur(upscaled, (k, k), s)
            _, result_img = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            params.update({"blur_ksize": k, "blur_sigma": s, "threshold": "otsu"})

        elif profile_id == "adaptive_2x":
            result_img = cv2.adaptiveThreshold(
                upscaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, cfg["block_size"], cfg["c_value"],
            )
            params.update({"block_size": cfg["block_size"], "c_value": cfg["c_value"]})

        elif profile_id == "sharpen_clahe_2x":
            clahe = cv2.createCLAHE(clipLimit=cfg["clahe_clip"], tileGridSize=cfg["clahe_grid"])
            equalized = clahe.apply(upscaled)
            blur = cv2.GaussianBlur(equalized, (0, 0), cfg.get("sharpen_radius", 1))
            result_img = cv2.addWeighted(equalized, 1.0 + cfg.get("sharpen_amount", 1.0), blur, -cfg.get("sharpen_amount", 1.0), 0)
            params.update({"clahe_clip": cfg["clahe_clip"], "sharpen_amount": cfg.get("sharpen_amount", 1.0)})

        else:
            result_img = upscaled

    else:
        raise ValueError(f"Unknown profile: {profile_id}")

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    h, w = result_img.shape[:2] if len(result_img.shape) >= 2 else (result_img.shape[0], 1)

    return PreprocessResult(
        profile_id=profile_id,
        image=result_img,
        width=w,
        height=h,
        elapsed_ms=elapsed_ms,
        params=params,
    )


def save_temp(pp_result: PreprocessResult, output_dir: str) -> str:
    """Save preprocessed image to temp dir, return path."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"preprocessed_{pp_result.profile_id}.png")
    cv2.imwrite(path, pp_result.image)
    return path
