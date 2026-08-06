"""Idempotent migration: backfill empty normal-row multiplier_text fields
from the existing raw_text (no model calls, no overwrite of human values).

Rules (customer handoff spec):
- Only fill when multiplier_text is empty.
- Same existing value -> unchanged; different value -> conflict marker,
  never overwrite.
- Never touch model_raw_text / number_groups / review_action / approval.
- Record source=derived_from_existing_raw_text and before/after.
- Backup before writing; second run must report modified=0.

Usage:
    $env:BETGUARD_DATASET = "C:\\BetguardOCRDataset"
    python migration_backfill_multipliers.py [sample-007 ...]
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATASET = Path(os.environ["BETGUARD_DATASET"])
DRAFT = DATASET / "ground-truth-draft"
REPORT = Path(__file__).resolve().parent / "migration_multiplier_report.json"

# Normal-row multiplier: category may be Chinese (三四/二三/三/四), digit
# shorthand (34/23/2/3/4), fraction glyphs (¾/⅔) or spaced "3 / 4" / "2 / 3",
# followed by x/× and a numeric value. Last match wins (multiplier at end).
MULT_RE = re.compile(
    r"(?<!\d)(?:二三|二三四|三四|四三|3\s*/\s*4|2\s*/\s*3|3/4|2/3|三|四|¾|⅔|"
    r"23|2\.3|234|2\.3\.4|23\.4|34|24|[234])"
    r"\s*[xX×]\s*\d+(?:\.\d+)?(?:\s*\.\s*\d+)?"
)


def _norm_mult(value: str) -> str:
    """Compact + canonicalize a multiplier for equality comparison."""
    return re.sub(r"\s+", "", value).replace("×", "X").replace("x", "X")


def extract_normal_multiplier(raw_text: str | None) -> str | None:
    """Extract + normalize a normal-row multiplier from raw text.

    Accepted: "2 x 1", "3 / 4 x 1", "2 / 3 × 0.1", "34X1", "三四X1".
    Returns compact "CATXvalue" (e.g. "2X1", "3/4X1", "三四X1") or None.
    """
    if not raw_text:
        return None
    matches = list(MULT_RE.finditer(raw_text))
    if not matches:
        return None
    raw = matches[0].group(0)  # top play mark first
    raw = raw.replace("¾", "3/4").replace("⅔", "2/3")
    compact = re.sub(r"\s+", "", raw)
    cm = re.fullmatch(r"([^xX×]+)[xX×](\d+(?:\.\d+)?)", compact)
    if not cm:
        return None
    return f"{cm.group(1)}X{cm.group(2)}"


def all_normal_multipliers(raw_text: str | None) -> list[str]:
    """All normalized multiplier candidates in raw text (for conflict checks)."""
    if not raw_text:
        return []
    out = []
    for m in MULT_RE.finditer(raw_text):
        raw = m.group(0).replace("¾", "3/4").replace("⅔", "2/3")
        compact = re.sub(r"\s+", "", raw)
        cm = re.fullmatch(r"([^xX×]+)[xX×](\d+(?:\.\d+)?)", compact)
        if cm:
            out.append(_norm_mult(f"{cm.group(1)}X{cm.group(2)}"))
    return out


def _has_human_provenance(line: dict) -> bool:
    pm = line.get("play_mark") or {}
    return bool(
        line.get("human_raw_text")
        or line.get("correction_source")
        or line.get("human_edited")
        or pm.get("applied_roi")
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate_sample(sid: str) -> dict:
    target = DRAFT / f"{sid}.json"
    if not target.exists():
        return {"sample": sid, "error": "no draft"}
    j = json.loads(target.read_text(encoding="utf-8"))
    backup = target.with_name(target.name + ".bak-migration")
    shutil.copy2(target, backup)
    stats = {"modified": 0, "unchanged": 0, "conflicts": 0, "unparsed": 0}
    changed = False
    for line in j.get("lines", []):
        if line.get("layout_hint") != "normal_row":
            continue
        existing = line.get("multiplier_text")
        extracted = extract_normal_multiplier(line.get("raw_text"))
        candidates = all_normal_multipliers(line.get("raw_text"))
        if existing:
            if _has_human_provenance(line):
                stats["unchanged"] += 1
            elif extracted is None or _norm_mult(existing) in candidates:
                stats["unchanged"] += 1
            else:
                stats["conflicts"] += 1
                line.setdefault("warnings", [])
                if "multiplier_conflict" not in line["warnings"]:
                    line["warnings"].append("multiplier_conflict")
                line["uncertain"] = True
                line["uncertain_reason"] = "multiplier_conflict"
                changed = True
            continue
        if extracted is None:
            stats["unparsed"] += 1
            continue
        line["multiplier_text"] = extracted
        line.setdefault("derived_fields", []).append({
            "field": "multiplier_text",
            "source": "derived_from_existing_raw_text",
            "before": None,
            "after": extracted,
            "at": _now(),
        })
        stats["modified"] += 1
        changed = True
    if changed:
        target.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["sample"] = sid
    return stats


def main() -> None:
    samples = (
        [a for a in __import__("sys").argv[1:]]
        or [f"sample-{i:03d}" for i in range(7, 34)]
    )
    totals = {"modified": 0, "unchanged": 0, "conflicts": 0, "unparsed": 0}
    rows = []
    for sid in samples:
        st = migrate_sample(sid)
        rows.append(st)
        for k in ("modified", "unchanged", "conflicts", "unparsed"):
            totals[k] += st.get(k, 0)
        print(f"{sid}: modified={st.get('modified',0)} unchanged={st.get('unchanged',0)} "
              f"conflicts={st.get('conflicts',0)} unparsed={st.get('unparsed',0)}", flush=True)
    REPORT.write_text(
        json.dumps({"at": _now(), "totals": totals, "samples": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("TOTALS:", totals)


if __name__ == "__main__":
    main()
