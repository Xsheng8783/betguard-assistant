"""Test: single model call that outputs sections WITH per-token bboxes."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from betguard.vision.providers.qwen_dashscope import (
    DEFAULT_MODEL as MODEL,
    DEFAULT_URL as URL,
    QwenAPIKeyMissing,
    QwenClientError,
    QwenSchemaError,
    QwenTimeoutError,
    extract_json,
    get_default_client,
    load_normalized_image,
)
from betguard.vision.qwen_prompts import (
    COLUMN_COMBO_PROMPT,
    COLUMN_COMBO_PROMPT_VERSION,
    FOCUSED_PROMPT,
    FOCUSED_PROMPT_VERSION,
    PLAY_MARK_PROMPT,
    PLAY_MARK_PROMPT_VERSION,
    PROMPT,
    PROMPT_VERSION,
    TASK_TYPE_COLUMN_COMBO,
    TASK_TYPE_FOCUSED_CROP,
    TASK_TYPE_FULL_PAGE,
    TASK_TYPE_PLAY_MARK,
)


def _qwen_chat(
    b64: str,
    mime: str,
    prompt: str,
    *,
    max_tokens: int,
    image_sha256: str | None = None,
    crop_box: list[int] | None = None,
    scale: int | None = None,
    image_variant: str | None = None,
    prompt_version: str = PROMPT_VERSION,
    task_type: str = TASK_TYPE_FULL_PAGE,
    effective_crop_sha256: str | None = None,
    request_id: str | None = None,
    retries: int = 2,
) -> tuple[str, dict]:
    """Compatibility wrapper around the one formal Qwen client."""
    return get_default_client().chat(
        b64,
        mime,
        prompt,
        max_tokens=max_tokens,
        image_sha256=image_sha256,
        crop_box=crop_box,
        scale=scale,
        image_variant=image_variant,
        prompt_version=prompt_version,
        task_type=task_type,
        effective_crop_sha256=effective_crop_sha256,
        request_id=request_id,
        retries=retries,
    )


def _image_sha256(image_path: Path) -> str:
    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def load_normalized(image_path: Path):
    """EXIF-transposed RGB image + PNG bytes of the ACTUAL image sent to the
    model (full page), so hash/orientation always match the request."""
    return load_normalized_image(image_path)


def _gray_enhanced(img):
    from PIL import Image, ImageEnhance, ImageOps

    gray = ImageOps.grayscale(img).convert("RGB")
    return ImageEnhance.Contrast(gray).enhance(1.3)


def call(image_path: Path, *, meta: dict | None = None) -> str:
    norm, png_bytes = load_normalized(image_path)
    content, m = _qwen_chat(
        base64.b64encode(png_bytes).decode(),
        "image/png",
        PROMPT,
        max_tokens=8000,
        image_sha256=hashlib.sha256(png_bytes).hexdigest(),
        prompt_version=PROMPT_VERSION,
        task_type=TASK_TYPE_FULL_PAGE,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_with_prompt(
    image_path: Path,
    prompt: str,
    *,
    max_tokens: int = 8000,
    meta: dict | None = None,
) -> str:
    """Single full-page call with a caller-supplied prompt (for prompt A/B)."""
    norm, png_bytes = load_normalized(image_path)
    content, m = _qwen_chat(
        base64.b64encode(png_bytes).decode(),
        "image/png",
        prompt,
        max_tokens=max_tokens,
        image_sha256=hashlib.sha256(png_bytes).hexdigest(),
        prompt_version=PROMPT_VERSION,
        task_type=TASK_TYPE_FULL_PAGE,
    )
    if meta is not None:
        meta.update(m)
    return content



def call_column_combo_crop(
    image_path: Path,
    bbox: list[int],
    *,
    pad_x: int = 70,
    pad_y: int = 55,
    scale: int = 3,
    save_path: Path | None = None,
    box: list[int] | None = None,
    meta: dict | None = None,
    variant: str = "original_3x",
    request_id: str | None = None,
) -> str:
    """Stage-2 column-combo read: crop the FULL column grid region (numbers +
    play mark), upscale 3x, PNG, ask the column_combo prompt."""
    from io import BytesIO
    from PIL import Image

    img, png_bytes = load_normalized(image_path)
    if box is not None:
        bx1, by1, bx2, by2 = [int(v) for v in box]
        crop_box = (
            max(0, bx1),
            max(0, by1),
            min(img.width, bx2),
            min(img.height, by2),
        )
    else:
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        half_w = max((x2 - x1) // 2 + pad_x, 200)
        half_h = max((y2 - y1) // 2 + pad_y, 180)
        crop_box = (
            max(0, cx - half_w),
            max(0, cy - half_h),
            min(img.width, cx + half_w),
            min(img.height, cy + half_h),
        )
    base = _gray_enhanced(img) if variant == "gray_enhanced_3x" else img
    crop = base.crop(crop_box)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    if save_path is not None:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(save_path, format="PNG")
    buf = BytesIO()
    crop.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    b64 = base64.b64encode(crop_bytes).decode()
    content, m = _qwen_chat(
        b64, "image/png", COLUMN_COMBO_PROMPT,
        max_tokens=600,
        image_sha256=hashlib.sha256(crop_bytes).hexdigest(),
        crop_box=list(crop_box),
        scale=scale,
        image_variant=variant,
        prompt_version=COLUMN_COMBO_PROMPT_VERSION,
        task_type=TASK_TYPE_COLUMN_COMBO,
        request_id=request_id,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_play_mark_crop(
    image_path: Path,
    bbox: list[int],
    *,
    pad_x: int = 60,
    pad_y: int = 45,
    scale: int = 3,
    save_path: Path | None = None,
    box: list[int] | None = None,
    meta: dict | None = None,
    variant: str = "original_3x",
    request_id: str | None = None,
) -> str:
    """Stage-2 read: crop the right-side play zone with FULL vertical extent
    (stacked digits often extend above/below the main number row), upscale
    3x and send as PNG (no lossy JPEG) with the structured play_mark prompt."""
    from io import BytesIO
    from PIL import Image

    img, png_bytes = load_normalized(image_path)
    if box is not None:
        bx1, by1, bx2, by2 = [int(v) for v in box]
        crop_box = (
            max(0, bx1),
            max(0, by1),
            min(img.width, bx2),
            min(img.height, by2),
        )
    else:
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        half_w = max((x2 - x1) // 2 + pad_x, 170)
        half_h = max((y2 - y1) // 2 + pad_y, 170)
        crop_box = (
            max(0, cx - half_w),
            max(0, cy - half_h),
            min(img.width, cx + half_w),
            min(img.height, cy + half_h),
        )
    base = _gray_enhanced(img) if variant == "gray_enhanced_3x" else img
    crop = base.crop(crop_box)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    if save_path is not None:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(save_path, format="PNG")
    buf = BytesIO()
    crop.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    b64 = base64.b64encode(crop_bytes).decode()
    content, m = _qwen_chat(
        b64, "image/png", PLAY_MARK_PROMPT,
        max_tokens=400,
        image_sha256=hashlib.sha256(crop_bytes).hexdigest(),
        crop_box=list(crop_box),
        scale=scale,
        image_variant=variant,
        prompt_version=PLAY_MARK_PROMPT_VERSION,
        task_type=TASK_TYPE_PLAY_MARK,
        request_id=request_id,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_focused_crop(image_path: Path, bbox: list[int], pad: int = 70, min_w: int = 280, min_h: int = 190) -> str:
    """Zoomed re-read of one token region (e.g. a suspiciously tall category
    token) to recover stacked digits the full-page pass missed."""
    from io import BytesIO
    from PIL import Image

    img = Image.open(image_path)
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    half_w = max((x2 - x1) // 2 + pad, min_w // 2)
    half_h = max((y2 - y1) // 2 + pad, min_h // 2)
    box = (
        max(0, cx - half_w),
        max(0, cy - half_h),
        min(img.width, cx + half_w),
        min(img.height, cy + half_h),
    )
    buf = BytesIO()
    img.crop(box).save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    crop_sha = hashlib.sha256(crop_bytes).hexdigest()
    content, _ = _qwen_chat(
        base64.b64encode(crop_bytes).decode(),
        "image/png",
        FOCUSED_PROMPT,
        max_tokens=300,
        image_sha256=crop_sha,
        crop_box=list(box),
        scale=1,
        image_variant="original_1x",
        prompt_version=FOCUSED_PROMPT_VERSION,
        task_type=TASK_TYPE_FOCUSED_CROP,
        effective_crop_sha256=crop_sha,
        retries=0,
    )
    return content


def main() -> None:
    img = Path(os.environ["BETGUARD_DATASET"]) / "raw" / "sample-011.jpg"
    content = call(img)
    (Path(__file__).resolve().parent / "ab_results" / "prelim" / "sample-011-combined.json").write_text(
        content, encoding="utf-8")
    print(content[:1500])


if __name__ == "__main__":
    main()
