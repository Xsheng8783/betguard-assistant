"""One-time, evidence-driven, merge-only repair for sample-011.

ALL updates are keyed by ``line_id``; index/order/region-position based
correspondence is FORBIDDEN (a previous external edit mis-assigned R09's
fields to R02). The orchestrator asserts:

  - every target line_id exists exactly once,
  - all line_ids in the draft are unique,
  - the repair plan has no duplicate line_id,
  - number_groups / multiplier_text satisfy the expected preconditions,
  - on ANY precondition failure the whole batch fails closed (no write).

Phases:
  A. multiplier recovery from saved combined response + v3 prelabel
     (COMPLETE machine evidence only; fragments stay in fallback evidence)
  B. canonical raw repair for R03-L1..R09-L1 only:
     raw_text / human_raw_text = number_groups + multiplier_text,
     correction_source = "repair_sample011_canonical_raw_v2", provenance
     recorded; model_raw_text / number_groups / multiplier_text /
     multiplier_rules / layout_hint / region_id stay unchanged.

Usage:
    python repair_sample011_multipliers.py            # dry-run
    python repair_sample011_multipliers.py --write    # apply
"""
from __future__ import annotations

import json
import os
import re
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
    partial_tokens,
    split_complete_rules,
)

DATASET = Path(os.environ.get("BETGUARD_DATASET", ""))
DRAFT = DATASET / "ground-truth-draft"
PRELABELS = DATASET / "prelabels"
COMBINED = Path(os.environ.get("BETGUARD_COMBINED_DIR", r"C:\Users\USER\Documents\539\ab_results\geo"))

REPAIR_SOURCE = "repair_sample011_canonical_raw_v2"
TOOL_VERSION = "repair-sample011-v2"
TARGETS = ["R03-L1", "R04-L1", "R05-L1", "R06-L1", "R07-L1", "R08-L1", "R09-L1"]


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
            flat: list[str] = []

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


def _canonical_text(line: dict) -> str:
    groups = line.get("number_groups") or []
    if not groups:
        return str(line.get("raw_text") or "")
    if line.get("layout_hint") == "column_bet":
        body = " / ".join(" ".join(g or []) for g in groups)
    else:
        body = " ".join(x for g in groups for x in (g or []))
    mult = str(line.get("multiplier_text") or "").strip()
    return f"{body} {mult}".strip() if mult else body


def _multiplier_rules_from_text(text: str) -> list[dict]:
    out = []
    for r in (text or "").split():
        if not r:
            continue
        cats = [c for c in re.findall(r"[234]", r.split("X", 1)[0])] if "X" in r else []
        value = r.split("X", 1)[1] if "X" in r else None
        out.append({"rule_text": r, "categories": cats, "value": value})
    return out


def recover_multiplier(line: dict, section_rules: list[str], v3_rows: list, *, column_evidence: bool = False) -> dict:
    """Phase A: evidence-driven multiplier (+R09 structure) recovery.
    Returns {line_id, sources, changes, needs_human}; mutates line in place."""
    lid = line.get("line_id")
    plan: dict = {"line_id": lid, "sources": [], "changes": [], "needs_human": False}
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

    partial_toks = partial_tokens(str(line.get("multiplier_text") or "") + " " + " ".join(v3_raw))
    new_mult = line.get("multiplier_text")
    if evidence_rules:
        cur_set = set(current_rules)
        ev_set = set(evidence_rules)
        if not current_complete or (cur_set <= ev_set and cur_set != ev_set):
            new_mult = " ".join(evidence_rules)
    elif not current_complete:
        new_mult = None
    if (new_mult or "") != (line.get("multiplier_text") or ""):
        plan["changes"].append({
            "path": "multiplier_text",
            "before": line.get("multiplier_text"),
            "after": new_mult,
            "source": "saved_combined_response" if section_rules else ("v3_prelabel" if v3_rules else "existing_structured"),
        })

    if (
        v3 is not None
        and column_evidence
        and len(groups) == 1
        and len(v3_nested) >= 2
        and line.get("layout_hint") != "column_bet"
        and len(groups[0]) >= 3
        and sorted(ours) == sorted(v3_flat)
    ):
        plan["changes"].append({"path": "number_groups", "before": groups, "after": v3_nested, "source": "v3_prelabel"})
        plan["changes"].append({"path": "layout_hint", "before": line.get("layout_hint"), "after": "column_bet", "source": "v3_prelabel"})

    if not evidence_rules and not current_complete:
        plan["needs_human"] = True

    for ch in plan["changes"]:
        if ch["path"] == "multiplier_text":
            line["multiplier_text"] = ch["after"]
            line["multiplier_rules"] = _multiplier_rules_from_text(ch["after"]) if ch["after"] else []
        elif ch["path"] == "number_groups":
            line["number_groups"] = ch["after"]
        elif ch["path"] == "layout_hint":
            line["layout_hint"] = ch["after"]
            line["uncertain"] = False
            line["uncertain_reason"] = None
    if partial_toks:
        fb = dict(line.get("fallback_candidate") or {})
        fb["multiplier_partial_evidence"] = list(dict.fromkeys(
            list(fb.get("multiplier_partial_evidence") or []) + partial_toks))
        line["fallback_candidate"] = fb
        line.setdefault("warnings", [])
        if "incomplete_multiplier_evidence" not in line["warnings"]:
            line["warnings"].append("incomplete_multiplier_evidence")
    return plan


def apply_canonical_raw(line: dict) -> dict:
    """Phase B: canonical raw_text / human_raw_text from structured fields.
    line_id-keyed by the caller; returns {changed, warnings_removed}."""
    lid = line.get("line_id")
    canonical = _canonical_text(line)
    current_raw = str(line.get("raw_text") or "")
    current_human = str(line.get("human_raw_text") or "")
    current_src = str(line.get("correction_source") or "")
    changed = False
    warnings_removed: list[str] = []
    if current_src == REPAIR_SOURCE and current_raw == canonical and current_human == canonical:
        return {"line_id": lid, "changed": False, "warnings_removed": []}
    provenance = {
        "source": "confirmed_structured_fields",
        "rule": REPAIR_SOURCE,
        "line_id": lid,
        "before_raw_text": current_raw,
        "after_raw_text": canonical,
        "repaired_at": _now(),
        "tool_version": TOOL_VERSION,
        "input_number_groups": line.get("number_groups"),
        "input_multiplier_text": line.get("multiplier_text"),
    }
    line["raw_text"] = canonical
    line["human_raw_text"] = canonical
    line["correction_source"] = REPAIR_SOURCE
    fb = dict(line.get("fallback_candidate") or {})
    fb.setdefault("evidence", [])
    fb["evidence"].append(provenance)
    line["fallback_candidate"] = fb
    warnings = list(line.get("warnings") or [])
    if is_complete_multiplier(line.get("multiplier_text")):
        if "incomplete_multiplier_evidence" in warnings:
            warnings = [w for w in warnings if w != "incomplete_multiplier_evidence"]
            warnings_removed.append("incomplete_multiplier_evidence")
    if warnings != list(line.get("warnings") or []):
        line["warnings"] = warnings
    return {"line_id": lid, "changed": True, "warnings_removed": warnings_removed}


def _assert_unique_ids(lines: list[dict]) -> None:
    ids = [l.get("line_id") for l in lines]
    if len(ids) != len(set(ids)):
        raise AssertionError(f"duplicate line_ids: {ids}")


def _assert_preconditions(line: dict) -> None:
    lid = line.get("line_id")
    groups = line.get("number_groups") or []
    flat = [n for g in groups for n in (g if isinstance(g, list) else [g])]
    if not flat:
        raise AssertionError(f"{lid}: number_groups empty")
    if not all(re.fullmatch(r"\d{1,2}", str(n)) and 1 <= int(str(n)) <= 39 for n in flat):
        raise AssertionError(f"{lid}: invalid numbers {flat}")
    if not is_complete_multiplier(line.get("multiplier_text")):
        raise AssertionError(f"{lid}: multiplier_text not complete: {line.get('multiplier_text')!r}")
    if not groups or len(groups) < 1:
        raise AssertionError(f"{lid}: no number_groups")


def run_repair(draft: dict, combined: dict, prelabel: dict, *, apply: bool = True) -> dict:
    """line_id-keyed orchestrator. Fails closed on ANY precondition problem."""
    lines = draft.get("lines") or []
    _assert_unique_ids(lines)
    lines_by_id = {l.get("line_id"): l for l in lines}
    if len(lines_by_id) != len(lines):
        raise AssertionError("line_id map size mismatch")
    for target in TARGETS:
        if target not in lines_by_id:
            raise AssertionError(f"target missing: {target}")
    if len({t for t in TARGETS}) != len(TARGETS):
        raise AssertionError("repair plan has duplicate line_id")

    sections = _section_rules(combined)
    flags = _section_column_flags(combined)
    v3 = _v3_rows(prelabel)
    report = {"sources": {}, "phase_a": [], "phase_b": [], "needs_human": []}

    # Phase A: evidence-driven multiplier recovery, targets only.
    for i, line in enumerate(lines):
        if line.get("line_id") not in TARGETS:
            continue
        sec_rules = sections[i] if i < len(sections) else []
        col_flag = flags[i] if i < len(flags) else False
        plan = recover_multiplier(line, sec_rules, v3, column_evidence=col_flag)
        report["sources"][line.get("line_id")] = plan["sources"]
        report["phase_a"].append(plan)
        if plan["needs_human"]:
            report["needs_human"].append(line.get("line_id"))

    # Preconditions must hold for every target BEFORE any write.
    for target in TARGETS:
        _assert_preconditions(lines_by_id[target])

    # Phase B: canonical raw for targets only.
    for target in TARGETS:
        if apply:
            res = apply_canonical_raw(lines_by_id[target])
        else:
            canonical = _canonical_text(lines_by_id[target])
            res = {"line_id": target, "changed": lines_by_id[target].get("correction_source") != REPAIR_SOURCE
                   or str(lines_by_id[target].get("raw_text") or "") != canonical
                   or str(lines_by_id[target].get("human_raw_text") or "") != canonical,
                   "warnings_removed": []}
        report["phase_b"].append(res)

    # Global identity checks (R02 vs R09 must never be aliases).
    r02 = lines_by_id["R02-L1"]
    r09 = lines_by_id["R09-L1"]
    if str(r02.get("model_raw_text")) == str(r09.get("model_raw_text")) and r02.get("model_raw_text") is not None:
        raise AssertionError("R02 and R09 model_raw_text identical")
    if r02.get("number_groups") == r09.get("number_groups"):
        raise AssertionError("R02 and R09 number_groups identical")
    payloads = []
    for line in lines:
        for ev in (line.get("fallback_candidate") or {}).get("evidence") or []:
            payloads.append(json.dumps({k: v for k, v in ev.items() if k != "at"}, ensure_ascii=False, sort_keys=True))
    if len(payloads) != len(set(payloads)):
        raise AssertionError("duplicate provenance repair payloads across different lines")
    return report


def main() -> None:
    apply = "--write" in sys.argv
    sid = "sample-011"
    draft = json.loads((DRAFT / f"{sid}.json").read_text(encoding="utf-8"))
    combined = json.loads((COMBINED / f"{sid}-combined.json").read_text(encoding="utf-8"))
    pre = json.loads((PRELABELS / f"{sid}.json").read_text(encoding="utf-8"))
    print(f"{sid}: mode={'WRITE' if apply else 'DRY'}")
    report = run_repair(draft, combined, pre, apply=apply)
    for lid in TARGETS:
        print(" ", lid, "| sources:", ",".join(report["sources"].get(lid, [])), "| needs_human:", lid in report["needs_human"])
    print("phase_b:", [(r["line_id"], r["changed"], r.get("warnings_removed")) for r in report["phase_b"]])
    if apply:
        (DRAFT / f"{sid}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WRITTEN")


if __name__ == "__main__":
    main()
