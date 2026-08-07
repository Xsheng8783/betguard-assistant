"""One-time, line_id-keyed repair for sample-012 human-verified rule fixes.

Only R03 / R04 / R06 / R08 are patched, with correction_source =
"human_review_sample012_rule_fix" and provenance source =
"human_verified_image_ground_truth" (never claimed as OCR success).
review_action is NEVER auto-confirmed; model_raw_text / uncertain / region_id /
order / revision stay untouched. Any precondition mismatch fails closed.

Usage:
    python repair_sample012_rules.py            # dry-run
    python repair_sample012_rules.py --write    # apply
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from betguard.vision.multiplier_policy import (  # noqa: E402
    is_complete_multiplier,
    merge_complete_rules,
    normalize_rule,
    split_complete_rules,
)

DATASET = Path(os.environ.get("BETGUARD_DATASET", ""))
DRAFT = DATASET / "ground-truth-draft"
COMBINED = Path(os.environ.get("BETGUARD_COMBINED_DIR", r"C:\Users\USER\Documents\539\ab_results\geo"))

CORRECTION_SOURCE = "human_review_sample012_rule_fix"
TOOL_VERSION = "repair-sample012-v1"


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


def _canonical_text(line: dict) -> str:
    groups = line.get("number_groups") or []
    if line.get("layout_hint") == "column_bet":
        body = " / ".join(" ".join(g or []) for g in groups)
    else:
        body = " ".join(x for g in groups for x in (g or []))
    mult = str(line.get("multiplier_text") or "").strip()
    return f"{body} {mult}".strip() if mult else body


def _upgrade_candidate_metadata(line: dict) -> dict:
    """Backward-compatible candidate metadata upgrade (allowed merge)."""
    fb = dict(line.get("fallback_candidate") or {})
    adopted = fb.get("adopted_multiplier")
    if adopted and not fb.get("adopted_multipliers"):
        fb["adopted_multipliers"] = [str(adopted)]
    cands = fb.get("multiplier_candidates") or []
    upgraded = []
    seen = set()
    for i, c in enumerate(cands):
        if isinstance(c, dict):
            upgraded.append(c)
            seen.add(normalize_rule(str(c.get("rule_text") or "")))
            continue
        rule = normalize_rule(str(c))
        if rule in seen:
            continue
        seen.add(rule)
        upgraded.append({
            "rule_text": rule,
            "candidate_mode": "additional_rule",
            "candidate_group_id": f"{line.get('line_id')}-slot-{i}",
            "source": "legacy",
            "evidence": None,
        })
    if upgraded:
        fb["multiplier_candidates"] = upgraded
    line["fallback_candidate"] = fb
    return fb


EXPECTED = {
    "R03-L1": {
        "groups": [["02", "30", "33"]],
        "multiplier_text": "2X2 3X5",
        "canonical": "02 30 33 2X2 3X5",
    },
    "R04-L1": {
        "groups": [["02", "05", "17"]],
        "multiplier_text": "2X2 3X5",
        "canonical": "02 05 17 2X2 3X5",
    },
    "R06-L1": {
        "groups": [["34"], ["15", "25"]],
        "multiplier_text": "2X4",
        "canonical": "34 / 15 25 2X4",
        "layout_hint": "column_bet",
    },
    "R08-L1": {
        "groups": [["24", "34"], ["19", "39"], ["16", "36"], ["27", "37"]],
        "multiplier_text": "2/3/4X0.1",
        "canonical": "24 34 / 19 39 / 16 36 / 27 37 2/3/4X0.1",
    },
}


def repair_line(line: dict, *, apply: bool = True) -> dict:
    """One-time human-verified repair for ONE sample-012 target line."""
    lid = line.get("line_id")
    exp = EXPECTED.get(lid)
    if exp is None:
        return {"line_id": lid, "changed": False, "skipped": "not a target"}
    current_groups = line.get("number_groups")
    if lid == "R06-L1":
        if line.get("layout_hint") not in ("normal_row", "column_bet"):
            raise AssertionError(f"{lid}: unexpected layout {line.get('layout_hint')}")
        if current_groups not in ([["34", "15"]], [["34"], ["15"]], [["34"], ["15", "25"]]):
            raise AssertionError(f"{lid}: unexpected short-column groups {current_groups}")
    elif current_groups != exp["groups"]:
        raise AssertionError(f"{lid}: number_groups precondition mismatch: {current_groups}")
    if not is_complete_multiplier(exp["multiplier_text"]):
        raise AssertionError(f"{lid}: expected multiplier invalid: {exp['multiplier_text']}")

    changed = False
    changes = []
    if line.get("multiplier_text") != exp["multiplier_text"]:
        changes.append({"path": "multiplier_text", "before": line.get("multiplier_text"), "after": exp["multiplier_text"]})
    if lid == "R06-L1" and line.get("layout_hint") != "column_bet":
        changes.append({"path": "layout_hint", "before": line.get("layout_hint"), "after": "column_bet"})
    if line.get("number_groups") != exp["groups"]:
        changes.append({"path": "number_groups", "before": line.get("number_groups"), "after": exp["groups"]})
    if str(line.get("raw_text") or "") != exp["canonical"]:
        changes.append({"path": "raw_text", "before": line.get("raw_text"), "after": exp["canonical"]})
    if str(line.get("human_raw_text") or "") != exp["canonical"]:
        changes.append({"path": "human_raw_text", "before": line.get("human_raw_text"), "after": exp["canonical"]})
    if str(line.get("correction_source") or "") != CORRECTION_SOURCE:
        changes.append({"path": "correction_source", "before": line.get("correction_source"), "after": CORRECTION_SOURCE})

    if apply and changes:
        line["multiplier_text"] = exp["multiplier_text"]
        line["multiplier_rules"] = _rules_struct(exp["multiplier_text"])
        if "number_groups" in [c["path"] for c in changes]:
            line["number_groups"] = exp["groups"]
        if "layout_hint" in [c["path"] for c in changes]:
            line["layout_hint"] = exp["layout_hint"] if lid == "R06-L1" else line.get("layout_hint")
        line["raw_text"] = exp["canonical"]
        line["human_raw_text"] = exp["canonical"]
        line["correction_source"] = CORRECTION_SOURCE
        fb = dict(line.get("fallback_candidate") or {})
        fb.setdefault("evidence", [])
        fb["evidence"].append({
            "source": "human_verified_image_ground_truth",
            "rule": CORRECTION_SOURCE,
            "line_id": lid,
            "changes": changes,
            "machine_evidence": {
                "saved_combined_response": True,
                "v3_prelabel": True,
            },
            "at": _now(),
            "tool_version": TOOL_VERSION,
        })
        line["fallback_candidate"] = fb
        changed = True
    return {"line_id": lid, "changed": changed, "changes": changes}


def run_repair(draft: dict, *, apply: bool = True) -> dict:
    ids = [l.get("line_id") for l in draft.get("lines", [])]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate line_ids")
    by_id = {l.get("line_id"): l for l in draft.get("lines", [])}
    for t in EXPECTED:
        if t not in by_id:
            raise AssertionError(f"target missing: {t}")
    # preconditions for ALL targets before any write
    results = []
    for t in EXPECTED:
        results.append(repair_line(by_id[t], apply=False))
    if apply:
        for t in EXPECTED:
            repair_line(by_id[t], apply=True)
        results = [repair_line(by_id[t], apply=False) for t in EXPECTED]
    return {"results": results, "changed_lines": sum(1 for r in results if r["changed"])}


def main() -> None:
    apply = "--write" in sys.argv
    sid = "sample-012"
    draft = json.loads((DRAFT / f"{sid}.json").read_text(encoding="utf-8"))
    for line in draft.get("lines", []):
        _upgrade_candidate_metadata(line)
    print(f"{sid}: mode={'WRITE' if apply else 'DRY'}")
    report = run_repair(draft, apply=apply)
    for r in report["results"]:
        print(" ", r["line_id"], "| changed:", r["changed"], "| changes:", [(c["path"], c["before"], c["after"]) for c in r.get("changes", [])])
    if apply:
        (DRAFT / f"{sid}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WRITTEN")


if __name__ == "__main__":
    main()
