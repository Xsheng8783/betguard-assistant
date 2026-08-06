"""Systemic 柱碰 pipeline: combined model call (per-bet sections + token bbox)
-> per-section X-clustering -> draft."""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from betguard.vision.column_geometry import build_columns_from_bbox, build_grid_from_rows  # noqa: E402
from betguard.vision.replay import parse_model_json  # noqa: E402
from test_combined_bbox import PROMPT, call  # noqa: E402

ROOT = Path(os.environ["BETGUARD_DATASET"])  # dataset root, e.g. C:\BetguardOCRDataset
DRAFT = ROOT / "ground-truth-draft"
PRELABELS = ROOT / "prelabels"
RAW = ROOT / "raw"
GEO = Path(__file__).resolve().parent / "ab_results" / "geo"
CROPS = ROOT / "audit" / "play_mark_crops"

MULT_EXTRACT_RE = __import__("re").compile(
    r"(?:二三|二三四|三四|三|四|¾|⅔|23|2\.3|234|2\.3\.4|23\.4|3/4|2/3|[234])\s*[xX×]\s*\d+(?:\.\d+)?"
)


def _cx(t: dict) -> float:
    return (t["bbox"][0] + t["bbox"][2]) / 2


def extract_multiplier(text: str) -> str | None:
    """Reliable multiplier from the model's own section text (last match)."""
    matches = MULT_EXTRACT_RE.findall(text)
    if not matches:
        return None
    return matches[-1].replace("¾", "3/4").replace("⅔", "2/3")


def section_to_lines(
    section: dict,
    region_id: str,
    img_path: Path | None = None,
    crop_box: list[int] | None = None,
) -> list[dict]:
    rows = section.get("rows") or []
    toks = []
    for r in rows:
        for t in (r.get("tokens") or []):
            if len(t.get("bbox", [])) == 4 and str(t.get("text") or "").strip() not in ("", " "):
                toks.append({"text": str(t.get("text") or ""), "bbox": [int(x) for x in t["bbox"]]})
    nums = [t for t in toks if re.fullmatch(r"\d{2}", str(t.get("text") or ""))]
    if not nums:
        return _normal_lines(rows, region_id, img_path=img_path)
    section_text = " ".join(str(t.get("text") or "") for t in toks)
    if "車" in section_text:
        return _car_lines(section, region_id)
    ys = [(t["bbox"][1] + t["bbox"][3]) / 2 for t in nums]
    single_row = (max(ys) - min(ys)) < 25
    # Column-combo detection: >=3 numbers AND (>=2 standalone x/× separators
    # OR a "/" token). Column bets go through the column_combo ROI (never the
    # play_mark ROI), so the parser can rebuild columns without flattening.
    x_seps = [t for t in toks if str(t.get("text") or "") in "xX×"]
    has_slash = any("/" in str(t.get("text") or "") for t in toks)
    column_like = len(nums) >= 3 and (len(x_seps) >= 2 or has_slash)
    if column_like and img_path:
        combo = _read_column_combo(
            img_path, toks,
            save_path=CROPS / f"{img_path.stem}-{region_id}-COL.png",
            crop_box=crop_box,
        )
        if combo is not None and len(combo.get("columns") or []) >= 2:
            return _column_combo_line(region_id, combo)
    if single_row or len(nums) < 3:
        return _normal_lines(rows, region_id, img_path=img_path)
    nums_cx_min = min((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
    nums_cx_max = max((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
    # clean zone: numbers + separators inside the number band; collision digits
    # and multiplier pieces only in the immediate right band (avoid neighbor bets)
    toks = [
        t for t in toks
        if (re.fullmatch(r"\d{2}", str(t.get("text") or "")) and 1 <= int(str(t.get("text") or "")) <= 49)
        or (str(t.get("text") or "") in "xX×")
        or (str(t.get("text") or "") in "234" and nums_cx_min - 25 <= _cx(t) <= nums_cx_max + 90)
        or (str(t.get("text") or "") in "01." and nums_cx_max + 8 <= _cx(t) <= nums_cx_max + 200)
    ]
    res = build_grid_from_rows(toks) if len(toks) >= 3 else None
    if res is not None and res["column_count"] >= 2:
        cols = res["columns"]
        mult = res["multiplier"]
        coll = res["collision_raw"]
        extracted = extract_multiplier(section_text)
        # The model often writes "2 x 0 . 1" with spaces; strip them so the
        # trailing-digit and collision-merge checks see "2x0.1".
        extracted_flat = re.sub(r"\s+", "", extracted) if extracted else ""
        if extracted and re.search(r"[xX×]\d+$", extracted_flat):
            rest = section_text[section_text.rfind(extracted) + len(extracted):]
            m2 = re.match(r"\s*\.\s*(\d+)", rest)
            if m2:
                extracted += "." + m2.group(1)
        if coll and extracted:
            cm = re.match(r"([234])[xX×]([\d.]+)", re.sub(r"\s+", "", extracted))
            if cm and cm.group(1) in coll:
                extracted = f"{coll}X{cm.group(2)}"
        mult_txt = extracted or (f"{coll}X{mult}" if coll and mult else (f"X{mult}" if mult else (coll or None)))
        uncertain = False
        uncertain_reason = None
        warnings = ["geometry_x_clustered"]
        play_mark = None
        text = " / ".join(" ".join(v) for v in cols.values())
        if mult_txt:
            text = f"{text} {mult_txt}"
        return [{
            "line_id": f"{region_id}-L1",
            "entry_id": f"{region_id}-E1",
            "region_id": region_id,
            "order": 1,
            "raw_text": text,
            "number_groups": [list(v) for v in cols.values()],
            "multiplier_text": mult_txt,
            "layout_hint": "column_bet",
            "play_text": None,
            "play_type": None,
            "uncertain": uncertain,
            "uncertain_reason": uncertain_reason,
            "alternatives": [],
            "play_mark": play_mark,
            "warnings": warnings,
            "review_action": "pending",
            "human_added": False,
        }]
    # normal row: use model row numbers
    lines = []
    for i, r in enumerate(rows, 1):
        nums = r.get("numbers") or []
        flat = [str(x) for sub in (nums if nums and isinstance(nums[0], list) else [nums]) for x in (sub if isinstance(sub, list) else [sub])] if nums else []
        toktxt = "".join(str(t.get("text") or "") for t in (r.get("tokens") or []) if str(t.get("text") or "") != " ")
        lines.append({
            "line_id": f"{region_id}-L{i}",
            "entry_id": f"{region_id}-E{i}",
            "region_id": region_id,
            "order": i,
            "raw_text": toktxt or " ".join(flat),
            "number_groups": [flat] if flat else [],
            "multiplier_text": r.get("multiplier"),
            "layout_hint": "normal_row",
            "play_text": None,
            "play_type": None,
            "uncertain": False,
            "uncertain_reason": None,
            "alternatives": [],
            "warnings": [],
            "review_action": "pending",
            "human_added": False,
        })
    return lines


def _car_lines(section: dict, region_id: str) -> list[dict]:
    toks = [
        str(t.get("text") or "")
        for r in (section.get("rows") or []) for t in (r.get("tokens") or [])
        if str(t.get("text") or "").strip() not in ("", " ")
    ]
    text = " ".join(toks)
    numbers = re.findall(r"\d{2}", text)
    numbers = [n for n in numbers if 1 <= int(n) <= 49]
    m = re.search(r"各[\d.\s]*車", text)
    if m:
        text = " ".join(numbers) + " " + m.group(0) if numbers else m.group(0)
    return [{
        "line_id": f"{region_id}-L1",
        "entry_id": f"{region_id}-E1",
        "region_id": region_id,
        "order": 1,
        "raw_text": text,
        "number_groups": [numbers] if numbers else [],
        "multiplier_text": extract_multiplier(text),
        "layout_hint": "normal_row",
        "play_text": text,
        "play_type": "car_bet",
        "uncertain": True,
        "uncertain_reason": "unsupported_play_semantics",
        "alternatives": [],
        "warnings": ["car_bet_preserved"],
        "review_action": "pending",
        "human_added": False,
    }]


def _normal_lines(rows: list[dict], region_id: str, img_path: Path | None = None) -> list[dict]:
    lines = []
    for i, r in enumerate(rows, 1):
        nums = r.get("numbers") or []
        flat = []
        if nums:
            if isinstance(nums[0], list):
                flat = [str(x) for sub in nums for x in sub]
            else:
                flat = [str(x) for x in nums]
        toktxt = " ".join(str(t.get("text") or "") for t in (r.get("tokens") or []) if str(t.get("text") or "") not in ("", " "))
        mult = r.get("multiplier")
        if not mult:
            m = re.search(
                r"(?:二三|二三四|三四|三|四|¾|⅔|23|2\.3|234|2\.3\.4|23\.4|3/4|2/3)\s*[xX×]\s*\d+(?:\.\d+)?",
                toktxt,
            )
            if m:
                mult = m.group(0).replace("¾", "3/4").replace("⅔", "2/3")
        warnings = []
        uncertain = False
        uncertain_reason = None
        play_mark = None
        toks = [
            t for t in (r.get("tokens") or [])
            if len(t.get("bbox", [])) == 4 and str(t.get("text") or "").strip()
        ]
        # Stage 2 (three-stage design): the right-side play zone is re-read as
        # a zoomed crop with a prompt that treats vertical stacking as text.
        # Stage 3: merge — if the two AI passes disagree, do NOT auto-fill;
        # mark needs_review and keep the crop + both readings as evidence.
        num_toks = [t for t in toks if re.fullmatch(r"\d{2}", str(t.get("text") or ""))]
        max_num_cx = max((_cx(t) for t in num_toks), default=-1)
        play_toks = [
            t for t in toks
            if _cx(t) >= max_num_cx + 10
            and not re.fullmatch(r"\d{2}", str(t.get("text") or ""))
        ]
        first_cats = sorted({
            c for t in play_toks for c in re.findall(r"[234]", str(t.get("text") or ""))
        })
        if play_toks and first_cats and img_path:
            save_path = CROPS / f"{img_path.stem}-{region_id}-L{i}.png"
            roi = _read_play_mark(img_path, play_toks, save_path=save_path)
            uncertain, uncertain_reason, warn, play_mark = _merge_play_mark(first_cats, roi)
            if warn:
                warnings.append(warn)
        rt = toktxt or " ".join(flat)
        lines.append({
            "line_id": f"{region_id}-L{i}",
            "entry_id": f"{region_id}-E{i}",
            "region_id": region_id,
            "order": i,
            "raw_text": rt,
            "model_raw_text": rt,
            "human_raw_text": None,
            "number_groups": [flat] if flat else [],
            "multiplier_text": mult,
            "layout_hint": "normal_row",
            "play_text": None,
            "play_type": None,
            "uncertain": uncertain,
            "uncertain_reason": uncertain_reason,
            "alternatives": [],
            "play_mark": play_mark,
            "warnings": warnings,
            "review_action": "pending",
            "human_added": False,
        })
    return lines
def _read_column_combo(
    img_path: Path,
    toks: list[dict],
    save_path: Path | None = None,
    crop_box: list[int] | None = None,
) -> dict | None:
    """Stage-2 column-combo read: crop the FULL column grid and ask for
    columns + collision + multiplier (never flatten to a single line)."""
    from test_combined_bbox import call_column_combo_crop

    x1 = min(t["bbox"][0] for t in toks)
    y1 = min(t["bbox"][1] for t in toks)
    x2 = max(t["bbox"][2] for t in toks)
    y2 = max(t["bbox"][3] for t in toks)
    best: tuple[dict, list[list[str]]] | None = None
    best_score = -1
    for attempt in range(3):
        box = crop_box
        if box is not None:
            dy = (-8, 0, 8)[attempt]
            box = [box[0], box[1] + dy, box[2], box[3]]
        try:
            content = call_column_combo_crop(
                img_path, [x1, y1, x2, y2],
                save_path=save_path,
                box=box,
            )
        except Exception:
            continue
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        cols = obj.get("columns")
        if not isinstance(cols, list) or len(cols) < 3:
            continue
        norm: list[list[str]] = []
        for col in cols:
            if not isinstance(col, list):
                norm = []
                break
            cells = [str(c).strip() for c in col if str(c).strip()]
            if not cells or any(not re.fullmatch(r"\d{1,2}", c) for c in cells):
                norm = []
                break
            norm.append(cells)
        if not norm:
            continue
        score = sum(len(c) for c in norm)
        if score > best_score:
            best_score = score
            best = (obj, norm)
    if best is None:
        return None
    obj, norm_cols = best
    mult = obj.get("multiplier")
    coll = obj.get("collision")
    return {
        "columns": norm_cols,
        "multiplier": str(mult).strip() if mult not in (None, "") else None,
        "collision": str(coll).strip() if coll not in (None, "") else None,
        "uncertain": bool(obj.get("uncertain")),
        "uncertain_reason": obj.get("uncertain_reason"),
    }


def _column_combo_line(region_id: str, combo: dict) -> list[dict]:
    cols = combo["columns"]
    coll = combo.get("collision")
    mult = combo.get("multiplier")
    if coll and mult:
        mult_txt = f"{coll}X{mult}"
    elif mult:
        mult_txt = f"X{mult}"
    else:
        mult_txt = coll
    text = " / ".join(" ".join(c) for c in cols)
    if mult_txt:
        text = f"{text} {mult_txt}"
    warnings = ["column_combo_roi"]
    if combo.get("uncertain"):
        warnings.append("column_combo_roi_uncertain")
    return [{
        "line_id": f"{region_id}-L1",
        "entry_id": f"{region_id}-E1",
        "region_id": region_id,
        "order": 1,
        "raw_text": text,
        "number_groups": cols,
        "multiplier_text": mult_txt,
        "layout_hint": "column_bet",
        "play_text": None,
        "play_type": None,
        "uncertain": bool(combo.get("uncertain")),
        "uncertain_reason": "column_combo_uncertain" if combo.get("uncertain") else None,
        "alternatives": [],
        "play_mark": None,
        "warnings": warnings,
        "review_action": "pending",
        "human_added": False,
    }]


def _read_play_mark(img_path: Path, play_toks: list[dict], save_path: Path | None = None) -> dict | None:
    """Stage-2 ROI read: crop the union of the play tokens (full vertical
    extent), upscale + PNG, ask the structured play_mark prompt."""
    from test_combined_bbox import call_play_mark_crop

    x1 = min(t["bbox"][0] for t in play_toks)
    y1 = min(t["bbox"][1] for t in play_toks)
    x2 = max(t["bbox"][2] for t in play_toks)
    y2 = max(t["bbox"][3] for t in play_toks)
    try:
        content = call_play_mark_crop(img_path, [x1, y1, x2, y2], save_path=save_path)
    except Exception:
        return None
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    pm = obj.get("play_mark") if isinstance(obj, dict) else None
    if not isinstance(pm, dict):
        return None
    # New user schema: upper/lower/other slots. Keep only single category
    # digits 2/3/4; multi-char junk like "31"/"39" from neighboring rows must
    # not count as category digits.
    def _digits(value):
        return [c for raw in (value or []) for c in re.findall(r"[234]", str(raw))]

    upper = _digits(pm.get("upper_digits"))
    lower = _digits(pm.get("lower_digits"))
    other = _digits(pm.get("other_visible_digits"))
    cats = upper + lower + other
    seen: set[str] = set()
    uniq = [c for c in cats if not (c in seen or seen.add(c))]
    mult = pm.get("multiplier")
    return {
        "layout": str(pm.get("layout") or ""),
        "categories": uniq,
        "upper_digits": upper,
        "lower_digits": lower,
        "other_visible_digits": other,
        "multiplier": str(mult) if mult is not None else None,
        "raw_text": str(pm.get("raw_play_text") or ""),
        "uncertain": bool(pm.get("uncertain")) or bool(obj.get("overall_uncertain")),
        "uncertain_candidates": [str(x) for x in (pm.get("uncertain_candidates") or [])],
        "uncertain_reason": pm.get("uncertain_reason") or obj.get("overall_uncertain_reason"),
        "crop_path": str(save_path) if save_path else None,
    }


def _merge_play_mark(first_cats: list[str], roi: dict | None) -> tuple[bool, str | None, str | None, dict | None]:
    """Three-stage merge. Returns (uncertain, reason, warning, evidence)."""
    def _evidence():
        return {
            "first_pass_categories": first_cats,
            "roi_categories": roi["categories"],
            "roi_upper_digits": roi.get("upper_digits"),
            "roi_lower_digits": roi.get("lower_digits"),
            "roi_multiplier": roi.get("multiplier"),
            "roi_layout": roi.get("layout"),
            "roi_raw_text": roi.get("raw_text"),
            "roi_uncertain": roi.get("uncertain"),
            "roi_uncertain_candidates": roi.get("uncertain_candidates"),
            "crop_path": roi.get("crop_path"),
        }

    if roi is None:
        return True, "play_mark_unclear", "play_mark_roi_unreadable", None
    if roi["uncertain"]:
        return True, "play_mark_unclear", "play_mark_roi_unclear", _evidence()
    if sorted(first_cats) != sorted(roi["categories"]):
        return True, "play_mark_divergent", "play_mark_divergent", _evidence()
    return False, None, None, _evidence()


def process_parsed(sid: str, parsed: dict, *, write: bool = True) -> int:
    """Build + write the review draft from an already-parsed combined output."""
    lines, regions, warnings = [], [], []
    sections = parsed.get("sections") or []
    # Per-region y-bands from the gap midpoints between adjacent sections, so
    # the column-combo crop covers one bet (incl. its stacked second row) but
    # never bleeds into the neighboring bets.
    sec_ys: list[tuple[int | None, int | None]] = []
    for sec in sections:
        ys: list[int] = []
        for r in sec.get("rows") or []:
            for t in r.get("tokens") or []:
                if len(t.get("bbox", [])) == 4:
                    ys.extend((t["bbox"][1], t["bbox"][3]))
        sec_ys.append((min(ys), max(ys)) if ys else (None, None))
    n = len(sec_ys)
    bands: list[list[int] | None] = []
    for i, (lo, hi) in enumerate(sec_ys):
        if lo is None:
            bands.append(None)
            continue
        y1 = int((sec_ys[i - 1][1] + lo) / 2) if i > 0 and sec_ys[i - 1][1] is not None else int(lo - 40)
        y2 = int((hi + sec_ys[i + 1][0]) / 2) + 8 if i < n - 1 and sec_ys[i + 1][0] is not None else int(hi + 40)
        bands.append([y1, y2])

    for si, sec in enumerate(parsed.get("sections") or [], 1):
        rid = f"R{si:02d}"
        img_path = RAW / f"{sid}.jpg"
        xs = []
        for r in sec.get("rows") or []:
            for t in r.get("tokens") or []:
                if len(t.get("bbox", [])) == 4:
                    xs.extend((t["bbox"][0], t["bbox"][2]))
        band = bands[si - 1]
        crop_box = [min(xs) - 40, band[0], max(xs) + 80, band[1]] if band and xs else None
        sec_lines = section_to_lines(sec, rid, img_path=img_path, crop_box=crop_box)
        lines.extend(sec_lines)
        regions.append({
            "region_id": rid, "order": si, "column": "unknown",
            "region_type": "column_bet" if any(l["layout_hint"] == "column_bet" for l in sec_lines) else "normal_block",
            "line_ids": [l["line_id"] for l in sec_lines],
            "bounding_box": None, "boundary_uncertain": False,
        })
    draft = {
        "gt_schema_version": "539-semantic-gt-v1",
        "sample_id": sid,
        "review_status": "pending_human_review",
        "reviewed_by": None, "reviewed_at": None,
        "source": "model_prelabel_not_ground_truth",
        "regions": regions, "lines": lines,
        "shared_multiplier_rules": [], "warnings": warnings,
    }
    for line in draft.get("lines", []):
        if line.get("raw_text"):
            line["raw_text"] = line["raw_text"].replace("¾", "3/4").replace("⅔", "2/3")
        if line.get("multiplier_text"):
            line["multiplier_text"] = line["multiplier_text"].replace("¾", "3/4").replace("⅔", "2/3")
    _apply_v3_fallback(sid, draft)
    if not write:
        for l in lines:
            print(f"  {l['line_id']} | {l['layout_hint']} | groups={l['number_groups']} | {l['raw_text'][:60]!r}")
        return 1
    p = DRAFT / f"{sid}.json"
    if p.exists():
        shutil.copy2(p, str(p) + ".bak-geo2")
    p.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = sum(1 for l in lines if l["layout_hint"] == "column_bet")
    print(f"{sid}: sections={len(regions)} lines={len(lines)} column_bet={cols}", flush=True)
    return 1


def process_sample(sid: str, *, write: bool = True) -> int:
    img = RAW / f"{sid}.jpg"
    if not img.exists():
        return 0
    content = call(img)
    (GEO / f"{sid}-combined.json").write_text(content, encoding="utf-8")
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return 0
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return 0
    return process_parsed(sid, parsed, write=write)


def _apply_v3_fallback(sid: str, draft: dict) -> None:
    """Validity fallback: if a combined line has numbers outside 01-49 (model
    misread like 58/68), replace its number_groups with the v3 prelabel line
    that has valid numbers (the v3 read was correct for those cases)."""
    import re as _re

    pre_path = PRELABELS / f"{sid}.json"
    if not pre_path.exists():
        return
    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    parsed = parse_model_json(pre.get("raw_model_output") or "")
    if parsed is None:
        return
    v3_rows: list[tuple[list[list[str]], list[str]]] = []
    for sec in parsed.get("sections") or []:
        for row in sec.get("rows") or []:
            nums = row.get("numbers") or []
            def _flat(v):
                if isinstance(v, list):
                    out = []
                    for x in v:
                        out.extend(_flat(x))
                    return out
                return [str(v)]
            flat = _flat(nums)
            if isinstance(nums, list) and nums and isinstance(nums[0], list):
                nested = [[str(x) for x in col] for col in nums]
            else:
                nested = [flat] if flat else []
            v3_rows.append((nested, flat))
    for line in draft.get("lines", []):
        groups = line.get("number_groups") or []
        invalid = any(
            not _re.fullmatch(r"\d{1,2}", str(n)) or not (1 <= int(str(n)) <= 49)
            for g in groups for n in (g if isinstance(g, list) else [g])
        )
        if invalid:
            # find a v3 line whose numbers are all valid and overlap this line
            ours = [str(n) for g in groups for n in (g if isinstance(g, list) else [g])]
            best = None
            for v3_nested, v3_flat in v3_rows:
                valid = all(_re.fullmatch(r"\d{1,2}", str(n)) and 1 <= int(str(n)) <= 49 for n in v3_flat)
                if not valid:
                    continue
                overlap = [n for n in ours if _re.fullmatch(r"\d{1,2}", str(n)) and str(n) in v3_flat]
                # fall back when combined is invalid OR strictly incomplete vs v3
                if overlap and set(v3_flat) - set(ours):
                    best = v3_nested
                    break
            if best is not None:
                line["number_groups"] = best
                line.setdefault("warnings", []).append("v3_validity_fallback")


def main() -> None:
    GEO.mkdir(parents=True, exist_ok=True)
    write = "--dry" not in sys.argv
    reprocess = "--reprocess" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a not in ("--dry", "--reprocess")]
    samples = args or [f"sample-{i:03d}" for i in range(8, 34)]
    for sid in samples:
        try:
            if reprocess:
                path = GEO / f"{sid}-combined.json"
                if not path.exists():
                    print(f"{sid}: no saved combined json", flush=True)
                    continue
                m = re.search(r"\{.*\}", path.read_text(encoding="utf-8"), re.S)
                parsed = json.loads(m.group(0)) if m else None
                if parsed is None:
                    print(f"{sid}: combined parse failed", flush=True)
                    continue
                process_parsed(sid, parsed, write=write)
            else:
                process_sample(sid, write=write)
        except Exception as e:
            print(f"{sid}: ERROR {type(e).__name__} {str(e)[:120]}", flush=True)


if __name__ == "__main__":
    main()
