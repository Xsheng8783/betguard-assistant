"""Deterministic geometry-only comparison for optional OCR shadow evidence.

The matcher never chooses an OCR authority and never mutates either input.  It
first establishes a unique physical row, then (only when a PP region has its
own token-sized box) a unique token.  Text is compared after both geometry
gates pass.  A fused PP row is retained as row evidence and is never split into
invented child boxes.
"""

from __future__ import annotations

import math
import time
from typing import Any


COMPARISON_SCHEMA_VERSION = "betguard.vision.ocr-shadow-comparison.v2"
MATCHING_METHOD = "two_stage_row_then_local_token_geometry"
LOW_CONFIDENCE_THRESHOLD = 0.75
AMBIGUITY_SCORE_DELTA = 0.08
_QWEN_NORMALIZED_1000_MODEL = "qwen3-vl-plus"
_QWEN_NORMALIZED_1000_TASK = "full_page_combined"
_QWEN_NORMALIZED_1000_PROMPT = "combined-bbox-v1"


def compare_ppocr_to_qwen(
    qwen_result: dict[str, Any],
    ppocr_regions: list[dict[str, Any]],
    *,
    image_width: int,
    image_height: int,
) -> dict[str, Any]:
    """Return evidence-only matches based exclusively on physical geometry."""
    started = time.perf_counter()
    qwen_items, qwen_rows, transform = _qwen_geometry(
        qwen_result,
        image_width,
        image_height,
    )
    pp_items = [_pp_item(region, index) for index, region in enumerate(ppocr_regions)]

    records: list[dict[str, Any]] = []
    matched_qwen: set[int] = set()
    pending: dict[int, tuple[dict[str, Any], int, float, dict[str, Any]]] = {}

    if not transform["reliable"]:
        for pp_item in pp_items:
            records.append(_unmatched_pp_record(
                pp_item,
                "coordinate_transform_unreliable",
                coordinate_transform=transform,
            ))
    else:
        for pp_index, pp_item in enumerate(pp_items):
            bbox = pp_item["bbox"]
            if bbox is None:
                records.append(_unmatched_pp_record(pp_item, "pp_bbox_invalid"))
                continue

            row_match = _unique_row_match(bbox, qwen_rows)
            if row_match["status"] == "ambiguous":
                records.append(_unmatched_pp_record(
                    pp_item,
                    "row_geometry_ambiguous",
                    row_association=row_match,
                ))
                continue
            if row_match["status"] != "matched":
                records.append(_unmatched_pp_record(
                    pp_item,
                    "no_qwen_row_geometry_match",
                    "PP_ONLY",
                    row_association=row_match,
                ))
                continue

            row_index = int(row_match["row_index"])
            row = qwen_rows[row_index]
            local_match = _unique_local_token_match(bbox, row, qwen_items)
            association = _public_row_association(row_match, row)
            if local_match["status"] == "spans_multiple_tokens":
                records.append(_unmatched_pp_record(
                    pp_item,
                    "pp_fused_region_has_no_token_bbox",
                    row_association=association,
                    local_token_geometry=local_match,
                ))
                continue
            if local_match["status"] == "ambiguous":
                records.append(_unmatched_pp_record(
                    pp_item,
                    "token_geometry_ambiguous",
                    row_association=association,
                    local_token_geometry=local_match,
                ))
                continue
            if local_match["status"] != "matched":
                records.append(_unmatched_pp_record(
                    pp_item,
                    "no_qwen_token_at_pp_geometry",
                    "PP_ONLY",
                    row_association=association,
                    local_token_geometry=local_match,
                ))
                continue

            qwen_index = int(local_match["qwen_index"])
            pending[pp_index] = (
                association,
                qwen_index,
                float(local_match["score"]),
                local_match,
            )

        # Any token claimed by more than one physical PP region remains unresolved.
        claims: dict[int, list[int]] = {}
        for pp_index, (_, qwen_index, _, _) in pending.items():
            claims.setdefault(qwen_index, []).append(pp_index)
        contested = {
            pp_index
            for indexes in claims.values()
            if len(indexes) > 1
            for pp_index in indexes
        }

        for pp_index, pp_item in enumerate(pp_items):
            match = pending.get(pp_index)
            if match is None:
                continue
            association, qwen_index, score, local_match = match
            if pp_index in contested:
                records.append(_unmatched_pp_record(
                    pp_item,
                    "multiple_pp_regions_claim_qwen_token",
                    row_association=association,
                    local_token_geometry=local_match,
                ))
                continue
            qwen_item = qwen_items[qwen_index]
            matched_qwen.add(qwen_index)
            confidence = pp_item["confidence"]
            agrees = _normalized_text(pp_item["text"]) == _normalized_text(qwen_item["text"])
            if confidence is None or confidence < LOW_CONFIDENCE_THRESHOLD:
                classification = "LOW_CONFIDENCE_PP"
            else:
                classification = "AGREE" if agrees else "DISAGREE"
            records.append({
                "classification": classification,
                "resolved": True,
                "authority": "qwen-dashscope",
                "qwen": qwen_item,
                "ppocr": pp_item,
                "row_association": association,
                "geometry": {
                    "score": round(score, 6),
                    "method": MATCHING_METHOD,
                    "metrics": local_match["metrics"],
                },
                "text_agrees": agrees,
                **_safety_metadata(),
            })

    for qwen_index, qwen_item in enumerate(qwen_items):
        if qwen_index in matched_qwen:
            continue
        bbox_valid = qwen_item["bbox"] is not None and transform["reliable"]
        classification = "QWEN_ONLY" if bbox_valid else "UNMATCHED_GEOMETRY"
        records.append({
            "classification": classification,
            "resolved": classification == "QWEN_ONLY",
            "authority": "qwen-dashscope",
            "qwen": qwen_item,
            "ppocr": None,
            "geometry": {"score": None, "method": MATCHING_METHOD},
            "warning": (
                "no_ppocr_geometry_match"
                if bbox_valid
                else "coordinate_transform_unreliable"
                if not transform["reliable"]
                else "qwen_bbox_invalid"
            ),
            **_safety_metadata(),
        })

    counts: dict[str, int] = {}
    for record in records:
        key = str(record["classification"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "matching_method": MATCHING_METHOD,
        "coordinate_transform": transform,
        "index_fallback": False,
        "string_fallback": False,
        "fused_bbox_split": False,
        "authority": "qwen-dashscope",
        "records": records,
        "summary": counts,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "needs_review": any(key != "AGREE" for key in counts),
        **_safety_metadata(),
    }


def _safety_metadata() -> dict[str, bool]:
    return {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _unmatched_pp_record(
    pp_item: dict[str, Any],
    warning: str,
    classification: str = "UNMATCHED_GEOMETRY",
    *,
    row_association: dict[str, Any] | None = None,
    local_token_geometry: dict[str, Any] | None = None,
    coordinate_transform: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "classification": classification,
        "resolved": classification == "PP_ONLY",
        "authority": "qwen-dashscope",
        "qwen": None,
        "ppocr": pp_item,
        "geometry": {"score": None, "method": MATCHING_METHOD},
        "warning": warning,
        **_safety_metadata(),
    }
    if row_association is not None:
        record["row_association"] = row_association
    if local_token_geometry is not None:
        record["local_token_geometry"] = _public_local_match(local_token_geometry)
    if coordinate_transform is not None:
        record["coordinate_transform"] = coordinate_transform
    return record


def _qwen_geometry(
    result: dict[str, Any],
    image_width: int,
    image_height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    lines = result.get("lines") if isinstance(result, dict) else None
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict):
                continue
            tokens = line.get("tokens")
            if not isinstance(tokens, list):
                continue
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                raw_bbox, coordinate_space = _raw_contract_bbox(token.get("bounding_box"))
                raw_items.append({
                    "line_id": str(line.get("line_id") or ""),
                    "token_id": str(token.get("token_id") or ""),
                    "text": str(token.get("text") or ""),
                    "raw_bbox": raw_bbox,
                    "contract_coordinate_space": coordinate_space,
                    "provider": "qwen-dashscope",
                })

    transform = _resolve_qwen_transform(result, raw_items, image_width, image_height)
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        bbox = _apply_qwen_transform(raw_item, transform, image_width, image_height)
        items.append({
            "line_id": raw_item["line_id"],
            "token_id": raw_item["token_id"],
            "text": raw_item["text"],
            "bbox": bbox,
            "raw_bbox": raw_item["raw_bbox"],
            "raw_coordinate_space": raw_item["contract_coordinate_space"],
            "provider": "qwen-dashscope",
        })

    row_groups: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        row_groups.setdefault(item["line_id"], []).append(index)
    rows: list[dict[str, Any]] = []
    for line_id, indexes in row_groups.items():
        boxes = [items[index]["bbox"] for index in indexes if items[index]["bbox"] is not None]
        rows.append({
            "line_id": line_id,
            "token_indexes": indexes,
            "bbox": _union_xyxy(boxes),
        })
    return items, rows, transform


def _resolve_qwen_transform(
    result: dict[str, Any],
    raw_items: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> dict[str, Any]:
    provider = result.get("provider") if isinstance(result, dict) else None
    preprocessing = result.get("preprocessing") if isinstance(result, dict) else None
    provider_id = str(provider.get("id") or "") if isinstance(provider, dict) else ""
    model = str(provider.get("model_name") or "") if isinstance(provider, dict) else ""
    task_type = str(preprocessing.get("task_type") or "") if isinstance(preprocessing, dict) else ""
    prompt_version = (
        str(preprocessing.get("prompt_version") or "") if isinstance(preprocessing, dict) else ""
    )
    base = {
        "provider": provider_id,
        "model": model,
        "source_width": image_width,
        "source_height": image_height,
        "contract_labels": sorted({
            str(item["contract_coordinate_space"])
            for item in raw_items
            if item["contract_coordinate_space"] is not None
        }),
    }
    if image_width <= 0 or image_height <= 0:
        return {**base, "reliable": False, "reason": "source_dimensions_invalid"}
    valid_boxes = [item["raw_bbox"] for item in raw_items if item["raw_bbox"] is not None]
    if not valid_boxes:
        return {**base, "reliable": False, "reason": "qwen_bbox_evidence_missing"}

    audited_qwen3vl_contract = (
        provider_id == "qwen-dashscope"
        and model == _QWEN_NORMALIZED_1000_MODEL
        and task_type == _QWEN_NORMALIZED_1000_TASK
        and prompt_version == _QWEN_NORMALIZED_1000_PROMPT
    )
    if audited_qwen3vl_contract:
        flattened = [number for bbox in valid_boxes for number in bbox]
        if any(number < 0.0 or number > 1000.0 for number in flattened):
            return {
                **base,
                "reliable": False,
                "reason": "qwen3vl_normalized_1000_out_of_range",
            }
        return {
            **base,
            "reliable": True,
            "source_contract": "qwen3vl_normalized_1000",
            "recognition_contract_label": "pixel",
            "recognition_contract_mismatch": "known_audited_provider_label_mismatch",
            "scale_x": image_width / 1000.0,
            "scale_y": image_height / 1000.0,
            "reason": "audited_qwen3vl_full_page_coordinate_contract",
        }

    if (
        provider_id == "qwen-dashscope"
        and model == _QWEN_NORMALIZED_1000_MODEL
        and task_type == _QWEN_NORMALIZED_1000_TASK
    ):
        return {
            **base,
            "reliable": False,
            "reason": "qwen3vl_full_page_coordinate_contract_not_audited",
        }

    spaces = set(base["contract_labels"])
    if spaces == {"pixel"}:
        return {
            **base,
            "reliable": True,
            "source_contract": "pixel",
            "scale_x": 1.0,
            "scale_y": 1.0,
            "reason": "explicit_recognition_bbox_contract",
        }
    if spaces == {"normalized"}:
        flattened = [number for bbox in valid_boxes for number in bbox]
        if any(number < 0.0 or number > 1.0 for number in flattened):
            return {**base, "reliable": False, "reason": "normalized_bbox_out_of_range"}
        return {
            **base,
            "reliable": True,
            "source_contract": "normalized",
            "scale_x": float(image_width),
            "scale_y": float(image_height),
            "reason": "explicit_recognition_bbox_contract",
        }
    return {**base, "reliable": False, "reason": "coordinate_contract_mixed_or_unsupported"}


def _apply_qwen_transform(
    raw_item: dict[str, Any],
    transform: dict[str, Any],
    image_width: int,
    image_height: int,
) -> list[float] | None:
    bbox = raw_item["raw_bbox"]
    if bbox is None or not transform.get("reliable"):
        return None
    contract = transform.get("source_contract")
    if contract == "qwen3vl_normalized_1000":
        result = [
            bbox[0] * image_width / 1000.0,
            bbox[1] * image_height / 1000.0,
            bbox[2] * image_width / 1000.0,
            bbox[3] * image_height / 1000.0,
        ]
    elif contract == "normalized":
        result = [
            bbox[0] * image_width,
            bbox[1] * image_height,
            bbox[2] * image_width,
            bbox[3] * image_height,
        ]
    else:
        result = list(bbox)
    if result[0] < 0 or result[1] < 0 or result[2] > image_width or result[3] > image_height:
        return None
    return _xyxy(result)


def _raw_contract_bbox(value: Any) -> tuple[list[float] | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    polygon = value.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 3:
        return None, str(value.get("coordinate_space") or "pixel")
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return None, str(value.get("coordinate_space") or "pixel")
    return (
        _xyxy([min(xs), min(ys), max(xs), max(ys)]),
        str(value.get("coordinate_space") or "pixel"),
    )


def _pp_item(region: dict[str, Any], index: int) -> dict[str, Any]:
    bbox = _xyxy(region.get("bbox")) or _xyxy(region.get("bbox_xyxy"))
    polygon = region.get("polygon")
    return {
        "evidence_id": str(region.get("evidence_id") or f"PP-{index + 1:04d}"),
        "order": int(region.get("order", index + 1)),
        "text": str(region.get("text") or ""),
        "confidence": _confidence(region.get("confidence")),
        "polygon": polygon if isinstance(polygon, list) else [],
        "bbox": bbox,
        "provider": str(region.get("provider") or "ppocrv6-shadow"),
        "model": str(region.get("model") or "PP-OCRv6_medium"),
    }


def _unique_row_match(pp_bbox: list[float], rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[tuple[float, int, dict[str, float]]] = []
    for row_index, row in enumerate(rows):
        bbox = row.get("bbox")
        if bbox is None:
            continue
        metrics = _geometry_metrics(pp_bbox, bbox)
        if metrics["vertical_overlap_ratio"] < 0.5 or metrics["horizontal_overlap"] <= 0.0:
            continue
        score = (
            metrics["iou"]
            + 0.75 * metrics["vertical_overlap_ratio"]
            + 0.25 * metrics["horizontal_overlap_ratio"]
            - 0.08 * metrics["normalized_center_dy"]
        )
        candidates.append((score, row_index, metrics))
    candidates.sort(key=lambda item: (-item[0], str(rows[item[1]]["line_id"])))
    if not candidates:
        return {"status": "unmatched", "candidate_count": 0}
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] <= AMBIGUITY_SCORE_DELTA:
        return {
            "status": "ambiguous",
            "candidate_count": len(candidates),
            "candidate_line_ids": [rows[item[1]]["line_id"] for item in candidates],
            "score_delta": round(candidates[0][0] - candidates[1][0], 6),
        }
    score, row_index, metrics = candidates[0]
    return {
        "status": "matched",
        "candidate_count": len(candidates),
        "row_index": row_index,
        "score": round(score, 6),
        "metrics": _rounded_metrics(metrics),
    }


def _public_row_association(match: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "matched",
        "line_id": row["line_id"],
        "score": match["score"],
        "candidate_count": match["candidate_count"],
        "metrics": match["metrics"],
    }


def _unique_local_token_match(
    pp_bbox: list[float],
    row: dict[str, Any],
    qwen_items: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates: list[tuple[float, int, dict[str, float]]] = []
    intersecting: list[int] = []
    for qwen_index in row["token_indexes"]:
        bbox = qwen_items[qwen_index]["bbox"]
        if bbox is None:
            continue
        metrics = _geometry_metrics(pp_bbox, bbox)
        if (
            metrics["vertical_overlap_ratio"] >= 0.5
            and metrics["horizontal_overlap_ratio"] >= 0.35
        ):
            intersecting.append(qwen_index)
            score = (
                metrics["iou"]
                + 0.5 * metrics["vertical_overlap_ratio"]
                + 0.35 * metrics["horizontal_overlap_ratio"]
                - 0.12 * metrics["normalized_center_dx"]
                - 0.12 * metrics["normalized_center_dy"]
            )
            candidates.append((score, qwen_index, metrics))
    if len(intersecting) > 1:
        return {
            "status": "spans_multiple_tokens",
            "candidate_count": len(intersecting),
            "candidate_token_ids": [qwen_items[index]["token_id"] for index in intersecting],
        }
    if not candidates:
        return {"status": "unmatched", "candidate_count": 0}
    candidates.sort(key=lambda item: (-item[0], qwen_items[item[1]]["token_id"]))
    score, qwen_index, metrics = candidates[0]
    if score < 0.05:
        return {"status": "unmatched", "candidate_count": len(candidates)}
    return {
        "status": "matched",
        "candidate_count": len(candidates),
        "qwen_index": qwen_index,
        "token_id": qwen_items[qwen_index]["token_id"],
        "score": score,
        "metrics": _rounded_metrics(metrics),
    }


def _public_local_match(match: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in match.items() if key != "qwen_index"}


def _geometry_metrics(a: list[float], b: list[float]) -> dict[str, float]:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    aw, ah = ax2 - ax1, ay2 - ay1
    bw, bh = bx2 - bx1, by2 - by1
    overlap_x = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    overlap_y = max(0.0, min(ay2, by2) - max(ay1, by1))
    center_dx_px = abs((ax1 + ax2) / 2.0 - (bx1 + bx2) / 2.0)
    center_dy_px = abs((ay1 + ay2) / 2.0 - (by1 + by2) / 2.0)
    intersection = overlap_x * overlap_y
    union = aw * ah + bw * bh - intersection
    return {
        "horizontal_overlap": overlap_x,
        "vertical_overlap": overlap_y,
        "horizontal_overlap_ratio": overlap_x / min(aw, bw),
        "vertical_overlap_ratio": overlap_y / min(ah, bh),
        "center_dx_px": center_dx_px,
        "center_dy_px": center_dy_px,
        "normalized_center_dx": center_dx_px / max(aw, bw),
        "normalized_center_dy": center_dy_px / max(ah, bh),
        "iou": intersection / union if union > 0 else 0.0,
    }


def _rounded_metrics(metrics: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in metrics.items()}


def _union_xyxy(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _xyxy(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        return None
    if result[0] < 0 or result[1] < 0 or result[2] <= result[0] or result[3] <= result[1]:
        return None
    return result


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None


def _normalized_text(value: str) -> str:
    return "".join(str(value).split()).casefold()
