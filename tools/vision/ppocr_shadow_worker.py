"""PP-OCRv6 worker executed only by the isolated benchmark interpreter."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "betguard.vision.ppocr-worker.v1"
DET_MODEL = "PP-OCRv6_medium_det"
REC_MODEL = "PP-OCRv6_medium_rec"


def _primitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if hasattr(value, "tolist"):
        return _primitive(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _result_payload(result: Any) -> dict[str, Any]:
    value = getattr(result, "json", None)
    if callable(value):
        value = value()
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        return _primitive(value)
    if hasattr(result, "to_dict"):
        value = result.to_dict()
        if isinstance(value, dict):
            return _primitive(value)
    return _primitive(dict(result))


def _bbox(polygon: Any) -> list[float] | None:
    if not isinstance(polygon, list) or len(polygon) < 3:
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return None
    result = [min(xs), min(ys), max(xs), max(ys)]
    if result[0] < 0 or result[1] < 0 or result[2] <= result[0] or result[3] <= result[1]:
        return None
    return result


def _normalize(results: list[Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for result in results:
        raw = _result_payload(result)
        payload = raw.get("res") if isinstance(raw.get("res"), dict) else raw
        texts = payload.get("rec_texts") if isinstance(payload.get("rec_texts"), list) else []
        scores = payload.get("rec_scores") if isinstance(payload.get("rec_scores"), list) else []
        polygons = payload.get("rec_polys") if isinstance(payload.get("rec_polys"), list) else []
        if not polygons and isinstance(payload.get("dt_polys"), list):
            polygons = payload["dt_polys"]
        count = max(len(texts), len(scores), len(polygons))
        for index in range(count):
            text = str(texts[index]).strip() if index < len(texts) else ""
            score = float(scores[index]) if index < len(scores) else None
            polygon = _primitive(polygons[index]) if index < len(polygons) else None
            bbox = _bbox(polygon)
            if not text or score is None or bbox is None:
                continue
            regions.append({
                "evidence_id": f"PP-{len(regions) + 1:04d}",
                "text": text,
                "confidence": score,
                "polygon": polygon,
                "bbox": bbox,
                "order": len(regions) + 1,
            })
    return regions


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--model-cache", required=True)
    parser.add_argument("--det-model-dir", required=True)
    parser.add_argument("--rec-model-dir", required=True)
    args = parser.parse_args()

    os.environ["PADDLE_PDX_CACHE_HOME"] = args.model_cache
    # Paddle imports are intentionally confined to this isolated process.
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        ocr_version="PP-OCRv6",
        text_detection_model_name=DET_MODEL,
        text_detection_model_dir=args.det_model_dir,
        text_recognition_model_name=REC_MODEL,
        text_recognition_model_dir=args.rec_model_dir,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device=args.device,
    )
    started = time.perf_counter()
    results = list(ocr.predict(args.image))
    latency_ms = (time.perf_counter() - started) * 1000.0
    _write_atomic(Path(args.output), {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "regions": _normalize(results),
        "latency_ms": round(latency_ms, 3),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
