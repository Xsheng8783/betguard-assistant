"""Offline regression replay for the OCR pipeline (M2 regression framework).

Replays fixed raw model responses end-to-end:

    raw model output
      -> normalized semantics (closed_set)
      -> semantic_parser -> executable / blocked decision

Every replay produces a structured, comparable record so future changes can
be diffed. The golden fixtures live in ``tests/fixtures/ab_formal/`` with an
expected snapshot under ``tests/fixtures/replay_expected.json``.

Safety invariant enforced by the test suite: any case that was blocked in
the expected snapshot must never become executable in a later replay.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from betguard.vision.closed_set import (
    parse_multi_category_shared,
    parse_shared_multiplier,
)
from betguard.vision.deterministic_checks import check_region_conflicts
from betguard.vision.pipeline import merge_column_slices, process_row


def parse_model_json(raw: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def replay_row(row: dict[str, Any], *, region_bound: bool) -> dict[str, Any]:
    """Thin wrapper: replay shares the SAME pipeline implementation."""
    return process_row(row, region_bound=region_bound)


def replay_response(raw: str, *, sample_id: str, model: str, run: int) -> dict[str, Any]:
    parsed = parse_model_json(raw)
    rows: list[dict[str, Any]] = []
    shared: list[dict[str, Any]] = []
    region_conflicts: list[str] = []
    if parsed is None:
        return {
            "sample_id": sample_id,
            "model": model,
            "run": run,
            "parseable": False,
            "rows": [],
            "shared_multipliers": [],
            "summary": {"rows": 0, "parse_ok": 0, "parse_fail": 1, "blocked": 0, "executable": 0},
        }
    for si, sec in enumerate(parsed.get("sections") or [], 1):
        # each section is a region; shared multiplier binds the region
        sec_rows: list[dict[str, Any]] = []
        for row in merge_column_slices(sec.get("rows") or []):
            rec = replay_row(row, region_bound=True)
            rec["section"] = si
            rows.append(rec)
            sec_rows.append(rec)
        sm = sec.get("shared_multiplier")
        sec_scope = "current_group"
        sec_multipliers: list[dict[str, Any]] = []
        for rec in sec_rows:
            sec_multipliers.extend(rec.get("semantics_multipliers") or [])
        if sm and str(sm).strip():
            sm_text = str(sm).strip()
            shared.append({"section": si, "raw_text": sm_text})
            rule = parse_shared_multiplier(sm_text) or parse_multi_category_shared(sm_text)
            if rule:
                sec_multipliers.extend(rule.get("multipliers") or [])
                sec_scope = rule.get("scope") or "current_group"
        region_conflicts.extend(
            f"S{si}:{c}" for c in check_region_conflicts(sec_multipliers, scope=sec_scope)
        )

    blocked = sum(1 for r in rows if not r.get("executable"))
    executable = sum(1 for r in rows if r.get("executable"))
    parse_ok = sum(1 for r in rows if r.get("parse_error") is None)
    summary = {
        "rows": len(rows),
        "parse_ok": parse_ok,
        "parse_fail": len(rows) - parse_ok,
        "blocked": blocked,
        "executable": executable,
        "shared_multipliers": len(shared),
        "region_conflicts": len(region_conflicts),
    }
    return {
        "sample_id": sample_id,
        "model": model,
        "run": run,
        "parseable": True,
        "rows": rows,
        "shared_multipliers": shared,
        "region_conflicts": sorted(set(region_conflicts)),
        "summary": summary,
    }


def replay_file(path: Path) -> dict[str, Any]:
    name = path.stem  # sample-002-qwen3-vl-plus-run1
    parts = name.split("-")
    sample_id = f"{parts[0]}-{parts[1]}"
    run = int(parts[-1].replace("run", ""))
    model = "-".join(parts[2:-1])
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("raw_output") or data.get("content") or ""
    return replay_response(raw, sample_id=sample_id, model=model, run=run)


def compare_replays(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return human-readable diffs between two replay records ([] == identical)."""
    diffs: list[str] = []
    key = f"{actual.get('sample_id')}-{actual.get('model')}-run{actual.get('run')}"
    if expected.get("parseable") != actual.get("parseable"):
        return [f"{key}: parseable changed {expected.get('parseable')} -> {actual.get('parseable')}"]
    exp_rows = expected.get("rows", [])
    act_rows = actual.get("rows", [])
    if len(exp_rows) != len(act_rows):
        diffs.append(f"{key}: row count {len(exp_rows)} -> {len(act_rows)}")
    for i, (er, ar) in enumerate(zip(exp_rows, act_rows)):
        for field in ("bet_type", "executable", "supported", "block_reason", "money", "parse_error"):
            if er.get(field) != ar.get(field):
                diffs.append(
                    f"{key} row{i}: {field} {er.get(field)!r} -> {ar.get(field)!r} "
                    f"(raw={ar.get('raw_text')!r})"
                )
    exp_sum = expected.get("summary", {})
    act_sum = actual.get("summary", {})
    for k in exp_sum:
        if exp_sum.get(k) != act_sum.get(k):
            diffs.append(f"{key}: summary.{k} {exp_sum.get(k)} -> {act_sum.get(k)}")
    return diffs


def safety_violations(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Blocked-in-expected must never become executable-in-actual."""
    key = f"{actual.get('sample_id')}-{actual.get('model')}-run{actual.get('run')}"
    violations: list[str] = []
    exp_rows = expected.get("rows", [])
    act_rows = actual.get("rows", [])
    for i, (er, ar) in enumerate(zip(exp_rows, act_rows)):
        if er.get("executable") is False and ar.get("executable") is True:
            violations.append(
                f"{key} row{i}: was blocked, now executable (raw={ar.get('raw_text')!r})"
            )
    return violations
