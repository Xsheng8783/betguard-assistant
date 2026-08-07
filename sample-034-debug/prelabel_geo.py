"""Systemic 柱碰 pipeline: combined model call (per-bet sections + token bbox)
-> per-section X-clustering -> draft."""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from betguard.vision.column_geometry import build_columns_from_bbox, build_grid_from_rows  # noqa: E402
from betguard.vision.multiplier_policy import (  # noqa: E402
    COMPLETE,
    classify_multiplier_token,
    merge_complete_rules,
    partial_tokens,
    split_complete_rules,
)
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


def _cy(t: dict) -> float:
    return (t["bbox"][1] + t["bbox"][3]) / 2


def _norm_rule(rule: str) -> str:
    """Normalize one multiplier rule for the draft field: collapse whitespace,
    x/× -> X, ¾/⅔ -> 3/4 / 2/3 (e.g. "2 x 5" -> "2X5")."""
    return (
        re.sub(r"\s+", "", rule or "")
        .replace("¾", "3/4")
        .replace("⅔", "2/3")
        .replace("×", "X")
        .replace("x", "X")
    )


def extract_multiplier_rules(text: str) -> list[str]:
    """ALL category×value rules in reading order (top->bottom / left->right),
    each normalized. NEVER silently drops earlier rules.

    A "CATxVALUE" token where BOTH sides are two-digit lottery numbers is a
    column separator (e.g. "24 x 22"), NOT a multiplier, and is skipped.
    """
    out: list[str] = []
    for m in MULT_EXTRACT_RE.finditer(text or ""):
        raw = _norm_rule(m.group(0))
        cm = re.fullmatch(r"([^X]+)X(\d+(?:\.\d+)?)", raw)
        if cm and re.fullmatch(r"\d{2}", cm.group(1)) and re.fullmatch(r"\d{2}", cm.group(2)):
            continue
        out.append(raw)
    return out


def _rule_categories(rule: str) -> list[str]:
    """Category digits from a normalized rule like "2X1" / "2/3X0.1"."""
    return [c for c in re.findall(r"[234]", rule.split("X", 1)[0] if "X" in rule else rule)]


def _rule_value(rule: str) -> str | None:
    m = re.search(r"X(\d+(?:\.\d+)?)", rule)
    return m.group(1) if m else None


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
    """Backward-compatible single-field view of the model's own section text.

    Returns ALL rules joined by a space (never just the last match), or None
    when there is no play multiplier.
    """
    rules = extract_multiplier_rules(text)
    return " ".join(rules) if rules else None


def _compose_collision_rules(rules: list[str], coll: str | None) -> list[str]:
    """Canonical merge policy: merge categories ONLY on identical value,
    canonical 2/3/4 order, never merge different values."""
    return merge_complete_rules(rules)


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
    # Column-combo detection: >=3 numbers AND (>=2 separator x/× BETWEEN
    # number columns, OR a "/" token, OR one separator x plus a right-side
    # play x). A normal row like "11 15 24 37 2X5 3X2" has its x's only in
    # the right play zone, so it must NOT be routed to the column_combo ROI.
    x_seps = [t for t in toks if str(t.get("text") or "") in "xX×"]
    has_slash = any("/" in str(t.get("text") or "") for t in toks)
    nums_cx_min = min((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
    nums_cx_max = max((t["bbox"][0] + t["bbox"][2]) / 2 for t in nums)
    column_seps = [
        t for t in x_seps
        if nums_cx_min - 10 <= _cx(t) <= nums_cx_max + 10
    ]
    play_xs = [t for t in x_seps if _cx(t) > nums_cx_max + 20]
    column_like = len(nums) >= 3 and (
        len(column_seps) >= 2 or has_slash or (len(column_seps) >= 1 and len(play_xs) >= 1)
    )
    # Short column (only TWO numbers): never silently flatten to a normal row
    # when an x/× separator sits between the numbers AND a complete right-side
    # multiplier exists. The vertical column band may hide a stacked number
    # (e.g. 34 x 15 with 25 below); without a secondary-pass read the result
    # stays possible_column_bet / needs_review (never fabricate the missing
    # value).
    short_column_like = (
        len(nums) == 2
        and len(column_seps) >= 1
        and len(play_xs) >= 1
        and bool(extract_multiplier(section_text))
    )
    # Generic bbox sanity: a two-digit NUMBER token whose center_x falls into
    # (or just left of) the right-side play zone is suspicious (mis-tokenized
    # stacked digit / mis-attached continuation). When present, the column
    # reconstruction must stay needs_review instead of being confident.
    # Anchor the right-side play zone on the FIRST row's rightmost number
    # (continuation/staggered tokens may sit inside the play zone and would
    # otherwise pollute the anchor).
    row_lo = min(_cy(t) for t in nums)
    first_row_nums = [t for t in nums if _cy(t) <= row_lo + 18]
    first_row_max_cx = max((_cx(t) for t in first_row_nums), default=max(_cx(t) for t in nums))
    play_zone_x_min = None
    play_cy_lo = None
    play_cy_hi = None
    for t in toks:
        if not re.fullmatch(r"\d{2}", str(t.get("text") or "")) and _cx(t) > first_row_max_cx + 20:
            c = _cx(t)
            play_zone_x_min = c if play_zone_x_min is None else min(play_zone_x_min, c)
            play_cy_lo = _cy(t) if play_cy_lo is None else min(play_cy_lo, _cy(t))
            play_cy_hi = _cy(t) if play_cy_hi is None else max(play_cy_hi, _cy(t))
    play_zone_overlap = False
    if play_zone_x_min is not None and play_cy_lo is not None:
        for t in toks:
            if (
                re.fullmatch(r"\d{2}", str(t.get("text") or ""))
                and _cx(t) >= play_zone_x_min - 15
                and play_cy_lo - 20 <= _cy(t) <= play_cy_hi + 20
            ):
                play_zone_overlap = True
                break
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
            if combo.get("status") == "no_attempts":
                combo_status = "insufficient_evidence"
        else:
            combo_status = "insufficient_evidence"
    if not short_column_like and (single_row or len(nums) < 3):
        return _mark_combo_fallback(
            _normal_lines(rows, region_id, img_path=img_path, crop_box=crop_box),
            combo_status,
        )
    # clean zone: numbers + separators inside the number band; collision digits
    # and multiplier pieces in the right band bounded by the SECTION y-range
    # (so the play mark is kept, but neighboring bets above/below never leak).
    y_lo = min(t["bbox"][1] for t in toks) - 30
    y_hi = max(t["bbox"][3] for t in toks) + 30
    right_x_max = nums_cx_max + 300
    toks = [
        t for t in toks
        if (re.fullmatch(r"\d{2}", str(t.get("text") or "")) and 1 <= int(str(t.get("text") or "")) <= 49)
        or (str(t.get("text") or "") in "xX×")
        or (
            (str(t.get("text") or "") in "23401.")
            and nums_cx_min - 25 <= _cx(t) <= right_x_max
            and y_lo <= _cy(t) <= y_hi
        )
    ]
    res = build_grid_from_rows(toks) if len(toks) >= 3 else None
    if res is not None and res["column_count"] >= 2:
        cols = res["columns"]
        mult = res["multiplier"]
        coll = res["collision_raw"]
        raw_rules = extract_multiplier_rules(section_text)
        # The model often writes "2 x 0 . 1" with a detached trailing digit;
        # attach it to the LAST raw match so the rule becomes "2X0.1".
        raw_matches = list(MULT_EXTRACT_RE.finditer(section_text))
        if raw_matches:
            last = raw_matches[-1]
            rest = section_text[last.end():]
            m2 = re.match(r"\s*\.\s*(\d+)", rest)
            if m2:
                raw_rules[-1] = _norm_rule(last.group(0) + "." + m2.group(1))
        rules = _compose_collision_rules(raw_rules, coll)
        # Geometry collision evidence may contain MORE category digits than
        # the model's text (e.g. separate stacked 2/3/4 tokens that join
        # textually as "3 / 4 x 0.1"). Only when there is exactly ONE rule
        # whose categories are covered by the collision, expand it with the
        # collision's canonical categories (same value). Never guess digits
        # absent from the collision evidence.
        if len(rules) == 1 and coll:
            coll_digits = set(re.findall(r"[234]", coll))
            rule_head = rules[0].split("X", 1)[0] if "X" in rules[0] else ""
            rule_digits = set(re.findall(r"[234]", rule_head))
            rule_value = rules[0].split("X", 1)[1] if "X" in rules[0] else None
            # Guard: when the rule's VALUE is itself a category digit present
            # in the collision extras (e.g. "2X4" + collision "2/4"), the
            # collision "4" is almost certainly the multiplier value, NOT a
            # stacked category. Do not fabricate a second category.
            if rule_value and rule_value in coll_digits - rule_digits:
                rule_digits = set()
            if rule_digits and rule_digits <= coll_digits and len(coll_digits) > len(rule_digits) and rule_value:
                rules = [f"{'/'.join(sorted(coll_digits))}X{rule_value}"]
        partial_evidence: list[str] = []
        if not rules:
            coll_txt = re.sub(r"\s+", "", coll or "")
            if coll_txt and mult is not None:
                val = f"{mult:g}"
                if re.fullmatch(r"[234](/[234])*", coll_txt) and float(mult) > 0:
                    rules = [f"{coll_txt}X{val}"]
                else:
                    partial_evidence.append(coll_txt)
                    partial_evidence.append(f"X{val}")
            elif coll_txt:
                partial_evidence.append(coll_txt)
            elif mult is not None:
                partial_evidence.append(f"X{mult:g}")
        mult_txt = " ".join(rules) if rules else None
        multiplier_rules = [
            {
                "rule_text": r,
                "categories": _rule_categories(r),
                "value": _rule_value(r),
            }
            for r in rules
        ] if rules else []
        fallback_candidate = None
        if partial_evidence:
            fallback_candidate = {
                "source": "geometry_fallback",
                "rule": "partial_evidence_only",
                "multiplier_partial_evidence": list(partial_evidence),
                "evidence": [{
                    "source": "geometry_fallback",
                    "rule": "incomplete_multiplier_evidence",
                    "partial_tokens": list(partial_evidence),
                }],
            }
        uncertain = False
        uncertain_reason = None
        warnings = ["geometry_x_clustered"]
        if not mult_txt:
            uncertain = True
            uncertain_reason = "incomplete_multiplier_evidence" if partial_evidence else "missing_multiplier_or_collision"
            if "column_combo_needs_review" not in warnings:
                warnings.append("column_combo_needs_review")
            if partial_evidence and "incomplete_multiplier_evidence" not in warnings:
                warnings.append("incomplete_multiplier_evidence")
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
            "model_raw_text": section_text,
            "human_raw_text": None,
            "number_groups": [list(v) for v in cols.values()],
            "multiplier_text": mult_txt,
            "multiplier_rules": multiplier_rules,
            "layout_hint": "column_bet",
            "play_text": None,
            "play_type": None,
            "uncertain": uncertain,
            "uncertain_reason": uncertain_reason,
            "alternatives": [],
            "play_mark": play_mark,
            "fallback_candidate": fallback_candidate,
            "warnings": warnings,
            "review_action": "pending",
            "human_added": False,
        }]
        if short_column_like:
            uncertain = True
            uncertain_reason = "possible_column_bet"
            if "possible_column_bet" not in warnings:
                warnings.append("possible_column_bet")
            if "column_combo_needs_review" not in warnings:
                warnings.append("column_combo_needs_review")
            fb = fallback_candidate or {}
            fb = dict(fb)
            fb["evidence"] = list(fb.get("evidence") or []) + [{
                "source": "geometry_fallback",
                "rule": "possible_column_bet_vertical_extension_unverified",
                "short_column_numbers": [list(v) for v in cols.values()],
            }]
            fallback_candidate = fb
            out[0]["uncertain"] = uncertain
            out[0]["uncertain_reason"] = uncertain_reason
            out[0]["warnings"] = warnings
            out[0]["fallback_candidate"] = fallback_candidate
        if play_zone_overlap:
            if not uncertain:
                uncertain = True
                uncertain_reason = "number_token_overlaps_play_zone"
            if "number_token_overlaps_play_zone" not in warnings:
                warnings.append("number_token_overlaps_play_zone")
            fb = fallback_candidate or {}
            fb = dict(fb)
            fb["evidence"] = list(fb.get("evidence") or []) + [{
                "source": "geometry_fallback",
                "rule": "number_token_overlaps_play_zone",
                "note": "a two-digit number token falls inside the play zone; column layout is ambiguous",
            }]
            fallback_candidate = fb
            out[0]["uncertain"] = uncertain
            out[0]["uncertain_reason"] = uncertain_reason
            out[0]["warnings"] = warnings
            out[0]["fallback_candidate"] = fallback_candidate
        return _mark_combo_fallback(out, combo_status)
    # normal row: use model row numbers
    lines = []
    for i, r in enumerate(rows, 1):
        nums = r.get("numbers") or []
        flat = [str(x) for sub in (nums if nums and isinstance(nums[0], list) else [nums]) for x in (sub if isinstance(sub, list) else [sub])] if nums else []
        toktxt = "".join(str(t.get("text") or "") for t in (r.get("tokens") or []) if str(t.get("text") or "") != " ")
        rules = extract_multiplier_rules(toktxt)
        mult_txt = " ".join(rules) if rules else (r.get("multiplier") or None)
        lines.append({
            "line_id": f"{region_id}-L{i}",
            "entry_id": f"{region_id}-E{i}",
            "region_id": region_id,
            "order": i,
            "raw_text": toktxt or " ".join(flat),
            "number_groups": [flat] if flat else [],
            "multiplier_text": mult_txt,
            "multiplier_rules": [
                {
                    "rule_text": r,
                    "categories": _rule_categories(r),
                    "value": _rule_value(r),
                }
                for r in rules
            ] if rules else [],
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
    if short_column_like:
        for line in lines:
            line["uncertain"] = True
            line["uncertain_reason"] = "possible_column_bet"
            line.setdefault("warnings", [])
            if "possible_column_bet" not in line["warnings"]:
                line["warnings"].append("possible_column_bet")
            if "column_combo_needs_review" not in line["warnings"]:
                line["warnings"].append("column_combo_needs_review")
    if play_zone_overlap:
        for line in lines:
            if not line.get("uncertain"):
                line["uncertain"] = True
                line["uncertain_reason"] = "number_token_overlaps_play_zone"
            line.setdefault("warnings", [])
            if "number_token_overlaps_play_zone" not in line["warnings"]:
                line["warnings"].append("number_token_overlaps_play_zone")
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
    rules = extract_multiplier_rules(text)
    return [{
        "line_id": f"{region_id}-L1",
        "entry_id": f"{region_id}-E1",
        "region_id": region_id,
        "order": 1,
        "raw_text": text,
        "number_groups": [numbers] if numbers else [],
        "multiplier_text": extract_multiplier(text),
        "multiplier_rules": [
            {
                "rule_text": r,
                "categories": _rule_categories(r),
                "value": _rule_value(r),
            }
            for r in rules
        ] if rules else [],
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
        rules = extract_multiplier_rules(toktxt)
        model_mult = str(r.get("multiplier") or "").strip()
        if not rules and model_mult:
            rules = split_complete_rules(model_mult)
        # A bare model multiplier (e.g. "1") is superseded by the COMPLETE
        # rules extracted from the tokens; only treat it as partial evidence
        # when we have no complete rules at all.
        partial_toks = partial_tokens(model_mult) if not rules else []
        mult = " ".join(merge_complete_rules(rules)) if rules else None
        warnings = []
        uncertain = False
        uncertain_reason = None
        play_mark = None
        fallback_candidate = None
        if partial_toks:
            uncertain = True
            uncertain_reason = "incomplete_multiplier_evidence"
            if "incomplete_multiplier_evidence" not in warnings:
                warnings.append("incomplete_multiplier_evidence")
            fallback_candidate = {
                "source": "first_pass_model",
                "rule": "partial_evidence_only",
                "multiplier_partial_evidence": list(partial_toks),
                "evidence": [{
                    "source": "first_pass_model",
                    "rule": "incomplete_multiplier_evidence",
                    "partial_tokens": list(partial_toks),
                }],
            }
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
        # A first-pass token like "3x1" with an INDEPENDENT single-digit
        # token (2/3/4) directly below it is a stacked category: merge it
        # into upper/lower and compose "3/4X1". Multiple separate multiplier
        # rules (e.g. "2X5 3X2") are never collapsed.
        composed = _compose_mult_from_play_mark(first_play, rules)
        if composed and mult != composed:
            mult = composed
            rules = [composed]
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
        final_rules = [r for r in (mult or "").split() if r] if mult else []
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
            "multiplier_rules": [
                {
                    "rule_text": r,
                    "categories": _rule_categories(r),
                    "value": _rule_value(r),
                }
                for r in final_rules
            ],
            "layout_hint": "normal_row",
            "play_text": None,
            "play_type": None,
            "uncertain": uncertain,
            "uncertain_reason": uncertain_reason,
            "alternatives": [],
            "play_mark": play_mark,
            "fallback_candidate": fallback_candidate,
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

    def _attempt_path(variant: str, dy: int, rid: str) -> Path | None:
        if save_path is None:
            return None
        return save_path.with_name(f"{save_path.stem}-{variant}-dy{dy:+d}-{rid[:8]}.png")

    def _run_variant(variant: str):
        parsed_attempts: list[dict] = []
        evidence: list[dict] = []
        for dy in (-8, 0, 8):
            box = crop_box
            if box is not None:
                box = [box[0], box[1] + dy, box[2], box[3] + dy]
            rid = uuid.uuid4().hex[:12]
            attempt_meta: dict = {}
            attempt_path = _attempt_path(variant, dy, rid)
            try:
                content = call_column_combo_crop(
                    img_path, [x1, y1, x2, y2],
                    save_path=attempt_path,
                    box=box,
                    variant=variant,
                    request_id=rid,
                    meta=attempt_meta,
                )
            except Exception as e:
                evidence.append({
                    "signature": None,
                    "columns": [],
                    "collision": None,
                    "multiplier": None,
                    "uncertain": True,
                    "parsed": None,
                    "error": f"{type(e).__name__}: {e}",
                    "crop_path": str(attempt_path) if attempt_path else None,
                    "crop_box": box,
                    "image_sha256": attempt_meta.get("image_sha256"),
                    "variant": variant,
                    "dy": dy,
                    "raw_response": None,
                    "request_id": attempt_meta.get("request_id") or rid,
                })
                continue
            parsed = _parse_column_combo(content, game=game)
            rec = {
                "signature": None,
                "columns": [],
                "collision": None,
                "multiplier": None,
                "uncertain": True,
                "parsed": parsed,
                "error": None,
                "crop_path": str(attempt_path) if attempt_path else None,
                "crop_box": box,
                "image_sha256": attempt_meta.get("image_sha256"),
                "variant": variant,
                "dy": dy,
                "raw_response": content,
                "request_id": attempt_meta.get("request_id") or rid,
            }
            if parsed is None:
                evidence.append(rec)
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
            parsed["dy"] = dy
            parsed["request_id"] = rec["request_id"]
            parsed["image_sha256"] = rec["image_sha256"]
            parsed["crop_path"] = rec["crop_path"]
            parsed_attempts.append(parsed)
            rec.update({
                "signature": sig,
                "columns": parsed["columns"],
                "collision": parsed.get("collision"),
                "multiplier": parsed.get("multiplier"),
                "uncertain": parsed["uncertain"],
            })
            evidence.append(rec)
        if not parsed_attempts:
            return None, evidence, "no_attempts", True
        counts = Counter(a["signature"] for a in parsed_attempts)
        sig, n = counts.most_common(1)[0]
        any_uncertain = any(a["uncertain"] for a in parsed_attempts)
        if n >= 2:
            chosen = next(a for a in parsed_attempts if a["signature"] == sig)
            return chosen, evidence, "consensus", chosen["uncertain"] or any_uncertain
        if len(parsed_attempts) == 1:
            return parsed_attempts[0], evidence, "insufficient_evidence", True
        return None, evidence, "divergent", True

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
        "attempts": attempts,
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
    partial_evidence: list[str] = []
    multiplier_rules = []
    mult_txt = None
    if coll and mult:
        mult_txt = f"{coll}X{mult}"
        multiplier_rules.append({
            "rule_text": mult_txt,
            "categories": [c for c in re.findall(r"[234]", coll)],
            "value": str(mult),
        })
    else:
        if coll:
            partial_evidence.append(coll)
        if mult:
            partial_evidence.append(f"X{mult}")
    fallback_candidate = None
    if partial_evidence:
        fallback_candidate = {
            "source": "column_combo_roi",
            "rule": "partial_evidence_only",
            "multiplier_partial_evidence": list(partial_evidence),
            "evidence": [{
                "source": "column_combo_roi",
                "rule": "incomplete_multiplier_evidence",
                "partial_tokens": list(partial_evidence),
            }],
        }
        if "incomplete_multiplier_evidence" not in warnings:
            warnings.append("incomplete_multiplier_evidence")
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
        "multiplier_rules": multiplier_rules,
        "layout_hint": "column_bet",
        "play_text": None,
        "play_type": None,
        "uncertain": uncertain,
        "uncertain_reason": reason or ("column_combo_uncertain" if combo.get("uncertain") else None),
        "alternatives": [],
        "play_mark": None,
        "fallback_candidate": fallback_candidate,
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
        if re.fullmatch(r"[234]", text):
            # Independent stacked digit (e.g. the 4 below "3x1"): it carries
            # no ×value of its own, but IS a play category.
            cats = [text]
        else:
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


def _compose_mult_from_play_mark(pm: dict | None, rules: list[str]) -> str | None:
    """Compose a stacked-category multiplier from the positional play mark.

    Only used when there is exactly ONE extracted rule and the play mark shows
    EXTRA single category digits (e.g. "3x1" + independent 4 below -> 3/4X1).
    Multiple separate rules (2X5 3X2) are never collapsed here.
    """
    if not pm or len(rules) != 1:
        return None
    mult = pm.get("multiplier")
    if mult is None:
        return None
    digits = (
        list(pm.get("upper_digits") or [])
        + list(pm.get("lower_digits") or [])
        + list(pm.get("other_visible_digits") or [])
    )
    digits = [d for d in digits if _safe_play_digit(d)]
    seen: set[str] = set()
    uniq = [d for d in digits if not (d in seen or seen.add(d))]
    rule_cats = set(_rule_categories(rules[0]))
    if not uniq or set(uniq) <= rule_cats:
        return None
    return "/".join(uniq) + "X" + str(mult)


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
        rid = uuid.uuid4().hex[:12]
        attempt_meta: dict = {}
        attempt_path = None
        if save_path is not None:
            attempt_path = save_path.with_name(f"{save_path.stem}-{variant}-{rid[:8]}.png")
        try:
            content = call_play_mark_crop(
                img_path, [x1, y1, x2, y2],
                save_path=attempt_path,
                box=crop_box,
                variant=variant,
                request_id=rid,
                meta=attempt_meta,
            )
        except Exception:
            continue
        obj = extract_json(content)
        pm = obj.get("play_mark") if isinstance(obj, dict) else None
        variant_results.append({
            "variant": variant,
            "content": content,
            "parsed": pm if isinstance(pm, dict) else None,
            "raw_response": content,
            "crop_path": str(attempt_path) if attempt_path else None,
            "crop_box": crop_box,
            "image_sha256": attempt_meta.get("image_sha256"),
            "request_id": attempt_meta.get("request_id") or rid,
            "dy": 0,
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
        "crop_path": variant_results[0].get("crop_path") if variant_results else (str(save_path) if save_path else None),
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
    v3_rows: list[tuple[list[list[str]], list[str], list[str]]] = []
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
            v3_rows.append((nested, flat, extract_multiplier_rules(str(row.get("multiplier") or ""))))
    for line in draft.get("lines", []):
        groups = line.get("number_groups") or []
        ours = [str(n) for g in groups for n in (g if isinstance(g, list) else [g])]
        candidates = []
        best_row: tuple[list[list[str]], list[str], list[str]] | None = None
        best_overlap = 0
        best_extra = 10**9
        for v3_nested, v3_flat, v3_mult in v3_rows:
            valid = all(_re.fullmatch(r"\d{1,2}", str(n)) and 1 <= int(str(n)) <= 49 for n in v3_flat)
            if not valid:
                continue
            overlap = [n for n in ours if _re.fullmatch(r"\d{1,2}", str(n)) and str(n) in v3_flat]
            extra = len(set(v3_flat) - set(ours))
            if (len(overlap), -extra) > (best_overlap, -best_extra):
                best_overlap = len(overlap)
                best_extra = extra
                best_row = (v3_nested, v3_flat, v3_mult)
            if overlap and set(v3_flat) - set(ours):
                candidates.append({
                    "number_groups": v3_nested,
                    "numbers": v3_flat,
                    "overlap": overlap,
                })
        matched_v3_mult: list[str] = best_row[2] if best_row is not None and best_overlap >= 2 else []
        fallback: dict | None = line.get("fallback_candidate") if isinstance(line.get("fallback_candidate"), dict) else None
        if candidates:
            fallback = {
                "source": "v3_prelabel",
                "rule": "partial_overlap_only_never_replace",
                "candidates": candidates[:3],
            }
        ours_mult_rules = [_norm_rule(r) for r in (line.get("multiplier_text") or "").split()]
        mult_diff = [r for r in matched_v3_mult if r not in ours_mult_rules]
        if ours_mult_rules and mult_diff:
            line.setdefault("warnings", [])
            if "cross_pass_multiplier_divergent" not in line["warnings"]:
                line["warnings"].append("cross_pass_multiplier_divergent")
            ours_cats = {c for r in ours_mult_rules for c in _rule_categories(r)}
            if any(set(_rule_categories(r)) - ours_cats for r in mult_diff):
                if "possible_stacked_category_digit" not in line["warnings"]:
                    line["warnings"].append("possible_stacked_category_digit")
            line["uncertain"] = True
            if not line.get("uncertain_reason"):
                line["uncertain_reason"] = "cross_pass_multiplier_divergent"
            if fallback is None:
                fallback = {}
            existing = fallback.setdefault("multiplier_candidates", [])
            for r in mult_diff:
                if r not in existing:
                    existing.append(r)
        if fallback is not None:
            line["fallback_candidate"] = fallback
        if candidates:
            line.setdefault("warnings", [])
            if "cross_pass_divergent" not in line["warnings"]:
                line["warnings"].append("cross_pass_divergent")
            line["uncertain"] = True
            if not line.get("uncertain_reason"):
                line["uncertain_reason"] = "cross_pass_divergent"
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
