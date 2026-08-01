"""OCR 模組 — 從圖片取出文字（RapidOCR / ONNX Runtime）。

設計原則:
  * 引擎 lazy 載入 — 首次呼叫才 import onnxruntime，不拖慢 app 啟動。
  * 純記憶體處理 — 輸入 bytes、輸出文字行，圖片不落盤。
  * 輸出每行含信心度 (score)，由呼叫端決定如何呈現。
"""
from __future__ import annotations

import threading
from typing import Any

_engine: Any | None = None
_engine_lock = threading.Lock()

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB — 避免超大圖片拖垮記憶體


class OcrError(Exception):
    """OCR 處理失敗（引擎載入失敗、圖片無法解碼等）。"""


class ImageTooLargeError(OcrError):
    """圖片超過大小上限。"""


def _get_engine() -> Any:
    """取得（並快取）RapidOCR 引擎。執行緒安全。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:  # pragma: no cover — 打包缺依賴時
                    raise OcrError(f"OCR 引擎未安裝 (rapidocr_onnxruntime): {exc}") from exc
                try:
                    _engine = RapidOCR()
                except Exception as exc:
                    raise OcrError(f"OCR 引擎載入失敗: {exc}") from exc
    return _engine


def extract_text(image_bytes: bytes) -> list[dict[str, Any]]:
    """從圖片 bytes 取出文字行。

    Args:
        image_bytes: 圖片原始 bytes（png / jpg / webp / bmp 等）。

    Returns:
        list of {text, score, box} — 依版面順序排列；
        無文字時回傳空 list。

    Raises:
        ImageTooLargeError: 圖片超過 MAX_IMAGE_BYTES。
        OcrError: 引擎載入失敗或圖片無法解碼。
    """
    if not image_bytes:
        raise OcrError("空白圖片")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(
            f"圖片超過大小上限 ({MAX_IMAGE_BYTES // (1024 * 1024)} MB)"
        )

    engine = _get_engine()
    try:
        # RapidOCR 接受 bytes，內部以 cv2 解碼；回傳 (result, elapse)
        result, _elapse = engine(image_bytes)
    except Exception as exc:
        raise OcrError(f"OCR 辨識失敗: {exc}") from exc

    if not result:
        return []

    lines: list[dict[str, Any]] = []
    for item in result:
        # item = [box([[x1,y1],[x2,y2],...]), text, score]
        box, text, score = item[0], item[1], item[2]
        lines.append(
            {
                "text": str(text),
                "score": round(float(score), 4),
                "box": [[float(x), float(y)] for x, y in box],
            }
        )
    return lines


def extract_text_joined(image_bytes: bytes, sep: str = "\n") -> str:
    """從圖片取出文字並以 sep 連接（方便直接貼進工作台）。"""
    lines = extract_text(image_bytes)
    return sep.join(line["text"] for line in lines)
