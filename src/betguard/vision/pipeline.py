"""Unified vision/review pipeline entry (production + replay share this code).

Vertical flow:

    raw vision response
      -> normalization (closed_set.normalize_text)
      -> closed_set validation
      -> semantic_parser
      -> deterministic_checks
      -> single decision function (PipelineDecision)
      -> review record (per-stage results + provenance)

Hard rules (single decision function, no module overwrites):
    parse success AND supported AND no blocking issue AND human approved
        -> exportable
    car bets stay supported=false / not exportable even when confirmed.

State machine forbids:
    raw -> human_approved
    blocked -> exportable
    unresolved scope -> exportable
    deterministic blocking issue -> exportable after approval
    unsupported car bet -> exportable
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from betguard.parser import ParseError
from betguard.semantic_parser import parse_ocr_text
from betguard.vision.closed_set import normalize_text, validate_line
from betguard.vision.deterministic_checks import check_bet

STATES = ("raw", "extracted", "validated", "needs_review", "human_approved", "exportable")


def merge_column_slices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive column-slice rows into ONE column bet.

    A head row is 2+ 2-digit numbers separated by × (the column heads).
    Following rows with fewer numbers are continuations and right-align into
    the columns (matching real slips where the first column often has only
    the head number). Rows that are not a clean continuation stay separate.
    """
    if not rows:
        return rows
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(rows):
        rt = str(rows[i].get("raw_text") or "").strip()
        head_tokens = [t.strip() for t in re.split(r"[xX×]", rt)] if rt else []
        if (
            len(head_tokens) >= 2
            and all(re.fullmatch(r"\d{2}", t) for t in head_tokens)
            and "車" not in rt
        ):
            columns: list[list[str]] = [[t] for t in head_tokens]
            consumed = 1
            j = i + 1
            while j < len(rows):
                nxt = str(rows[j].get("raw_text") or "").strip()
                if "車" in nxt or re.search(
                    r"(?:二三|二三四|三四|三|四|23|2\.3|234|2\.3\.4|23\.4)\s*[xX×]\s*\d",
                    nxt,
                ):
                    break
                toks = re.findall(r"\d{1,2}", nxt)
                if not toks or len(toks) > len(columns):
                    break
                if len(toks) == len(columns):
                    # full-width continuation row: zip into every column
                    for k, tok in enumerate(toks):
                        columns[k].append(tok.zfill(2) if len(tok) == 1 else tok)
                else:
                    # partial row: right-align to the last k columns
                    offset = len(columns) - len(toks)
                    for k, tok in enumerate(toks):
                        columns[offset + k].append(tok.zfill(2) if len(tok) == 1 else tok)
                consumed += 1
                j += 1
            merged_text = " / ".join(" ".join(c) for c in columns)
            out.append(
                {
                    "raw_text": merged_text,
                    "numbers": columns,
                    "multiplier": rows[i].get("multiplier"),
                    "layout_hint": "column_bet",
                    "play_type": None,
                    "merged_row_count": consumed,
                }
            )
            i = j
        else:
            out.append(rows[i])
            i += 1
    return out


@dataclass
class PipelineDecision:
    parse_status: str
    supported: bool
    executable: bool
    review_status: str
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    check_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_status": self.parse_status,
            "supported": self.supported,
            "executable": self.executable,
            "review_status": self.review_status,
            "block_reasons": list(self.block_reasons),
            "warnings": list(self.warnings),
            "check_issues": list(self.check_issues),
        }


def decide(
    *,
    parse_status: str,
    supported: bool,
    executable_hint: bool,
    block_reasons: list[str],
    check_issues: list[str],
    warnings: list[str],
    human_approved: bool = False,
) -> PipelineDecision:
    """THE single decision function. Everything else only supplies inputs."""
    parse_ok = parse_status == "success"
    executable = parse_ok and supported and executable_hint and not block_reasons
    if executable and human_approved:
        review_status = "exportable"
    else:
        review_status = "needs_review"
    return PipelineDecision(
        parse_status=parse_status,
        supported=supported,
        executable=executable,
        review_status=review_status,
        block_reasons=list(block_reasons),
        warnings=list(warnings),
        check_issues=list(check_issues),
    )


def can_transition(
    current: str,
    target: str,
    *,
    supported: bool = True,
    executable: bool = True,
    block_reasons: list[str] | None = None,
    unresolved_scope: bool = False,
) -> tuple[bool, str]:
    """State machine guards (backend-enforced; UI cannot infer)."""
    block_reasons = block_reasons or []
    if current == "raw" and target == "human_approved":
        return False, "raw->human_approved forbidden"
    if target == "exportable":
        if current != "human_approved":
            return False, "must be human_approved before exportable"
        if not supported:
            return False, "unsupported (e.g. car bet) cannot export"
        if not executable or block_reasons:
            return False, "blocked item cannot export"
        if unresolved_scope:
            return False, "unresolved scope cannot export"
    return True, ""


def idempotency_key(
    *,
    sample_id: str,
    model: str,
    run: int,
    prompt_version: str,
    parser_version: str,
) -> str:
    payload = json.dumps(
        {
            "sample_id": sample_id,
            "model": model,
            "run": run,
            "prompt_version": prompt_version,
            "parser_version": parser_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def process_row(
    row: dict[str, Any],
    *,
    region_bound: bool,
    provenance: dict[str, Any] | None = None,
    game: str = "539",
) -> dict[str, Any]:
    """Run the full vertical flow for ONE model row."""
    raw_text = str(row.get("raw_text") or "").strip()
    layout = row.get("layout_hint") or "unknown"
    multiplier = row.get("multiplier")
    numbers = row.get("numbers")

    if isinstance(numbers, list):
        if numbers and isinstance(numbers[0], list):
            number_groups = [[str(x) for x in g] for g in numbers]
        else:
            number_groups = [[str(x) for x in numbers]] if numbers else []
    else:
        number_groups = []

    record: dict[str, Any] = {
        "raw_text": raw_text,
        "normalized_text": normalize_text(raw_text),
        "layout_hint": layout,
        "multiplier_text": str(multiplier) if multiplier else None,
    }

    # stage 2/3: closed_set
    v = validate_line(
        number_groups=number_groups,
        multiplier_text=str(multiplier) if multiplier else None,
        raw_text=raw_text,
        layout_hint=layout,
        uncertain=False,
        region_bound=region_bound,
    )
    sem = v.get("semantics") or {}
    record["closed_set"] = {
        "issues": sorted(set(v["issues"])),
        "semantics_layout": sem.get("layout"),
        "semantics_scope": sem.get("scope"),
        "semantics_numbers": sem.get("numbers") or [],
        "semantics_multipliers": sem.get("multipliers") or [],
    }

    # stage 4: semantic parser
    bet = None
    try:
        bet = parse_ocr_text(raw_text, game=game)
        record["semantic"] = {
            "bet_type": bet.type,
            "numbers": bet.numbers,
            "columns": bet.columns,
            "stars": bet.stars,
            "unit": _json_value(bet.unit),
            "money": bet.money,
            "car_units": _json_value(bet.car_units),
            "parse_error": None,
        }
    except ParseError as e:
        record["semantic"] = {
            "bet_type": "error",
            "numbers": [],
            "columns": [],
            "stars": [],
            "unit": None,
            "money": None,
            "car_units": None,
            "parse_error": str(e),
        }

    # stage 5: deterministic checks
    checks = check_bet(bet, game=game)
    record["checks"] = {
        "issues": checks["issues"],
        "warnings": checks["warnings"],
        "blocked": checks["blocked"],
        "block_reason": checks["block_reason"],
        "expected_combination_count": checks["expected_combination_count"],
    }

    # stage 6: single decision
    decision = decide(
        parse_status="error" if record["semantic"]["parse_error"] else "success",
        supported=record["semantic"]["bet_type"] != "car" if bet is not None else False,
        executable_hint=(bet is not None and bet.executable),
        block_reasons=[checks["block_reason"]] if checks["blocked"] and checks["block_reason"] else [],
        check_issues=checks["issues"],
        warnings=checks["warnings"],
    )
    record["decision"] = decision.to_dict()
    # flat aliases kept for backward-compatible replay records
    record["bet_type"] = record["semantic"]["bet_type"]
    record["numbers"] = record["semantic"]["numbers"]
    record["columns"] = record["semantic"]["columns"]
    record["stars"] = record["semantic"]["stars"]
    record["unit"] = record["semantic"]["unit"]
    record["money"] = record["semantic"]["money"]
    record["car_units"] = record["semantic"]["car_units"]
    record["parse_error"] = record["semantic"]["parse_error"]
    record["executable"] = decision.executable
    record["supported"] = decision.supported
    record["block_reason"] = checks["block_reason"]
    record["check_issues"] = checks["issues"]
    record["check_warnings"] = checks["warnings"]
    record["expected_combination_count"] = checks["expected_combination_count"]
    record["semantics_layout"] = record["closed_set"]["semantics_layout"]
    record["semantics_scope"] = record["closed_set"]["semantics_scope"]
    record["semantics_numbers"] = record["closed_set"]["semantics_numbers"]
    record["semantics_multipliers"] = record["closed_set"]["semantics_multipliers"]
    record["provenance"] = provenance or {}
    return record


def reapply_after_human_edit(
    original: dict[str, Any],
    *,
    raw_text: str | None = None,
    multiplier: str | None = None,
    layout_hint: str | None = None,
    provenance: dict[str, Any] | None = None,
    game: str = "539",
) -> dict[str, Any]:
    """Human edits re-run ALL downstream stages; never patch the final record."""
    row = {
        "raw_text": raw_text if raw_text is not None else original.get("raw_text", ""),
        "multiplier": multiplier if multiplier is not None else original.get("multiplier_text"),
        "layout_hint": layout_hint if layout_hint is not None else original.get("layout_hint"),
        "numbers": [],
    }
    return process_row(row, region_bound=True, provenance=provenance, game=game)


def _json_value(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        norm = value.normalize()
        return int(norm) if norm == norm.to_integral_value() else float(norm)
    return value
