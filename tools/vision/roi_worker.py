"""ROI diagnostic worker — full-group crops, 3 versions, OCR per version.

Protocol: betguard.vision.roi.v1
Runs in the OCR venv (imports cv2 + paddleocr). stdout carries exactly one
JSON object (OS-level fd isolation like the production worker).
"""

from __future__ import annotations

import json
import os
import sys
import time

try:
    from roi_preprocess import VERSION_NAMES, make_versions
except ImportError:
    from tools.vision.roi_preprocess import VERSION_NAMES, make_versions

ROI_PROTOCOL_VERSION = "betguard.vision.roi.v1"

# ── stdout isolation (native C logs must not pollute protocol JSON) ──────────

_protocol_fd = None


def _setup_protocol_fd() -> None:
    global _protocol_fd
    _protocol_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(_protocol_fd, False)


def _send_response(obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    os.write(_protocol_fd, raw)


class _FdRedirect:
    """Temporarily point OS fd 1 → fd 2 (native logs go to stderr)."""

    def __enter__(self):
        sys.stdout.flush()
        sys.stderr.flush()
        self._saved = os.dup(1)
        os.dup2(2, 1)
        return self

    def __exit__(self, *exc):
        sys.stdout.flush()
        os.dup2(self._saved, 1)
        os.close(self._saved)
        return False


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
        for i in range(len(texts)):
            poly = polys[i] if i < len(polys) else []
            if hasattr(poly, "tolist"):
                poly = poly.tolist()
            if isinstance(poly, (list, tuple)) and poly and isinstance(poly[0], (list, tuple)):
                poly = [[float(v) for v in p] for p in poly]
            elif isinstance(poly, (list, tuple)) and poly:
                poly = [[float(poly[j]), float(poly[j + 1])] for j in range(0, len(poly) - 1, 2)]
            b = boxes[i] if i < len(boxes) else []
            if hasattr(b, "tolist"):
                b = b.tolist()
            items.append({
                "text": str(texts[i]),
                "score": float(scores[i]) if i < len(scores) else 0.0,
                "polygon": poly if isinstance(poly, list) else [],
                "box": [float(v) for v in b] if isinstance(b, (list, tuple)) and b else [],
            })
    return items


def main() -> int:
    _setup_protocol_fd()

    try:
        raw = sys.stdin.buffer.read()
        req = json.loads(raw.decode("utf-8"))
    except Exception:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION,
            "request_id": "", "error": {"code": "REQUEST_INVALID", "message": "invalid request", "retryable": False},
        })
        return 0

    request_id = req.get("request_id", "")
    if req.get("protocol_version") != ROI_PROTOCOL_VERSION:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION,
            "request_id": request_id,
            "error": {"code": "PROTOCOL_MISMATCH", "message": "protocol version mismatch", "retryable": False},
        })
        return 0

    image_path = req.get("image_path", "")
    if not image_path or not os.path.isfile(image_path):
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
            "error": {"code": "IMAGE_NOT_FOUND", "message": "image not found", "retryable": False},
        })
        return 0

    out_dir = req.get("output_dir", "")
    if not out_dir:
        out_dir = os.path.join(os.path.dirname(image_path), "roi_diagnostic_crops")
    os.makedirs(out_dir, exist_ok=True)

    regions = req.get("regions", [])
    if not regions:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
            "error": {"code": "NO_REGIONS", "message": "no regions in request", "retryable": False},
        })
        return 0

    t0 = time.perf_counter()

    try:
        import cv2
    except ImportError:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
            "error": {"code": "PREPROCESS_UNAVAILABLE", "message": "cv2 unavailable", "retryable": False},
        })
        return 0

    img = cv2.imread(image_path)
    if img is None:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
            "error": {"code": "IMAGE_LOAD_FAILED", "message": "cannot load image", "retryable": False},
        })
        return 0

    engine = None
    with _FdRedirect():
        try:
            from paddleocr import PaddleOCR
            engine = PaddleOCR(
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
        except Exception as e:
            _send_response({
                "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
                "error": {"code": "ENGINE_INIT_FAILED", "message": "OCR engine init failed", "retryable": True},
            })
            return 0

    results = []
    try:
        with _FdRedirect():
            for reg in regions:
                gid = str(reg.get("id", "region"))
                bbox = [int(v) for v in reg.get("bbox", [0, 0, 0, 0])]
                try:
                    version_paths = make_versions(img, bbox, out_dir, f"roi_{gid}", padding=8)
                except Exception:
                    _send_response({
                        "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
                        "error": {"code": "CROP_FAILED", "message": "crop failed", "retryable": False},
                    })
                    return 0

                per_version: dict[str, dict] = {}
                for vname, vpath in version_paths.items():
                    try:
                        raw_result = engine.predict(vpath)
                        items = _extract_items(raw_result)
                    except Exception:
                        items = []
                    per_version[vname] = {
                        "items": items,
                        "ocr_text": " ".join(it["text"] for it in items),
                        "item_count": len(items),
                    }
                results.append({
                    "id": gid,
                    "bbox": bbox,
                    "versions": {
                        vname: {
                            "file": version_paths[vname],
                            "ocr_text": per_version[vname]["ocr_text"],
                            "item_count": per_version[vname]["item_count"],
                            "items": per_version[vname]["items"],
                        }
                        for vname in VERSION_NAMES
                    },
                })
    except Exception:
        _send_response({
            "ok": False, "protocol_version": ROI_PROTOCOL_VERSION, "request_id": request_id,
            "error": {"code": "INFERENCE_FAILED", "message": "inference failed", "retryable": True},
        })
        return 0

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _send_response({
        "ok": True,
        "protocol_version": ROI_PROTOCOL_VERSION,
        "request_id": request_id,
        "engine": {"name": "paddleocr", "device": "cpu"},
        "elapsed_ms": round(elapsed_ms, 1),
        "image_size": [int(img.shape[1]), int(img.shape[0])],
        "regions": results,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
