"""Offline-only messy bet structure reconstruction audit helpers.

This module is deliberately outside ``src``.  It never calls a model and never
mutates RecognitionResult, production reconstruction, cache, or human truth.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "betguard.shadow.messy-structure-audit.v1"
TRUTH_SHA256 = "39efc3eb26f7e8113b99c891e29dbeb29ec8e275d55e3bf0f2b41bde9f605d80"
SAFETY = {
    "evidence_only": True,
    "human_confirmation_required": True,
    "needs_review": True,
    "auto_apply": False,
    "auto_confirm": False,
    "auto_submit": False,
    "executable": False,
}
_NUMBER = re.compile(r"^(?:0[1-9]|[12]\d|3[0-9])$")
_MULTIPLIER = re.compile(r"^(?P<category>\?|[234](?:/[234])*)X(?P<value>\d+(?:\.\d+)?)$", re.I)


class ShadowAmbiguity(ValueError):
    """Stable fail-closed error for geometry that is not unique."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_multiplier(value: Any) -> str:
    text = str(value or "").strip().upper().replace("×", "X").replace(" ", "")
    if text.startswith("23X"):
        return "2/3X" + text[3:]
    if text.startswith("234X"):
        return "2/3/4X" + text[4:]
    return text


def _xyxy(token: Mapping[str, Any]) -> tuple[float, float, float, float]:
    value = token.get("bbox")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ShadowAmbiguity("bbox_missing_or_invalid")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ShadowAmbiguity("bbox_missing_or_invalid")
    x1, y1, x2, y2 = map(float, value)
    if not all(math.isfinite(item) for item in (x1, y1, x2, y2)) or x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
        raise ShadowAmbiguity("bbox_missing_or_invalid")
    return x1, y1, x2, y2


def deterministic_column_anchors(tokens: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cluster literal 539 number tokens by center-x, never by text order.

    A token must have a real bbox.  Near-equal claims to two anchors fail
    closed; no synthetic sub-boxes are ever generated.
    """
    numeric: list[dict[str, Any]] = []
    widths: list[float] = []
    for token in tokens:
        text = str(token.get("text") or "").strip()
        if not _NUMBER.fullmatch(text):
            continue
        x1, y1, x2, y2 = _xyxy(token)
        widths.append(x2 - x1)
        numeric.append({"text": text, "bbox": [x1, y1, x2, y2], "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2})
    if not numeric:
        raise ShadowAmbiguity("number_geometry_missing")
    tolerance = max(8.0, median(widths) * 0.72)
    anchors: list[dict[str, Any]] = []
    for token in sorted(numeric, key=lambda item: (item["cx"], item["cy"], item["text"])):
        distances = sorted((abs(token["cx"] - anchor["x"]), index) for index, anchor in enumerate(anchors))
        if not distances or distances[0][0] > tolerance:
            anchors.append({"x": token["cx"], "tokens": [token]})
            continue
        if len(distances) > 1 and distances[1][0] <= tolerance and abs(distances[1][0] - distances[0][0]) < 1e-6:
            raise ShadowAmbiguity("column_anchor_tie")
        anchor = anchors[distances[0][1]]
        anchor["tokens"].append(token)
        anchor["x"] = sum(item["cx"] for item in anchor["tokens"]) / len(anchor["tokens"])
    anchors.sort(key=lambda item: item["x"])
    return {
        "anchors": [round(item["x"], 6) for item in anchors],
        "number_groups": [
            [token["text"] for token in sorted(item["tokens"], key=lambda token: (token["cy"], token["cx"]))]
            for item in anchors
        ],
        "tolerance": tolerance,
        "provenance": "literal_token_bbox_center_x",
        **SAFETY,
    }


def attach_continuation_by_geometry(
    anchor_x: Sequence[float],
    continuation_tokens: Sequence[Mapping[str, Any]],
    *,
    tolerance: float,
) -> dict[str, Any]:
    """Attach continuation literals only to one uniquely nearest anchor."""
    if not anchor_x:
        raise ShadowAmbiguity("continuation_anchor_missing")
    groups: list[list[str]] = [[] for _ in anchor_x]
    assignments: list[dict[str, Any]] = []
    for token in continuation_tokens:
        text = str(token.get("text") or "").strip()
        if not _NUMBER.fullmatch(text):
            continue
        x1, _y1, x2, _y2 = _xyxy(token)
        cx = (x1 + x2) / 2
        ranked = sorted((abs(cx - float(anchor)), index) for index, anchor in enumerate(anchor_x))
        if ranked[0][0] > tolerance:
            raise ShadowAmbiguity("continuation_outside_anchor_tolerance")
        if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 1e-6:
            raise ShadowAmbiguity("continuation_anchor_tie")
        groups[ranked[0][1]].append(text)
        assignments.append({"text": text, "anchor_index": ranked[0][1], "center_x": cx})
    if not assignments:
        raise ShadowAmbiguity("continuation_number_geometry_missing")
    return {"groups": groups, "assignments": assignments, "provenance": "unique_nearest_anchor_bbox", **SAFETY}


def category_scope_fallback(suggestion: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a review-only 2/3 category fallback; never auto-apply it.

    This policy is intentionally narrow: a column suggestion already contains
    an unresolved ``?Xvalue`` and exactly one terminal literal ``23``.  Without
    geometry it cannot become authoritative, but the two competing readings
    can be presented as a deterministic fallback candidate.
    """
    groups = copy.deepcopy(suggestion.get("number_groups"))
    if suggestion.get("suggested_layout") != "column" or not isinstance(groups, list) or not groups:
        return None
    match = _MULTIPLIER.fullmatch(canonical_multiplier(suggestion.get("suggested_multiplier")))
    if not match or match.group("category") != "?":
        return None
    occurrences = sum(str(value) == "23" for group in groups if isinstance(group, list) for value in group)
    if occurrences != 1 or not isinstance(groups[-1], list) or not groups[-1] or str(groups[-1][-1]) != "23":
        return None
    groups[-1].pop()
    if not groups[-1]:
        groups.pop()
    return {
        "candidate_kind": "ambiguous_number_or_2_3_category",
        "number_groups": groups,
        "multiplier_rules": [f"2/3X{match.group('value')}"],
        "layout": "column",
        "status": "partial",
        "warning": "category_scope_requires_geometry_or_human_confirmation",
        "source_number_literal": "23",
        "mapping_policy": "no_physical_mapping_without_bbox",
        **SAFETY,
    }


def shadow_reconstruct_suggestion(suggestion: Mapping[str, Any]) -> dict[str, Any]:
    """Build immutable shadow evidence from one already-linked suggestion."""
    before = copy.deepcopy(dict(suggestion))
    result = {
        "number_groups": copy.deepcopy(suggestion.get("number_groups") or []),
        "multiplier_rules": [canonical_multiplier(suggestion.get("suggested_multiplier"))]
        if suggestion.get("suggested_multiplier") else [],
        "layout": suggestion.get("suggested_layout") or "unknown",
        "continuation": suggestion.get("suggested_continuation") is True,
        "special_play": str(suggestion.get("suggested_special_play") or ""),
        "scope": suggestion.get("suggested_scope") or "normal",
        "cancelled": suggestion.get("suggested_cancelled") is True,
        "fallback_candidate": category_scope_fallback(suggestion),
        "source_immutable": True,
        **SAFETY,
    }
    if dict(suggestion) != before:
        raise AssertionError("source suggestion mutated")
    return result


def _truth_record(line: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number_groups": copy.deepcopy(line.get("number_groups") or []),
        "multiplier_rules": [canonical_multiplier(line.get("multiplier_text"))] if line.get("multiplier_text") else [],
        "layout": "column" if line.get("layout_hint") == "column_bet" else "normal",
        "continuation": line.get("continuation") is True,
        "special_play": str(line.get("special_play") or ""),
        "scope": line.get("scope") or "normal",
        "cancelled": line.get("cancelled") is True,
    }


def _machine_record(suggestion: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number_groups": copy.deepcopy(suggestion.get("number_groups") or []),
        "multiplier_rules": [canonical_multiplier(suggestion.get("suggested_multiplier"))]
        if suggestion.get("suggested_multiplier") else [],
        "layout": suggestion.get("suggested_layout") or "unknown",
        "continuation": suggestion.get("suggested_continuation") is True,
        "special_play": str(suggestion.get("suggested_special_play") or ""),
        "scope": suggestion.get("suggested_scope") or "normal",
        "cancelled": suggestion.get("suggested_cancelled") is True,
    }


def classify_errors(machine: Mapping[str, Any], truth: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if truth["cancelled"]:
        return [] if machine["cancelled"] else ["CANCELLED_SCOPE_ERROR"]
    mgroups, tgroups = machine["number_groups"], truth["number_groups"]
    mmult, tmult = machine["multiplier_rules"], truth["multiplier_rules"]
    flat_m = [str(value) for group in mgroups for value in group]
    flat_t = [str(value) for group in tgroups for value in group]
    category_pattern = (
        canonical_multiplier(mmult[0] if mmult else "").startswith("?X")
        and Counter(flat_m) - Counter(flat_t) == Counter({"23": 1})
        and not (Counter(flat_t) - Counter(flat_m))
    )
    if mgroups != tgroups:
        if category_pattern:
            errors.append("CATEGORY_AS_NUMBER_ERROR")
        elif Counter(flat_m) == Counter(flat_t):
            errors.append("COLUMN_GROUPING_ERROR")
        else:
            missing, extra = Counter(flat_t) - Counter(flat_m), Counter(flat_m) - Counter(flat_t)
            if missing and extra:
                errors.append("OCR_SUBSTITUTION")
            elif missing:
                errors.append("OCR_MISSING")
            else:
                errors.append("OCR_EXTRA")
    if mmult != tmult:
        errors.append("MULTIPLIER_SCOPE_ERROR" if category_pattern or any(value.startswith("?X") for value in mmult) else "OCR_MULTIPLIER")
    if machine["layout"] != truth["layout"]:
        errors.append("BET_SPLIT_MERGE_ERROR")
    if machine["continuation"] != truth["continuation"]:
        errors.append("CONTINUATION_ERROR")
    if machine["special_play"].rstrip("?") != truth["special_play"] or machine["special_play"].endswith("?"):
        if machine["special_play"] != truth["special_play"]:
            errors.append("SPECIAL_PLAY_SCOPE_ERROR")
    if machine["scope"] != truth["scope"]:
        errors.append("SPECIAL_PLAY_SCOPE_ERROR")
    if machine["cancelled"] != truth["cancelled"]:
        errors.append("CANCELLED_SCOPE_ERROR")
    return errors


def _value_equal(value: Mapping[str, Any], truth: Mapping[str, Any]) -> bool:
    return all(value.get(key) == truth.get(key) for key in (
        "number_groups", "multiplier_rules", "layout", "continuation", "special_play", "scope", "cancelled"
    ))


def evaluate_sample007(truth: Mapping[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    counts = truth.get("truth_counts") or {}
    if counts.get("human_records") != 25 or counts.get("active_bets") != 24 or counts.get("cancelled_bets") != 1:
        raise ValueError("sample-007 truth count contract mismatch")
    lines_by_human_id = {
        str((line.get("human_truth_provenance") or {}).get("source_human_bet_id")): line
        for line in truth.get("lines", [])
    }
    cancelled_by_id = {
        str(item.get("human_bet_id") or (item.get("human_truth_provenance") or {}).get("source_human_bet_id")): item
        for item in truth.get("cancelled_bets", [])
    }
    rows: list[dict[str, Any]] = []
    for linked in review.get("human_bets", []):
        human_id = str(linked.get("human_bet_id") or "")
        truth_line = lines_by_human_id.get(human_id) or cancelled_by_id.get(human_id)
        if truth_line is None:
            raise ValueError(f"truth linkage missing: {human_id}")
        suggestion = linked.get("machine_suggestion") or {}
        machine = _machine_record(suggestion)
        expected = _truth_record(truth_line)
        if truth_line.get("cancelled") is True:
            expected.update({
                "number_groups": [],
                "multiplier_rules": [],
                "layout": "unknown",
                "continuation": False,
                "special_play": "",
                "scope": truth_line.get("human_answer", {}).get("scope", "normal"),
                "cancelled": True,
            })
        baseline_errors = classify_errors(machine, expected)
        shadow = shadow_reconstruct_suggestion(suggestion)
        fallback = shadow.get("fallback_candidate")
        evaluated = dict(machine)
        if fallback:
            evaluated["number_groups"] = fallback["number_groups"]
            evaluated["multiplier_rules"] = fallback["multiplier_rules"]
        shadow_errors = classify_errors(evaluated, expected)
        baseline_exact = not baseline_errors
        value_exact = not shadow_errors
        if value_exact and not fallback:
            result_class = "EXACT"
        elif value_exact:
            result_class = "PARTIAL_REVIEW_REQUIRED"
        elif evaluated["number_groups"] == expected["number_groups"] or evaluated["multiplier_rules"] == expected["multiplier_rules"]:
            result_class = "PARTIAL"
        else:
            result_class = "FAIL"
        rows.append({
            "record_id": human_id,
            "truth_region_id": truth_line.get("region_id"),
            "physical_linkage": suggestion.get("physical_linkage", "unresolved"),
            "baseline": machine,
            "truth": expected,
            "baseline_errors": baseline_errors,
            "baseline_exact": baseline_exact,
            "shadow": shadow,
            "shadow_evaluated_value": evaluated,
            "shadow_errors": shadow_errors,
            "shadow_value_exact": value_exact,
            "result_class": result_class,
            "primary_error": baseline_errors[0] if baseline_errors else None,
        })
    if len(rows) != 25:
        raise ValueError(f"review record count mismatch: {len(rows)}")
    counts = Counter(row["result_class"] for row in rows)
    baseline_exact = sum(row["baseline_exact"] for row in rows)
    value_exact = sum(row["shadow_value_exact"] for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "records": rows,
        "metrics": {
            "records": 25,
            "active": 24,
            "cancelled": 1,
            "baseline_exact": baseline_exact,
            "baseline_exact_rate": baseline_exact / 25,
            "shadow_value_exact": value_exact,
            "shadow_value_exact_rate": value_exact / 25,
            "safe_auto_exact": baseline_exact,
            "safe_auto_exact_rate": baseline_exact / 25,
            "result_classes": dict(counts),
            "category_scope_fallbacks": sum(row["shadow"]["fallback_candidate"] is not None for row in rows),
            "external_qwen_calls": 0,
            "external_gemma_calls": 0,
        },
        "alignment": {
            "truth_bboxes": "all_null",
            "truth_physical_linkage": "all_unresolved",
            "qwen_validated_rows": 0,
            "pp_regions": 65,
            "machine_to_truth_mapping": "UNAVAILABLE_FAIL_CLOSED",
            "reason": "truth has neither bbox nor authoritative physical linkage; PP cannot be reverse-aligned from truth text/order",
        },
        "safety": dict(SAFETY),
    }


def replay_cache(path: Path) -> dict[str, Any]:
    """Replay one validated legacy cache through production reconstruction offline."""
    from betguard.vision.providers.qwen_dashscope import _lines_from_full_page_response
    from betguard.vision.structure_reconstruction import reconstruct_structure

    record = json.loads(path.read_text(encoding="utf-8"))
    parsed = json.loads(record["response_content"])
    lines = _lines_from_full_page_response(parsed)
    result = {
        "status": "completed",
        "provider": {"id": "qwen-dashscope"},
        "source_image": {"image_id": path.stem, "width": 1000, "height": 1000},
        "raw_text": "\n".join(line.text for line in lines),
        "lines": [line.to_dict() for line in lines],
        "preprocessing": {"qwen_response": parsed},
    }
    before = copy.deepcopy(result)
    evidence = reconstruct_structure(result, game="539")
    if result != before:
        raise AssertionError("RecognitionResult-like payload mutated during replay")
    primaries = [item for item in evidence if item.get("line_role") == "primary"]
    return {
        "cache_filename": path.name,
        "cache_sha256": sha256_file(path),
        "identity": record.get("identity"),
        "recognition_deep_equal": True,
        "evidence_records": len(evidence),
        "primary_structures": len(primaries),
        "status_counts": dict(Counter(item.get("status") for item in primaries)),
        "reconstruction_sha256": hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    }


def write_audit_artifacts(
    *,
    truth_path: Path,
    review_path: Path,
    pp_path: Path,
    cache_paths: Sequence[Path],
    output_dir: Path,
) -> dict[str, Any]:
    if sha256_file(truth_path) != TRUTH_SHA256:
        raise ValueError("sample-007 truth SHA-256 mismatch")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    pp = json.loads(pp_path.read_text(encoding="utf-8"))
    result = evaluate_sample007(truth, review)
    result["alignment"]["pp_regions"] = len(pp.get("regions") or [])
    replays = [replay_cache(path) for path in cache_paths]
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Any] = {
        "truth-provenance.json": {
            "authority": str(truth_path),
            "sha256": sha256_file(truth_path),
            "review_status": truth.get("review_status"),
            "reviewed_by": truth.get("reviewed_by"),
            "counts": truth.get("truth_counts"),
            "machine_truth_prohibited": True,
        },
        "error-matrix.json": {"schema_version": SCHEMA_VERSION, "records": result["records"]},
        "aggregate-metrics.json": {"schema_version": SCHEMA_VERSION, **result["metrics"]},
        "alignment-audit.json": {"schema_version": SCHEMA_VERSION, **result["alignment"]},
        "regression-replay.json": {"schema_version": SCHEMA_VERSION, "samples": replays},
        "policy.json": {
            "schema_version": SCHEMA_VERSION,
            "column_anchors": "bbox center-x only; ambiguity fails closed",
            "continuation": "unique nearest existing anchor only; no fabricated bbox",
            "number_23_vs_category_2_3": "review-only fallback when unresolved ?Xvalue and exactly one terminal 23; never auto-apply",
            "multiplier_scope": "must remain within linked structure; no cross-card propagation",
            "special_scope": "preserve explicit source only",
            "cancellation_scope": "preserve explicit cancelled flag only",
            **SAFETY,
        },
    }
    for name, payload in artifacts.items():
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _report_markdown(result, replays)
    (output_dir / "final-report.md").write_text(report, encoding="utf-8")
    manifest_items = []
    for path in sorted(output_dir.iterdir()):
        if path.name == "artifact-manifest.json" or not path.is_file():
            continue
        manifest_items.append({"name": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "external_qwen_calls": 0,
        "external_gemma_calls": 0,
        "files": manifest_items,
    }
    (output_dir / "artifact-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"metrics": result["metrics"], "replays": replays, "manifest": manifest}


def _report_markdown(result: Mapping[str, Any], replays: Sequence[Mapping[str, Any]]) -> str:
    metrics = result["metrics"]
    return f"""# Messy Bet Structure Reconstruction Audit

- Mode: offline shadow-only
- External Qwen/Gemma calls: 0 / 0
- Truth: sample-007 human-reviewed draft, SHA-256 `{TRUTH_SHA256}`
- Records: 25 (24 active, 1 cancelled)
- Baseline value exact: {metrics['baseline_exact']}/25 ({metrics['baseline_exact_rate']:.1%})
- Shadow value exact (including review-only fallback): {metrics['shadow_value_exact']}/25 ({metrics['shadow_value_exact_rate']:.1%})
- Safe auto exact: {metrics['safe_auto_exact']}/25 ({metrics['safe_auto_exact_rate']:.1%})
- Category-scope fallback candidates: {metrics['category_scope_fallbacks']} (all needs_review; none auto-applied)
- Result classes: `{json.dumps(metrics['result_classes'], sort_keys=True)}`

## Fail-closed finding

The truth records have null bboxes and unresolved physical linkage. The Qwen run is truncated with zero validated rows. PP has 65 real regions, but none can be authoritatively mapped to truth records. PP-to-truth reverse alignment by text, order, or human answer is prohibited.

## Generic policies

Column anchors and continuation attachment require real token bboxes and unique geometry. A terminal `23` next to unresolved `?Xvalue` creates only a `2/3Xvalue` fallback candidate; it remains non-executable and human-review-required. Special-play and cancellation scope are never propagated without explicit linkage.

## Regression

{json.dumps(list(replays), ensure_ascii=False, indent=2)}
"""


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--pp", type=Path, required=True)
    parser.add_argument("--cache", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = write_audit_artifacts(
        truth_path=args.truth,
        review_path=args.review,
        pp_path=args.pp,
        cache_paths=args.cache,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
