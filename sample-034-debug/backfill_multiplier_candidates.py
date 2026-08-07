"""Merge-only multiplier-candidate backfill for EXISTING review drafts.

This maintenance tool NEVER regenerates a draft and NEVER overwrites the
protected review fields. It only:

  - adds/merges ``line["fallback_candidate"]`` (source / rule / evidence /
    multiplier_candidates; ``adopted_multiplier`` from a previous adoption is
    preserved),
  - appends candidate-related warnings ONLY when they are missing,
  - reads ``prelabels/<sid>.json`` (read-only) for the v3 cross-pass evidence.

Protected fields stay byte-for-byte identical:
    model_raw_text, raw_text, human_raw_text, number_groups, multiplier_text,
    multiplier_rules, layout_hint, region_id, review_action, uncertain,
    reviewed_by, reviewed_at, revision

Usage:
    $env:BETGUARD_DATASET = "C:\\BetguardOCRDataset"
    python backfill_multiplier_candidates.py --dry-run
    python backfill_multiplier_candidates.py --exclude sample-010 --exclude sample-011
    python backfill_multiplier_candidates.py --verify --backup-dir <path>
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from betguard.vision.multiplier_policy import (  # noqa: E402
    COMPLETE,
    classify_multiplier_token,
    partial_tokens as policy_partial_tokens,
    split_complete_rules,
)

DATASET = Path(os.environ.get("BETGUARD_DATASET", ""))
DRAFT = DATASET / "ground-truth-draft"
PRELABELS = DATASET / "prelabels"

MULT_EXTRACT_RE = re.compile(
    r"(?<!\d)(?:二三|二三四|三四|四三|3\s*/\s*4|2\s*/\s*3|3/4|2/3|三|四|¾|⅔|"
    r"23|2\.3|234|2\.3\.4|23\.4|34|24|[234])\s*[xX×]\s*\d+(?:\.\d+)?(?:\s*\.\s*\d+)?"
)

PROTECTED_FIELDS = (
    "model_raw_text",
    "raw_text",
    "human_raw_text",
    "number_groups",
    "multiplier_text",
    "multiplier_rules",
    "layout_hint",
    "region_id",
    "review_action",
    "uncertain",
    "reviewed_by",
    "reviewed_at",
    "revision",
    "correction_source",
    "adopted",
)


def _norm_rule(rule: str) -> str:
    return (
        re.sub(r"\s+", "", rule or "")
        .replace("¾", "3/4")
        .replace("⅔", "2/3")
        .replace("×", "X")
        .replace("x", "X")
    )


def extract_rules(text: str) -> list[str]:
    out: list[str] = []
    for m in MULT_EXTRACT_RE.finditer(text or ""):
        raw = _norm_rule(m.group(0))
        cm = re.fullmatch(r"([^X]+)X(\d+(?:\.\d+)?)", raw)
        if cm and re.fullmatch(r"\d{2}", cm.group(1)) and re.fullmatch(r"\d{2}", cm.group(2)):
            continue  # two 2-digit numbers joined by x = column separator
        out.append(raw)
    return out


def _rule_categories(rule: str) -> set[str]:
    return set(re.findall(r"[234]", rule.split("X", 1)[0] if "X" in rule else rule))


def _flat(v) -> list[str]:
    if isinstance(v, list):
        out = []
        for x in v:
            out.extend(_flat(x))
        return out
    return [str(v)]


def _v3_rows(prelabel: dict) -> list[tuple[list[str], list[str], list[str]]]:
    rows: list[tuple[list[str], list[str], list[str]]] = []
    try:
        parsed = json.loads(prelabel.get("raw_model_output") or "{}")
    except Exception:
        return rows
    for sec in parsed.get("sections") or []:
        for row in sec.get("rows") or []:
            nums = row.get("numbers") or []
            flat = [n for n in _flat(nums) if re.fullmatch(r"\d{1,2}", str(n))]
            raw_tokens = [t for t in str(row.get("multiplier") or "").split() if t.strip()]
            rows.append((flat, extract_rules(str(row.get("multiplier") or "")), raw_tokens))
    return rows


def _best_v3_row(v3_rows: list[tuple[list[str], list[str], list[str]]], ours: list[str]) -> tuple[list[str], list[str], list[str]] | None:
    best = None
    best_overlap = 0
    best_extra = 10**9
    for row in v3_rows:
        flat, rules = row[0], row[1]
        raw_tokens = row[2] if len(row) > 2 else []
        overlap = [n for n in ours if str(n) in flat]
        extra = len(set(flat) - set(ours))
        if (len(overlap), -extra) > (best_overlap, -best_extra):
            best_overlap = len(overlap)
            best_extra = extra
            best = (flat, rules, raw_tokens)
    return best if best is not None and best_overlap >= 2 else None


def _current_rules(line: dict) -> list[str]:
    return split_complete_rules(line.get("multiplier_text"))


def _rule_parts(rule: str) -> dict | None:
    m = re.fullmatch(r"([234/]+)X(\d+(?:\.\d+)?)", _norm_rule(rule))
    if not m:
        return None
    return {"cats": set(re.findall(r"[234]", m.group(1))), "value": m.group(2)}


def _infer_mode(rule: str, current: list[str]) -> str:
    """Legacy inference: same value as a current rule -> same slot family
    (alternative reading); different value -> additional rule."""
    p = _rule_parts(rule)
    for r in current:
        q = _rule_parts(r)
        if p and q and q["value"] == p["value"]:
            return "alternative_reading"
    return "additional_rule"


def backfill_line(line: dict, v3_rows: list[tuple[list[str], list[str], list[str]]]) -> dict:
    """Merge candidate evidence into ONE line. Never touches protected fields.

    Returns stats: {"line_id", "candidates", "evidence_added", "warnings_added"}.
    """
    stats: dict = {"line_id": line.get("line_id"), "candidates": [], "evidence_added": False, "warnings_added": []}
    groups = line.get("number_groups") or []
    ours = [str(n) for g in groups for n in (g if isinstance(g, list) else [g])]
    best = _best_v3_row(v3_rows, ours)
    if best is None:
        return stats
    v3_flat, v3_rules = best[0], best[1]
    v3_raw_tokens = best[2] if len(best) > 2 else []
    current = _current_rules(line)
    candidates = [r for r in v3_rules if r not in current]
    seen = set(current)
    uniq_candidates = []
    for r in candidates:
        if r not in seen:
            seen.add(r)
            uniq_candidates.append(r)
    stats["candidates"] = uniq_candidates

    partial_toks = policy_partial_tokens(" ".join(v3_raw_tokens))
    stats["partial_tokens"] = partial_toks

    evidence = {
        "source": "v3_prelabel",
        "rule": "merge_only_never_replace",
        "matched_numbers": v3_flat,
        "v3_multiplier_rules": v3_rules,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    fallback = dict(line.get("fallback_candidate") or {})
    existing_cands = list(fallback.get("multiplier_candidates") or [])

    def _norm_cand(c):
        if isinstance(c, dict):
            return _norm_rule(str(c.get("rule_text") or ""))
        return _norm_rule(str(c))

    merged_cands = list(existing_cands)
    existing_rules = {_norm_cand(c) for c in existing_cands}
    for i, r in enumerate(uniq_candidates):
        if r in existing_rules:
            continue
        merged_cands.append({
            "rule_text": r,
            "candidate_mode": _infer_mode(r, current),
            "candidate_group_id": f"{line.get('line_id')}-slot-{i}",
            "source": "v3_prelabel",
            "evidence": {"matched_numbers": v3_flat, "v3_multiplier_rules": v3_rules},
        })
        existing_rules.add(r)
    if uniq_candidates or existing_cands:
        fallback["multiplier_candidates"] = merged_cands
    evidence_list = list(fallback.get("evidence") or [])
    if evidence not in evidence_list:
        evidence_list.append(evidence)
        stats["evidence_added"] = True
    fallback["evidence"] = evidence_list
    fallback.setdefault("source", "v3_prelabel")
    fallback.setdefault("rule", "merge_only_never_replace")
    line["fallback_candidate"] = fallback

    warnings = list(line.get("warnings") or [])
    if partial_toks:
        existing_partial = list(fallback.get("multiplier_partial_evidence") or [])
        for t in partial_toks:
            if t not in existing_partial:
                existing_partial.append(t)
        fallback["multiplier_partial_evidence"] = existing_partial
        if "incomplete_multiplier_evidence" not in warnings:
            warnings.append("incomplete_multiplier_evidence")
            stats["warnings_added"].append("incomplete_multiplier_evidence")
    if uniq_candidates and "cross_pass_multiplier_divergent" not in warnings:
        warnings.append("cross_pass_multiplier_divergent")
        stats["warnings_added"].append("cross_pass_multiplier_divergent")
    ours_cats = {c for r in current for c in _rule_categories(r)}
    if any(_rule_categories(r) - ours_cats for r in uniq_candidates) and "possible_stacked_category_digit" not in warnings:
        warnings.append("possible_stacked_category_digit")
        stats["warnings_added"].append("possible_stacked_category_digit")
    if stats["warnings_added"]:
        line["warnings"] = warnings
    return stats


def _json_byte_str(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def diff_protected(before: dict, after: dict) -> list[dict]:
    """Byte-for-byte protected-field diff. Returns violations."""
    violations: list[dict] = []
    for key in ("reviewed_by", "reviewed_at", "revision"):
        bv = before.get(key)
        av = after.get(key)
        if _json_byte_str(bv) != _json_byte_str(av):
            violations.append({"path": key, "before": bv, "after": av})
    before_lines = {l.get("line_id"): l for l in before.get("lines", [])}
    after_lines = {l.get("line_id"): l for l in after.get("lines", [])}
    for lid in sorted(set(before_lines) | set(after_lines)):
        bl = before_lines.get(lid)
        al = after_lines.get(lid)
        if bl is None or al is None:
            violations.append({"line_id": lid, "path": "<line presence>", "before": bl is not None, "after": al is not None})
            continue
        for key in PROTECTED_FIELDS:
            bv = bl.get(key)
            av = al.get(key)
            if _json_byte_str(bv) != _json_byte_str(av):
                violations.append({"line_id": lid, "path": key, "before": bv, "after": av})
        b_adopted = (bl.get("fallback_candidate") or {}).get("adopted_multiplier")
        a_adopted = (al.get("fallback_candidate") or {}).get("adopted_multiplier")
        if _json_byte_str(b_adopted) != _json_byte_str(a_adopted):
            violations.append({
                "line_id": lid,
                "path": "fallback_candidate.adopted_multiplier",
                "before": b_adopted,
                "after": a_adopted,
            })
    return violations


def load_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def backfill_sample(sid: str, *, dry_run: bool = False) -> dict:
    target = DRAFT / f"{sid}.json"
    pre_path = PRELABELS / f"{sid}.json"
    draft = load_json(target)
    if draft is None or not draft.get("lines"):
        return {"sample": sid, "error": "no draft"}
    pre = load_json(pre_path)
    if pre is None:
        return {"sample": sid, "error": "no prelabel"}
    v3_rows = _v3_rows(pre)
    stats: dict = {"sample": sid, "lines": len(draft["lines"]), "candidate_lines": 0, "candidates": 0, "evidence_added": 0}
    for line in draft["lines"]:
        st = backfill_line(line, v3_rows)
        if st["candidates"]:
            stats["candidate_lines"] += 1
            stats["candidates"] += len(st["candidates"])
        if st["evidence_added"]:
            stats["evidence_added"] += 1
    if not dry_run:
        target.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    verify = "--verify" in args
    backup_arg = None
    if "--backup-dir" in args:
        backup_arg = Path(args[args.index("--backup-dir") + 1])
    excludes = set()
    while "--exclude" in args:
        i = args.index("--exclude")
        excludes.add(args[i + 1])
        args = args[:i] + args[i + 2:]
    samples = [a for a in args if not a.startswith("--") and a != "--dry-run" and a != "--verify" and a != "--backup-dir"]
    if not samples:
        samples = [f"sample-{i:03d}" for i in range(2, 35)]
    samples = [s for s in samples if s not in excludes]

    if verify:
        if backup_arg is None:
            print("--verify requires --backup-dir")
            sys.exit(2)
        total = 0
        for sid in samples:
            before = load_json(backup_arg / f"{sid}.json")
            after = load_json(DRAFT / f"{sid}.json")
            if before is None or after is None:
                continue
            violations = diff_protected(before, after)
            for v in violations:
                total += 1
                print(json.dumps({"sample": sid, **v}, ensure_ascii=False))
        print(f"PROTECTED_DIFF_VIOLATIONS={total}")
        sys.exit(1 if total else 0)

    totals = {"candidate_lines": 0, "candidates": 0, "evidence_added": 0}
    for sid in samples:
        st = backfill_sample(sid, dry_run=dry_run)
        for k in totals:
            totals[k] += st.get(k, 0)
        mode = "DRY" if dry_run else "WRITE"
        print(f"{sid}: {mode} candidate_lines={st.get('candidate_lines', 0)} candidates={st.get('candidates', 0)} evidence_added={st.get('evidence_added', 0)} {st.get('error', '')}".strip())
    print("TOTALS:", totals)


if __name__ == "__main__":
    main()
