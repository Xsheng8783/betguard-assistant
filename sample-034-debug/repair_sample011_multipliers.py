"""One-time, evidence-driven, merge-only repair for sample-011 multiplier loss.

Only COMPLETE machine evidence may be written into multiplier_text /
multiplier_rules:
  - saved combined response section tokens (geometry first pass)
  - v3 prelabel rows (numbers + multiplier)
Fragments (2, 2/3, 4/3, X1) only enter fallback_candidate evidence and are
marked incomplete_multiplier_evidence. Every changed field records provenance;
review_action is NEVER auto-confirmed and no paid API is called.

Usage:
    python repair_sample011_multipliers.py            # dry-run
    python repair_sample011_multipliers.py --write    # apply with provenance
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import prelabel_geo as pg  # noqa: E402
from betguard.vision.multiplier_policy import (  # noqa: E402
    COMPLETE,
    classify_multiplier_token,
    is_complete_multiplier,
    merge_complete_rules,
    split_complete_rules,
)

DATASET = Path(os.environ.get("BETGUARD_DATASET", ""))
DRAFT = DATASET / "ground-truth-draft"
PRELABELS = DATASET / "prelabels"
COMBINED = Path(os.environ.get("BETGUARD_COMBINED_DIR", r"C:\Users\USER\Documents\539\ab_results\geo"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _section_rules(combined: dict) -> list[list[str]]:
    out = []
    for sec in combined.get("sections") or []:
        text = " ".join(
            str(t.get("text") or "")
            for r in sec.get("rows") or []
            for t in r.get("tokens") or []
            if str(t.get("text") or "").strip() not in ("", " ")
        )
        out.append(merge_complete_rules(pg.extract_multiplier_rules(text)))
    return out


def _v3_rows(prelabel: dict) -> list[tuple[list[str], list[list[str]], list[str], list[str]]]:
    rows = []
    try:
        parsed = json.loads(prelabel.get("raw_model_output") or "{}")
    except Exception:
        return rows
    for sec in parsed.get("sections") or []:
        for row in sec.get("rows") or []:
            nums = row.get("numbers") or []
            flat = []

            def _flat(v):
                if isinstance(v, list):
                    for x in v:
                        _flat(x)
                else:
                    flat.append(str(v))
            _flat(nums)
            nested = []
            if isinstance(nums, list) and nums and isinstance(nums[0], list):
                nested = [[str(x) for x in col] for col in nums]
            elif flat:
                nested = [flat]
            raw = str(row.get("multiplier") or "")
            rows.append((flat, nested, merge_complete_rules(pg.extract_multiplier_rules(raw)), raw.split()))
    return rows


def _best_v3(rows, ours):
    best = None
    best_score = (-1, 10**9)
    for flat, nested, rules, raw in rows:
        overlap = [n for n in ours if n in flat]
        score = (len(overlap), -len(set(flat) - set(ours)))
        if score > best_score:
            best_score = score
            best = (flat, nested, rules, raw)
    return best if best is not None and best_score[0] >= 2 else None


def _section_column_flags(combined: dict) -> list[bool]:
    """Per-section column evidence: >=2 x/× separators BETWEEN number tokens,
    or a "/" token. Dots between numbers mean a normal row."""
    flags = []
    for sec in combined.get("sections") or []:
        toks = [
            t
            for r in sec.get("rows") or []
            for t in r.get("tokens") or []
            if len(t.get("bbox", [])) == 4 and str(t.get("text") or "").strip()
        ]
        nums = [t for t in toks if str(t.get("text") or "").isdigit() and len(str(t.get("text") or "")) == 2]
        if not nums:
            flags.append(False)
            continue
        x_min = min((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
        x_max = max((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
        xs = [
            t for t in toks
            if str(t.get("text") or "") in "xX×" and x_min - 10 <= (t["bbox"][0] + t["bbox"][2]) / 2 <= x_max + 10
        ]
        has_slash = any(
            "/" in str(t.get("text") or "")
            and x_min - 10 <= (t["bbox"][0] + t["bbox"][2]) / 2 <= x_max + 10
            for t in toks
        )
        flags.append(len(xs) >= 2 or has_slash)
    return flags


def repair_line(line: dict, section_rules: list[str], v3_rows: list, *, apply: bool = True, column_evidence: bool = False) -> dict:
    """Plan (and optionally apply) one line's evidence-driven repair.

    Returns {line_id, sources, changes: [{path, before, after, source}],
             needs_human, plan_only}.
    """
    lid = line.get("line_id")
    plan: dict = {
        "line_id": lid,
        "sources": [],
        "changes": [],
        "needs_human": False,
    }
    current_rules = split_complete_rules(line.get("multiplier_text"))
    current_complete = is_complete_multiplier(line.get("multiplier_text"))

    groups = line.get("number_groups") or []
    ours = [str(n) for g in groups for n in (g if isinstance(g, list) else [g])]
    v3 = _best_v3(v3_rows, ours)
    v3_flat, v3_nested, v3_rules, v3_raw = (v3 if v3 is not None else ([], [], [], []))
    evidence_rules = merge_complete_rules(section_rules + v3_rules)
    plan["sources"].append("existing_structured" if current_complete else "existing_partial_or_missing")
    if section_rules:
        plan["sources"].append("saved_combined_response")
    if v3:
        plan["sources"].append("v3_prelabel")

    partial_tokens = [t for t in (str(line.get("multiplier_text") or "").split() + v3_raw)
                      if classify_multiplier_token(t) != COMPLETE]
    new_mult = line.get("multiplier_text")
    if evidence_rules:
        current_set = set(current_rules)
        evidence_set = set(evidence_rules)
        if not current_complete or (current_set <= evidence_set and current_set != evidence_set):
            # monotonic upgrade: only add complete rules that evidence proves
            new_mult = " ".join(evidence_rules)
    elif not current_complete:
        new_mult = None  # a partial fragment must leave multiplier_text

    if (new_mult or "") != (line.get("multiplier_text") or ""):
        source = "saved_combined_response" if section_rules else ("v3_prelabel" if v3_rules else "existing_structured")
        plan["changes"].append({
            "path": "multiplier_text",
            "before": line.get("multiplier_text"),
            "after": new_mult,
            "source": source,
        })

    # Column-structure recovery (e.g. R09): current single flat group +
    # v3 nested columns covering the SAME numbers -> restore column_bet.
    if (
        v3 is not None
        and column_evidence
        and len(groups) == 1
        and len(v3_nested) >= 2
        and line.get("layout_hint") != "column_bet"
        and len(groups[0]) >= 3
        and sorted(ours) == sorted(v3_flat)
    ):
        plan["changes"].append({
            "path": "number_groups",
            "before": groups,
            "after": v3_nested,
            "source": "v3_prelabel",
        })
        plan["changes"].append({
            "path": "layout_hint",
            "before": line.get("layout_hint"),
            "after": "column_bet",
            "source": "v3_prelabel",
        })

    if not evidence_rules and not current_complete:
        plan["needs_human"] = True
        if partial_tokens:
            plan["changes"].append({
                "path": "fallback_candidate.multiplier_partial_evidence",
                "before": None,
                "after": partial_tokens,
                "source": "partial_evidence",
            })

    real_changes = [c for c in plan["changes"] if not c.get("unchanged")]
    if apply:
        for ch in real_changes:
            if ch["path"].startswith("fallback_candidate"):
                continue
            if ch["path"] == "multiplier_text":
                line["multiplier_text"] = ch["after"]
                line["multiplier_rules"] = [
                    {
                        "rule_text": r,
                        "categories": [c for c in r.split("X", 1)[0] if c in "234"],
                        "value": r.split("X", 1)[1] if "X" in r else None,
                    }
                    for r in (ch["after"] or "").split() if r
                ] if ch["after"] else []
            elif ch["path"] == "number_groups":
                line["number_groups"] = ch["after"]
            elif ch["path"] == "layout_hint":
                line["layout_hint"] = ch["after"]
                line["uncertain"] = False
                line["uncertain_reason"] = None
        fb = dict(line.get("fallback_candidate") or {})
        if real_changes:
            fb.setdefault("evidence", [])
            fb["evidence"].append({
                "source": "repair_sample011",
                "rule": "merge_only_recovery",
                "line_id": lid,
                "changes": real_changes,
                "at": _now(),
            })
        if partial_tokens:
            fb["multiplier_partial_evidence"] = list(dict.fromkeys(
                list(fb.get("multiplier_partial_evidence") or []) + partial_tokens))
            line.setdefault("warnings", [])
            if "incomplete_multiplier_evidence" not in line["warnings"]:
                line["warnings"].append("incomplete_multiplier_evidence")
        if fb:
            line["fallback_candidate"] = fb
    return plan


def main() -> None:
    apply = "--write" in sys.argv
    sid = "sample-011"
    draft = json.loads((DRAFT / f"{sid}.json").read_text(encoding="utf-8"))
    combined = json.loads((COMBINED / f"{sid}-combined.json").read_text(encoding="utf-8"))
    pre = json.loads((PRELABELS / f"{sid}.json").read_text(encoding="utf-8"))
    v3 = _v3_rows(pre)
    sections = _section_rules(combined)
    column_flags = _section_column_flags(combined)
    print(f"{sid}: sections={len(sections)} lines={len(draft['lines'])} mode={'WRITE' if apply else 'DRY'}")
    for i, line in enumerate(draft["lines"]):
        sec_rules = sections[i] if i < len(sections) else []
        plan = repair_line(line, sec_rules, v3, apply=apply, column_evidence=column_flags[i] if i < len(column_flags) else False)
        print(" ", plan["line_id"], "| sources:", ",".join(plan["sources"]), "| needs_human:", plan["needs_human"])
        for ch in plan["changes"]:
            print("     change", ch["path"], "|", repr(ch.get("before")), "->", repr(ch.get("after")), "|", ch.get("source"))
    if apply:
        (DRAFT / f"{sid}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WRITTEN")


if __name__ == "__main__":
    main()
