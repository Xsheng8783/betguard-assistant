"""GLM-OCR 引擎 — 本地 transformers 推論 + 手寫牌單後處理。

設計原則:
  * 引擎 lazy 載入 — 首次呼叫才 import torch/transformers，不拖慢 app 啟動。
  * 純記憶體處理 — 輸入 bytes、輸出文字行，圖片不落盤。
  * 純 CPU 推論 — 不依賴 GPU（最低設備假設）。
  * 後處理白名單 — 真實牌單只有數字、乘號(x)、中文「一二三四」與小數點；
    其餘符號（> < ≡ □ ≥ 等）是模型把筆跡/塗改當成符號，一律清除。
"""
from __future__ import annotations

import io
import os
import re
import threading
from typing import Any

from .ocr import OcrError, ImageTooLargeError, MAX_IMAGE_BYTES

# 預設模型路徑：優先環境變數；fallback 到 src 旁的 glm-ocr-model 目錄
# （打包後 PyInstaller 會把模型放進 _internal，用 sys._MEIPASS 解析）。
import sys as _sys

_def_model_dir = os.path.join(
    getattr(_sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "glm-ocr-model",
)
_MODEL_PATH = os.environ.get("BETGUARD_GLM_MODEL_PATH", _def_model_dir)

# 白名單：數字、乘號、中文一二三四、小數點、空白。
# 其餘一律視為筆跡噪音，替換成空格（避免兩側數字黏連成 53 這種假數字）。
_KEEP_RE = re.compile(r"[^0-9x×一二三四.\s]")

_engine: Any | None = None
_engine_lock = threading.Lock()
_infer_lock = threading.Lock()  # transformers 推論非 thread-safe，串行化


class GlmOcrError(OcrError):
    """GLM-OCR 引擎專屬錯誤。"""


def _get_engine() -> Any:
    """取得（並快取）GLM-OCR 模型。執行緒安全。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                try:
                    from transformers import (
                        AutoModelForImageTextToText,
                        AutoProcessor,
                    )
                except ImportError as exc:  # pragma: no cover — 打包缺依賴時
                    raise OcrError(f"GLM-OCR 引擎未安裝 (transformers): {exc}") from exc
                try:
                    processor = AutoProcessor.from_pretrained(_MODEL_PATH)
                    model = AutoModelForImageTextToText.from_pretrained(
                        _MODEL_PATH,
                        dtype="auto",
                        attn_implementation="sdpa",  # CPU 上比 eager 快 ~1.7x
                    )
                    model.eval()
                    _engine = (processor, model)
                except Exception as exc:
                    raise OcrError(f"GLM-OCR 引擎載入失敗: {exc}") from exc
    return _engine


def clean_glm_text(raw: str) -> str:
    """過濾 GLM-OCR 原始輸出：只保留數字/x/一二三四/小數點與空白。

    Args:
        raw: 模型原始輸出文字（可能含 > < ≡ □ 等筆跡噪音）。

    Returns:
        過濾後文字，保留換行結構；空內容回傳空字串。
    """
    lines = []
    for line in raw.splitlines():
        # 噪音字元 → 空格（保留 token 邊界，避免 5 和 3 黏成 53）
        cleaned = _KEEP_RE.sub(" ", line)
        # 壓縮連續空白為單一空格
        cleaned = " ".join(cleaned.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def extract_text(image_bytes: bytes) -> list[dict[str, Any]]:
    """從圖片 bytes 取出文字行（GLM-OCR 引擎）。

    Args:
        image_bytes: 圖片原始 bytes（png / jpg / webp / bmp 等）。

    Returns:
        list of {text, score, box} — score/box 為 None/[]（GLM-OCR 不提供
        信心度與位置資訊）；無文字時回傳空 list。

    Raises:
        ImageTooLargeError: 圖片超過大小上限。
        OcrError: 引擎載入失敗或圖片無法解碼。
    """
    if not image_bytes:
        raise OcrError("空白圖片")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(
            f"圖片超過大小上限 ({MAX_IMAGE_BYTES // (1024 * 1024)} MB)"
        )

    processor, model = _get_engine()

    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise OcrError(f"圖片無法解碼: {exc}") from exc

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": "Text Recognition:"},
            ],
        }
    ]

    try:
        inputs = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs.pop("token_type_ids", None)
        with _infer_lock:
            generated_ids = model.generate(**inputs, max_new_tokens=4096)
        output_text = processor.decode(
            generated_ids[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
    except Exception as exc:
        raise OcrError(f"GLM-OCR 辨識失敗: {exc}") from exc

    cleaned = clean_glm_text(output_text)
    if not cleaned:
        return []

    return [
        {"text": line, "score": None, "box": []}
        for line in cleaned.splitlines()
        if line.strip()
    ]
