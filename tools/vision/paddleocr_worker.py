"""PaddleOCR subprocess worker — stdin JSON → OCR → stdout JSON.

Protocol: betguard.vision.worker.v1
This is the ONLY module that imports paddleocr.
Run via: BETGUARD_OCR_PYTHON tools/vision/paddleocr_worker.py

stdout isolation: before any Paddle imports, we dup the original stdout fd
(protocol_fd). All Paddle/PaddleX/oneDNN native C-level writes to fd 1 are
redirected to fd 2 via dup2. The final JSON response is written exclusively
through protocol_fd via os.write(), ensuring stdout contains exactly one
JSON object.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time


PROTOCOL_VERSION = "betguard.vision.worker.v1"
STDOUT_MAX = 5 * 1024 * 1024  # 5 MiB


def _send_response(obj: dict) -> None:
    """Write a single JSON object via the saved protocol_fd."""
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw) > STDOUT_MAX:
        obj["items"] = obj.get("items", [])[:10]
        obj["warnings"] = obj.get("warnings", []) + ["OCR_OUTPUT_TOO_LARGE"]
        raw = json.dumps(obj, ensure_ascii=False, default=str)
        if len(raw) > STDOUT_MAX:
            raw = json.dumps({"ok": False, "error": {"code": "OCR_OUTPUT_TOO_LARGE", "message": "輸出過大"}}, ensure_ascii=False)
    data = raw.encode("utf-8") + b"\n"
    os.write(_protocol_fd, data)


def _error(code: str, message: str, request_id: str = "", retryable: bool = False) -> dict:
    return {
        "ok": False,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def _validate_request(req: dict) -> str:
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
        return "image_path does not exist"
    if not os.path.isfile(image_path):
        return "image_path must be a regular file"
    if os.path.islink(image_path):
        return "image_path must not be a symbolic link"
    if os.path.isdir(image_path):
        return "image_path must not be a directory"
    return ""


def _extract_ocr_items(raw_result: list) -> list[dict]:
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
            item: dict = {"text": str(texts[i]), "score": _to_float(scores[i]), "polygon": [], "box": []}
            poly = polys[i]
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
                item["polygon"] = flat
            b = boxes[i]
            if hasattr(b, "tolist"):
                b = b.tolist()
            if isinstance(b, (list, tuple)):
                item["box"] = [_to_float(v) for v in b]
            items.append(item)
    return items


def _to_float(v) -> float:
    if hasattr(v, "item"):
        return float(v.item())
    return float(v)


# ── OS-level fd redirect context manager ─────────────────────────────────────


@contextlib.contextmanager
def _redirect_fd1_to_fd2():
    """Redirect OS file descriptor 1 (stdout) to fd 2 (stderr).

    This catches C/C++ native writes (Paddle/oneDNN) that bypass Python's
    sys.stdout. Use *before* any Paddle imports/init/predict.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        # Also redirect Python-level stdout for extra safety
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


# ── Protocol fd (must be set BEFORE any Paddle code runs) ────────────────────


_protocol_fd: int = -1


def _setup_protocol_fd() -> None:
    """Duplicate the original stdout fd for protocol output.

    Must be called after stdin is read but before any Paddle imports.
    """
    global _protocol_fd
    _protocol_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(_protocol_fd, False)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    request_id = ""
    try:
        raw_input = sys.stdin.buffer.read(1024 * 1024)
        if not raw_input:
            _send_response(_error("OCR_REQUEST_INVALID", "empty stdin"))
            return

        req = json.loads(raw_input.decode("utf-8"))
        request_id = req.get("request_id", "")

        err = _validate_request(req)
        if err:
            _send_response(_error("OCR_REQUEST_INVALID", err, request_id))
            return

        # ── Save protocol fd BEFORE any fd redirect ──
        _setup_protocol_fd()

        image_path = req["image_path"]
        options = req.get("options", {})

        t0 = time.perf_counter()

        # ── OS-level fd 1→2 redirect for all Paddle code ──
        with _redirect_fd1_to_fd2():
            try:
                from paddleocr import PaddleOCR
            except ImportError:
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
                    enable_mkldnn=options.get("enable_mkldnn", False),
                    cpu_threads=options.get("cpu_threads", 4),
                )
            except Exception:
                _send_response(_error("OCR_MODEL_INIT_FAILED", "Failed to initialize PaddleOCR", request_id))
                return

            try:
                result = ocr.predict(image_path)
            except Exception:
                _send_response(_error("OCR_INFERENCE_FAILED", "OCR inference failed", request_id))
                return

            # Version checks
            try:
                import paddle
                paddle_ver = paddle.__version__
            except Exception:
                paddle_ver = "unknown"
            try:
                import paddleocr as _pocr
                ocr_ver = _pocr.__version__
            except Exception:
                ocr_ver = "unknown"

        # ── fd 1 restored, now build & send response via protocol_fd ──
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        items = _extract_ocr_items(result)

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
    finally:
        # Close protocol fd
        if _protocol_fd >= 0:
            try:
                os.close(_protocol_fd)
            except OSError:
                pass


if __name__ == "__main__":
    main()
