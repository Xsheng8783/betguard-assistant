"""PaddleOCR subprocess worker — stdin JSON → OCR → stdout JSON.

Protocol: betguard.vision.worker.v1
This is the ONLY module that imports paddleocr.
Run via: BETGUARD_OCR_PYTHON tools/vision/paddleocr_worker.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback


PROTOCOL_VERSION = "betguard.vision.worker.v1"
STDOUT_MAX = 5 * 1024 * 1024  # 5 MiB


def _send_response(obj: dict) -> None:
    """Write a single JSON object to stdout. Only one JSON per invocation."""
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw) > STDOUT_MAX:
        # Truncate items to fit
        obj["items"] = obj.get("items", [])[:10]
        obj["warnings"] = obj.get("warnings", []) + ["OCR_OUTPUT_TOO_LARGE"]
        raw = json.dumps(obj, ensure_ascii=False, default=str)
        if len(raw) > STDOUT_MAX:
            raw = json.dumps({"ok": False, "error": {"code": "OCR_OUTPUT_TOO_LARGE", "message": "輸出過大"}}, ensure_ascii=False)
    sys.stdout.buffer.write(raw.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.flush()


def _error(code: str, message: str, request_id: str = "", retryable: bool = False) -> dict:
    return {
        "ok": False,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def _validate_request(req: dict) -> str:
    """Validate request. Returns error message or empty string."""
    if req.get("protocol_version") != PROTOCOL_VERSION:
        return f"protocol_version mismatch: expected {PROTOCOL_VERSION}"
    if not req.get("request_id"):
        return "missing request_id"
    image_path = req.get("image_path", "")
    if not image_path:
        return "missing image_path"
    if "://" in image_path:
        return "image_path must be local file, not URL"
    if not os.path.exists(image_path):
        return f"image_path does not exist"
    if not os.path.isfile(image_path):
        return "image_path must be a regular file"
    if os.path.islink(image_path):
        return "image_path must not be a symbolic link"
    if os.path.isdir(image_path):
        return "image_path must not be a directory"
    return ""


def _extract_ocr_items(raw_result: list) -> list[dict]:
    """Extract items from PaddleOCR 3.7 predict() result.

    PaddleOCR 3.7 returns list of dicts. Each dict has 'res' key with:
      rec_texts, rec_scores, rec_polys, rec_boxes
    """
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
        scores = scores if len(scores) == n else [0.0] * n
        polys = polys if len(polys) == n else [[] for _ in range(n)]
        boxes = boxes if len(boxes) == n else [[] for _ in range(n)]

        for i in range(n):
            item: dict = {
                "text": str(texts[i]),
                "score": _to_float(scores[i]),
                "polygon": [],
                "box": [],
            }
            # Convert polys
            poly = polys[i]
            if hasattr(poly, "tolist"):
                poly = poly.tolist()
            if isinstance(poly, (list, tuple)):
                # Ensure list of [x,y] pairs
                flat = []
                for v in poly:
                    if hasattr(v, "tolist"):
                        v = v.tolist()
                    if hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
                        flat.append([_to_float(x) for x in v])
                    else:
                        flat.append(_to_float(v))
                # Check if flat list of coords → regroup into pairs
                if flat and not isinstance(flat[0], list):
                    flat = [[flat[j], flat[j+1]] for j in range(0, len(flat) - 1, 2)]
                item["polygon"] = flat

            # Convert boxes
            b = boxes[i]
            if hasattr(b, "tolist"):
                b = b.tolist()
            if isinstance(b, (list, tuple)):
                item["box"] = [_to_float(v) for v in b]

            items.append(item)
    return items


def _to_float(v) -> float:
    """Safely convert numpy scalar or other type to float."""
    if hasattr(v, "item"):
        return float(v.item())
    return float(v)


def main() -> None:
    request_id = ""
    try:
        raw_input = sys.stdin.buffer.read(1024 * 1024)  # max 1 MiB input
        if not raw_input:
            _send_response(_error("OCR_REQUEST_INVALID", "empty stdin"))
            return

        req = json.loads(raw_input.decode("utf-8"))
        request_id = req.get("request_id", "")

        err = _validate_request(req)
        if err:
            _send_response(_error("OCR_REQUEST_INVALID", err, request_id))
            return

        image_path = req["image_path"]
        options = req.get("options", {})

        t0 = time.perf_counter()

        # Import PaddleOCR (may trigger model download on first run)
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            _send_response(_error("OCR_ENGINE_UNAVAILABLE", "PaddleOCR not installed in worker environment", request_id))
            return

        try:
            ocr = PaddleOCR(
                device=options.get("device", "cpu"),
                text_detection_model_name=options.get("text_detection_model_name", "PP-OCRv5_mobile_det"),
                text_recognition_model_name=options.get("text_recognition_model_name", "PP-OCRv5_mobile_rec"),
                use_doc_orientation_classify=options.get("use_doc_orientation_classify", False),
                use_doc_unwarping=options.get("use_doc_unwarping", False),
                use_textline_orientation=options.get("use_textline_orientation", False),
                enable_hpi=False,
                enable_mkldnn=options.get("enable_mkldnn", True),
                cpu_threads=options.get("cpu_threads", 4),
            )
        except Exception as e:
            _send_response(_error("OCR_MODEL_INIT_FAILED", "Failed to initialize PaddleOCR", request_id))
            return

        # Run OCR
        try:
            result = ocr.predict(image_path)
        except Exception as e:
            _send_response(_error("OCR_INFERENCE_FAILED", "OCR inference failed", request_id))
            return

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Extract items
        items = _extract_ocr_items(result)

        # Get versions
        try:
            import paddle
            paddle_ver = paddle.__version__
        except Exception:
            paddle_ver = "unknown"
        try:
            import paddleocr
            ocr_ver = paddleocr.__version__
        except Exception:
            ocr_ver = "unknown"

        _send_response({
            "ok": True,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "engine": {
                "name": "paddleocr",
                "paddle_version": str(paddle_ver),
                "paddleocr_version": str(ocr_ver),
                "device": options.get("device", "cpu"),
                "detection_model": options.get("text_detection_model_name", "PP-OCRv5_mobile_det"),
                "recognition_model": options.get("text_recognition_model_name", "PP-OCRv5_mobile_rec"),
            },
            "elapsed_ms": round(elapsed_ms, 1),
            "items": items,
            "warnings": [],
        })

    except json.JSONDecodeError:
        _send_response(_error("OCR_REQUEST_INVALID", "invalid JSON", request_id))
    except Exception:
        _send_response(_error("OCR_PROCESS_FAILED", "unexpected worker error", request_id))


if __name__ == "__main__":
    main()
