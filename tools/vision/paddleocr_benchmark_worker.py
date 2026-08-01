"""PaddleOCR benchmark worker — runs multiple OCR profiles in one process.

Protocol: betguard.vision.benchmark.v1
stdin JSON → benchmark matrix → stdout JSON
Runs in OCR venv (imports paddleocr, cv2, numpy).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import tempfile
import time

BENCHMARK_VERSION = "betguard.vision.benchmark.v1"
STDOUT_MAX = 5 * 1024 * 1024
MAX_INFERENCES = 14

# ── OS-level fd isolation (same pattern as production worker) ────────────────

_protocol_fd: int = -1


def _setup_protocol_fd() -> None:
    global _protocol_fd
    _protocol_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(_protocol_fd, False)


def _send_response(obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw) > STDOUT_MAX:
        obj["profiles"] = obj.get("profiles", [])[:10]
        obj["warnings"] = obj.get("warnings", []) + ["BENCHMARK_OUTPUT_TOO_LARGE"]
        raw = json.dumps(obj, ensure_ascii=False, default=str)
        if len(raw) > STDOUT_MAX:
            raw = json.dumps({"ok": False, "error": {"code": "BENCHMARK_OUTPUT_TOO_LARGE"}}, ensure_ascii=False)
    os.write(_protocol_fd, raw.encode("utf-8") + b"\n")


@contextlib.contextmanager
def _redirect_fd1_to_fd2():
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


# ── Helpers ──────────────────────────────────────────────────────────────────

_DETECTION_PROFILES = {
    "balanced": {
        "text_det_limit_side_len": 960,
        "text_det_limit_type": "min",
        "text_det_thresh": 0.4,
        "text_det_box_thresh": 0.6,
        "text_det_unclip_ratio": 1.5,
        "text_rec_score_thresh": 0.0,
    },
    "high_recall": {
        "text_det_limit_side_len": 1216,
        "text_det_limit_type": "min",
        "text_det_thresh": 0.3,
        "text_det_box_thresh": 0.5,
        "text_det_unclip_ratio": 1.5,
        "text_rec_score_thresh": 0.0,
    },
    "experimental_low_box": {
        "text_det_limit_side_len": 1216,
        "text_det_limit_type": "min",
        "text_det_thresh": 0.25,
        "text_det_box_thresh": 0.4,
        "text_det_unclip_ratio": 1.8,
        "text_rec_score_thresh": 0.0,
        "_experimental": True,
    },
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


def _has_single_low_conf_cjk(items: list[dict]) -> bool:
    """Check if result is a single low-confidence CJK character."""
    if len(items) != 1:
        return False
    item = items[0]
    text = item.get("text", "")
    if len(text) != 1:
        return False
    cp = ord(text)
    is_cjk = (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF)
    return is_cjk and item.get("score", 0.0) < 0.3


def _full_image_box(items: list[dict], img_w: int, img_h: int) -> bool:
    """Check if a single box covers nearly the entire image."""
    if len(items) != 1:
        return False
    b = items[0].get("box", [])
    if len(b) < 4:
        return False
    box_w = abs(b[2] - b[0]) if len(b) >= 3 else 0
    box_h = abs(b[3] - b[1]) if len(b) >= 4 else 0
    return box_w > img_w * 0.9 or box_h > img_h * 0.9


def _compute_heuristic(items: list[dict], img_w: int, img_h: int) -> dict:
    detected = len(items)
    non_empty = sum(1 for it in items if it.get("text", "").strip())
    scores = [it.get("score", 0.0) for it in items]
    mean_conf = sum(scores) / len(scores) if scores else 0.0
    max_conf = max(scores) if scores else 0.0
    digit_count = sum(1 for it in items for ch in it.get("text", "") if ch.isdigit())
    single_cjk = _has_single_low_conf_cjk(items)
    full_box = _full_image_box(items, img_w, img_h)

    hscore = 0.0
    hscore += detected * 10.0
    hscore += non_empty * 15.0
    hscore += mean_conf * 50.0
    hscore += digit_count * 2.0
    if single_cjk:
        hscore -= 30.0
    if full_box:
        hscore -= 40.0
    hscore = max(0.0, hscore)

    return {
        "detected_count": detected,
        "non_empty_count": non_empty,
        "digit_count": digit_count,
        "mean_confidence": round(mean_conf, 4),
        "max_confidence": round(max_conf, 4),
        "heuristic_score": round(hscore, 1),
    }


def _build_ocr(profile_name: str) -> tuple:
    """Build PaddleOCR with detection parameters."""
    import inspect
    from paddleocr import PaddleOCR

    cfg = _DETECTION_PROFILES.get(profile_name, _DETECTION_PROFILES["balanced"])
    kw: dict = {
        "device": "cpu",
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_recognition_model_name": "PP-OCRv5_mobile_rec",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "enable_hpi": False,
        "enable_mkldnn": False,
        "cpu_threads": 4,
    }

    # Try to build with detection params — constructor may or may not accept them.
    # We try constructor first, then fall back to passing at predict() time.
    det_kwargs = {k: v for k, v in cfg.items() if not k.startswith("_")}
    try:
        ocr = PaddleOCR(**kw, **det_kwargs)
    except TypeError:
        ocr = PaddleOCR(**kw)
    return ocr, det_kwargs


def _run_single(
    ocr,
    det_kwargs: dict,
    image_path: str,
    rotation: int,
    preprocess_profile: str,
    detection_profile: str,
    src_w: int,
    src_h: int,
    tmp_dir: str,
) -> dict:
    """Run a single OCR profile. Returns ProfileRun as dict."""
    from ocr_preprocess import load_image, preprocess, save_temp  # type: ignore[import-untyped]
    t0 = time.perf_counter()

    try:
        img = load_image(image_path)
        pp = preprocess(img, preprocess_profile)
        tmp_path = save_temp(pp, tmp_dir)

        # Try predict with detection kwargs
        try:
            result = ocr.predict(tmp_path, **det_kwargs)
        except TypeError:
            result = ocr.predict(tmp_path)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        items = _extract_items(result)
        h = _compute_heuristic(items, pp.width, pp.height)

        # Transform polygons back to original coords
        scale = pp.params.get("scale", 1.0)
        for item in items:
            poly = item.get("polygon", [])
            if poly and not any(math.isnan(p[0]) or math.isinf(p[0]) for p in poly if len(p) >= 2):
                try:
                    orig_poly = _transform_poly(poly, rotation, src_w, src_h, scale)
                    item["original_polygon"] = orig_poly
                except (ValueError, IndexError):
                    item["original_polygon"] = poly
            item["processed_polygon"] = poly

        raw_text = "\n".join(it["text"] for it in items)

        return {
            "rotation": rotation,
            "preprocess_profile": preprocess_profile,
            "detection_profile": detection_profile,
            "status": "completed",
            "elapsed_ms": round(elapsed_ms, 1),
            "items": items,
            "raw_text": raw_text,
            "scores": [it["score"] for it in items],
            "detected_count": h["detected_count"],
            "non_empty_count": h["non_empty_count"],
            "digit_count": h["digit_count"],
            "mean_confidence": h["mean_confidence"],
            "max_confidence": h["max_confidence"],
            "heuristic_score": h["heuristic_score"],
            "actual_text_det_params": det_kwargs,
            "preprocess_result": {
                "profile_id": pp.profile_id, "width": pp.width, "height": pp.height,
                "elapsed_ms": round(pp.elapsed_ms, 1), "params": pp.params,
            },
            "warnings": [],
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "rotation": rotation,
            "preprocess_profile": preprocess_profile,
            "detection_profile": detection_profile,
            "status": "failed",
            "elapsed_ms": round(elapsed_ms, 1),
            "error_code": "OCR_INFERENCE_FAILED",
            "error_message": str(e)[:200],
            "retryable": False,
            "warnings": [],
        }


def _transform_poly(polygon: list, rotation: int, src_w: int, src_h: int, scale: float) -> list:
    """Transform polygon back to original coordinates."""
    result = []
    for pt in polygon:
        if len(pt) < 2:
            continue
        x, y = float(pt[0]), float(pt[1])
        x, y = x / scale, y / scale
        if rotation == 90:
            x, y = src_h - y, x
        elif rotation == 180:
            x, y = src_w - x, src_h - y
        elif rotation == 270:
            x, y = y, src_w - x
        x = max(0.0, min(float(src_w), x))
        y = max(0.0, min(float(src_h), y))
        result.append([x, y])
    return result


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    request_id = ""
    try:
        raw_input = sys.stdin.buffer.read(1024 * 1024)
        if not raw_input:
            _send_response({"ok": False, "error": {"code": "EMPTY_REQUEST"}})
            return

        req = json.loads(raw_input.decode("utf-8"))
        if req.get("protocol_version") != BENCHMARK_VERSION:
            _send_response({"ok": False, "error": {"code": "PROTOCOL_MISMATCH"}})
            return

        request_id = req.get("request_id", "")
        image_path = req.get("image_path", "")
        if not image_path or not os.path.isfile(image_path):
            _send_response({"ok": False, "error": {"code": "INVALID_IMAGE"}})
            return

        rotations = req.get("rotations", [0, 90, 180, 270])
        preprocess_profiles = req.get("preprocess_profiles", ["original"])
        det_profiles = req.get("detection_profiles", {"balanced": _DETECTION_PROFILES["balanced"]})

        _setup_protocol_fd()

        # Get original dimensions
        import cv2
        orig = cv2.imread(image_path)
        if orig is None:
            _send_response({"ok": False, "error": {"code": "INVALID_IMAGE"}})
            return
        src_h, src_w = orig.shape[:2]

        tmp_dir = tempfile.mkdtemp(prefix="benchmark_")

        with _redirect_fd1_to_fd2():
            from paddleocr import PaddleOCR
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

        t_total = time.perf_counter()
        profiles = []
        inference_count = 0
        phase1_rotations: list[dict] = []

        # ── Phase 1: Rotation benchmark ──
        ocr_balanced, det_kwargs = _build_ocr("balanced")
        with _redirect_fd1_to_fd2():
            for rot in rotations:
                if inference_count >= MAX_INFERENCES:
                    break
                inference_count += 1
                pr = _run_single(ocr_balanced, det_kwargs, image_path, rot, "original", "balanced", src_w, src_h, tmp_dir)
                profiles.append(pr)
                phase1_rotations.append(pr)

        # Select best rotation
        best_rot = 0
        best_h = -1.0
        for pr in phase1_rotations:
            if pr.get("status") == "completed" and pr.get("heuristic_score", 0) > best_h:
                best_h = pr["heuristic_score"]
                best_rot = pr["rotation"]
            elif pr.get("status") == "completed" and pr.get("heuristic_score", 0) == best_h:
                if {0: 0, 90: 1, 180: 2, 270: 3}.get(pr["rotation"], 99) < {0: 0, 90: 1, 180: 2, 270: 3}.get(best_rot, 99):
                    best_rot = pr["rotation"]

        # ── Phase 2: Preprocess benchmark on best rotation ──
        ocr_balanced2, det_kwargs2 = _build_ocr("balanced")
        with _redirect_fd1_to_fd2():
            for pp_name in preprocess_profiles:
                if inference_count >= MAX_INFERENCES:
                    break
                inference_count += 1
                pr = _run_single(ocr_balanced2, det_kwargs2, image_path, best_rot, pp_name, "balanced", src_w, src_h, tmp_dir)
                profiles.append(pr)

        # ── Phase 3: Best 2 preprocess × high_recall + experimental ──
        pp_ranked = sorted(
            [p for p in profiles if p.get("preprocess_profile", "") != "original" and p.get("detection_profile") == "balanced" and p.get("status") == "completed"],
            key=lambda p: -p.get("heuristic_score", 0),
        )
        top_pps = list(dict.fromkeys(p["preprocess_profile"] for p in pp_ranked[:2]))

        for det_name in ["high_recall", "experimental_low_box"]:
            if det_name not in det_profiles and det_name not in _DETECTION_PROFILES:
                continue
            ocr_det, det_kwargs_det = _build_ocr(det_name)
            with _redirect_fd1_to_fd2():
                for pp_name in top_pps:
                    if inference_count >= MAX_INFERENCES:
                        break
                    inference_count += 1
                    pr = _run_single(ocr_det, det_kwargs_det, image_path, best_rot, pp_name, det_name, src_w, src_h, tmp_dir)
                    profiles.append(pr)

        total_elapsed = (time.perf_counter() - t_total) * 1000.0

        # Find best preprocess and detection
        best_pp = ""
        best_det = ""
        best_overall = -1.0
        for p in profiles:
            if p.get("status") == "completed" and p.get("heuristic_score", 0) > best_overall:
                best_overall = p["heuristic_score"]
                best_pp = p.get("preprocess_profile", "")
                best_det = p.get("detection_profile", "")

        _send_response({
            "ok": True,
            "protocol_version": BENCHMARK_VERSION,
            "request_id": request_id,
            "image_path": image_path,
            "engine": {
                "name": "paddleocr",
                "paddle_version": str(paddle_ver),
                "paddleocr_version": str(ocr_ver),
                "device": "cpu",
                "detection_model": "PP-OCRv5_mobile_det",
                "recognition_model": "PP-OCRv5_mobile_rec",
            },
            "total_elapsed_ms": round(total_elapsed, 1),
            "inference_count": inference_count,
            "profiles": profiles,
            "provisional_best_rotation": best_rot,
            "provisional_best_preprocess": best_pp,
            "provisional_best_detection": best_det,
            "benchmark_score": round(best_overall, 1),
            "warnings": [],
        })

    except json.JSONDecodeError:
        _send_response({"ok": False, "error": {"code": "INVALID_JSON"}})
    except Exception:
        _send_response({"ok": False, "error": {"code": "BENCHMARK_FAILED"}})
    finally:
        if _protocol_fd >= 0:
            try:
                os.close(_protocol_fd)
            except OSError:
                pass


if __name__ == "__main__":
    main()
