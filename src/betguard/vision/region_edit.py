"""整區套用柱碰 — batch human-edit for the review UI.

The tool ONLY produces an edit patch; it never writes executable / money /
expanded results directly. Applying the patch always goes through the
official pipeline (``pipeline.process_row``), so every downstream check is
recomputed and the record stays ``needs_review`` (never auto-approved).

Edge rules:
- mixed regions (car bet / header / other play types) -> rejected
- 各二三×0.5 stays all_groups_in_region
- 二三×0.5 without 各 applies to the merged current_group (never upgraded)
- empty / invalid columns or zero combinations -> fail-closed
- original rows are preserved in the patch (undo support)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from betguard.semantic_parser import _max_number
from betguard.vision.deterministic_checks import combination_count
from betguard.vision.pipeline import process_row

EDIT_TYPE = "APPLY_COLUMN_COMBO_TO_REGION"


@dataclass
class ColumnComboEdit:
    edit_type: str = EDIT_TYPE
    region_id: str = ""
    source_row_ids: list[str] = field(default_factory=list)
    columns: list[list[str]] = field(default_factory=list)
    edited_by: str = "human"
    previous_semantics: dict[str, Any] = field(default_factory=dict)
    reason: str = "bulk_review_edit"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_edit_patch(
    *,
    region_id: str,
    source_row_ids: list[str],
    columns: list[list[str]],
    previous_semantics: dict[str, Any] | None = None,
    edited_by: str = "human",
    reason: str = "bulk_review_edit",
) -> dict[str, Any]:
    return ColumnComboEdit(
        region_id=region_id,
        source_row_ids=list(source_row_ids),
        columns=[list(c) for c in columns],
        edited_by=edited_by,
        previous_semantics=dict(previous_semantics or {}),
        reason=reason,
    ).to_dict()


def columns_to_text(columns: list[list[str]]) -> str:
    """Canonical 柱碰 text: columns joined by /, numbers inside a column by space."""
    return " / ".join(" ".join(str(x) for x in col) for col in columns)


def _region_multiplier(region_rows: list[dict[str, Any]]) -> str | None:
    """Multiplier for the merged bet: 各 -> all_groups, bare -> current_group."""
    candidates: list[str] = []
    for row in region_rows:
        mult = row.get("multiplier_text") or row.get("multiplier")
        if mult:
            candidates.append(str(mult).strip())
    uniq = sorted(set(candidates))
    if len(uniq) > 1:
        raise ValueError(f"MULTIPLIER_CONFLICT:{','.join(uniq)}")
    return uniq[0] if uniq else None


def validate_edit_patch(
    patch: dict[str, Any],
    *,
    region_rows: list[dict[str, Any]],
    game: str = "539",
) -> list[str]:
    """Fail-closed validation. Returns blocking reasons ([] == valid)."""
    issues: list[str] = []
    columns = patch.get("columns") or []
    if len(columns) < 2:
        issues.append("COLUMN_COMBO_NEEDS_AT_LEAST_2_COLUMNS")
    max_n = _max_number(game)
    for ci, col in enumerate(columns, 1):
        if not col:
            issues.append(f"EMPTY_COLUMN:{ci}")
            continue
        for tok in col:
            s = str(tok).strip()
            if not s.isdigit() or not (1 <= int(s) <= max_n):
                issues.append(f"INVALID_NUMBER_IN_COLUMN:{ci}:{s}")
    if columns and combination_count([[int(x) for x in c] for c in columns]) < 1:
        issues.append("ZERO_COMBINATION_COUNT")

    for row in region_rows:
        rt = str(row.get("raw_text") or "").strip()
        if "車" in rt or row.get("play_type") == "car_bet":
            issues.append("MIXED_REGION_CAR_BET")
        elif not re_search_number(rt):
            issues.append("MIXED_REGION_NON_NUMBER_ROW")
    return sorted(set(issues))


def re_search_number(text: str) -> bool:
    import re

    return bool(re.search(r"\d", text))


def apply_region_edit(
    patch: dict[str, Any],
    *,
    region_rows: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
    game: str = "539",
) -> dict[str, Any]:
    """Apply the patch through the official pipeline.

    Returns {"patch", "new_row", "decision", "combination_count",
             "blocked", "block_reason", "undo": previous rows}.
    Never sets human_approved.
    """
    issues = validate_edit_patch(patch, region_rows=region_rows, game=game)
    if issues:
        return {
            "patch": patch,
            "blocked": True,
            "block_reason": "COLUMN_COMBO_INVALID:" + ";".join(issues),
            "issues": issues,
            "new_row": None,
            "decision": None,
            "combination_count": None,
            "undo": [dict(r) for r in region_rows],
        }

    multiplier = _region_multiplier(region_rows)
    columns = patch["columns"]
    text = columns_to_text(columns)
    if multiplier:
        text = f"{text} {multiplier}"
    row = {
        "raw_text": text,
        "numbers": [list(c) for c in columns],
        "multiplier": multiplier,
        "layout_hint": "column_bet",
    }
    rec = process_row(row, region_bound=True, provenance=provenance, game=game)
    decision = rec["decision"]
    return {
        "patch": patch,
        "new_row": rec,
        "decision": decision,
        "combination_count": combination_count([[int(x) for x in c] for c in columns]),
        "blocked": not decision["executable"],
        "block_reason": decision["block_reasons"][0] if decision["block_reasons"] else None,
        "issues": [],
        "undo": [dict(r) for r in region_rows],
    }
