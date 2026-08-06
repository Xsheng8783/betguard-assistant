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
    r"(?<!\d)(?:二三|二三四|三四|四三|3\s*/\s*4|2\s*/\s*3|3/4|2/3|三|四|¾|⅔|"
    r"23|2\.3|234|2\.3\.4|23\.4|34|24|[234])\s*[xX×]\s*\d+(?:\.\d+)?(?:\s*\.\s*\d+)?"
)


def _cx(t: dict) -> float:
    return (t["bbox"][0] + t["bbox"][2]) / 2


def check_image_quality(path: Path) -> list[str]:
    """Pre-OCR quality / orientation gates. Returns issue codes."""
    from PIL import Image, ImageFilter, ImageOps, ImageStat

    if not path.exists():
        return ["IMAGE_MISSING"]
    try:
        with Image.open(path) as im:
            exif_im = ImageOps.exif_transpose(im.copy())
            w, h = exif_im.size
    except Exception:
        return ["IMAGE_UNREADABLE"]
    issues: list[str] = []
    if min(w, h) < 200:
        issues.append("IMAGE_LOW_RESOLUTION")
    gray = exif_im.convert("L")
    mean = ImageStat.Stat(gray).mean[0]
    if mean < 45:
        issues.append("IMAGE_TOO_DARK")
    elif mean > 225:
        issues.append("IMAGE_OVEREXPOSED")
    edge_mean = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    if edge_mean < 3.0:
        issues.append("IMAGE_BLURRY")
    rw = max(1, w // 6)
    if ImageStat.Stat(gray.crop((w - rw, 0, w, h))).stddev[0] < 6:
        issues.append("PLAY_ZONE_CROPPED")
    return issues


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
    game: str = "539",
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
    combo_status: str | None = None
    if column_like and img_path:
        combo = _read_column_combo(
            img_path, toks,
            save_path=CROPS / f"{img_path.stem}-{region_id}-COL.png",
            crop_box=crop_box,
            game=game,
        )
        if combo is not None:
            if combo.get("status") in ("consensus", "insufficient_evidence", "divergent"):
                return _column_combo_line(region_id, combo, game=game)
        else:
            combo_status = "insufficient_evidence"
    if single_row or len(nums) < 3:
        return _mark_combo_fallback(
            _normal_lines(rows, region_id, img_path=img_path, crop_box=crop_box),
            combo_status,
        )
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
        out = [{
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
        return _mark_combo_fallback(out, combo_status)
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
    return _mark_combo_fallback(lines, combo_status)


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


def _normal_lines(
    rows: list[dict],
    region_id: str,
    img_path: Path | None = None,
    crop_box: list[int] | None = None,
) -> list[dict]:
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
        first_play = _first_pass_play_mark(play_toks)
        if play_toks and (
            first_play["upper_digits"] or first_play["lower_digits"] or first_play["other_visible_digits"]
        ) and img_path:
            save_path = CROPS / f"{img_path.stem}-{region_id}-L{i}.png"
            roi = _read_play_mark(img_path, play_toks, save_path=save_path, crop_box=crop_box)
            uncertain, uncertain_reason, warn, play_mark = _merge_play_mark(first_play, roi)
            if warn:
                warnings.append(warn)
        # bbox-height guard: a play token much taller than the row's numbers
        # means a stacked digit may have been missed by BOTH passes; such a row
        # must never be treated as consistent/confirmed.
        num_hs = [t["bbox"][3] - t["bbox"][1] for t in num_toks]
        if num_hs:
            med_h = statistics.median(num_hs)
            for t in play_toks:
                if (t["bbox"][3] - t["bbox"][1]) >= 1.2 * med_h:
                    if not uncertain:
                        uncertain = True
                        uncertain_reason = "possible_stacked_category_digit"
                    if "possible_stacked_category_digit" not in warnings:
                        warnings.append("possible_stacked_category_digit")
                    break
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
def _mark_combo_fallback(lines: list[dict], status: str | None) -> list[dict]:
    """column_like detected but no usable consensus -> the legacy fallback must
    stay needs_review (never uncertain=false / executable)."""
    if not status:
        return lines
    for line in lines:
        line["uncertain"] = True
        line["uncertain_reason"] = f"column_combo_{status}"
        line.setdefault("warnings", [])
        if "column_combo_needs_review" not in line["warnings"]:
            line["warnings"].append("column_combo_needs_review")
    return lines


def _read_column_combo(
    img_path: Path,
    toks: list[dict],
    save_path: Path | None = None,
    crop_box: list[int] | None = None,
    game: str = "539",
) -> dict | None:
    """Stage-2 column-combo read with 3-offset CONSENSUS.

    Each attempt shifts BOTH y1 and y2 by dy (crop height unchanged) and runs
    two image variants (original + grayscale-enhanced); variant divergence is
    recorded as roi_variant_divergent (never vote to fill digits).
    Signature = (columns, collision, multiplier):
      - >=2/3 identical  -> consensus (any uncertain attempt still review)
      - all three differ -> column_combo_divergent
      - only one valid   -> column_combo_insufficient_evidence
    Never prefers "more cells"; never trusts uncertain=false alone.
    """
    from collections import Counter
    from test_combined_bbox import call_column_combo_crop

    x1 = min(t["bbox"][0] for t in toks)
    y1 = min(t["bbox"][1] for t in toks)
    x2 = max(t["bbox"][2] for t in toks)
    y2 = max(t["bbox"][3] for t in toks)
    def _run_variant(variant: str):
        attempts: list[dict] = []
        for dy in (-8, 0, 8):
            box = crop_box
            if box is not None:
                box = [box[0], box[1] + dy, box[2], box[3] + dy]
            try:
                content = call_column_combo_crop(
                    img_path, [x1, y1, x2, y2],
                    save_path=save_path,
                    box=box,
                    variant=variant,
                )
            except Exception:
                continue
            parsed = _parse_column_combo(content, game=game)
            if parsed is None:
                continue
            sig = (
                tuple(tuple(c) for c in parsed["columns"]),
                parsed.get("collision"),
                parsed.get("multiplier"),
            )
            parsed["signature"] = sig
            parsed["crop_box"] = box
            parsed["raw_response"] = content
            parsed["variant"] = variant
            attempts.append(parsed)
        if not attempts:
            return None, attempts, "no_attempts", True
        counts = Counter(a["signature"] for a in attempts)
        sig, n = counts.most_common(1)[0]
        any_uncertain = any(a["uncertain"] for a in attempts)
        if n >= 2:
            chosen = next(a for a in attempts if a["signature"] == sig)
            return chosen, attempts, "consensus", chosen["uncertain"] or any_uncertain
        if len(attempts) == 1:
            return attempts[0], attempts, "insufficient_evidence", True
        return None, attempts, "divergent", True

    orig_chosen, orig_attempts, orig_status, orig_unc = _run_variant("original_3x")
    gray_chosen, gray_attempts, gray_status, gray_unc = _run_variant("gray_enhanced_3x")
    attempts = orig_attempts + gray_attempts
    if orig_chosen is None and gray_chosen is None:
        status = orig_status if orig_status != "no_attempts" else gray_status
        return {
            "columns": [], "collision": None, "multiplier": None,
            "uncertain": True, "uncertain_reason": None,
            "status": status, "roi_variant_divergent": False,
            "attempts": attempts,
        }
    primary = orig_chosen if orig_chosen is not None else gray_chosen
    status = orig_status if orig_chosen is not None else gray_status
    variant_divergent = (
        orig_chosen is not None
        and gray_chosen is not None
        and orig_chosen["signature"] != gray_chosen["signature"]
    )
    uncertain = orig_unc or gray_unc or variant_divergent or status != "consensus"
    return {
        "columns": primary["columns"],
        "collision": primary.get("collision"),
        "multiplier": primary.get("multiplier"),
        "uncertain": uncertain,
        "uncertain_reason": primary.get("uncertain_reason"),
        "status": status,
        "roi_variant_divergent": variant_divergent,
        "attempts": [
            {
                "signature": a["signature"],
                "columns": a["columns"],
                "collision": a.get("collision"),
                "multiplier": a.get("multiplier"),
                "uncertain": a["uncertain"],
                "crop_box": a["crop_box"],
                "raw_response": a["raw_response"],
                "variant": a["variant"],
            }
            for a in attempts
        ],
    }


def _parse_column_combo(content: str, game: str = "539") -> dict | None:
    """Parse + strict-structure validate one column_combo response.
    Columns: >=2, non-empty, each cell exactly 2 digits, in game range."""
    from test_combined_bbox import extract_json

    obj = extract_json(content)
    if obj is None:
        return None
    cols = obj.get("columns")
    if not isinstance(cols, list) or len(cols) < 2:
        return None
    norm: list[list[str]] = []
    for col in cols:
        if not isinstance(col, list) or not col:
            return None
        cells = [str(c).strip() for c in col if str(c).strip()]
        max_n = 39 if game == "539" else 49
        if not cells or any(
            not re.fullmatch(r"\d{2}", c) or not (1 <= int(c) <= max_n) for c in cells
        ):
            return None
        norm.append(cells)
    mult = obj.get("multiplier")
    coll = obj.get("collision")
    return {
        "columns": norm,
        "multiplier": str(mult).strip() if mult not in (None, "") else None,
        "collision": str(coll).strip() if coll not in (None, "") else None,
        "uncertain": bool(obj.get("uncertain")),
        "uncertain_reason": obj.get("uncertain_reason"),
    }


def _column_combo_line(region_id: str, combo: dict, game: str = "539") -> list[dict]:
    from decimal import Decimal

    cols = combo["columns"]
    coll = combo.get("collision")
    mult = combo.get("multiplier")
    warnings = ["column_combo_roi"]
    uncertain = bool(combo.get("uncertain"))
    reason = None
    problems: list[str] = []
    max_n = 39 if game == "539" else 49
    if len(cols) < 2:
        problems.append("少於兩欄")
    for ci, col in enumerate(cols):
        if not col:
            problems.append(f"欄{ci + 1}為空")
        for n in col:
            if not re.fullmatch(r"\d{2}", n):
                problems.append(f"非兩位數字 {n}")
            elif not (1 <= int(n) <= max_n):
                problems.append(f"超出範圍 {n}")
    if any(len(set(c)) != len(c) for c in cols):
        problems.append("同欄重複")
    if coll is None or not re.fullmatch(r"[234](/[234])?", coll):
        problems.append("碰法缺失/非法")
    ok_mult = mult is not None and re.fullmatch(r"\d+(?:\.\d+)?", mult) and Decimal(mult) > 0
    if not ok_mult:
        problems.append("倍率缺失/非法")
    status = combo.get("status")
    if status in ("divergent", "insufficient_evidence"):
        uncertain = True
        reason = "column_combo_" + status
    if problems:
        uncertain = True
        reason = "column_combo_invalid_structure"
    if uncertain and combo.get("uncertain"):
        warnings.append("column_combo_roi_uncertain")
    if status in ("divergent", "insufficient_evidence") or problems:
        warnings.append("column_combo_needs_review")
    if combo.get("roi_variant_divergent"):
        uncertain = True
        reason = "roi_variant_divergent"
        warnings.append("roi_variant_divergent")
    if coll and mult:
        mult_txt = f"{coll}X{mult}"
    elif mult:
        mult_txt = f"X{mult}"
    else:
        mult_txt = coll
    text = " / ".join(" ".join(c) for c in cols)
    if mult_txt:
        text = f"{text} {mult_txt}"
    combo_count = 1
    for c in cols:
        combo_count *= len(c)
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
        "uncertain": uncertain,
        "uncertain_reason": reason or ("column_combo_uncertain" if combo.get("uncertain") else None),
        "alternatives": [],
        "play_mark": None,
        "column_combo_status": status,
        "expected_combination_count": combo_count,
        "column_combo_attempts": combo.get("attempts"),
        "column_combo_problems": problems,
        "warnings": warnings,
        "review_action": "pending",
        "human_added": False,
    }]


def _safe_play_digit(value) -> str | None:
    """Shared rule: a play category digit is ONLY a single 2/3/4 character.
    Never extract digits from "39"/"24"/"31" etc."""
    s = str(value).strip()
    return s if re.fullmatch(r"[234]", s) else None


def _play_categories_from_token(text: str) -> list[str]:
    """Categories from a VALIDATED play token like "3x1" / "34x1" / "3/4x1".
    The token itself is a play mark (right band), so leading 1-2 category
    digits are safe; multi-char number tokens are filtered out upstream."""
    m = re.fullmatch(
        r"([234]/[234]|[234]{1,2})\s*[xX×]\s*\d+(?:\.\d+)?",
        str(text).strip(),
    )
    if not m:
        return []
    return [c for c in m.group(1) if _safe_play_digit(c)]


def _first_pass_play_mark(play_toks: list[dict]) -> dict:
    """Positional play-mark structure from the first-pass tokens:
    upper/lower by center_y (top vs bottom marks), multiplier, layout."""
    entries = []
    for t in play_toks:
        text = str(t.get("text") or "")
        cats = _play_categories_from_token(text)
        vm = re.search(r"[xX×]\s*(\d+(?:\.\d+)?)", text)
        entries.append({
            "cats": cats,
            "mult": vm.group(1) if vm else None,
            "cy": (t["bbox"][1] + t["bbox"][3]) / 2,
        })
    upper: list[str] = []
    lower: list[str] = []
    other: list[str] = []
    if len(entries) >= 2:
        ys = sorted(e["cy"] for e in entries)
        mid = (ys[0] + ys[-1]) / 2
        for e in entries:
            if e["cy"] < mid - 2:
                upper.extend(e["cats"])
            elif e["cy"] > mid + 2:
                lower.extend(e["cats"])
            else:
                other.extend(e["cats"])
        layout = "vertical_stack" if upper and lower else (
            "mixed" if len({round(e["cy"]) for e in entries}) > 1 else "horizontal")
    elif entries:
        upper.extend(entries[0]["cats"])
        layout = "horizontal"
    else:
        layout = "unknown"
    mults = [e["mult"] for e in entries if e["mult"]]
    return {
        "upper_digits": upper,
        "lower_digits": lower,
        "other_visible_digits": other,
        "multiplier": mults[-1] if mults else None,
        "layout": layout,
    }


def _read_play_mark(
    img_path: Path,
    play_toks: list[dict],
    save_path: Path | None = None,
    crop_box: list[int] | None = None,
) -> dict | None:
    """Stage-2 ROI read bounded by the section y-band (crop_box), upscale +
    PNG, structured play_mark prompt. Marks ROI_INCOMPLETE when the band is
    missing or the crop is clipped by the image edge."""
    from test_combined_bbox import call_play_mark_crop, extract_json

    x1 = min(t["bbox"][0] for t in play_toks)
    y1 = min(t["bbox"][1] for t in play_toks)
    x2 = max(t["bbox"][2] for t in play_toks)
    y2 = max(t["bbox"][3] for t in play_toks)
    incomplete = crop_box is None
    if crop_box is not None and (
        crop_box[0] > x1 - 4
        or crop_box[1] > y1 - 4
        or crop_box[2] < x2 + 4
        or crop_box[3] < y2 + 4
    ):
        incomplete = True
    variant_results = []
    parsed_obj = None
    for variant in ("original_3x", "gray_enhanced_3x"):
        try:
            content = call_play_mark_crop(
                img_path, [x1, y1, x2, y2],
                save_path=save_path,
                box=crop_box,
                variant=variant,
            )
        except Exception:
            continue
        obj = extract_json(content)
        pm = obj.get("play_mark") if isinstance(obj, dict) else None
        variant_results.append({
            "variant": variant,
            "content": content,
            "parsed": pm if isinstance(pm, dict) else None,
        })
        if parsed_obj is None and isinstance(pm, dict):
            parsed_obj = obj
    if parsed_obj is None:
        return None
    obj = parsed_obj
    pm = obj.get("play_mark")

    def _digits(value):
        return [c for raw in (value or []) for c in re.findall(r"[234]", str(raw))]

    upper = _digits(pm.get("upper_digits"))
    lower = _digits(pm.get("lower_digits"))
    other = _digits(pm.get("other_visible_digits"))
    cats = upper + lower + other
    seen: set[str] = set()
    uniq = [c for c in cats if not (c in seen or seen.add(c))]
    mult = pm.get("multiplier")
    variant_divergent = False
    parsed_variants = [v["parsed"] for v in variant_results if v["parsed"] is not None]
    if len(parsed_variants) >= 2:
        def _sig(p):
            return (
                tuple(str(d) for d in (p.get("upper_digits") or [])),
                tuple(str(d) for d in (p.get("lower_digits") or [])),
                tuple(str(d) for d in (p.get("other_visible_digits") or [])),
                str(p.get("multiplier") or ""),
                str(p.get("layout") or ""),
                bool(p.get("uncertain")),
            )
        variant_divergent = _sig(parsed_variants[0]) != _sig(parsed_variants[1])
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
        "incomplete": incomplete,
        "variant_divergent": variant_divergent,
        "variant_results": variant_results,
        "crop_box": crop_box,
        "scale": 3,
        "image_variant": "original_3x",
    }


def _norm_mult(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("×", "X").replace("x", "X")


def _merge_play_mark(first: dict, roi: dict | None) -> tuple[bool, str | None, str | None, dict | None]:
    """Three-stage merge. Returns (uncertain, reason, warning, evidence).

    Comparison is POSITIONAL: upper_digits / lower_digits / other_visible_digits
    / multiplier must match; never sort or dedupe into a category bag.
    """
    def _evidence():
        return {
            "first_pass_categories": (
                first.get("upper_digits", []) + first.get("lower_digits", [])
                + first.get("other_visible_digits", [])
            ),
            "first_upper_digits": first.get("upper_digits"),
            "first_lower_digits": first.get("lower_digits"),
            "first_other_digits": first.get("other_visible_digits"),
            "first_multiplier": first.get("multiplier"),
            "first_layout": first.get("layout"),
            "roi_categories": roi["categories"],
            "roi_upper_digits": roi.get("upper_digits"),
            "roi_lower_digits": roi.get("lower_digits"),
            "roi_multiplier": roi.get("multiplier"),
            "roi_layout": roi.get("layout"),
            "roi_raw_text": roi.get("raw_text"),
            "roi_uncertain": roi.get("uncertain"),
            "roi_uncertain_candidates": roi.get("uncertain_candidates"),
            "crop_path": roi.get("crop_path"),
            "roi_incomplete": roi.get("incomplete", False),
            "roi_variant_divergent": roi.get("variant_divergent", False),
            "roi_variant_results": roi.get("variant_results"),
            "crop_box": roi.get("crop_box"),
            "scale": roi.get("scale"),
            "image_variant": roi.get("image_variant"),
        }

    if roi is None:
        return True, "play_mark_unclear", "play_mark_roi_unreadable", None
    if roi.get("incomplete"):
        return True, "play_mark_unclear", "ROI_INCOMPLETE", _evidence()
    if roi.get("variant_divergent"):
        return True, "play_mark_unclear", "roi_variant_divergent", _evidence()
    if roi["uncertain"]:
        return True, "play_mark_unclear", "play_mark_roi_unclear", _evidence()
    pos_ok = (
        first.get("upper_digits") == roi.get("upper_digits")
        and first.get("lower_digits") == roi.get("lower_digits")
        and first.get("other_visible_digits") == roi.get("other_visible_digits")
    )
    mult_ok = _norm_mult(first.get("multiplier")) == _norm_mult(roi.get("multiplier"))
    if not (pos_ok and mult_ok):
        return True, "play_mark_divergent", "play_mark_divergent", _evidence()
    fl = first.get("layout")
    rl = roi.get("layout")
    if rl not in ("", None, "unknown") and fl not in ("", None, "unknown") and fl != rl:
        return True, "play_mark_unclear", "play_mark_layout_divergent", _evidence()
    return False, None, None, _evidence()


def process_parsed(
    sid: str,
    parsed: dict,
    *,
    write: bool = True,
    quality_issues: list[str] | None = None,
    game: str = "539",
) -> int:
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
        sec_lines = section_to_lines(sec, rid, img_path=img_path, crop_box=crop_box, game=game)
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
        "game": game,
        "regions": regions, "lines": lines,
        "shared_multiplier_rules": [], "warnings": warnings,
    }
    if quality_issues:
        draft["warnings"].append("image_quality_review")
        draft["image_quality_issues"] = quality_issues
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


def process_sample(sid: str, *, write: bool = True, game: str = "539") -> int:
    img = RAW / f"{sid}.jpg"
    if not img.exists():
        return 0
    quality = check_image_quality(img)
    content = call(img)
    (GEO / f"{sid}-combined.json").write_text(content, encoding="utf-8")
    from test_combined_bbox import extract_json

    parsed = extract_json(content)
    if parsed is None:
        return 0
    return process_parsed(sid, parsed, write=write, quality_issues=quality, game=game)


def _apply_v3_fallback(sid: str, draft: dict) -> None:
    """Cross-pass divergence guard: NEVER silently replace number_groups.

    When the v3 prelabel overlaps this line but contains extra/different
    numbers, keep the primary read, save a fallback_candidate and mark
    cross_pass_divergent (needs_review). Only a verifiable 1:1 region/line
    identity with a fully consistent structure may auto-merge (not implemented
    here)."""
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
        ours = [str(n) for g in groups for n in (g if isinstance(g, list) else [g])]
        candidates = []
        for v3_nested, v3_flat in v3_rows:
            valid = all(_re.fullmatch(r"\d{1,2}", str(n)) and 1 <= int(str(n)) <= 49 for n in v3_flat)
            if not valid:
                continue
            overlap = [n for n in ours if _re.fullmatch(r"\d{1,2}", str(n)) and str(n) in v3_flat]
            if overlap and set(v3_flat) - set(ours):
                candidates.append({
                    "number_groups": v3_nested,
                    "numbers": v3_flat,
                    "overlap": overlap,
                })
        if candidates:
            line.setdefault("warnings", [])
            if "cross_pass_divergent" not in line["warnings"]:
                line["warnings"].append("cross_pass_divergent")
            line["uncertain"] = True
            line["uncertain_reason"] = "cross_pass_divergent"
            line["fallback_candidate"] = {
                "source": "v3_prelabel",
                "rule": "partial_overlap_only_never_replace",
                "candidates": candidates[:3],
            }
            # number_groups intentionally NOT modified.


def main() -> None:
    GEO.mkdir(parents=True, exist_ok=True)
    write = "--dry" not in sys.argv
    reprocess = "--reprocess" in sys.argv[1:]
    game = "539"
    if "--game" in sys.argv[1:]:
        gi = sys.argv[1:].index("--game")
        game = sys.argv[gi + 1] if gi + 1 < len(sys.argv) else "539"
    args = [a for a in sys.argv[1:] if a not in ("--dry", "--reprocess", "--game", game)]
    samples = args or [f"sample-{i:03d}" for i in range(8, 34)]
    for sid in samples:
        try:
            if reprocess:
                path = GEO / f"{sid}-combined.json"
                if not path.exists():
                    print(f"{sid}: no saved combined json", flush=True)
                    continue
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    parsed = None
                if parsed is None:
                    print(f"{sid}: combined parse failed", flush=True)
                    continue
                quality = check_image_quality(RAW / f"{sid}.jpg")
                process_parsed(sid, parsed, write=write, quality_issues=quality, game=game)
            else:
                process_sample(sid, write=write, game=game)
        except Exception as e:
            print(f"{sid}: ERROR {type(e).__name__} {str(e)[:120]}", flush=True)


if __name__ == "__main__":
    main()
