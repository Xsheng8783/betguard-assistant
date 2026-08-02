"""ROI diagnostic evaluation — per-region metrics and failure classification.

Failure types are kept distinct:
  - region_detection_failure   : no OCR items for the annotated bbox
  - parser_failure             : OCR found digits but token extraction failed
  - ocr_recognition_failure    : OCR items exist but tokens do not match GT
  - match                      : tokens match ground truth

Missed regions are NEVER removed from the denominator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .evaluation import character_error_rate, evaluate_lottery_tokens

MULTIPLIER_RE = re.compile(r"(?:x|×)(\d{1,2})", re.IGNORECASE)


def extract_multipliers(text: str) -> list[str]:
    """Extract 'x1'/'×2' multipliers from a text fragment."""
    return [m.group(1) for m in MULTIPLIER_RE.finditer(text)]


def normalize_group_text(text: str) -> str:
    """Whitespace-normalized comparison text (does not alter numbers)."""
    return " ".join(text.split())


def numbers_match(gt_text: str, ocr_text: str) -> bool:
    from collections import Counter
    gt_tokens = Counter(evaluate_lottery_tokens(gt_text, gt_text).lottery_actual)
    ocr_tokens = Counter(evaluate_lottery_tokens(gt_text, ocr_text).lottery_actual)
    return gt_tokens == ocr_tokens


def numbers_plus_multiplier_match(gt_text: str, ocr_text: str) -> bool:
    if not numbers_match(gt_text, ocr_text):
        return False
    gt_mult = sorted(extract_multipliers(gt_text))
    ocr_mult = sorted(extract_multipliers(ocr_text))
    # GT multiplier absent → ignore multiplier comparison
    if not gt_mult:
        return True
    return gt_mult == ocr_mult


@dataclass
class RegionEval:
    group_id: str = ""
    ground_truth: str = ""
    ocr_text: str = ""
    item_count: int = 0
    version: str = ""  # original | contrast | grid_suppressed | best
    exact_group_match: bool = False
    number_exact_match: bool = False
    number_plus_multiplier_match: bool = False
    token_f1: float = 0.0
    cer: float = 1.0
    failure_type: str = "match"
    human_correction_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "ground_truth": self.ground_truth,
            "ocr_text": self.ocr_text,
            "item_count": self.item_count,
            "version": self.version,
            "exact_group_match": self.exact_group_match,
            "number_exact_match": self.number_exact_match,
            "number_plus_multiplier_match": self.number_plus_multiplier_match,
            "token_f1": round(self.token_f1, 4),
            "cer": round(self.cer, 4),
            "failure_type": self.failure_type,
            "human_correction_required": self.human_correction_required,
        }


def classify_failure(gt_text: str, ocr_text: str, item_count: int) -> str:
    """Classify per-region failure type (see module docstring)."""
    if item_count == 0:
        return "region_detection_failure"
    if not ocr_text.strip():
        return "region_detection_failure"
    gt_has_tokens = bool(evaluate_lottery_tokens(gt_text, gt_text).lottery_actual)
    ocr_has_tokens = bool(evaluate_lottery_tokens(gt_text, ocr_text).lottery_actual)
    if gt_has_tokens and not ocr_has_tokens and re.search(r"\d", ocr_text):
        return "parser_failure"
    if not numbers_match(gt_text, ocr_text):
        return "ocr_recognition_failure"
    return "match"


def evaluate_region(gt_text: str, ocr_text: str, item_count: int, version: str = "best") -> RegionEval:
    tm = evaluate_lottery_tokens(gt_text, ocr_text)
    return RegionEval(
        ground_truth=gt_text,
        ocr_text=ocr_text,
        item_count=item_count,
        version=version,
        exact_group_match=normalize_group_text(gt_text) == normalize_group_text(ocr_text),
        number_exact_match=numbers_match(gt_text, ocr_text),
        number_plus_multiplier_match=numbers_plus_multiplier_match(gt_text, ocr_text),
        token_f1=tm.f1,
        cer=character_error_rate(gt_text, ocr_text),
        failure_type=classify_failure(gt_text, ocr_text, item_count),
        human_correction_required=not numbers_match(gt_text, ocr_text),
    )


def pick_best_version(per_version: dict[str, dict], gt_text: str = "") -> dict[str, Any]:
    """Pick the version with highest token F1 (tie → original order).

    per_version: {"original": {"ocr_text", "item_count"}, ...}
    Returns {"ocr_text", "item_count", "version"}.
    """
    order = ["original", "contrast", "grid_suppressed"]
    best = None
    best_f1 = -1.0
    for v in order:
        if v not in per_version:
            continue
        pv = per_version[v]
        tm = evaluate_lottery_tokens(gt_text, pv.get("ocr_text", ""))
        f1 = tm.f1
        if f1 > best_f1:
            best_f1 = f1
            best = {
                "ocr_text": pv.get("ocr_text", ""),
                "item_count": pv.get("item_count", 0),
                "version": v,
            }
    if best is None:
        best = {"ocr_text": "", "item_count": 0, "version": "original"}
    return best


@dataclass
class DiagnosticSummary:
    region_count: int = 0
    unmatched_region_count: int = 0
    exact_group_match_count: int = 0
    number_exact_match_count: int = 0
    number_plus_multiplier_match_count: int = 0
    mean_token_f1: float = 0.0
    mean_cer: float = 0.0
    human_correction_required_count: int = 0
    failure_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_count": self.region_count,
            "unmatched_region_count": self.unmatched_region_count,
            "exact_group_match_count": self.exact_group_match_count,
            "number_exact_match_count": self.number_exact_match_count,
            "number_plus_multiplier_match_count": self.number_plus_multiplier_match_count,
            "mean_token_f1": round(self.mean_token_f1, 4),
            "mean_cer": round(self.mean_cer, 4),
            "human_correction_required_count": self.human_correction_required_count,
            "failure_counts": self.failure_counts,
        }


def summarize(regions: list[RegionEval]) -> DiagnosticSummary:
    s = DiagnosticSummary(region_count=len(regions))
    total_f1 = 0.0
    total_cer = 0.0
    for r in regions:
        if not r.number_exact_match:
            s.unmatched_region_count += 1
        if r.exact_group_match:
            s.exact_group_match_count += 1
        if r.number_exact_match:
            s.number_exact_match_count += 1
        if r.number_plus_multiplier_match:
            s.number_plus_multiplier_match_count += 1
        if r.human_correction_required:
            s.human_correction_required_count += 1
        total_f1 += r.token_f1
        total_cer += r.cer
        s.failure_counts[r.failure_type] = s.failure_counts.get(r.failure_type, 0) + 1
    if regions:
        s.mean_token_f1 = total_f1 / len(regions)
        s.mean_cer = total_cer / len(regions)
    return s
