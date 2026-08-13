"""Offline-only strict contract audit for full-page Qwen shadow responses.

This module is deliberately not imported by the production provider.  It is
used to evaluate a future, versioned shadow contract without changing the
current prompt, transport, cache, or response conversion behavior.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CANONICAL_LAYOUT_HINTS = frozenset({"normal_row", "column_bet"})
TOKEN_OBJECT_KEYS = frozenset({"text", "bbox"})
V2_RESPONSE_SCHEMA_VERSION = "full-page-response-v2-shadow"
V2_CANONICALIZER_VERSION = "legacy-compatibility-v1"
V3_OBJECT_SCHEMA_VERSION = "full-page-response-v3-a-object-shadow"
V3_TUPLE_SCHEMA_VERSION = "full-page-response-v3-b-tuple-shadow"
V3_REFERENCE_SCHEMA_VERSION = "full-page-response-v3-c-token-reference-shadow"
V3_REFERENCE_TRANSPORT_VERSION = 3
V3_REFERENCE_VALIDATOR_VERSION = "full-page-v3-c-object-validator-v1"
V4_HYBRID_SCHEMA_VERSION = "full-page-response-v4-hybrid-object-shadow"
V4_HYBRID_TRANSPORT_VERSION = 4
V4_HYBRID_VALIDATOR_VERSION = "full-page-v4-hybrid-object-validator-v1"
V41_COMPACT_SCHEMA_VERSION = "full-page-response-v4.1-compact-object-shadow"
V41_COMPACT_TRANSPORT_VERSION = 41
V41_COMPACT_VALIDATOR_VERSION = "full-page-v4.1-compact-object-validator-v1"
DENSE_EVIDENCE_SCHEMA_VERSION = "dense-page-simplified-evidence-v1-shadow"
DENSE_EVIDENCE_TRANSPORT_VERSION = "D1"
DENSE_EVIDENCE_VALIDATOR_VERSION = "dense-page-simplified-evidence-validator-v1"
_V2_METADATA_KEY = "_v2"
_LEGACY_LAYOUT_HINTS = frozenset({"row_bet", "multiplier_only", "note"})
_COLUMN_SEPARATOR_RE = re.compile(r"[xX×]")
_NORMAL_SEPARATOR_RE = re.compile(r"[.,、，]")
_LAYOUT_TO_CODE = {"normal_row": "N", "column_bet": "C", None: None}
_CODE_TO_LAYOUT = {value: key for key, value in _LAYOUT_TO_CODE.items()}
_LEGACY_LAYOUT_TO_CODE = {
    "normal_row": "N",
    "column_bet": "C",
    "row_bet": "R",
    "multiplier_only": "M",
    "note": "S",
    None: None,
}
_CODE_TO_LEGACY_LAYOUT = {value: key for key, value in _LEGACY_LAYOUT_TO_CODE.items()}
_ENTRY_TO_CODE = {
    "bet_row": "B",
    "unresolved_row": "U",
    "multiplier_evidence": "M",
    "note_evidence": "S",
}
_CODE_TO_ENTRY = {value: key for key, value in _ENTRY_TO_CODE.items()}
_V41_ROW_STATE_TO_CODE = {
    ("normal_row", "bet_row", False): "N",
    ("column_bet", "bet_row", False): "C",
    ("row_bet", "bet_row", False): "R",
    ("row_bet", "unresolved_row", True): "U",
    ("multiplier_only", "multiplier_evidence", True): "M",
    ("note", "note_evidence", True): "S",
}
_V41_CODE_TO_ROW_STATE = {value: key for key, value in _V41_ROW_STATE_TO_CODE.items()}
_SHARED_STATUS_TO_CODE = {
    "absent": "A",
    "unresolved": "U",
    "complete_visual_evidence": "V",
}
_CODE_TO_SHARED_STATUS = {value: key for key, value in _SHARED_STATUS_TO_CODE.items()}
_V3_SAFETY = [True, False, False, False, False]

V3_REFERENCE_VALIDATOR_SPEC = {
    "root": {"type": "object", "exact_keys": ["s", "v"], "v": 3},
    "section_tuple_length": 5,
    "row_tuple_length": 9,
    "token_tuple_length": 5,
    "layout_codes": ["N", "C", None],
    "role_codes": ["B", "U", "M", "S"],
    "shared_state_codes": ["A", "U", "V"],
    "number_references": "exact_unique_in_range_physical_token_identity",
    "fixed_safety": {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    },
}
V3_REFERENCE_VALIDATOR_SHA256 = hashlib.sha256(
    json.dumps(
        V3_REFERENCE_VALIDATOR_SPEC,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

V4_HYBRID_VALIDATOR_SPEC = {
    "root": {"type": "object", "exact_keys": ["s", "v"], "v": 4},
    "section": {
        "type": "short_key_object",
        "required_keys": ["r", "s", "x"],
        "optional_keys": ["u"],
    },
    "row": {
        "type": "short_key_object",
        "required_keys": ["e", "h", "k", "l", "m", "n", "t", "x"],
        "optional_keys": ["c"],
    },
    "token": "[one_visible_mark_text,x1,y1,x2,y2]; fused text forbidden; pixel bbox finite, nonnegative, positive area",
    "numbers": "grouped_strings_no_token_references",
    "number_evidence": "grouped-number multiset must not exceed standalone numeric-token multiset",
    "layout_codes": ["N", "C", None],
    "multiplier": "string_or_null",
    "shared": "null_or_[text,x1,y1,x2,y2] with strict pixel bbox",
    "fixed_safety": {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    },
}
V4_HYBRID_VALIDATOR_SHA256 = hashlib.sha256(
    json.dumps(
        V4_HYBRID_VALIDATOR_SPEC,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

V41_COMPACT_VALIDATOR_SPEC = {
    "root": {"type": "object", "exact_keys": ["s", "v"], "v": 41},
    "section": {
        "type": "compact_object",
        "required_keys": ["r"],
        "optional_keys": ["s", "u"],
    },
    "row": {
        "type": "compact_object",
        "required_keys": ["k", "l", "n", "t"],
        "optional_keys": ["c", "m", "u"],
    },
    "token": "[one_visible_mark_text,x1,y1,x2,y2]; strict pixel bbox; fused text forbidden",
    "numbers": "grouped_literal_strings_no_token_references",
    "number_evidence": "grouped-number multiset bounded by standalone numeric-token multiset",
    "layout_codes": ["N", "C", None],
    "row_state_codes": ["N", "C", "R", "U", "M", "S", "Q"],
    "omission_defaults": {"m": None, "c": "absent", "s": None, "u": "absent"},
    "shared": "omitted_or_[text,x1,y1,x2,y2]; unresolved legacy text in u only",
    "fixed_safety": {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    },
}
V41_COMPACT_VALIDATOR_SHA256 = hashlib.sha256(
    json.dumps(
        V41_COMPACT_VALIDATOR_SPEC,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

DENSE_EVIDENCE_VALIDATOR_SPEC = {
    "root": {"type": "object", "exact_keys": ["s", "v"], "v": "D1"},
    "section": {
        "required_keys": ["id", "r"],
        "optional_keys": ["s", "u"],
    },
    "row": {
        "required_keys": ["b", "id", "k", "l", "n", "q", "x"],
        "optional_keys": ["c", "m", "t", "u"],
    },
    "row_modes": {
        "R": "dense row evidence: exact raw text plus one row bbox; no token list",
        "T": "normal token evidence: exact raw text, row bbox, and visible-mark token tuples",
    },
    "token": "T mode only: [one_visible_mark_text,x1,y1,x2,y2]",
    "numbers": "grouped literal strings; evidence only",
    "multiplier": "raw string omitted when absent",
    "layout": "N, C, or null",
    "shared": "section s=[text,x1,y1,x2,y2] or unresolved u string",
    "no_synthetic_geometry": True,
    "fixed_safety": {
        "human_confirmation_required": True,
        "needs_review": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    },
}
DENSE_EVIDENCE_VALIDATOR_SHA256 = hashlib.sha256(
    json.dumps(
        DENSE_EVIDENCE_VALIDATOR_SPEC,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

V3_OCR_SEMANTIC_RULES: tuple[tuple[str, str], ...] = (
    (
        "game_ranges",
        "This is a handwritten Taiwan 539 (01-39) or Mark Six/六合彩 (01-49) lottery betting slip.",
    ),
    (
        "one_bet_per_entry",
        "Read the whole slip. Represent each distinct bet as exactly one bet entry: never combine two bets and never split one bet across entries.",
    ),
    (
        "column_bet_geometry",
        "A 柱碰 (column bet) is one vertical grid read top-to-bottom; keep all lines of that grid in one bet entry.",
    ),
    (
        "normal_row_geometry",
        "A normal bet is exactly one horizontal handwritten line.",
    ),
    (
        "visible_mark_bbox",
        "For every visible mark (number, separator x, digit, or symbol), preserve its exact text and pixel bounding box [x1,y1,x2,y2].",
    ),
    (
        "column_grouping",
        "Within a column bet, keep each vertical column's stacked number values together from top to bottom, including every visible stacked value.",
    ),
    (
        "stacked_category",
        "A horizontal bet may end with stacked category digits: 3 above 4 then x1 means complete play text 34x1 or 3/4x1, never 3x1; 2 above 3 means 23x1 or 2/3x1. Preserve both visible digits.",
    ),
    (
        "column_x_separator",
        "Handwritten x strokes between number columns are column separators, not play values.",
    ),
    (
        "bottom_right_play_marks",
        "Bottom-right collision/category marks (for example 4/3) and value marks (for example x0.1) are separate visible marks outside every number column.",
    ),
    (
        "leading_zero_and_range",
        "Keep leading zeros (03, not 3); values 40-49 are valid for Mark Six/六合彩.",
    ),
    (
        "no_expand_or_invent",
        "Do not expand combinations, invent values, or miss values below the top of a column.",
    ),
)
V3_OCR_SEMANTIC_PROMPT = "\n".join(text for _, text in V3_OCR_SEMANTIC_RULES)

# Offline prompt-complexity candidate only.  It is never imported by the
# production prompt/provider.  The existing OCR-semantic prompt remains the
# prefix; this suffix describes output positions and fail-closed behavior.
V3_REFERENCE_PROMPT_CONTRACT = """V3C minified JSON object only; no Markdown or extra keys.
root={"v":3,"s":[section,...]}. v=3 fixes human_required=true and auto_apply/auto_confirm/auto_submit/executable=false.
section=[rows,canonical_shared,legacy_shared,shared_state,section_executable].
row=[tokens,number_refs,multiplier,layout,collision_slot,legacy_layout_role,role,evidence_only,row_executable].
token=[text,x1,y1,x2,y2]. number_refs=grouped exact 0-based token indexes; never reuse,reorder,guess.
layout=N(normal_row),C(column_bet),null(unresolved); collision_slot=[0] or [1,text].
role=B(bet),U(unresolved),M(multiplier evidence),S(note/scope evidence); evidence_only=(B:false,U/M/S:true).
Direct V3 legacy_layout_role: B=>layout; U=>R(row_bet); M=>M(multiplier_only); S=>S(note). null only for unknown legacy source.
canonical_shared=[0] or [2,text,x1,y1,x2,y2]. legacy_shared=[0],[1,text],or[2,text,x1,y1,x2,y2]; direct V3 sets both identical [0]/[2].
shared_state: both [0]=>A(absent); bbox evidence without complete category+value=>U(unresolved); complete category+value+bbox=>V(complete evidence). U/V remain evidence-only,non-executable.
section_executable=row_executable=false. Preserve every visible token+bbox and raw multiplier string/null, including stacked 2/3/4. Never invent/omit."""
V3_SHADOW_PROMPT = V3_OCR_SEMANTIC_PROMPT + "\n\n" + V3_REFERENCE_PROMPT_CONTRACT

# Candidate V4 is deliberately more redundant than Candidate C: short-key
# objects preserve visible hierarchy, while number evidence remains grouped
# text and is never coupled to physical token indexes.  This is shadow-only.
V4_HYBRID_PROMPT_CONTRACT = """V4 hybrid JSON object only; no Markdown or extra keys.
root={"v":4,"s":[section,...]}. section={"r":[row,...],"s":shared,"x":false}; optional "u" only for unresolved legacy shared text.
row={"t":[token,...],"n":[[number,...],...],"m":multiplier,"l":layout,"h":legacy_layout,"k":kind,"e":evidence_only,"x":false}; optional "c" preserves collision evidence.
token=[text,x1,y1,x2,y2]. Pixel bbox is finite, nonnegative, x2>x1,y2>y1. One visible mark per token: 05,x,08 are three tokens; 2,/,3,X,0,.,1 are seven. Fused 05x08,05.05,X1 are invalid. Never split, omit, reorder, or invent. n contains grouped number strings, never token indexes, and cannot exceed standalone numeric token evidence.
layout l=N(normal_row),C(column_bet),null(unresolved). h=N/C/R(row_bet)/M(multiplier_only)/S(note)/null. k=B(bet),U(unresolved),M(multiplier evidence),S(note/scope evidence).
multiplier m is exact visible string or null. shared s is null or flat [text,x1,y1,x2,y2]. u is exact unresolved legacy shared text when and only when s=null.
Safety is fixed: human confirmation required; auto_apply=false; auto_confirm=false; auto_submit=false; every x=false. Preserve evidence even when unresolved; never make evidence executable."""
V4_SHADOW_PROMPT = V3_OCR_SEMANTIC_PROMPT + "\n\n" + V4_HYBRID_PROMPT_CONTRACT

# V4.1 retains V4's literal token/number evidence but removes repeated null and
# safety fields.  The version and row-state code deterministically regenerate
# the omitted non-executable metadata; no token-reference graph is introduced.
V41_COMPACT_PROMPT_CONTRACT = """V4.1 minified JSON object only; no Markdown or extra keys.
root={"v":41,"s":[section,...]}. section={"r":[row,...]}; optional s=[text,x1,y1,x2,y2] for complete shared evidence, or u=exact unresolved shared text; never both.
row={"t":[token,...],"n":[[number,...],...],"l":layout,"k":state}; optional m=multiplier, c=collision, u=exact unsupported legacy layout text. Missing m means null; missing c/s/u means absent.
token=[text,x1,y1,x2,y2]. Pixel bbox is finite, nonnegative, x2>x1,y2>y1. One visible mark per token; never fuse, split, omit, reorder, or invent. n is grouped literal number strings, never token indexes, and cannot exceed standalone numeric token evidence.
l=N(normal_row),C(column_bet),null(unresolved). k=N/C(canonical legacy bet),R(resolved row_bet),U(unresolved row_bet),M(multiplier evidence),S(note/scope evidence),Q(unsupported legacy layout; optional u preserves its exact text).
m is exact visible multiplier string; c is exact collision evidence. Preserve stacked multiplier fragments, notes, unresolved scope, shared evidence, and every bbox.
Version 41 fixes human_confirmation_required=true and auto_apply=auto_confirm=auto_submit=executable=false for root, sections, rows, and attachments. Evidence never becomes executable."""
V41_SHADOW_PROMPT = V3_OCR_SEMANTIC_PROMPT + "\n\n" + V41_COMPACT_PROMPT_CONTRACT

DENSE_EVIDENCE_PROMPT_CONTRACT = """Dense evidence JSON object only; no Markdown or executable candidate.
root={"v":"D1","s":[section,...]}. section={"id":"S01","r":[row,...]}; optional s=[shared_text,x1,y1,x2,y2] or u=unresolved shared text, never both.
row={"id":"S01-L01","q":mode,"x":exact_raw_text,"b":[x1,y1,x2,y2],"n":[[literal_number,...],...],"l":layout,"k":state}; optional m=raw_multiplier,c=collision,u=unresolved role text.
q=R uses one row bbox and MUST NOT emit or imply token/sub-bboxes. q=T additionally requires t=[[one_visible_mark_text,x1,y1,x2,y2],...]. Never manufacture child geometry from b.
l=N(normal row),C(column),null(unresolved). k=N/C/R/U/M/S/Q as version-41 evidence roles. Preserve section/row identity, exact text, grouped literals, multiplier, layout, shared, note, and unresolved evidence.
R-mode column placement, continuation alignment, multiplier scope, collision, and number/play boundary always need human review or targeted geometry verification. Safety is fixed: human confirmation and needs_review true; auto_apply/auto_confirm/auto_submit/executable false."""
DENSE_EVIDENCE_SHADOW_PROMPT = (
    V3_OCR_SEMANTIC_PROMPT + "\n\n" + DENSE_EVIDENCE_PROMPT_CONTRACT
)


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ShadowContractValidationError(ValueError):
    """Raised when a response fails the isolated shadow contract."""

    def __init__(self, issues: Sequence[ContractIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.path}: {issue.code}" for issue in issues))


_PRODUCTION_PROMPT_LINE_PLAN: dict[int, tuple[str, str, tuple[str, ...]]] = {
    1: ("semantic", "preserve", ("game_ranges",)),
    2: ("mixed", "replace", ("one_bet_per_entry",)),
    3: ("mixed", "replace", ("column_bet_geometry",)),
    4: ("mixed", "replace", ("normal_row_geometry",)),
    5: ("mixed", "replace", ("visible_mark_bbox",)),
    6: ("output-format", "replace", ()),
    7: ("output-format", "replace", ()),
    8: ("output-format", "replace", ()),
    9: ("output-format", "replace", ()),
    10: ("output-format", "replace", ()),
    11: ("output-format", "replace", ()),
    12: ("semantic", "preserve", ()),
    13: ("mixed", "replace", ("column_grouping",)),
    14: ("mixed", "replace", ("stacked_category",)),
    15: ("mixed", "replace", ("column_x_separator",)),
    16: ("mixed", "replace", ("bottom_right_play_marks",)),
    17: ("semantic", "preserve", ("leading_zero_and_range",)),
    18: ("semantic", "preserve", ("no_expand_or_invent",)),
}
_V3_SEMANTIC_RULE_TEXT = dict(V3_OCR_SEMANTIC_RULES)
_LEGACY_SCHEMA_VOCAB_RE = re.compile(
    r"\b(?:sections|rows|tokens|numbers|multiplier|layout_hint|shared_multiplier)\b",
    re.IGNORECASE,
)


def production_prompt_audit_matrix(prompt: str | None = None) -> list[dict[str, Any]]:
    """Classify every production prompt line without changing production text."""

    if prompt is None:
        from betguard.vision.qwen_prompts import PROMPT

        prompt = PROMPT
    lines = prompt.splitlines()
    if len(lines) != len(_PRODUCTION_PROMPT_LINE_PLAN):
        raise _v3_error(
            "production_prompt_line_count_changed",
            "$prompt",
            f"expected {len(_PRODUCTION_PROMPT_LINE_PLAN)} lines, got {len(lines)}",
        )
    output: list[dict[str, Any]] = []
    for line_number, text in enumerate(lines, start=1):
        classification, disposition, rule_ids = _PRODUCTION_PROMPT_LINE_PLAN[line_number]
        output.append(
            {
                "line_number": line_number,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "classification": classification,
                "v3_disposition": disposition,
                "semantic_rule_ids": list(rule_ids),
                "semantic_replacements": [
                    _V3_SEMANTIC_RULE_TEXT[rule_id] for rule_id in rule_ids
                ],
                "semantic_meaning_preserved": classification == "output-format"
                or all(rule_id in _V3_SEMANTIC_RULE_TEXT for rule_id in rule_ids),
            }
        )
    return output


def audit_v3_semantic_coverage(matrix: Sequence[Mapping[str, Any]]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    from betguard.vision.qwen_prompts import PROMPT

    production_lines = PROMPT.splitlines()
    records_by_line: dict[int, Mapping[str, Any]] = {}
    for record_index, record in enumerate(matrix):
        line_number = record.get("line_number")
        if not isinstance(line_number, int) or isinstance(line_number, bool):
            issues.append(
                _issue(
                    "v3_prompt_audit_line_number_invalid",
                    f"$prompt.audit[{record_index}].line_number",
                    "line_number must be an integer from the trusted production prompt plan",
                )
            )
            continue
        if line_number in records_by_line:
            issues.append(
                _issue(
                    "v3_prompt_audit_line_duplicate",
                    f"$prompt.audit[{record_index}].line_number",
                    f"duplicate production prompt line {line_number}",
                )
            )
            continue
        records_by_line[line_number] = record

    covered: set[str] = set()
    for line_number, (classification, disposition, rule_ids) in _PRODUCTION_PROMPT_LINE_PLAN.items():
        path = f"$prompt.audit.line[{line_number}]"
        record = records_by_line.get(line_number)
        if record is None:
            issues.append(
                _issue(
                    "v3_prompt_audit_line_missing",
                    path,
                    "trusted production prompt line is missing from the audit matrix",
                )
            )
            continue

        expected_text = production_lines[line_number - 1]
        expected_text_sha256 = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        if record.get("text") != expected_text or record.get("text_sha256") != expected_text_sha256:
            issues.append(
                _issue(
                    "v3_prompt_audit_source_line_mismatch",
                    path,
                    "audited source text/hash must exactly match the trusted production prompt line",
                )
            )
        if record.get("classification") != classification:
            issues.append(
                _issue(
                    "v3_prompt_audit_classification_mismatch",
                    path,
                    f"expected classification {classification}",
                )
            )
        if record.get("v3_disposition") != disposition:
            issues.append(
                _issue(
                    "v3_prompt_audit_disposition_mismatch",
                    path,
                    f"expected disposition {disposition}",
                )
            )

        expected_rule_ids = list(rule_ids)
        actual_rule_ids = record.get("semantic_rule_ids")
        if actual_rule_ids != expected_rule_ids:
            issues.append(
                _issue(
                    "v3_semantic_rule_ids_mismatch",
                    path,
                    f"expected exact rule IDs {expected_rule_ids!r}",
                )
            )
        else:
            covered.update(expected_rule_ids)

        expected_replacements = [_V3_SEMANTIC_RULE_TEXT[rule_id] for rule_id in rule_ids]
        actual_replacements = record.get("semantic_replacements")
        if classification == "output-format" and actual_replacements != []:
            issues.append(
                _issue(
                    "v3_output_format_semantic_injection",
                    path,
                    "output-format lines must not inject OCR semantic replacement text",
                )
            )
        elif actual_replacements != expected_replacements:
            issues.append(
                _issue(
                    "v3_semantic_replacement_mismatch",
                    path,
                    "semantic replacements must exactly equal trusted V3 rule text",
                )
            )

        if record.get("semantic_meaning_preserved") is not True:
            issues.append(
                _issue(
                    "v3_mixed_line_semantic_not_preserved",
                    path,
                    "semantic meaning preservation must be explicitly true",
                )
            )

    for line_number in sorted(set(records_by_line) - set(_PRODUCTION_PROMPT_LINE_PLAN)):
        issues.append(
            _issue(
                "v3_prompt_audit_line_unexpected",
                f"$prompt.audit.line[{line_number}]",
                "line is not part of the trusted production prompt plan",
            )
        )

    expected = set(_V3_SEMANTIC_RULE_TEXT)
    if covered != expected:
        issues.append(
            _issue(
                "v3_semantic_coverage_incomplete",
                "$prompt.audit",
                f"missing={sorted(expected - covered)} unexpected={sorted(covered - expected)}",
            )
        )
    return issues


def audit_v3_shadow_prompt(prompt: str) -> list[ContractIssue]:
    """Reject legacy schema vocabulary/examples and root-shape contradictions."""

    issues: list[ContractIssue] = []
    # Candidate C intentionally reuses a few legacy nouns as positional slot
    # labels.  They are allowed only inside the exact, versioned semantic
    # prefix/contract bytes.  Scan every residual byte so positive, negative,
    # quoted, and JSON-example mentions all fail closed without weakening the
    # fixed Candidate C contract.
    residual = prompt.replace(V3_OCR_SEMANTIC_PROMPT, "", 1)
    residual = residual.replace(V3_REFERENCE_PROMPT_CONTRACT, "", 1)
    legacy_matches = sorted({match.lower() for match in _LEGACY_SCHEMA_VOCAB_RE.findall(residual)})
    if legacy_matches:
        issues.append(
            _issue(
                "legacy_schema_vocabulary_present",
                "$prompt",
                f"legacy schema vocabulary remains in the V3 shadow prompt: {legacy_matches}",
            )
        )
    if re.search(r"(?m)^root\s*=\s*\[", prompt):
        issues.append(
            _issue(
                "v3_array_root_instruction_present",
                "$prompt",
                "response_format=json_object requires an object root",
            )
        )
    if 'root={"v":3,"s":' not in prompt:
        issues.append(
            _issue(
                "v3_object_root_instruction_missing",
                "$prompt",
                "transport-compatible V3 object root must be explicit",
            )
        )
    return issues


def v3_reference_validator_module_sha256() -> str:
    """Hash the isolated shadow module used by a future preflight."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def audit_v3_live_preflight(
    *,
    response_format: Mapping[str, Any],
    expected_root: str,
    prompt: str,
    expected_prompt_sha256: str,
    validator_version: str,
    validator_sha256: str,
    validator_module_sha256: str,
    expected_validator_module_sha256: str,
    schema_version: str,
    transport_version: int,
    external_call_count: int,
) -> list[ContractIssue]:
    """Offline hard gate that must pass before any future transport call."""

    issues: list[ContractIssue] = []
    if dict(response_format) != {"type": "json_object"}:
        issues.append(
            _issue("preflight_response_format_mismatch", "$.response_format", "must be json_object")
        )
    if expected_root != "object":
        issues.append(
            _issue("preflight_root_transport_mismatch", "$.expected_root", "must be object")
        )
    issues.extend(audit_v3_shadow_prompt(prompt))
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != expected_prompt_sha256:
        issues.append(_issue("preflight_prompt_sha_mismatch", "$.prompt", "prompt bytes changed"))
    if validator_version != V3_REFERENCE_VALIDATOR_VERSION:
        issues.append(
            _issue("preflight_validator_version_mismatch", "$.validator_version", "validator version changed")
        )
    if validator_sha256 != V3_REFERENCE_VALIDATOR_SHA256:
        issues.append(
            _issue("preflight_validator_sha_mismatch", "$.validator_sha256", "validator spec changed")
        )
    if validator_module_sha256 != expected_validator_module_sha256:
        issues.append(
            _issue(
                "preflight_validator_module_sha_mismatch",
                "$.validator_module_sha256",
                "validator implementation module changed",
            )
        )
    if schema_version != V3_REFERENCE_SCHEMA_VERSION:
        issues.append(
            _issue("preflight_schema_version_mismatch", "$.schema_version", "Candidate C version changed")
        )
    if transport_version != V3_REFERENCE_TRANSPORT_VERSION:
        issues.append(
            _issue("preflight_transport_version_mismatch", "$.transport_version", "wire version changed")
        )
    if isinstance(external_call_count, bool) or external_call_count != 0:
        issues.append(
            _issue("preflight_external_counter_not_zero", "$.external_call_count", "live gate is already spent")
        )
    return issues


def audit_full_page_response(value: Any) -> list[ContractIssue]:
    """Return every strict-contract violation without modifying ``value``."""

    issues: list[ContractIssue] = []
    if not isinstance(value, Mapping):
        return [_issue("root_wrong_type", "$", "root must be an object")]

    sections = value.get("sections")
    if not isinstance(sections, list):
        return [_issue("sections_wrong_type", "$.sections", "sections must be a list")]
    if not sections:
        return [_issue("sections_empty", "$.sections", "sections must not be empty")]

    for section_index, section_value in enumerate(sections):
        section_path = f"$.sections[{section_index}]"
        if not isinstance(section_value, Mapping):
            issues.append(_issue("section_wrong_type", section_path, "section must be an object"))
            continue
        section = section_value
        rows = section.get("rows")
        if not isinstance(rows, list):
            issues.append(_issue("rows_wrong_type", f"{section_path}.rows", "rows must be a list"))
        elif not rows:
            issues.append(_issue("rows_empty", f"{section_path}.rows", "rows must not be empty"))
        else:
            for row_index, row in enumerate(rows):
                issues.extend(_audit_row(row, f"{section_path}.rows[{row_index}]"))

        shared_path = f"{section_path}.shared_multiplier"
        if "shared_multiplier" not in section:
            issues.append(
                _issue(
                    "shared_multiplier_missing",
                    shared_path,
                    "shared_multiplier is required and may be null",
                )
            )
        else:
            issues.extend(_audit_shared_multiplier(section.get("shared_multiplier"), shared_path))
    return issues


def validate_full_page_response(value: Any) -> None:
    """Raise a stable, multi-issue error when the shadow contract is invalid."""

    issues = audit_full_page_response(value)
    if issues:
        raise ShadowContractValidationError(issues)


def canonicalize_legacy_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a versioned, evidence-preserving V2 shadow representation.

    This is deliberately an offline compatibility adapter.  It never mutates
    the legacy response, never imports into the production provider, and never
    promotes compatibility evidence into an executable candidate.  Runtime
    round-tripping is possible because the original legacy layout and shared
    multiplier values are retained under the reserved ``_v2`` metadata key.
    """

    if not isinstance(value, Mapping):
        raise ShadowContractValidationError(
            [_issue("root_wrong_type", "$", "legacy response root must be an object")]
        )
    if "response_schema_version" in value or _V2_METADATA_KEY in value:
        raise ShadowContractValidationError(
            [
                _issue(
                    "v2_reserved_field_collision",
                    "$",
                    "legacy response already contains a reserved V2 field",
                )
            ]
        )
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ShadowContractValidationError(
            audit_full_page_response(value)
            or [_issue("sections_empty", "$.sections", "sections must not be empty")]
        )

    output = copy.deepcopy(dict(value))
    output["response_schema_version"] = V2_RESPONSE_SCHEMA_VERSION
    output[_V2_METADATA_KEY] = {
        "source_schema_version": "legacy-full-page-v1",
        "canonicalizer_version": V2_CANONICALIZER_VERSION,
        "artifact_kind": "offline_shadow",
        "safety": {
            "human_confirmation_required": True,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
            "executable": False,
        },
    }

    for section_index, section_value in enumerate(output["sections"]):
        section_path = f"$.sections[{section_index}]"
        if not isinstance(section_value, dict):
            raise ShadowContractValidationError(
                [_issue("section_wrong_type", section_path, "section must be an object")]
            )
        if _V2_METADATA_KEY in section_value:
            raise ShadowContractValidationError(
                [_issue("v2_reserved_field_collision", section_path, "reserved _v2 field")]
            )
        rows = section_value.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ShadowContractValidationError(
                [_issue("rows_empty", f"{section_path}.rows", "rows must not be empty")]
            )

        resolutions = [_initial_layout_resolution(row) for row in rows]
        resolved_bet_layouts = {
            resolution["layout_hint"]
            for resolution in resolutions
            if resolution["entry_kind"] == "bet_row"
            and resolution["layout_hint"] in CANONICAL_LAYOUT_HINTS
        }
        # A geometry-less continuation may inherit column scope only when the
        # same section has one unambiguous column structure.  Normal-row
        # inheritance would risk merging independent physical bets.
        if resolved_bet_layouts == {"column_bet"}:
            for resolution in resolutions:
                if (
                    resolution["entry_kind"] == "unresolved_row"
                    and resolution["legacy_layout_hint"] == "row_bet"
                ):
                    resolution.update(
                        {
                            "entry_kind": "bet_row",
                            "layout_hint": "column_bet",
                            "status": "resolved",
                            "source": "same_section_column_inheritance",
                            "reason": "single_unambiguous_column_structure_in_section",
                        }
                    )

        resolved_rows = [
            index
            for index, resolution in enumerate(resolutions, start=1)
            if resolution["entry_kind"] == "bet_row"
            and resolution["status"] == "resolved"
        ]
        section_id = f"S{section_index + 1:02d}"
        for row_index, (row, resolution) in enumerate(zip(rows, resolutions), start=1):
            row_path = f"{section_path}.rows[{row_index - 1}]"
            if not isinstance(row, dict):
                raise ShadowContractValidationError(
                    [_issue("row_wrong_type", row_path, "row must be an object")]
                )
            if _V2_METADATA_KEY in row:
                raise ShadowContractValidationError(
                    [_issue("v2_reserved_field_collision", row_path, "reserved _v2 field")]
                )
            legacy_layout = row.get("layout_hint")
            row["layout_hint"] = resolution["layout_hint"]
            attachment = _evidence_attachment(
                resolution,
                section_id=section_id,
                resolved_rows=resolved_rows,
            )
            row[_V2_METADATA_KEY] = {
                "row_id": f"{section_id}-R{row_index:02d}",
                "legacy_layout_hint": legacy_layout,
                "entry_kind": resolution["entry_kind"],
                "layout_resolution": {
                    key: copy.deepcopy(resolution[key])
                    for key in ("status", "source", "reason", "evidence")
                },
                "attachment": attachment,
                "evidence_only": resolution["entry_kind"]
                in {"multiplier_evidence", "note_evidence", "unresolved_row"},
                "executable": False,
            }

        legacy_shared = copy.deepcopy(section_value.get("shared_multiplier"))
        canonical_shared, shared_resolution = _canonical_shared_multiplier(legacy_shared)
        section_value["shared_multiplier"] = canonical_shared
        section_value[_V2_METADATA_KEY] = {
            "section_id": section_id,
            "legacy_shared_multiplier": legacy_shared,
            "shared_multiplier_resolution": shared_resolution,
            "executable": False,
        }

    issues = audit_v2_response(output)
    if issues:
        raise ShadowContractValidationError(issues)
    return output


def adapt_v2_response_to_runtime(value: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the byte-equivalent legacy object contract from a V2 artifact."""

    validate_v2_response(value)
    output = copy.deepcopy(dict(value))
    for section in output["sections"]:
        section_meta = section.pop(_V2_METADATA_KEY)
        section["shared_multiplier"] = copy.deepcopy(
            section_meta["legacy_shared_multiplier"]
        )
        for row in section["rows"]:
            row_meta = row.pop(_V2_METADATA_KEY)
            row["layout_hint"] = copy.deepcopy(row_meta["legacy_layout_hint"])
    output.pop("response_schema_version")
    output.pop(_V2_METADATA_KEY)
    return output


def audit_v2_response(value: Any) -> list[ContractIssue]:
    """Validate the canonical V2 artifact, including fail-closed metadata."""

    issues: list[ContractIssue] = []
    if not isinstance(value, Mapping):
        return [_issue("root_wrong_type", "$", "root must be an object")]
    if value.get("response_schema_version") != V2_RESPONSE_SCHEMA_VERSION:
        issues.append(
            _issue(
                "v2_schema_version_invalid",
                "$.response_schema_version",
                f"must equal {V2_RESPONSE_SCHEMA_VERSION}",
            )
        )
    root_meta = value.get(_V2_METADATA_KEY)
    if not isinstance(root_meta, Mapping):
        issues.append(_issue("v2_metadata_missing", "$._v2", "V2 metadata is required"))
    else:
        safety = root_meta.get("safety")
        expected_safety = {
            "human_confirmation_required": True,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
            "executable": False,
        }
        if not isinstance(safety, Mapping) or any(
            safety.get(key) != expected for key, expected in expected_safety.items()
        ):
            issues.append(
                _issue("v2_safety_invalid", "$._v2.safety", "shadow safety flags must fail closed")
            )

    sections = value.get("sections")
    if not isinstance(sections, list):
        issues.append(_issue("sections_wrong_type", "$.sections", "sections must be a list"))
        return issues
    if not sections:
        issues.append(_issue("sections_empty", "$.sections", "sections must not be empty"))
        return issues
    for section_index, section in enumerate(sections):
        section_path = f"$.sections[{section_index}]"
        if not isinstance(section, Mapping):
            issues.append(_issue("section_wrong_type", section_path, "section must be an object"))
            continue
        section_meta = section.get(_V2_METADATA_KEY)
        if not isinstance(section_meta, Mapping):
            issues.append(
                _issue("v2_section_metadata_missing", f"{section_path}._v2", "metadata required")
            )
        elif section_meta.get("executable") is not False:
            issues.append(
                _issue("v2_section_executable", f"{section_path}._v2.executable", "must be false")
            )
        if isinstance(section_meta, Mapping):
            shared_resolution = section_meta.get("shared_multiplier_resolution")
            if not isinstance(shared_resolution, Mapping):
                issues.append(
                    _issue(
                        "v2_shared_resolution_missing",
                        f"{section_path}._v2.shared_multiplier_resolution",
                        "shared multiplier resolution metadata is required",
                    )
                )
            elif (
                shared_resolution.get("evidence_only") is not True
                or shared_resolution.get("executable") is not False
            ):
                issues.append(
                    _issue(
                        "v2_shared_resolution_not_fail_closed",
                        f"{section_path}._v2.shared_multiplier_resolution",
                        "shared multiplier resolution must remain evidence-only and non-executable",
                    )
                )
        rows = section.get("rows")
        if not isinstance(rows, list) or not rows:
            issues.append(_issue("rows_empty", f"{section_path}.rows", "rows must not be empty"))
        else:
            for row_index, row in enumerate(rows):
                issues.extend(_audit_v2_row(row, f"{section_path}.rows[{row_index}]"))
        issues.extend(
            _audit_shared_multiplier(section.get("shared_multiplier"), f"{section_path}.shared_multiplier")
        )
    return issues


def validate_v2_response(value: Any) -> None:
    issues = audit_v2_response(value)
    if issues:
        raise ShadowContractValidationError(issues)


def encode_v3_object(value: Mapping[str, Any]) -> dict[str, Any]:
    """Candidate A: verbose object wire schema with explicit safety fields."""

    validate_v2_response(value)
    _validate_v3_source_shape(value)
    sections: list[dict[str, Any]] = []
    for section in value["sections"]:
        section_meta = section[_V2_METADATA_KEY]
        rows: list[dict[str, Any]] = []
        for row in section["rows"]:
            row_meta = row[_V2_METADATA_KEY]
            encoded_row: dict[str, Any] = {
                "tokens": copy.deepcopy(row["tokens"]),
                "numbers": copy.deepcopy(row["numbers"]),
                "multiplier": copy.deepcopy(row["multiplier"]),
                "layout_hint": copy.deepcopy(row["layout_hint"]),
                "legacy_layout_hint": copy.deepcopy(row_meta["legacy_layout_hint"]),
                "entry_kind": row_meta["entry_kind"],
                "evidence_only": bool(row_meta["evidence_only"]),
                "executable": bool(row_meta["executable"]),
            }
            if "collision" in row:
                encoded_row["collision"] = copy.deepcopy(row["collision"])
            rows.append(encoded_row)
        shared_resolution = section_meta["shared_multiplier_resolution"]
        sections.append(
            {
                "rows": rows,
                "shared_multiplier": copy.deepcopy(section["shared_multiplier"]),
                "legacy_shared_multiplier": copy.deepcopy(
                    section_meta["legacy_shared_multiplier"]
                ),
                "shared_resolution_status": shared_resolution["status"],
                "evidence_only": True,
                "executable": False,
            }
        )
    return {
        "version": V3_OBJECT_SCHEMA_VERSION,
        "safety": {
            "human_confirmation_required": True,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
            "executable": False,
        },
        "sections": sections,
    }


def decode_v3_object(value: Any) -> dict[str, Any]:
    """Expand Candidate A and verify all compact claims against regenerated V2."""

    if not isinstance(value, Mapping) or value.get("version") != V3_OBJECT_SCHEMA_VERSION:
        raise _v3_error("v3_object_root_invalid", "$", "invalid Candidate A root/version")
    _require_exact_keys(value, {"version", "safety", "sections"}, "$")
    expected_safety = {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    }
    if value.get("safety") != expected_safety:
        raise _v3_error("v3_safety_invalid", "$.safety", "safety flags must fail closed")
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise _v3_error("v3_sections_invalid", "$.sections", "sections must be non-empty")
    legacy_sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        path = f"$.sections[{section_index}]"
        if not isinstance(section, Mapping):
            raise _v3_error("v3_section_invalid", path, "section must be an object")
        _require_exact_keys(
            section,
            {
                "rows",
                "shared_multiplier",
                "legacy_shared_multiplier",
                "shared_resolution_status",
                "evidence_only",
                "executable",
            },
            path,
        )
        if section.get("evidence_only") is not True or section.get("executable") is not False:
            raise _v3_error("v3_section_safety_invalid", path, "section must be evidence-only")
        rows = section.get("rows")
        if not isinstance(rows, list) or not rows:
            raise _v3_error("v3_rows_invalid", f"{path}.rows", "rows must be non-empty")
        legacy_rows: list[dict[str, Any]] = []
        row_claims: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            row_path = f"{path}.rows[{row_index}]"
            if not isinstance(row, Mapping):
                raise _v3_error("v3_row_invalid", row_path, "row must be an object")
            allowed = {
                "tokens",
                "numbers",
                "multiplier",
                "layout_hint",
                "legacy_layout_hint",
                "entry_kind",
                "evidence_only",
                "executable",
                "collision",
            }
            _require_exact_keys(row, allowed, row_path)
            if row.get("executable") is not False:
                raise _v3_error("v3_row_executable", row_path, "row must be non-executable")
            legacy_row = {
                "tokens": copy.deepcopy(row.get("tokens")),
                "numbers": copy.deepcopy(row.get("numbers")),
                "multiplier": copy.deepcopy(row.get("multiplier")),
                "layout_hint": copy.deepcopy(row.get("legacy_layout_hint")),
            }
            if "collision" in row:
                legacy_row["collision"] = copy.deepcopy(row.get("collision"))
            legacy_rows.append(legacy_row)
            row_claims.append(
                {
                    "layout_hint": copy.deepcopy(row.get("layout_hint")),
                    "entry_kind": copy.deepcopy(row.get("entry_kind")),
                    "evidence_only": copy.deepcopy(row.get("evidence_only")),
                }
            )
        legacy_sections.append(
            {
                "rows": legacy_rows,
                "shared_multiplier": copy.deepcopy(section.get("legacy_shared_multiplier")),
            }
        )
        claims.append(
            {
                "rows": row_claims,
                "shared_multiplier": copy.deepcopy(section.get("shared_multiplier")),
                "shared_resolution_status": section.get("shared_resolution_status"),
            }
        )
    return _regenerate_and_verify_v2({"sections": legacy_sections}, claims)


def encode_v3_tuple(value: Mapping[str, Any]) -> list[Any]:
    """Candidate B: fully positional arrays; number text remains explicit."""

    return _encode_v3_array(value, references=False)


def decode_v3_tuple(value: Any) -> dict[str, Any]:
    return _decode_v3_array(value, references=False)


def encode_v3_token_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """Candidate C: transport-compatible object with exact token indexes."""

    return _encode_v3_array(value, references=True)


def decode_v3_token_reference(value: Any) -> dict[str, Any]:
    return _decode_v3_array(value, references=True)


def encode_v4_hybrid(value: Mapping[str, Any]) -> dict[str, Any]:
    """Encode model-friendly V4: short objects, token tuples, string number groups."""

    validate_v2_response(value)
    _validate_v4_source_wire_values(value)
    _validate_v3_source_shape(value)
    sections: list[dict[str, Any]] = []
    for section_index, section in enumerate(value["sections"]):
        section_meta = section[_V2_METADATA_KEY]
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(section["rows"]):
            row_meta = row[_V2_METADATA_KEY]
            encoded_tokens: list[list[Any]] = []
            for token_index, token in enumerate(row["tokens"]):
                token_path = (
                    f"S{section_index + 1:02d}-R{row_index + 1:02d}-T{token_index + 1:02d}"
                )
                encoded_token = _encode_token_tuple(token, token_path)
                _validate_v4_token_granularity(encoded_token[0], token_path)
                if not _valid_v4_pixel_bbox(encoded_token[1:]):
                    raise _v4_error(
                        "v4_token_bbox_invalid",
                        token_path,
                        "pixel bbox must be finite, nonnegative, and have positive area",
                    )
                encoded_tokens.append(encoded_token)
            number_groups = _validate_v4_number_groups(
                row["numbers"],
                f"S{section_index + 1:02d}-R{row_index + 1:02d}.n",
            )
            _validate_v4_number_evidence(
                number_groups,
                row["tokens"],
                f"S{section_index + 1:02d}-R{row_index + 1:02d}.n",
            )
            encoded_row: dict[str, Any] = {
                "t": encoded_tokens,
                "n": number_groups,
                "m": copy.deepcopy(row["multiplier"]),
                "l": _LAYOUT_TO_CODE[row["layout_hint"]],
                "h": _LEGACY_LAYOUT_TO_CODE[row_meta["legacy_layout_hint"]],
                "k": _ENTRY_TO_CODE[row_meta["entry_kind"]],
                "e": bool(row_meta["evidence_only"]),
                "x": bool(row_meta["executable"]),
            }
            if "collision" in row:
                encoded_row["c"] = copy.deepcopy(row["collision"])
            rows.append(encoded_row)

        encoded_section: dict[str, Any] = {
            "r": rows,
            "s": _encode_v4_shared(section["shared_multiplier"]),
            "x": bool(section_meta["executable"]),
        }
        legacy_shared = section_meta["legacy_shared_multiplier"]
        if isinstance(legacy_shared, str):
            encoded_section["u"] = legacy_shared
        sections.append(encoded_section)
    return {"v": V4_HYBRID_TRANSPORT_VERSION, "s": sections}


def _validate_v4_source_wire_values(value: Mapping[str, Any]) -> None:
    """Apply V4 wire constraints before V2 metadata reproducibility checks.

    A malformed V4-only source value must report its stable V4 error instead of
    being masked by the later legacy-to-V2 reproducibility guard.
    """

    for section_index, section in enumerate(value["sections"]):
        _encode_v4_shared(section["shared_multiplier"])
        for row_index, row in enumerate(section["rows"]):
            row_path = f"S{section_index + 1:02d}-R{row_index + 1:02d}"
            for token_index, token in enumerate(row["tokens"]):
                token_path = f"{row_path}-T{token_index + 1:02d}"
                _validate_v4_token_granularity(token["text"], token_path)
                if not _valid_v4_pixel_bbox(token["bbox"]):
                    raise _v4_error(
                        "v4_token_bbox_invalid",
                        token_path,
                        "pixel bbox must be finite, nonnegative, and have positive area",
                    )
            number_groups = _validate_v4_number_groups(row["numbers"], f"{row_path}.n")
            _validate_v4_number_evidence(number_groups, row["tokens"], f"{row_path}.n")


def decode_v4_hybrid(value: Any) -> dict[str, Any]:
    """Strictly decode V4 without inferring token identity or repairing hierarchy."""

    if not isinstance(value, Mapping):
        raise _v4_error("v4_root_must_be_object", "$", "V4 root must be an object")
    _require_exact_keys(value, {"v", "s"}, "$")
    if (
        not isinstance(value.get("v"), int)
        or isinstance(value.get("v"), bool)
        or value.get("v") != V4_HYBRID_TRANSPORT_VERSION
    ):
        raise _v4_error("v4_version_invalid", "$.v", "transport version must equal 4")
    sections = value.get("s")
    if not isinstance(sections, list) or not sections:
        raise _v4_error("v4_sections_invalid", "$.s", "sections must be a non-empty list")

    legacy_sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        path = f"$.s[{section_index}]"
        if not isinstance(section, Mapping):
            raise _v4_error("v4_section_invalid", path, "section must be an object")
        _require_keys(section, required={"r", "s", "x"}, optional={"u"}, path=path)
        if section.get("x") is not False:
            raise _v4_error("v4_section_executable", f"{path}.x", "must be false")
        canonical_shared = _decode_v4_shared(section.get("s"), f"{path}.s")
        if "u" in section:
            if canonical_shared is not None:
                raise _v4_error(
                    "v4_unresolved_shared_conflict",
                    f"{path}.u",
                    "unresolved legacy shared text is allowed only when s is null",
                )
            legacy_shared = section.get("u")
            if not isinstance(legacy_shared, str) or not legacy_shared:
                raise _v4_error(
                    "v4_unresolved_shared_invalid",
                    f"{path}.u",
                    "u must be a non-empty exact legacy shared string",
                )
        else:
            legacy_shared = copy.deepcopy(canonical_shared)

        rows = section.get("r")
        if not isinstance(rows, list) or not rows:
            raise _v4_error("v4_rows_invalid", f"{path}.r", "rows must be a non-empty list")
        legacy_rows: list[dict[str, Any]] = []
        row_claims: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            row_path = f"{path}.r[{row_index}]"
            if not isinstance(row, Mapping):
                raise _v4_error("v4_row_invalid", row_path, "row must be an object")
            _require_keys(
                row,
                required={"t", "n", "m", "l", "h", "k", "e", "x"},
                optional={"c"},
                path=row_path,
            )
            if row.get("x") is not False:
                raise _v4_error("v4_row_executable", f"{row_path}.x", "must be false")
            layout_code = row.get("l")
            legacy_layout_code = row.get("h")
            role_code = row.get("k")
            if layout_code not in _CODE_TO_LAYOUT:
                raise _v4_error("v4_layout_invalid", f"{row_path}.l", "layout must be N, C, or null")
            if legacy_layout_code not in _CODE_TO_LEGACY_LAYOUT:
                raise _v4_error("v4_legacy_layout_invalid", f"{row_path}.h", "unknown legacy layout")
            if role_code not in _CODE_TO_ENTRY:
                raise _v4_error("v4_entry_kind_invalid", f"{row_path}.k", "unknown entry kind")
            multiplier = row.get("m")
            if multiplier is not None and not isinstance(multiplier, str):
                raise _v4_error("v4_multiplier_invalid", f"{row_path}.m", "must be string or null")
            tokens = _decode_token_tuples(row.get("t"), f"{row_path}.t")
            for token_index, token in enumerate(tokens):
                _validate_v4_token_granularity(
                    token["text"], f"{row_path}.t[{token_index}][0]"
                )
                if not _valid_v4_pixel_bbox(token["bbox"]):
                    raise _v4_error(
                        "v4_token_bbox_invalid",
                        f"{row_path}.t[{token_index}][1:5]",
                        "pixel bbox must be finite, nonnegative, and have positive area",
                    )
            numbers = _validate_v4_number_groups(row.get("n"), f"{row_path}.n")
            _validate_v4_number_evidence(numbers, tokens, f"{row_path}.n")
            evidence_only = row.get("e")
            if not isinstance(evidence_only, bool):
                raise _v4_error("v4_evidence_only_invalid", f"{row_path}.e", "must be boolean")
            legacy_row: dict[str, Any] = {
                "tokens": tokens,
                "numbers": numbers,
                "multiplier": copy.deepcopy(multiplier),
                "layout_hint": _CODE_TO_LEGACY_LAYOUT[legacy_layout_code],
            }
            if "c" in row:
                collision = row.get("c")
                if collision is not None and not isinstance(collision, str):
                    raise _v4_error("v4_collision_invalid", f"{row_path}.c", "must be string or null")
                legacy_row["collision"] = copy.deepcopy(collision)
            legacy_rows.append(legacy_row)
            row_claims.append(
                {
                    "layout_hint": _CODE_TO_LAYOUT[layout_code],
                    "entry_kind": _CODE_TO_ENTRY[role_code],
                    "evidence_only": evidence_only,
                }
            )

        shared_status = _canonical_shared_multiplier(legacy_shared)[1]["status"]
        legacy_sections.append(
            {"rows": legacy_rows, "shared_multiplier": copy.deepcopy(legacy_shared)}
        )
        claims.append(
            {
                "rows": row_claims,
                "shared_multiplier": canonical_shared,
                "shared_resolution_status": shared_status,
            }
        )
    return _regenerate_and_verify_v2({"sections": legacy_sections}, claims)


def encode_v41_compact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Encode lossless V4.1 while omitting deterministic null/safety fields."""

    validate_v2_response(value)
    _validate_v4_source_wire_values(value)
    _validate_v3_source_shape(value)
    sections: list[dict[str, Any]] = []
    for section_index, section in enumerate(value["sections"]):
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(section["rows"]):
            row_path = f"S{section_index + 1:02d}-R{row_index + 1:02d}"
            row_meta = row[_V2_METADATA_KEY]
            state_key = (
                row_meta["legacy_layout_hint"],
                row_meta["entry_kind"],
                bool(row_meta["evidence_only"]),
            )
            state_code = _V41_ROW_STATE_TO_CODE.get(state_key)
            unresolved_legacy: str | None = None
            if state_code is None:
                if (
                    row_meta["entry_kind"] == "unresolved_row"
                    and row_meta["evidence_only"] is True
                    and row_meta["legacy_layout_hint"] is None
                ):
                    state_code = "Q"
                elif (
                    row_meta["entry_kind"] == "unresolved_row"
                    and row_meta["evidence_only"] is True
                    and isinstance(row_meta["legacy_layout_hint"], str)
                    and row_meta["legacy_layout_hint"]
                ):
                    state_code = "Q"
                    unresolved_legacy = row_meta["legacy_layout_hint"]
                else:
                    raise _v41_error(
                        "v41_row_state_not_lossless",
                        f"{row_path}.k",
                        "legacy layout, entry kind, and evidence safety are not representable",
                    )

            encoded_tokens: list[list[Any]] = []
            for token_index, token in enumerate(row["tokens"]):
                token_path = f"{row_path}-T{token_index + 1:02d}"
                encoded_token = _encode_token_tuple(token, token_path)
                _validate_v4_token_granularity(encoded_token[0], token_path)
                if not _valid_v4_pixel_bbox(encoded_token[1:]):
                    raise _v41_error(
                        "v41_token_bbox_invalid",
                        token_path,
                        "pixel bbox must be finite, nonnegative, and have positive area",
                    )
                encoded_tokens.append(encoded_token)
            number_groups = _validate_v4_number_groups(row["numbers"], f"{row_path}.n")
            _validate_v4_number_evidence(number_groups, row["tokens"], f"{row_path}.n")
            encoded_row: dict[str, Any] = {
                "t": encoded_tokens,
                "n": number_groups,
                "l": _LAYOUT_TO_CODE[row["layout_hint"]],
                "k": state_code,
            }
            if row["multiplier"] is not None:
                encoded_row["m"] = copy.deepcopy(row["multiplier"])
            if "collision" in row:
                encoded_row["c"] = copy.deepcopy(row["collision"])
            if unresolved_legacy is not None:
                encoded_row["u"] = unresolved_legacy
            rows.append(encoded_row)

        encoded_section: dict[str, Any] = {"r": rows}
        canonical_shared = _encode_v4_shared(section["shared_multiplier"])
        if canonical_shared is not None:
            encoded_section["s"] = canonical_shared
        legacy_shared = section[_V2_METADATA_KEY]["legacy_shared_multiplier"]
        if isinstance(legacy_shared, str):
            if canonical_shared is not None:
                raise _v41_error(
                    "v41_shared_conflict",
                    f"S{section_index + 1:02d}.s",
                    "canonical and unresolved shared evidence cannot coexist",
                )
            encoded_section["u"] = legacy_shared
        sections.append(encoded_section)
    return {"v": V41_COMPACT_TRANSPORT_VERSION, "s": sections}


def decode_v41_compact(value: Any) -> dict[str, Any]:
    """Strictly decode V4.1 without inferring or repairing omitted evidence."""

    if not isinstance(value, Mapping):
        raise _v41_error("v41_root_must_be_object", "$", "V4.1 root must be an object")
    _require_v41_keys(value, required={"v", "s"}, optional=set(), path="$")
    if (
        not isinstance(value.get("v"), int)
        or isinstance(value.get("v"), bool)
        or value.get("v") != V41_COMPACT_TRANSPORT_VERSION
    ):
        raise _v41_error("v41_version_invalid", "$.v", "transport version must equal 41")
    sections = value.get("s")
    if not isinstance(sections, list) or not sections:
        raise _v41_error("v41_sections_invalid", "$.s", "sections must be a non-empty list")

    legacy_sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        path = f"$.s[{section_index}]"
        if not isinstance(section, Mapping):
            raise _v41_error("v41_section_invalid", path, "section must be an object")
        _require_v41_keys(section, required={"r"}, optional={"s", "u"}, path=path)
        canonical_shared = _decode_v4_shared(section.get("s"), f"{path}.s")
        if "u" in section:
            if canonical_shared is not None:
                raise _v41_error(
                    "v41_unresolved_shared_conflict",
                    f"{path}.u",
                    "unresolved shared text is allowed only when s is omitted",
                )
            legacy_shared = section.get("u")
            if not isinstance(legacy_shared, str) or not legacy_shared:
                raise _v41_error(
                    "v41_unresolved_shared_invalid",
                    f"{path}.u",
                    "u must be a non-empty exact legacy shared string",
                )
        else:
            legacy_shared = copy.deepcopy(canonical_shared)

        rows = section.get("r")
        if not isinstance(rows, list) or not rows:
            raise _v41_error("v41_rows_invalid", f"{path}.r", "rows must be a non-empty list")
        legacy_rows: list[dict[str, Any]] = []
        row_claims: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            row_path = f"{path}.r[{row_index}]"
            if not isinstance(row, Mapping):
                raise _v41_error("v41_row_invalid", row_path, "row must be an object")
            _require_v41_keys(
                row,
                required={"t", "n", "l", "k"},
                optional={"m", "c", "u"},
                path=row_path,
            )
            layout_code = row.get("l")
            if layout_code not in _CODE_TO_LAYOUT:
                raise _v41_error("v41_layout_invalid", f"{row_path}.l", "layout must be N, C, or null")
            state_code = row.get("k")
            if state_code in _V41_CODE_TO_ROW_STATE:
                if "u" in row:
                    raise _v41_error(
                        "v41_unsupported_layout_conflict",
                        f"{row_path}.u",
                        "u is allowed only for Q state",
                    )
                legacy_layout, entry_kind, evidence_only = _V41_CODE_TO_ROW_STATE[state_code]
            elif state_code == "Q":
                legacy_layout = row.get("u")
                if legacy_layout is not None and (
                    not isinstance(legacy_layout, str) or not legacy_layout
                ):
                    raise _v41_error(
                        "v41_unsupported_layout_invalid",
                        f"{row_path}.u",
                        "Q state u must be omitted or a non-empty exact string",
                    )
                entry_kind = "unresolved_row"
                evidence_only = True
            else:
                raise _v41_error("v41_row_state_invalid", f"{row_path}.k", "unknown row state")

            canonical_layout = _CODE_TO_LAYOUT[layout_code]
            if (
                (state_code == "N" and layout_code != "N")
                or (state_code == "C" and layout_code != "C")
                or (state_code == "R" and layout_code not in {"N", "C"})
                or (state_code in {"U", "M", "S", "Q"} and layout_code is not None)
            ):
                raise _v41_error(
                    "v41_state_layout_mismatch",
                    row_path,
                    "row state and canonical layout are inconsistent",
                )

            tokens = _decode_token_tuples(row.get("t"), f"{row_path}.t")
            for token_index, token in enumerate(tokens):
                token_path = f"{row_path}.t[{token_index}]"
                _validate_v4_token_granularity(token["text"], f"{token_path}[0]")
                if not _valid_v4_pixel_bbox(token["bbox"]):
                    raise _v41_error(
                        "v41_token_bbox_invalid",
                        f"{token_path}[1:5]",
                        "pixel bbox must be finite, nonnegative, and have positive area",
                    )
            numbers = _validate_v4_number_groups(row.get("n"), f"{row_path}.n")
            _validate_v4_number_evidence(numbers, tokens, f"{row_path}.n")
            multiplier = row.get("m")
            if multiplier is not None and not isinstance(multiplier, str):
                raise _v41_error("v41_multiplier_invalid", f"{row_path}.m", "must be string or omitted")
            legacy_row: dict[str, Any] = {
                "tokens": tokens,
                "numbers": numbers,
                "multiplier": copy.deepcopy(multiplier),
                "layout_hint": copy.deepcopy(legacy_layout),
            }
            if "c" in row:
                collision = row.get("c")
                if collision is not None and not isinstance(collision, str):
                    raise _v41_error("v41_collision_invalid", f"{row_path}.c", "must be string or null")
                legacy_row["collision"] = copy.deepcopy(collision)
            legacy_rows.append(legacy_row)
            row_claims.append(
                {
                    "layout_hint": canonical_layout,
                    "entry_kind": entry_kind,
                    "evidence_only": evidence_only,
                }
            )

        shared_status = _canonical_shared_multiplier(legacy_shared)[1]["status"]
        legacy_sections.append(
            {"rows": legacy_rows, "shared_multiplier": copy.deepcopy(legacy_shared)}
        )
        claims.append(
            {
                "rows": row_claims,
                "shared_multiplier": canonical_shared,
                "shared_resolution_status": shared_status,
            }
        )
    return _regenerate_and_verify_v2({"sections": legacy_sections}, claims)


def encode_dense_simplified_evidence(
    value: Mapping[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    """Encode D1 row-level or token-level evidence without inventing geometry."""

    if mode not in {"dense_row", "token"}:
        raise _dense_error("dense_mode_invalid", "$.mode", "mode must be dense_row or token")
    v41 = encode_v41_compact(value)
    sections: list[dict[str, Any]] = []
    for section_index, section in enumerate(v41["s"], start=1):
        section_id = f"S{section_index:02d}"
        dense_section: dict[str, Any] = {"id": section_id, "r": []}
        if "s" in section:
            dense_section["s"] = copy.deepcopy(section["s"])
        if "u" in section:
            dense_section["u"] = copy.deepcopy(section["u"])
        for row_index, row in enumerate(section["r"], start=1):
            row_id = f"{section_id}-L{row_index:02d}"
            tokens = copy.deepcopy(row["t"])
            dense_row: dict[str, Any] = {
                "id": row_id,
                "q": "R" if mode == "dense_row" else "T",
                "x": " ".join(str(token[0]) for token in tokens),
                "b": _dense_union_bbox([token[1:] for token in tokens]),
                "n": copy.deepcopy(row["n"]),
                "l": copy.deepcopy(row["l"]),
                "k": copy.deepcopy(row["k"]),
            }
            for optional in ("m", "c", "u"):
                if optional in row:
                    dense_row[optional] = copy.deepcopy(row[optional])
            if mode == "token":
                dense_row["t"] = tokens
            dense_section["r"].append(dense_row)
        sections.append(dense_section)
    output = {"v": DENSE_EVIDENCE_TRANSPORT_VERSION, "s": sections}
    audit_dense_simplified_evidence(output)
    return output


def audit_dense_simplified_evidence(value: Any) -> dict[str, Any]:
    """Validate D1 and report safe capabilities without creating candidates."""

    if not isinstance(value, Mapping):
        raise _dense_error("dense_root_invalid", "$", "root must be an object")
    _require_dense_keys(value, required={"v", "s"}, optional=set(), path="$")
    if value.get("v") != DENSE_EVIDENCE_TRANSPORT_VERSION:
        raise _dense_error("dense_version_invalid", "$.v", "version must equal D1")
    sections = value.get("s")
    if not isinstance(sections, list) or not sections:
        raise _dense_error("dense_sections_invalid", "$.s", "sections must be non-empty")

    modes: Counter[str] = Counter()
    capabilities: list[dict[str, Any]] = []
    row_count = 0
    for section_index, section in enumerate(sections, start=1):
        section_path = f"$.s[{section_index - 1}]"
        if not isinstance(section, Mapping):
            raise _dense_error("dense_section_invalid", section_path, "section must be object")
        _require_dense_keys(
            section,
            required={"id", "r"},
            optional={"s", "u"},
            path=section_path,
        )
        expected_section_id = f"S{section_index:02d}"
        if section.get("id") != expected_section_id:
            raise _dense_error(
                "dense_section_identity_invalid",
                f"{section_path}.id",
                f"must equal {expected_section_id}",
            )
        canonical_shared = _decode_v4_shared(section.get("s"), f"{section_path}.s")
        if "u" in section:
            if canonical_shared is not None:
                raise _dense_error(
                    "dense_shared_conflict",
                    section_path,
                    "complete and unresolved shared evidence cannot coexist",
                )
            if not isinstance(section.get("u"), str) or not section.get("u"):
                raise _dense_error(
                    "dense_unresolved_shared_invalid",
                    f"{section_path}.u",
                    "must be non-empty string",
                )
        rows = section.get("r")
        if not isinstance(rows, list) or not rows:
            raise _dense_error("dense_rows_invalid", f"{section_path}.r", "rows required")
        for row_index, row in enumerate(rows, start=1):
            row_count += 1
            row_path = f"{section_path}.r[{row_index - 1}]"
            if not isinstance(row, Mapping):
                raise _dense_error("dense_row_invalid", row_path, "row must be object")
            _require_dense_keys(
                row,
                required={"id", "q", "x", "b", "n", "l", "k"},
                optional={"m", "c", "u", "t"},
                path=row_path,
            )
            expected_row_id = f"{expected_section_id}-L{row_index:02d}"
            if row.get("id") != expected_row_id:
                raise _dense_error(
                    "dense_row_identity_invalid",
                    f"{row_path}.id",
                    f"must equal {expected_row_id}",
                )
            mode = row.get("q")
            if mode not in {"R", "T"}:
                raise _dense_error("dense_row_mode_invalid", f"{row_path}.q", "must be R or T")
            modes[str(mode)] += 1
            if not isinstance(row.get("x"), str):
                raise _dense_error("dense_raw_text_invalid", f"{row_path}.x", "must be string")
            if not _valid_v4_pixel_bbox(row.get("b")):
                raise _dense_error(
                    "dense_row_bbox_invalid",
                    f"{row_path}.b",
                    "row bbox must be strict pixel xyxy",
                )
            numbers = _validate_v4_number_groups(row.get("n"), f"{row_path}.n")
            layout_code = row.get("l")
            state_code = row.get("k")
            if layout_code not in _CODE_TO_LAYOUT:
                raise _dense_error("dense_layout_invalid", f"{row_path}.l", "must be N, C, or null")
            if state_code not in {*_V41_CODE_TO_ROW_STATE, "Q"}:
                raise _dense_error("dense_row_state_invalid", f"{row_path}.k", "unknown state")
            if (
                (state_code == "N" and layout_code != "N")
                or (state_code == "C" and layout_code != "C")
                or (state_code == "R" and layout_code not in {"N", "C"})
                or (state_code in {"U", "M", "S", "Q"} and layout_code is not None)
            ):
                raise _dense_error(
                    "dense_state_layout_mismatch",
                    row_path,
                    "row state and layout disagree",
                )
            for optional in ("m", "c"):
                if optional in row and row.get(optional) is not None and not isinstance(row.get(optional), str):
                    raise _dense_error(
                        f"dense_{optional}_invalid",
                        f"{row_path}.{optional}",
                        "must be string or null",
                    )
            if "u" in row and (
                state_code != "Q"
                or not isinstance(row.get("u"), str)
                or not row.get("u")
            ):
                raise _dense_error(
                    "dense_unresolved_role_invalid",
                    f"{row_path}.u",
                    "u is non-empty text for Q state only",
                )

            tokens: list[dict[str, Any]] = []
            if mode == "R":
                if "t" in row:
                    raise _dense_error(
                        "dense_row_mode_must_not_have_tokens",
                        f"{row_path}.t",
                        "R mode cannot contain token/sub-bboxes",
                    )
            else:
                if "t" not in row:
                    raise _dense_error(
                        "dense_token_mode_tokens_missing",
                        f"{row_path}.t",
                        "T mode requires token evidence",
                    )
                tokens = _decode_token_tuples(row.get("t"), f"{row_path}.t")
                for token_index, token in enumerate(tokens):
                    _validate_v4_token_granularity(
                        token["text"], f"{row_path}.t[{token_index}][0]"
                    )
                    if not _valid_v4_pixel_bbox(token["bbox"]):
                        raise _dense_error(
                            "dense_token_bbox_invalid",
                            f"{row_path}.t[{token_index}][1:5]",
                            "token bbox must be strict pixel xyxy",
                        )
                expected_text = " ".join(token["text"] for token in tokens)
                if row.get("x") != expected_text:
                    raise _dense_error(
                        "dense_token_text_mismatch",
                        f"{row_path}.x",
                        "raw text must equal exact token sequence",
                    )
                expected_bbox = _dense_union_bbox([token["bbox"] for token in tokens])
                if row.get("b") != expected_bbox:
                    raise _dense_error(
                        "dense_row_bbox_mismatch",
                        f"{row_path}.b",
                        "row bbox must equal token union in T mode",
                    )
                _validate_v4_number_evidence(numbers, tokens, f"{row_path}.n")

            geometry_required = mode == "R" and (
                layout_code == "C"
                or len(rows) > 1
                or bool(row.get("m"))
                or bool(row.get("c"))
                or "s" in section
                or "u" in section
            )
            capabilities.append(
                {
                    "section_id": expected_section_id,
                    "row_id": expected_row_id,
                    "mode": mode,
                    "raw_text_available": True,
                    "row_bbox_available": True,
                    "grouped_literal_numbers_available": bool(numbers),
                    "token_geometry_available": mode == "T",
                    "coarse_overlay_available": True,
                    "text_parser_preview_available": bool(row.get("x")),
                    "deterministic_geometry_reconstruction_available": mode == "T",
                    "targeted_geometry_verification_required": geometry_required,
                    "needs_review": True,
                    "human_confirmation_required": True,
                    "auto_apply": False,
                    "auto_confirm": False,
                    "auto_submit": False,
                    "executable": False,
                }
            )
    return {
        "valid": True,
        "schema_version": DENSE_EVIDENCE_SCHEMA_VERSION,
        "sections": len(sections),
        "rows": row_count,
        "mode_counts": dict(sorted(modes.items())),
        "capabilities": capabilities,
        "safety": {
            "needs_review": True,
            "human_confirmation_required": True,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
            "executable": False,
        },
    }


def decode_dense_token_evidence(value: Any) -> dict[str, Any]:
    """Losslessly decode only T-mode D1; R-mode never gets synthetic tokens."""

    audit = audit_dense_simplified_evidence(value)
    if audit["mode_counts"].get("R"):
        raise _dense_error(
            "dense_geometry_verification_required",
            "$.s",
            "row-level evidence cannot become runtime tokens without targeted verification",
        )
    v41_sections: list[dict[str, Any]] = []
    for section in value["s"]:
        v41_section: dict[str, Any] = {"r": []}
        for optional in ("s", "u"):
            if optional in section:
                v41_section[optional] = copy.deepcopy(section[optional])
        for row in section["r"]:
            v41_row = {
                key: copy.deepcopy(row[key])
                for key in ("t", "n", "l", "k")
            }
            for optional in ("m", "c", "u"):
                if optional in row:
                    v41_row[optional] = copy.deepcopy(row[optional])
            v41_section["r"].append(v41_row)
        v41_sections.append(v41_section)
    return decode_v41_compact({"v": V41_COMPACT_TRANSPORT_VERSION, "s": v41_sections})


def ablate_recognition_to_row_bbox(value: Mapping[str, Any]) -> dict[str, Any]:
    """Drop token evidence while retaining immutable line text and row bbox."""

    output = copy.deepcopy(dict(value))
    raw_lines = output.get("lines")
    if isinstance(raw_lines, list):
        for raw_line in raw_lines:
            if isinstance(raw_line, dict):
                raw_line["tokens"] = []
    return output


def compact_size_metrics(value: Any) -> dict[str, int]:
    """Deterministic JSON pressure metrics; not a model-token estimate."""

    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    key_overhead = _key_overhead_chars(value)
    punctuation = _json_punctuation_chars(serialized)
    strings = _value_strings(value)
    counts: dict[str, int] = {}
    for item in strings:
        counts[item] = counts.get(item, 0) + 1
    duplicate_text = sum((count - 1) * len(text) for text, count in counts.items() if count > 1)
    return {
        "serialized_chars": len(serialized),
        "serialized_bytes": len(serialized.encode("utf-8")),
        "field_key_overhead_chars": key_overhead,
        "punctuation_overhead_chars": punctuation,
        "duplicate_value_text_chars": duplicate_text,
    }


def v3_reference_wrapper_metrics(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compare only the transport wrapper around identical compact sections."""

    object_wire = encode_v3_token_reference(value)
    sections = copy.deepcopy(object_wire["s"])
    minimal_array_wire = [V3_REFERENCE_TRANSPORT_VERSION, sections]
    previous_qa_array_wire = [
        V3_REFERENCE_SCHEMA_VERSION,
        copy.deepcopy(_V3_SAFETY),
        sections,
    ]
    object_metrics = compact_size_metrics(object_wire)
    minimal_array_metrics = compact_size_metrics(minimal_array_wire)
    previous_metrics = compact_size_metrics(previous_qa_array_wire)
    return {
        "minimal_array": minimal_array_metrics,
        "transport_object": object_metrics,
        "previous_qa_array": previous_metrics,
        "object_minus_minimal_array_chars": object_metrics["serialized_chars"]
        - minimal_array_metrics["serialized_chars"],
        "object_minus_minimal_array_bytes": object_metrics["serialized_bytes"]
        - minimal_array_metrics["serialized_bytes"],
        "object_minus_previous_qa_array_chars": object_metrics["serialized_chars"]
        - previous_metrics["serialized_chars"],
        "object_minus_previous_qa_array_bytes": object_metrics["serialized_bytes"]
        - previous_metrics["serialized_bytes"],
    }


def _encode_v3_array(value: Mapping[str, Any], *, references: bool) -> Any:
    validate_v2_response(value)
    _validate_v3_source_shape(value)
    sections: list[Any] = []
    for section_index, section in enumerate(value["sections"]):
        section_meta = section[_V2_METADATA_KEY]
        rows: list[Any] = []
        for row_index, row in enumerate(section["rows"]):
            row_meta = row[_V2_METADATA_KEY]
            tokens = [_encode_token_tuple(token, f"S{section_index + 1:02d}-R{row_index + 1:02d}") for token in row["tokens"]]
            numbers = (
                _encode_number_references(row, row_meta)
                if references
                else copy.deepcopy(row["numbers"])
            )
            collision = [1, copy.deepcopy(row["collision"])] if "collision" in row else [0]
            rows.append(
                [
                    tokens,
                    numbers,
                    copy.deepcopy(row["multiplier"]),
                    _LAYOUT_TO_CODE[row["layout_hint"]],
                    collision,
                    _LEGACY_LAYOUT_TO_CODE[row_meta["legacy_layout_hint"]],
                    _ENTRY_TO_CODE[row_meta["entry_kind"]],
                    bool(row_meta["evidence_only"]),
                    bool(row_meta["executable"]),
                ]
            )
        shared_resolution = section_meta["shared_multiplier_resolution"]
        sections.append(
            [
                rows,
                _encode_shared_value(section["shared_multiplier"]),
                _encode_shared_value(section_meta["legacy_shared_multiplier"]),
                _SHARED_STATUS_TO_CODE[shared_resolution["status"]],
                False,
            ]
        )
    if references:
        # V3 transport version fixes the root safety metadata.  The decoder
        # regenerates and verifies the full canonical V2 safety object.
        return {"v": V3_REFERENCE_TRANSPORT_VERSION, "s": sections}
    return [V3_TUPLE_SCHEMA_VERSION, copy.deepcopy(_V3_SAFETY), sections]


def _decode_v3_array(value: Any, *, references: bool) -> dict[str, Any]:
    if references:
        if isinstance(value, list):
            raise _v3_error("v3_root_must_be_object", "$", "Candidate C root cannot be an array")
        if not isinstance(value, Mapping):
            raise _v3_error("v3_root_must_be_object", "$", "Candidate C root must be an object")
        if "sections" in value:
            raise _v3_error(
                "legacy_representation_not_allowed",
                "$",
                "legacy sections object is not Candidate C",
            )
        unknown = set(value) - {"v", "s"}
        if unknown:
            raise _v3_error(
                "v3_root_unknown_key",
                "$",
                f"unknown root keys: {sorted(unknown)}",
            )
        if "v" not in value:
            raise _v3_error("v3_version_missing", "$.v", "transport version is required")
        if value.get("v") != V3_REFERENCE_TRANSPORT_VERSION or isinstance(value.get("v"), bool):
            raise _v3_error("v3_version_invalid", "$.v", "transport version must equal 3")
        if "s" not in value:
            raise _v3_error("v3_sections_missing", "$.s", "compact sections are required")
        sections = value["s"]
        sections_path = "$.s"
    else:
        if not isinstance(value, list) or len(value) != 3 or value[0] != V3_TUPLE_SCHEMA_VERSION:
            raise _v3_error("v3_array_root_invalid", "$", "invalid compact array root/version")
        if value[1] != _V3_SAFETY:
            raise _v3_error("v3_safety_invalid", "$[1]", "safety tuple was modified")
        sections = value[2]
        sections_path = "$[2]"
    if not isinstance(sections, list) or not sections:
        raise _v3_error("v3_sections_invalid", sections_path, "sections must be non-empty")
    legacy_sections: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        path = f"{sections_path}[{section_index}]"
        if not isinstance(section, list) or len(section) != 5:
            raise _v3_error("v3_section_tuple_invalid", path, "section tuple length must be 5")
        rows, shared_encoded, legacy_shared_encoded, shared_status_code, executable = section
        if executable is not False:
            raise _v3_error("v3_section_executable", f"{path}[4]", "must be false")
        if shared_status_code not in _CODE_TO_SHARED_STATUS:
            raise _v3_error("v3_shared_status_invalid", f"{path}[3]", "unknown shared status")
        if not isinstance(rows, list) or not rows:
            raise _v3_error("v3_rows_invalid", f"{path}[0]", "rows must be non-empty")
        legacy_rows: list[dict[str, Any]] = []
        row_claims: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            row_path = f"{path}[0][{row_index}]"
            if not isinstance(row, list) or len(row) != 9:
                raise _v3_error("v3_row_tuple_invalid", row_path, "row tuple length must be 9")
            token_values, numbers_value, multiplier, layout_code, collision, legacy_layout_code, role_code, evidence_only, row_executable = row
            if layout_code not in _CODE_TO_LAYOUT:
                raise _v3_error("v3_layout_code_invalid", f"{row_path}[3]", "unknown layout code")
            if role_code not in _CODE_TO_ENTRY:
                raise _v3_error("v3_entry_code_invalid", f"{row_path}[6]", "unknown entry code")
            if legacy_layout_code not in _CODE_TO_LEGACY_LAYOUT:
                raise _v3_error(
                    "v3_legacy_layout_code_invalid",
                    f"{row_path}[5]",
                    "unknown legacy layout code",
                )
            if row_executable is not False:
                raise _v3_error("v3_row_executable", f"{row_path}[8]", "must be false")
            tokens = _decode_token_tuples(token_values, f"{row_path}[0]")
            numbers = (
                _decode_number_references(numbers_value, tokens, f"{row_path}[1]")
                if references
                else _validate_number_groups(numbers_value, f"{row_path}[1]")
            )
            legacy_row: dict[str, Any] = {
                "tokens": tokens,
                "numbers": numbers,
                "multiplier": copy.deepcopy(multiplier),
                "layout_hint": _CODE_TO_LEGACY_LAYOUT[legacy_layout_code],
            }
            if not isinstance(collision, list) or len(collision) not in {1, 2}:
                raise _v3_error("v3_collision_slot_invalid", f"{row_path}[4]", "invalid collision slot")
            if collision[0] == 1 and len(collision) == 2:
                legacy_row["collision"] = copy.deepcopy(collision[1])
            elif collision != [0]:
                raise _v3_error("v3_collision_slot_invalid", f"{row_path}[4]", "invalid collision slot")
            legacy_rows.append(legacy_row)
            row_claims.append(
                {
                    "layout_hint": _CODE_TO_LAYOUT[layout_code],
                    "entry_kind": _CODE_TO_ENTRY[role_code],
                    "evidence_only": evidence_only,
                    **(
                        {"number_references": copy.deepcopy(numbers_value)}
                        if references
                        else {}
                    ),
                }
            )
        legacy_sections.append(
            {
                "rows": legacy_rows,
                "shared_multiplier": _decode_shared_value(legacy_shared_encoded, f"{path}[2]"),
            }
        )
        claims.append(
            {
                "rows": row_claims,
                "shared_multiplier": _decode_shared_value(shared_encoded, f"{path}[1]"),
                "shared_resolution_status": _CODE_TO_SHARED_STATUS[shared_status_code],
            }
        )
    return _regenerate_and_verify_v2({"sections": legacy_sections}, claims)


def compact_token_objects(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied tuple-C form: ``[text,x1,y1,x2,y2]``.

    The adapter fails closed if a token has fields outside the tuple-C
    contract; silently dropping such fields would not be lossless.
    """

    output = copy.deepcopy(dict(value))
    for token, path, replace in _token_slots(output):
        if not isinstance(token, Mapping):
            raise ShadowContractValidationError(
                [_issue("token_wrong_type", path, "token must be an object before compaction")]
            )
        if frozenset(token) != TOKEN_OBJECT_KEYS:
            raise ShadowContractValidationError(
                [
                    _issue(
                        "tuple_c_token_fields_not_lossless",
                        path,
                        "tuple-C only supports token fields text and bbox",
                    )
                ]
            )
        text = token.get("text")
        bbox = token.get("bbox")
        token_issues = _audit_token(token, path)
        if token_issues:
            raise ShadowContractValidationError(token_issues)
        replace([text, *bbox])
    return output


def expand_token_tuples(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied canonical object form from tuple-C tokens."""

    output = copy.deepcopy(dict(value))
    for token, path, replace in _token_slots(output):
        if (
            not isinstance(token, list)
            or len(token) != 5
            or not isinstance(token[0], str)
            or not _valid_bbox(token[1:])
        ):
            raise ShadowContractValidationError(
                [
                    _issue(
                        "tuple_c_token_invalid",
                        path,
                        "tuple-C token must be [text,x1,y1,x2,y2]",
                    )
                ]
            )
        replace({"text": token[0], "bbox": list(token[1:])})
    return output


def load_cache_response(path: Path) -> dict[str, Any]:
    """Load a successful persistent-cache record without network access."""

    record = json.loads(path.read_text(encoding="utf-8"))
    content = record.get("response_content")
    if not isinstance(content, str):
        raise ValueError("cache response_content must be a string")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("cached response root must be an object")
    return parsed


def _initial_layout_resolution(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return _layout_resolution(
            legacy_layout_hint=None,
            entry_kind="unresolved_row",
            layout_hint=None,
            status="unresolved",
            source="invalid_row",
            reason="row_wrong_type",
            evidence={},
        )
    row = value
    legacy_layout = row.get("layout_hint")
    evidence = _row_layout_evidence(row)
    if legacy_layout in CANONICAL_LAYOUT_HINTS:
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="bet_row",
            layout_hint=str(legacy_layout),
            status="resolved",
            source="canonical_legacy_value",
            reason="already_canonical",
            evidence=evidence,
        )
    if legacy_layout == "multiplier_only":
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="multiplier_evidence",
            layout_hint=None,
            status="not_applicable",
            source="legacy_evidence_role",
            reason="multiplier_only_is_not_a_bet_row",
            evidence=evidence,
        )
    if legacy_layout == "note":
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="note_evidence",
            layout_hint=None,
            status="unresolved",
            source="legacy_evidence_role",
            reason="note_scope_requires_human_resolution",
            evidence=evidence,
        )
    if legacy_layout != "row_bet":
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="unresolved_row",
            layout_hint=None,
            status="unresolved",
            source="legacy_layout_unknown",
            reason="unsupported_legacy_layout_hint",
            evidence=evidence,
        )

    column_separators = int(evidence["column_separator_count"])
    normal_separators = int(evidence["normal_separator_count"])
    number_groups = row.get("numbers")
    one_explicit_group = (
        isinstance(number_groups, list)
        and len(number_groups) == 1
        and isinstance(number_groups[0], list)
        and len(number_groups[0]) >= 2
    )
    if column_separators and not normal_separators:
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="bet_row",
            layout_hint="column_bet",
            status="resolved",
            source="number_separator_geometry",
            reason="x_separator_between_matched_number_tokens",
            evidence=evidence,
        )
    if normal_separators and not column_separators:
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="bet_row",
            layout_hint="normal_row",
            status="resolved",
            source="number_separator_geometry",
            reason="punctuation_separator_between_matched_number_tokens",
            evidence=evidence,
        )
    if (
        one_explicit_group
        and not column_separators
        and not evidence["rejected_separator_token_indices"]
        and evidence["number_bbox_order_consistent"]
    ):
        return _layout_resolution(
            legacy_layout_hint=legacy_layout,
            entry_kind="bet_row",
            layout_hint="normal_row",
            status="resolved",
            source="numbers_grouping",
            reason="one_explicit_multi_number_group",
            evidence=evidence,
        )
    return _layout_resolution(
        legacy_layout_hint=legacy_layout,
        entry_kind="unresolved_row",
        layout_hint=None,
        status="unresolved",
        source="structure_local_evidence",
        reason="row_bet_layout_not_unique",
        evidence=evidence,
    )


def _layout_resolution(
    *,
    legacy_layout_hint: Any,
    entry_kind: str,
    layout_hint: str | None,
    status: str,
    source: str,
    reason: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "legacy_layout_hint": copy.deepcopy(legacy_layout_hint),
        "entry_kind": entry_kind,
        "layout_hint": layout_hint,
        "status": status,
        "source": source,
        "reason": reason,
        "evidence": copy.deepcopy(dict(evidence)),
    }


def _row_layout_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    tokens = row.get("tokens")
    token_values = tokens if isinstance(tokens, list) else []
    expected_numbers = [
        str(number)
        for group in row.get("numbers", [])
        if isinstance(group, list)
        for number in group
        if isinstance(number, str)
    ]
    remaining = list(expected_numbers)
    matched_indices: list[int] = []
    matched_bboxes: list[list[float]] = []
    matched_texts: list[str] = []
    for token_index, token in enumerate(token_values):
        if not isinstance(token, Mapping):
            continue
        text = token.get("text")
        if text not in remaining:
            continue
        bbox = token.get("bbox")
        if not _valid_bbox(bbox):
            continue
        matched_indices.append(token_index)
        matched_bboxes.append(list(bbox))
        matched_texts.append(str(text))
        remaining.remove(text)

    column_separator_ids: list[int] = []
    normal_separator_ids: list[int] = []
    rejected_separator_ids: list[int] = []
    rejected_separator_reasons: dict[str, str] = {}
    bbox_order_consistent = all(
        _bbox_center_x(left_bbox) < _bbox_center_x(right_bbox)
        for left_bbox, right_bbox in zip(matched_bboxes, matched_bboxes[1:])
    )
    for pair_index, (left_index, right_index) in enumerate(
        zip(matched_indices, matched_indices[1:])
    ):
        left_bbox = matched_bboxes[pair_index]
        right_bbox = matched_bboxes[pair_index + 1]
        between = token_values[left_index + 1 : right_index]
        for offset, token in enumerate(between, start=left_index + 1):
            text = str(token.get("text") or "") if isinstance(token, Mapping) else ""
            if not (
                _COLUMN_SEPARATOR_RE.fullmatch(text)
                or _NORMAL_SEPARATOR_RE.fullmatch(text)
            ):
                continue
            separator_bbox = token.get("bbox") if isinstance(token, Mapping) else None
            geometry_reason = _separator_geometry_rejection(
                separator_bbox,
                left_bbox=left_bbox,
                right_bbox=right_bbox,
                bbox_order_consistent=bbox_order_consistent,
            )
            if geometry_reason is not None:
                rejected_separator_ids.append(offset)
                rejected_separator_reasons[str(offset)] = geometry_reason
                continue
            if _COLUMN_SEPARATOR_RE.fullmatch(text):
                column_separator_ids.append(offset)
            else:
                normal_separator_ids.append(offset)
    return {
        "expected_number_values": expected_numbers,
        "matched_number_values": matched_texts,
        "matched_number_token_indices": matched_indices,
        "matched_number_bboxes": matched_bboxes,
        "unmatched_number_values": remaining,
        "column_separator_token_indices": column_separator_ids,
        "normal_separator_token_indices": normal_separator_ids,
        "rejected_separator_token_indices": rejected_separator_ids,
        "rejected_separator_reasons": rejected_separator_reasons,
        "number_bbox_order_consistent": bbox_order_consistent,
        "column_separator_count": len(column_separator_ids),
        "normal_separator_count": len(normal_separator_ids),
    }


def _separator_geometry_rejection(
    value: Any,
    *,
    left_bbox: list[float],
    right_bbox: list[float],
    bbox_order_consistent: bool,
) -> str | None:
    if not bbox_order_consistent:
        return "matched_number_bbox_order_conflicts_with_token_order"
    if not _valid_bbox(value):
        return "separator_bbox_invalid"
    separator_bbox = [float(item) for item in value]
    left = [float(item) for item in left_bbox]
    right = [float(item) for item in right_bbox]
    separator_center_x = _bbox_center_x(separator_bbox)
    left_center_x = _bbox_center_x(left)
    right_center_x = _bbox_center_x(right)
    if not left_center_x < separator_center_x < right_center_x:
        return "separator_bbox_not_between_number_bboxes"

    # Permit slight handwriting overlap at the edges, but require the
    # separator's geometry to occupy the actual horizontal gap.
    separator_width = max(separator_bbox[2] - separator_bbox[0], 1.0)
    if (
        separator_bbox[0] < left[2] - separator_width * 0.5
        or separator_bbox[2] > right[0] + separator_width * 0.5
    ):
        return "separator_bbox_not_between_number_bboxes"

    left_height = max(left[3] - left[1], 1.0)
    right_height = max(right[3] - right[1], 1.0)
    separator_height = max(separator_bbox[3] - separator_bbox[1], 1.0)
    reference_height = max(left_height, right_height, separator_height)
    left_center_y = (left[1] + left[3]) / 2.0
    right_center_y = (right[1] + right[3]) / 2.0
    separator_center_y = (separator_bbox[1] + separator_bbox[3]) / 2.0
    if (
        abs(left_center_y - right_center_y) > reference_height
        or abs(separator_center_y - left_center_y) > reference_height
        or abs(separator_center_y - right_center_y) > reference_height
    ):
        return "separator_bbox_outside_number_y_band"
    return None


def _bbox_center_x(value: list[float]) -> float:
    return (float(value[0]) + float(value[2])) / 2.0


def _evidence_attachment(
    resolution: Mapping[str, Any],
    *,
    section_id: str,
    resolved_rows: list[int],
) -> dict[str, Any] | None:
    entry_kind = resolution.get("entry_kind")
    if entry_kind == "multiplier_evidence":
        if resolved_rows:
            return {
                "status": "section_scoped_evidence",
                "section_id": section_id,
                "target_row_ids": [f"{section_id}-R{index:02d}" for index in resolved_rows],
                "executable": False,
            }
        return {
            "status": "unresolved",
            "section_id": section_id,
            "target_row_ids": [],
            "reason": "no_resolved_bet_row_in_section",
            "executable": False,
        }
    if entry_kind == "note_evidence":
        return {
            "status": "unresolved_scope_evidence",
            "section_id": section_id,
            "target_row_ids": [],
            "reason": "no_deterministic_target_structure",
            "executable": False,
        }
    if entry_kind == "unresolved_row":
        return {
            "status": "unresolved",
            "section_id": section_id,
            "target_row_ids": [],
            "executable": False,
        }
    return None


def _canonical_shared_multiplier(value: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if value is None:
        return None, {
            "status": "absent",
            "reason": None,
            "canonical_rules": [],
            "evidence_only": True,
            "executable": False,
        }
    if not isinstance(value, Mapping):
        text = value if isinstance(value, str) else None
        return None, {
            "status": "unresolved",
            "reason": "shared_multiplier_bbox_missing",
            "raw_evidence": copy.deepcopy(value),
            "canonical_rules": _complete_multiplier_rules(text),
            "evidence_only": True,
            "executable": False,
        }
    text = value.get("text")
    bbox = value.get("bbox")
    if not isinstance(text, str) or not text.strip() or not _valid_bbox(bbox):
        return None, {
            "status": "unresolved",
            "reason": "shared_multiplier_visual_evidence_invalid",
            "raw_evidence": copy.deepcopy(dict(value)),
            "canonical_rules": _complete_multiplier_rules(text),
            "evidence_only": True,
            "executable": False,
        }
    rules = _complete_multiplier_rules(text)
    if rules:
        status = "complete_visual_evidence"
        reason = "runtime_scope_and_validator_still_required"
    else:
        status = "unresolved"
        reason = "shared_multiplier_category_or_value_incomplete"
    return copy.deepcopy(dict(value)), {
        "status": status,
        "reason": reason,
        "raw_evidence": copy.deepcopy(dict(value)),
        "canonical_rules": rules,
        "evidence_only": True,
        "executable": False,
    }


def _complete_multiplier_rules(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    # Keep the shadow tool independent from the production provider while
    # reusing the already shipped multiplier policy as the semantic authority.
    from betguard.vision.multiplier_policy import split_complete_rules

    return split_complete_rules(value)


def _audit_v2_row(value: Any, path: str) -> list[ContractIssue]:
    if not isinstance(value, Mapping):
        return [_issue("row_wrong_type", path, "row must be an object")]
    row = value
    # Reuse every structural check except the legacy layout enum check; V2
    # allows null only for an explicitly typed fail-closed evidence entry.
    issues = [
        issue
        for issue in _audit_row(row, path)
        if issue.code != "layout_hint_invalid_enum"
    ]
    metadata = row.get(_V2_METADATA_KEY)
    if not isinstance(metadata, Mapping):
        issues.append(_issue("v2_row_metadata_missing", f"{path}._v2", "metadata required"))
        return issues
    entry_kind = metadata.get("entry_kind")
    resolution = metadata.get("layout_resolution")
    layout = row.get("layout_hint")
    if metadata.get("executable") is not False:
        issues.append(_issue("v2_row_executable", f"{path}._v2.executable", "must be false"))
    if entry_kind == "bet_row":
        if layout not in CANONICAL_LAYOUT_HINTS:
            issues.append(
                _issue("v2_bet_layout_unresolved", f"{path}.layout_hint", "bet row requires canonical layout")
            )
        if not isinstance(resolution, Mapping) or resolution.get("status") != "resolved":
            issues.append(
                _issue("v2_layout_resolution_invalid", f"{path}._v2.layout_resolution", "must be resolved")
            )
    elif entry_kind in {"multiplier_evidence", "note_evidence", "unresolved_row"}:
        if layout is not None:
            issues.append(
                _issue("v2_evidence_layout_must_be_null", f"{path}.layout_hint", "evidence rows have no bet layout")
            )
        if metadata.get("evidence_only") is not True:
            issues.append(
                _issue("v2_evidence_not_fail_closed", f"{path}._v2.evidence_only", "must be true")
            )
        attachment = metadata.get("attachment")
        if not isinstance(attachment, Mapping) or attachment.get("executable") is not False:
            issues.append(
                _issue("v2_attachment_not_fail_closed", f"{path}._v2.attachment", "must be non-executable")
            )
        elif entry_kind == "note_evidence" and (
            not isinstance(resolution, Mapping)
            or resolution.get("status") != "unresolved"
            or attachment.get("status") != "unresolved_scope_evidence"
            or attachment.get("target_row_ids") != []
        ):
            issues.append(
                _issue(
                    "v2_note_scope_not_unresolved",
                    f"{path}._v2",
                    "note evidence must retain unresolved scope and no target",
                )
            )
        elif entry_kind == "unresolved_row" and (
            not isinstance(resolution, Mapping) or resolution.get("status") != "unresolved"
        ):
            issues.append(
                _issue(
                    "v2_unresolved_row_status_invalid",
                    f"{path}._v2.layout_resolution",
                    "unresolved row must retain unresolved status",
                )
            )
    else:
        issues.append(
            _issue("v2_entry_kind_invalid", f"{path}._v2.entry_kind", "unknown V2 entry kind")
        )
    return issues


def _validate_v3_source_shape(value: Mapping[str, Any]) -> None:
    _require_exact_keys(value, {"sections", "response_schema_version", _V2_METADATA_KEY}, "$")
    root_meta = value[_V2_METADATA_KEY]
    _require_exact_keys(
        root_meta,
        {"source_schema_version", "canonicalizer_version", "artifact_kind", "safety"},
        "$._v2",
    )
    _require_exact_keys(
        root_meta["safety"],
        {
            "human_confirmation_required",
            "auto_apply",
            "auto_confirm",
            "auto_submit",
            "executable",
        },
        "$._v2.safety",
    )
    for section_index, section in enumerate(value["sections"]):
        section_path = f"$.sections[{section_index}]"
        _require_exact_keys(section, {"rows", "shared_multiplier", _V2_METADATA_KEY}, section_path)
        section_meta = section[_V2_METADATA_KEY]
        _require_exact_keys(
            section_meta,
            {"section_id", "legacy_shared_multiplier", "shared_multiplier_resolution", "executable"},
            f"{section_path}._v2",
        )
        _require_keys(
            section_meta["shared_multiplier_resolution"],
            required={"status", "reason", "canonical_rules", "evidence_only", "executable"},
            optional={"raw_evidence"},
            path=f"{section_path}._v2.shared_multiplier_resolution",
        )
        for row_index, row in enumerate(section["rows"]):
            row_path = f"{section_path}.rows[{row_index}]"
            allowed = {"tokens", "numbers", "multiplier", "layout_hint", "collision", _V2_METADATA_KEY}
            _require_exact_keys(row, allowed, row_path)
            row_meta = row[_V2_METADATA_KEY]
            _require_exact_keys(
                row_meta,
                {
                    "row_id",
                    "legacy_layout_hint",
                    "entry_kind",
                    "layout_resolution",
                    "attachment",
                    "evidence_only",
                    "executable",
                },
                f"{row_path}._v2",
            )
            resolution = row_meta["layout_resolution"]
            _require_exact_keys(
                resolution,
                {"status", "source", "reason", "evidence"},
                f"{row_path}._v2.layout_resolution",
            )
            _require_exact_keys(
                resolution["evidence"],
                {
                    "expected_number_values",
                    "matched_number_values",
                    "matched_number_token_indices",
                    "matched_number_bboxes",
                    "unmatched_number_values",
                    "column_separator_token_indices",
                    "normal_separator_token_indices",
                    "rejected_separator_token_indices",
                    "rejected_separator_reasons",
                    "number_bbox_order_consistent",
                    "column_separator_count",
                    "normal_separator_count",
                },
                f"{row_path}._v2.layout_resolution.evidence",
            )
            attachment = row_meta["attachment"]
            if attachment is not None:
                _require_keys(
                    attachment,
                    required={"status", "section_id", "target_row_ids", "executable"},
                    optional={"reason"},
                    path=f"{row_path}._v2.attachment",
                )
            for token_index, token in enumerate(row["tokens"]):
                token_path = f"{row_path}.tokens[{token_index}]"
                if not isinstance(token, Mapping) or frozenset(token) != TOKEN_OBJECT_KEYS:
                    raise _v3_error(
                        "v3_token_fields_not_lossless",
                        token_path,
                        "V3 token tuple only supports exact text+bbox evidence",
                    )

    # All supported V2 metadata is derived deterministically from retained
    # legacy evidence.  Verify the whole artifact is reproducible so a known
    # metadata value cannot be changed and then silently normalized away.
    regenerated = canonicalize_legacy_response(adapt_v2_response_to_runtime(value))
    if regenerated != value:
        raise _v3_error(
            "v3_metadata_not_reproducible",
            "$._v2",
            "V2 metadata differs from deterministic canonical regeneration",
        )


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    _require_keys(value, required=allowed, optional=set(), path=path)


def _require_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    path: str,
) -> None:
    if not isinstance(value, Mapping):
        raise _v3_error("v3_fields_not_lossless", path, "metadata must be an object")
    actual = set(value)
    unknown = actual - required - optional
    missing = required - actual
    # collision is optional on rows and is the only allowed optional key.
    if path.endswith("]") and "tokens" in required:
        missing.discard("collision")
    if unknown or missing:
        raise _v3_error(
            "v3_fields_not_lossless",
            path,
            f"unknown={sorted(unknown)} missing={sorted(missing)}",
        )


def _encode_token_tuple(value: Any, path: str) -> list[Any]:
    if not isinstance(value, Mapping) or frozenset(value) != TOKEN_OBJECT_KEYS:
        raise _v3_error(
            "v3_token_fields_not_lossless",
            path,
            "token must contain exactly text and bbox",
        )
    issues = _audit_token(value, path)
    if issues:
        raise ShadowContractValidationError(issues)
    return [value["text"], *copy.deepcopy(value["bbox"])]


def _decode_token_tuples(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise _v3_error("v3_tokens_invalid", path, "tokens must be a non-empty list")
    output: list[dict[str, Any]] = []
    for index, token in enumerate(value):
        token_path = f"{path}[{index}]"
        if (
            not isinstance(token, list)
            or len(token) != 5
            or not isinstance(token[0], str)
            or not _valid_bbox(token[1:])
        ):
            raise _v3_error(
                "v3_token_tuple_invalid",
                token_path,
                "token must be [text,x1,y1,x2,y2]",
            )
        output.append({"text": token[0], "bbox": copy.deepcopy(token[1:])})
    return output


def _encode_number_references(
    row: Mapping[str, Any],
    row_meta: Mapping[str, Any],
) -> list[list[int]]:
    numbers = _validate_number_groups(row.get("numbers"), "row.numbers")
    resolution = row_meta.get("layout_resolution")
    evidence = resolution.get("evidence") if isinstance(resolution, Mapping) else None
    indexes = evidence.get("matched_number_token_indices") if isinstance(evidence, Mapping) else None
    flat_numbers = [number for group in numbers for number in group]
    if not isinstance(indexes, list) or len(indexes) != len(flat_numbers):
        if not flat_numbers and indexes in (None, []):
            return []
        raise _v3_error(
            "v3_number_reference_evidence_missing",
            "row.numbers",
            "exact matched token indexes are required; value search is forbidden",
        )
    if any(isinstance(index, bool) or not isinstance(index, int) for index in indexes):
        raise _v3_error("v3_number_reference_invalid", "row.numbers", "indexes must be integers")
    if len(indexes) != len(set(indexes)):
        raise _v3_error(
            "v3_number_reference_duplicate_index",
            "row.numbers",
            "one token index cannot represent two number evidence slots",
        )
    tokens = row.get("tokens")
    if not isinstance(tokens, list):
        raise _v3_error("v3_tokens_invalid", "row.tokens", "tokens must be a list")
    for number, index in zip(flat_numbers, indexes):
        if index < 0 or index >= len(tokens):
            raise _v3_error("v3_number_reference_out_of_range", "row.numbers", "index out of range")
        token = tokens[index]
        if not isinstance(token, Mapping) or token.get("text") != number:
            raise _v3_error(
                "v3_number_reference_text_mismatch",
                "row.numbers",
                "referenced token text must exactly equal model number evidence",
            )
    output: list[list[int]] = []
    cursor = 0
    for group in numbers:
        output.append(copy.deepcopy(indexes[cursor : cursor + len(group)]))
        cursor += len(group)
    return output


def _decode_number_references(
    value: Any,
    tokens: list[dict[str, Any]],
    path: str,
) -> list[list[str]]:
    if not isinstance(value, list):
        raise _v3_error("v3_number_references_invalid", path, "number refs must be groups")
    output: list[list[str]] = []
    used: set[int] = set()
    for group_index, group in enumerate(value):
        if not isinstance(group, list):
            raise _v3_error("v3_number_reference_group_invalid", f"{path}[{group_index}]", "group must be a list")
        values: list[str] = []
        for ref_index, token_index in enumerate(group):
            ref_path = f"{path}[{group_index}][{ref_index}]"
            if isinstance(token_index, bool) or not isinstance(token_index, int):
                raise _v3_error("v3_number_reference_invalid", ref_path, "reference must be integer")
            if token_index < 0 or token_index >= len(tokens):
                raise _v3_error("v3_number_reference_out_of_range", ref_path, "reference out of range")
            if token_index in used:
                raise _v3_error(
                    "v3_number_reference_duplicate_index",
                    ref_path,
                    "duplicate token reference is ambiguous",
                )
            used.add(token_index)
            text = tokens[token_index]["text"]
            if not re.fullmatch(r"\d{1,2}", text):
                raise _v3_error(
                    "v3_number_reference_not_number_token",
                    ref_path,
                    "reference must point to exact numeric model evidence",
                )
            values.append(text)
        output.append(values)
    return output


def _validate_number_groups(value: Any, path: str) -> list[list[str]]:
    if not isinstance(value, list):
        raise _v3_error("v3_numbers_invalid", path, "numbers must be a list of groups")
    output: list[list[str]] = []
    for group_index, group in enumerate(value):
        if not isinstance(group, list) or any(not isinstance(number, str) for number in group):
            raise _v3_error(
                "v3_number_group_invalid",
                f"{path}[{group_index}]",
                "number group must contain strings",
            )
        output.append(copy.deepcopy(group))
    return output


def _validate_v4_number_groups(value: Any, path: str) -> list[list[str]]:
    if not isinstance(value, list):
        raise _v4_error("v4_numbers_invalid", path, "numbers must be grouped string lists")
    output: list[list[str]] = []
    for group_index, group in enumerate(value):
        if not isinstance(group, list) or any(not isinstance(number, str) for number in group):
            raise _v4_error(
                "v4_number_group_invalid",
                f"{path}[{group_index}]",
                "each number group must contain strings",
            )
        for number_index, number in enumerate(group):
            if re.fullmatch(r"\d{1,2}", number) is None:
                raise _v4_error(
                    "v4_number_value_invalid",
                    f"{path}[{group_index}][{number_index}]",
                    "number evidence must be an exact one- or two-digit string",
                )
        output.append(copy.deepcopy(group))
    return output


def _validate_v4_token_granularity(text: str, path: str) -> None:
    visible_separators = frozenset({"x", "X", "×", "/", ".", ",", "，", "。"})
    fused_numeric = re.search(r"\d", text) and re.fullmatch(r"\d{1,2}", text) is None
    fused_separators = any(char in visible_separators for char in text) and (
        len(text) != 1 or text not in visible_separators
    )
    if fused_numeric or fused_separators:
        raise _v4_error(
            "v4_token_granularity_invalid",
            path,
            "numeric values, separators, operators, and decimal fragments must be separate tokens",
        )


def _validate_v4_number_evidence(
    numbers: list[list[str]],
    tokens: list[dict[str, Any]],
    path: str,
) -> None:
    available = Counter(
        token["text"]
        for token in tokens
        if re.fullmatch(r"\d{1,2}", token["text"]) is not None
    )
    claimed = Counter(number for group in numbers for number in group)
    excess = {
        number: count - available[number]
        for number, count in claimed.items()
        if count > available[number]
    }
    if excess:
        raise _v4_error(
            "v4_number_evidence_exceeds_tokens",
            path,
            f"grouped numbers exceed standalone numeric token evidence: {excess}",
        )


def _encode_v4_shared(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping) and frozenset(value) == TOKEN_OBJECT_KEYS:
        if not isinstance(value.get("text"), str) or not value["text"].strip():
            raise _v4_error(
                "v4_shared_text_invalid",
                "shared_multiplier.text",
                "shared text must be a non-empty string",
            )
        if not _valid_v4_pixel_bbox(value.get("bbox")):
            raise _v4_error(
                "v4_shared_bbox_invalid",
                "shared_multiplier.bbox",
                "pixel bbox must be finite, nonnegative, and have positive area",
            )
        return [value["text"], *copy.deepcopy(value["bbox"])]
    raise _v4_error(
        "v4_shared_not_lossless",
        "shared_multiplier",
        "shared must be null or exact [text,x1,y1,x2,y2] evidence",
    )


def _decode_v4_shared(value: Any, path: str) -> Any:
    if value is None:
        return None
    if (
        isinstance(value, list)
        and len(value) == 5
        and isinstance(value[0], str)
        and bool(value[0])
        and _valid_v4_pixel_bbox(value[1:])
    ):
        return {"text": value[0], "bbox": copy.deepcopy(value[1:])}
    raise _v4_error(
        "v4_shared_invalid",
        path,
        "shared must be null or [text,x1,y1,x2,y2]",
    )


def _valid_v4_pixel_bbox(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        return False
    x1, y1, x2, y2 = (float(item) for item in value)
    return x1 >= 0 and y1 >= 0 and x2 > x1 and y2 > y1


def _encode_shared_value(value: Any) -> list[Any]:
    if value is None:
        return [0]
    if isinstance(value, str):
        return [1, value]
    if isinstance(value, Mapping) and frozenset(value) == TOKEN_OBJECT_KEYS:
        issues = _audit_shared_multiplier(value, "shared_multiplier")
        if issues:
            raise ShadowContractValidationError(issues)
        return [2, value["text"], *copy.deepcopy(value["bbox"])]
    raise _v3_error(
        "v3_shared_value_not_lossless",
        "shared_multiplier",
        "shared value must be null, legacy string, or exact text+bbox object",
    )


def _decode_shared_value(value: Any, path: str) -> Any:
    if value == [0]:
        return None
    if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
        return value[1]
    if (
        isinstance(value, list)
        and len(value) == 6
        and value[0] == 2
        and isinstance(value[1], str)
        and _valid_bbox(value[2:])
    ):
        return {"text": value[1], "bbox": copy.deepcopy(value[2:])}
    raise _v3_error("v3_shared_value_invalid", path, "invalid shared multiplier tuple")


def _regenerate_and_verify_v2(
    legacy: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    regenerated = canonicalize_legacy_response(legacy)
    for section_index, (section, claim) in enumerate(zip(regenerated["sections"], claims)):
        path = f"$.sections[{section_index}]"
        section_meta = section[_V2_METADATA_KEY]
        if section["shared_multiplier"] != claim["shared_multiplier"]:
            raise _v3_error("v3_shared_claim_mismatch", path, "shared canonical evidence changed")
        if (
            section_meta["shared_multiplier_resolution"]["status"]
            != claim["shared_resolution_status"]
        ):
            raise _v3_error("v3_shared_status_mismatch", path, "shared status claim is not reproducible")
        if len(section["rows"]) != len(claim["rows"]):
            raise _v3_error("v3_row_count_mismatch", path, "row count changed")
        for row_index, (row, row_claim) in enumerate(zip(section["rows"], claim["rows"])):
            row_path = f"{path}.rows[{row_index}]"
            row_meta = row[_V2_METADATA_KEY]
            if row["layout_hint"] != row_claim["layout_hint"]:
                raise _v3_error("v3_layout_claim_mismatch", row_path, "layout claim is not reproducible")
            if row_meta["entry_kind"] != row_claim["entry_kind"]:
                raise _v3_error("v3_entry_claim_mismatch", row_path, "entry role claim is not reproducible")
            if row_meta["evidence_only"] != row_claim["evidence_only"]:
                raise _v3_error("v3_evidence_only_mismatch", row_path, "evidence safety claim changed")
            if "number_references" in row_claim:
                evidence = row_meta["layout_resolution"]["evidence"]
                regenerated_indexes = evidence["matched_number_token_indices"]
                expected_references: list[list[int]] = []
                cursor = 0
                for group in row["numbers"]:
                    expected_references.append(
                        copy.deepcopy(regenerated_indexes[cursor : cursor + len(group)])
                    )
                    cursor += len(group)
                if expected_references != row_claim["number_references"]:
                    raise _v3_error(
                        "v3_number_reference_identity_mismatch",
                        f"{row_path}.numbers",
                        "wire token-reference identity/order is not reproducible",
                    )
    validate_v2_response(regenerated)
    return regenerated


def _v3_error(code: str, path: str, message: str) -> ShadowContractValidationError:
    return ShadowContractValidationError([_issue(code, path, message)])


def _v4_error(code: str, path: str, message: str) -> ShadowContractValidationError:
    return ShadowContractValidationError([_issue(code, path, message)])


def _v41_error(code: str, path: str, message: str) -> ShadowContractValidationError:
    return ShadowContractValidationError([_issue(code, path, message)])


def _require_v41_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    path: str,
) -> None:
    actual = set(value)
    missing = required - actual
    extra = actual - required - optional
    if missing or extra:
        raise _v41_error(
            "v41_fields_not_lossless",
            path,
            f"missing={sorted(missing)} extra={sorted(extra)}",
        )


def _dense_error(code: str, path: str, message: str) -> ShadowContractValidationError:
    return ShadowContractValidationError([_issue(code, path, message)])


def _require_dense_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    path: str,
) -> None:
    actual = set(value)
    missing = required - actual
    extra = actual - required - optional
    if missing or extra:
        raise _dense_error(
            "dense_fields_invalid",
            path,
            f"missing={sorted(missing)} extra={sorted(extra)}",
        )


def _dense_union_bbox(boxes: list[list[Any]]) -> list[Any]:
    if not boxes or any(not _valid_v4_pixel_bbox(box) for box in boxes):
        raise _dense_error(
            "dense_row_bbox_unavailable",
            "$.row.b",
            "row bbox requires valid source token geometry",
        )
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _key_overhead_chars(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(
            len(json.dumps(str(key), ensure_ascii=False))
            + 1
            + _key_overhead_chars(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_key_overhead_chars(item) for item in value)
    return 0


def _value_strings(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [item for child in value.values() for item in _value_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _value_strings(child)]
    return [value] if isinstance(value, str) else []


def _json_punctuation_chars(serialized: str) -> int:
    in_string = False
    escaped = False
    count = 0
    for char in serialized:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{}[],:":
            count += 1
    return count


def _audit_row(value: Any, path: str) -> list[ContractIssue]:
    if not isinstance(value, Mapping):
        return [_issue("row_wrong_type", path, "row must be an object")]
    row = value
    issues: list[ContractIssue] = []

    tokens = row.get("tokens")
    if not isinstance(tokens, list):
        issues.append(_issue("tokens_wrong_type", f"{path}.tokens", "tokens must be a list"))
    elif not tokens:
        issues.append(_issue("tokens_empty", f"{path}.tokens", "tokens must not be empty"))
    else:
        for token_index, token in enumerate(tokens):
            issues.extend(_audit_token(token, f"{path}.tokens[{token_index}]"))

    numbers = row.get("numbers")
    numbers_path = f"{path}.numbers"
    if not isinstance(numbers, list):
        issues.append(_issue("numbers_wrong_type", numbers_path, "numbers must be a list of lists"))
    else:
        for group_index, group in enumerate(numbers):
            group_path = f"{numbers_path}[{group_index}]"
            if not isinstance(group, list):
                issues.append(
                    _issue("number_group_wrong_type", group_path, "number group must be a list")
                )
                continue
            for number_index, number in enumerate(group):
                if not isinstance(number, str):
                    issues.append(
                        _issue(
                            "number_value_wrong_type",
                            f"{group_path}[{number_index}]",
                            "number value must be a string",
                        )
                    )

    multiplier_path = f"{path}.multiplier"
    if "multiplier" not in row:
        issues.append(
            _issue("row_multiplier_missing", multiplier_path, "multiplier is required and may be null")
        )
    else:
        multiplier = row.get("multiplier")
        if multiplier is not None and not isinstance(multiplier, str):
            issues.append(
                _issue(
                    "row_multiplier_wrong_type",
                    multiplier_path,
                    "multiplier must be a string or null",
                )
            )

    layout_path = f"{path}.layout_hint"
    layout = row.get("layout_hint")
    if layout not in CANONICAL_LAYOUT_HINTS:
        issues.append(
            _issue(
                "layout_hint_invalid_enum",
                layout_path,
                "layout_hint must be normal_row or column_bet",
            )
        )

    if "collision" in row:
        collision = row.get("collision")
        if collision is not None and not isinstance(collision, str):
            issues.append(
                _issue(
                    "collision_wrong_type",
                    f"{path}.collision",
                    "collision must be a string or null",
                )
            )
    return issues


def _audit_token(value: Any, path: str) -> list[ContractIssue]:
    if not isinstance(value, Mapping):
        return [_issue("token_wrong_type", path, "token must be an object")]
    token = value
    issues: list[ContractIssue] = []
    if not isinstance(token.get("text"), str):
        issues.append(
            _issue("token_text_wrong_type", f"{path}.text", "token text must be a string")
        )
    if "bbox" not in token:
        issues.append(_issue("token_bbox_missing", f"{path}.bbox", "token bbox is required"))
    elif not _valid_bbox(token.get("bbox")):
        issues.append(
            _issue(
                "token_bbox_invalid",
                f"{path}.bbox",
                "bbox must contain four finite ordered numbers",
            )
        )
    return issues


def _audit_shared_multiplier(value: Any, path: str) -> list[ContractIssue]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return [
            _issue(
                "shared_multiplier_wrong_type",
                path,
                "shared_multiplier must be an object with text and bbox, or null",
            )
        ]
    issues: list[ContractIssue] = []
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        issues.append(
            _issue(
                "shared_multiplier_text_wrong_type",
                f"{path}.text",
                "shared multiplier text must be a non-empty string",
            )
        )
    if "bbox" not in value:
        issues.append(
            _issue("shared_multiplier_bbox_missing", f"{path}.bbox", "bbox is required")
        )
    elif not _valid_bbox(value.get("bbox")):
        issues.append(
            _issue(
                "shared_multiplier_bbox_invalid",
                f"{path}.bbox",
                "bbox must contain four finite ordered numbers",
            )
        )
    return issues


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        return False
    x1, y1, x2, y2 = (float(item) for item in value)
    return x1 <= x2 and y1 <= y2


def _token_slots(value: dict[str, Any]):
    sections = value.get("sections")
    if not isinstance(sections, list):
        raise ShadowContractValidationError(
            [_issue("sections_wrong_type", "$.sections", "sections must be a list")]
        )
    for section_index, section in enumerate(sections):
        rows = section.get("rows") if isinstance(section, Mapping) else None
        if not isinstance(rows, list):
            raise ShadowContractValidationError(
                [
                    _issue(
                        "rows_wrong_type",
                        f"$.sections[{section_index}].rows",
                        "rows must be a list",
                    )
                ]
            )
        for row_index, row in enumerate(rows):
            tokens = row.get("tokens") if isinstance(row, Mapping) else None
            if not isinstance(tokens, list):
                raise ShadowContractValidationError(
                    [
                        _issue(
                            "tokens_wrong_type",
                            f"$.sections[{section_index}].rows[{row_index}].tokens",
                            "tokens must be a list",
                        )
                    ]
                )
            for token_index, token in enumerate(list(tokens)):
                path = f"$.sections[{section_index}].rows[{row_index}].tokens[{token_index}]"

                def replace(replacement: Any, *, target=tokens, index=token_index) -> None:
                    target[index] = replacement

                yield token, path, replace


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code=code, path=path, message=message)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_file", type=Path)
    args = parser.parse_args()
    response = load_cache_response(args.cache_file)
    issues = audit_full_page_response(response)
    print(json.dumps({"valid": not issues, "issues": [i.to_dict() for i in issues]}, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(_main())
