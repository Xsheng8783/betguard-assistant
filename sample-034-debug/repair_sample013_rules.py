"""One-time, line_id-keyed repair for sample-013 human-verified rule fixes.

Only R01 (car text) / R03 (staggered columns) / R04 (multiplier rules) are
patched, with correction_source = "human_review_sample013_rule_fix" and
provenance source = "human_verified_image_ground_truth". review_action is
NEVER auto-confirmed; model_raw_text / uncertain stay untouched. Any
precondition mismatch fails closed (no write). Idempotent.

Usage:
    python repair_sample013_rules.py            # dry-run
    python repair_sample013_rules.py --write    # apply
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from betguard.vision.multiplier_policy import is_complete_multiplier  # noqa: E402

DATASET = Path(os.environ.get("BETGUARD_DATASET", ""))
DRAFT = DATASET / "ground-truth-draft"

CORRECTION_SOURCE = "human_review_sample013_rule_fix"
TOOL_VERSION = "repair-sample013-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rules_struct(text: str) -> list[dict]:
    return [
        {
            "rule_text": r,
            "categories": [c for c in r.split("X", 1)[0] if c in "234"] if "X" in r else [],
            "value": r.split("X", 1)[1] if "X" in r else None,
        }
        for r in (text or "").split() if r
    ]


EXPECTED = {
    "R01-L1": {
        "groups": [["15", "34"]],
        "layout_hint": "normal_row",
        "multiplier_text": None,
        "play_type": "car_bet",
        "play_text": "15 34 各半車",
        "raw": "15 34 各半車",
    },
    "R03-L1": {
        "groups": [["12"], ["15", "34"], ["08", "20"]],
        "layout_hint": "column_bet",
        "multiplier_text": "2X3 3X1",
        "raw": "12 / 15 34 / 08 20 2X3 3X1",
    },
    "R04-L1": {
        "groups": [["12", "15", "36", "37"]],
        "layout_hint": "normal_row",
        "multiplier_text": "2X3 3X1",
        "raw": "12 15 36 37 2X3 3X1",
    },
}


def repair_line(line: dict, *, apply: bool = True) -> dict:
    lid = line.get("line_id")
    exp = EXPECTED.get(lid)
    if exp is None:
        return {"line_id": lid, "changed": False, "skipped": "not a target"}
    groups = line.get("number_groups")
    if lid == "R01-L1":
        if groups != [["15", "34"]]:
            raise AssertionError(f"{lid}: groups precondition mismatch: {groups}")
        evidence = str(line.get("model_raw_text") or "") + str(line.get("raw_text") or "")
        if "各" not in evidence:
            raise AssertionError(f"{lid}: missing 車 evidence")
    elif lid == "R03-L1":
        if groups not in (
            [["12"], ["15"], ["34"], ["08"], ["20"]],
            [["12"], ["15"], ["34"], ["08", "20"]],
            [["12"], ["15", "34"], ["08", "20"]],
        ):
            raise AssertionError(f"{lid}: groups precondition mismatch: {groups}")
    else:
        if groups != [["12", "15", "36", "37"]]:
            raise AssertionError(f"{lid}: groups precondition mismatch: {groups}")
    if exp["multiplier_text"] is not None and not is_complete_multiplier(exp["multiplier_text"]):
        raise AssertionError(f"{lid}: expected multiplier invalid: {exp['multiplier_text']}")

    changes = []
    if lid == "R01-L1":
        if line.get("play_type") != "car_bet":
            changes.append({"path": "play_type", "before": line.get("play_type"), "after": "car_bet"})
        if line.get("play_text") != exp["play_text"]:
            changes.append({"path": "play_text", "before": line.get("play_text"), "after": exp["play_text"]})
    else:
        if line.get("number_groups") != exp["groups"]:
            changes.append({"path": "number_groups", "before": line.get("number_groups"), "after": exp["groups"]})
        if line.get("layout_hint") != exp["layout_hint"]:
            changes.append({"path": "layout_hint", "before": line.get("layout_hint"), "after": exp["layout_hint"]})
        if (line.get("multiplier_text") or "") != exp["multiplier_text"]:
            changes.append({"path": "multiplier_text", "before": line.get("multiplier_text"), "after": exp["multiplier_text"]})
    if str(line.get("raw_text") or "") != exp["raw"]:
        changes.append({"path": "raw_text", "before": line.get("raw_text"), "after": exp["raw"]})
    if str(line.get("human_raw_text") or "") != exp["raw"]:
        changes.append({"path": "human_raw_text", "before": line.get("human_raw_text"), "after": exp["raw"]})
    if str(line.get("correction_source") or "") != CORRECTION_SOURCE:
        changes.append({"path": "correction_source", "before": line.get("correction_source"), "after": CORRECTION_SOURCE})

    if apply and changes:
        if lid == "R01-L1":
            line["play_type"] = "car_bet"
            line["play_text"] = exp["play_text"]
            line["multiplier_text"] = None
            line["multiplier_rules"] = []
        else:
            line["number_groups"] = exp["groups"]
            line["layout_hint"] = exp["layout_hint"]
            line["multiplier_text"] = exp["multiplier_text"]
            line["multiplier_rules"] = _rules_struct(exp["multiplier_text"])
        line["raw_text"] = exp["raw"]
        line["human_raw_text"] = exp["raw"]
        line["correction_source"] = CORRECTION_SOURCE
        fb = dict(line.get("fallback_candidate") or {})
        fb.setdefault("evidence", [])
        fb["evidence"].append({
            "source": "human_verified_image_ground_truth",
            "rule": CORRECTION_SOURCE,
            "line_id": lid,
            "changes": changes,
            "machine_evidence": {
                "model_raw_text": line.get("model_raw_text"),
                "saved_combined_response": True,
            },
            "at": _now(),
            "tool_version": TOOL_VERSION,
        })
        line["fallback_candidate"] = fb
    return {"line_id": lid, "changed": bool(changes and apply), "changes": changes}


def run_repair(draft: dict, *, apply: bool = True) -> dict:
    ids = [l.get("line_id") for l in draft.get("lines", [])]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate line_ids")
    by_id = {l.get("line_id"): l for l in draft.get("lines", [])}
    for t in EXPECTED:
        if t not in by_id:
            raise AssertionError(f"target missing: {t}")
    for t in EXPECTED:
        repair_line(by_id[t], apply=False)  # precondition check, fail-closed
    results = []
    for t in EXPECTED:
        results.append(repair_line(by_id[t], apply=apply))
    return {"results": results, "changed_lines": sum(1 for r in results if r["changed"])}


def main() -> None:
    apply = "--write" in sys.argv
    sid = "sample-013"
    draft = json.loads((DRAFT / f"{sid}.json").read_text(encoding="utf-8"))
    print(f"{sid}: mode={'WRITE' if apply else 'DRY'}")
    report = run_repair(draft, apply=apply)
    for r in report["results"]:
        print(" ", r["line_id"], "| changed:", r["changed"], "| changes:", [(c["path"], c["before"], c["after"]) for c in r.get("changes", [])])
    if apply:
        (DRAFT / f"{sid}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WRITTEN")


if __name__ == "__main__":
    main()
