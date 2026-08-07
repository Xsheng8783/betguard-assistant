"""One-time, line_id-keyed repair for sample-014 human-verified rule fixes.

Only R01 / R05 / R09 / R11 / R12 are handled. R09/R11 already human-fixed
structured fields are idempotent NO-OPS (human provenance never downgraded);
candidate metadata is upgraded where needed (R01/R11 baseline-replacement
evidence). correction_source = "human_review_sample014_rule_fix", provenance
source = "human_verified_image_ground_truth". review_action is NEVER
auto-confirmed; model_raw_text stays immutable; preconditions fail closed.

Usage:
    python repair_sample014_rules.py            # dry-run
    python repair_sample014_rules.py --write    # apply
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

CORRECTION_SOURCE = "human_review_sample014_rule_fix"
TOOL_VERSION = "repair-sample014-v1"


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
        "groups": [["05", "06", "10", "28"]],
        "layout_hint": "normal_row",
        "multiplier_text": "3/4X1",
        "raw": "05 06 10 28 3/4X1",
    },
    "R05-L1": {
        "groups": [["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
        "layout_hint": "column_bet",
        "multiplier_text": "2/3/4X0.1",
        "raw": "24 34 / 08 38 / 16 36 / 03 13 2/3/4X0.1",
    },
    "R09-L1": {
        "groups": [["13"], ["34"], ["20"], ["08", "38"]],
        "layout_hint": "column_bet",
        "multiplier_text": "2X1",
        "raw": "13 / 34 / 20 / 08 38 2X1",
        "human_already_fixed": True,
    },
    "R11-L1": {
        "groups": [["12"], ["15"], ["34"], ["13", "20"]],
        "layout_hint": "column_bet",
        "multiplier_text": "2X3 3X1",
        "raw": "12 / 15 / 34 / 13 20 2X3 3X1",
        "human_already_fixed": True,
    },
    "R12-L1": {
        "groups": [["12"], ["15"], ["06", "16"]],
        "layout_hint": "column_bet",
        "multiplier_text": "2X3 3X1",
        "raw": "12 / 15 / 06 16 2X3 3X1",
    },
}

CANDIDATE_METADATA = {
    "R01-L1": [{
        "rule_text": "3/4X1",
        "candidate_mode": "alternative_reading",
        "candidate_group_id": "R01-slot-1",
        "replaces_current_rules": ["3X1"],
        "source": "human_verified_image_ground_truth",
        "confidence": "physical-slot",
    }],
    "R11-L1": [
        {"rule_text": "2X3", "candidate_mode": "additional_rule", "candidate_group_id": "R11-slot-A", "source": "human_verified_image_ground_truth", "confidence": "physical-slot"},
        {"rule_text": "3X1", "candidate_mode": "alternative_reading", "candidate_group_id": "R11-slot-B", "replaces_current_rules": ["2/3X1.0"], "source": "human_verified_image_ground_truth", "confidence": "physical-slot"},
    ],
    "R12-L1": [
        {"rule_text": "2X3", "candidate_mode": "additional_rule", "candidate_group_id": "R12-slot-A", "source": "human_verified_image_ground_truth", "confidence": "physical-slot"},
        {"rule_text": "3X1", "candidate_mode": "additional_rule", "candidate_group_id": "R12-slot-B", "source": "human_verified_image_ground_truth", "confidence": "physical-slot"},
    ],
}


def _upgrade_candidates(line: dict) -> list[dict]:
    meta = CANDIDATE_METADATA.get(line.get("line_id"))
    if not meta:
        return []
    fb = dict(line.get("fallback_candidate") or {})
    fb["multiplier_candidates"] = meta
    line["fallback_candidate"] = fb
    return meta


def repair_line(line: dict, *, apply: bool = True) -> dict:
    lid = line.get("line_id")
    exp = EXPECTED.get(lid)
    if exp is None:
        return {"line_id": lid, "changed": False, "skipped": "not a target"}
    groups = line.get("number_groups")
    if groups != exp["groups"]:
        raise AssertionError(f"{lid}: groups precondition mismatch: {groups}")
    if lid == "R01-L1":
        evidence = str(line.get("model_raw_text") or "") + str(line.get("raw_text") or "")
        if "¾" not in evidence and "3/4" not in evidence and "3 / 4" not in evidence:
            raise AssertionError(f"{lid}: missing 3/4 stacked evidence")
    if exp["multiplier_text"] is not None and not is_complete_multiplier(exp["multiplier_text"]):
        raise AssertionError(f"{lid}: expected multiplier invalid: {exp['multiplier_text']}")

    # R09/R11: structured fields already human-fixed -> NO-OP (never downgrade).
    if exp.get("human_already_fixed"):
        if (
            line.get("number_groups") == exp["groups"]
            and line.get("layout_hint") == exp["layout_hint"]
            and (line.get("multiplier_text") or "") == exp["multiplier_text"]
        ):
            _upgrade_candidates(line)  # metadata-only, allowed
            return {"line_id": lid, "changed": False, "no_op": True, "changes": []}

    changes = []
    if (line.get("multiplier_text") or "") != exp["multiplier_text"]:
        changes.append({"path": "multiplier_text", "before": line.get("multiplier_text"), "after": exp["multiplier_text"]})
    if (line.get("layout_hint") or "") != exp["layout_hint"]:
        changes.append({"path": "layout_hint", "before": line.get("layout_hint"), "after": exp["layout_hint"]})
    if str(line.get("raw_text") or "") != exp["raw"]:
        changes.append({"path": "raw_text", "before": line.get("raw_text"), "after": exp["raw"]})
    if str(line.get("human_raw_text") or "") != exp["raw"]:
        changes.append({"path": "human_raw_text", "before": line.get("human_raw_text"), "after": exp["raw"]})
    if str(line.get("correction_source") or "") != CORRECTION_SOURCE:
        changes.append({"path": "correction_source", "before": line.get("correction_source"), "after": CORRECTION_SOURCE})

    if apply and changes:
        line["multiplier_text"] = exp["multiplier_text"]
        line["multiplier_rules"] = _rules_struct(exp["multiplier_text"])
        line["layout_hint"] = exp["layout_hint"]
        line["raw_text"] = exp["raw"]
        line["human_raw_text"] = exp["raw"]
        line["correction_source"] = CORRECTION_SOURCE
        _upgrade_candidates(line)
        fb = dict(line.get("fallback_candidate") or {})
        fb.setdefault("evidence", [])
        fb["evidence"].append({
            "source": "human_verified_image_ground_truth",
            "rule": CORRECTION_SOURCE,
            "line_id": lid,
            "changes": changes,
            "machine_evidence": {"model_raw_text": line.get("model_raw_text"), "saved_combined_response": True},
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
        repair_line(by_id[t], apply=False)
    results = []
    for t in EXPECTED:
        results.append(repair_line(by_id[t], apply=apply))
    return {"results": results, "changed_lines": sum(1 for r in results if r["changed"])}


def main() -> None:
    apply = "--write" in sys.argv
    sid = "sample-014"
    draft = json.loads((DRAFT / f"{sid}.json").read_text(encoding="utf-8"))
    print(f"{sid}: mode={'WRITE' if apply else 'DRY'}")
    report = run_repair(draft, apply=apply)
    for r in report["results"]:
        print(" ", r["line_id"], "| changed:", r["changed"], "| no_op:", r.get("no_op", False), "| changes:", [(c["path"], c["before"], c["after"]) for c in r.get("changes", [])])
    if apply:
        (DRAFT / f"{sid}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WRITTEN")


if __name__ == "__main__":
    main()
