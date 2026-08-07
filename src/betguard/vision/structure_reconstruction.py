"""Deterministic, review-only structure evidence for Qwen recognition.

This module never reads an image and never calls a recognition provider.  It
uses only the immutable token text and bounding boxes already present in a
``RecognitionResult``.  Its output is evidence for human review, not a parsed
or executable betting candidate.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Mapping

from betguard.vision.closed_set import validate_number_token
from betguard.vision.column_geometry import (
    build_columns_from_bbox,
    build_grid_from_rows,
)
from betguard.vision.contracts import RecognitionResult
from betguard.vision.deterministic_checks import combination_count
from betguard.vision.multiplier_policy import (
    COMPLETE,
    PARTIAL,
    canonical_category,
    classify_multiplier_token,
    merge_complete_rules,
    normalize_rule,
    partial_tokens,
    split_complete_rules,
)


SOURCE = "deterministic_geometry_v1"
_LAYOUT_ALIASES = {
    "normal": "normal_row",
    "normal_row": "normal_row",
    "column": "column_bet",
    "column_bet": "column_bet",
}
_SEPARATOR_RE = re.compile(r"[xX×]")
_COLLISION_RE = re.compile(r"234|[234](?:[/\.][234])*")
_PLAY_CATEGORY_RE = re.compile(r"(?:23|24|34|234|[234](?:[/\.][234])*)")
_TWO_DIGIT_RE = re.compile(r"\d{2}")
_NUMBER_LIKE_RE = re.compile(r"[0-9?]+")
_MULTIPLIER_VALUE_RE = re.compile(r"[xX×]\d+(?:\.\d+)?")
_ALLOWED_COORDINATE_SPACES = frozenset({"pixel", "normalized"})
_PLAY_ZONE_RATIO = 0.68
_ZONE_RULE = "bbox_center_x_gte_play_zone_boundary_x"


def reconstruct_structure(
    result: RecognitionResult | Mapping[str, Any],
    *,
    game: str,
) -> list[dict[str, Any]]:
    """Return section-scoped structure evidence linked by exact line IDs.

    ``game`` controls only the closed number range.  It does not select a
    provider, alter model text, or trigger any follow-up recognition.
    """
    result_dict = result.to_dict() if isinstance(result, RecognitionResult) else result
    if not isinstance(result_dict, Mapping):
        return []

    qwen_response = _mapping(result_dict.get("preprocessing")).get("qwen_response")
    model_sections = _model_sections(qwen_response)
    model_by_line_id = _model_evidence_by_line_id(qwen_response)
    raw_lines = result_dict.get("lines")
    lines = raw_lines if isinstance(raw_lines, list) else []
    source_image = _mapping(result_dict.get("source_image"))
    image_width = _positive_float(source_image.get("width"))
    line_id_counts = Counter(
        str(line.get("line_id") or "")
        for line in lines
        if isinstance(line, Mapping)
    )

    line_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_line in lines:
        line = raw_line if isinstance(raw_line, Mapping) else {}
        line_id = str(line.get("line_id") or "")
        if line_id and line_id not in line_by_id:
            line_by_id[line_id] = line

    output: list[dict[str, Any]] = [
        _line_id_missing_evidence(line, game=game, image_width=image_width)
        for line in lines
        if isinstance(line, Mapping) and not str(line.get("line_id") or "")
    ]
    handled_line_ids: set[str] = set()
    for section in model_sections:
        member_line_ids = section["member_line_ids"]
        present_line_ids = [line_id for line_id in member_line_ids if line_id in line_by_id]
        if not present_line_ids:
            continue
        primary_line_id = section["primary_line_id"]
        if primary_line_id not in line_by_id:
            for line_id in present_line_ids:
                output.append(_continuation_evidence(
                    line_by_id[line_id],
                    structure_id=section["structure_id"],
                    primary_line_id=primary_line_id,
                    member_line_ids=member_line_ids,
                    game=game,
                    warnings=["primary_line_id_missing", "line_id_evidence_missing"],
                ))
                handled_line_ids.add(line_id)
            continue

        if len(member_line_ids) > 1:
            combined_line = _combined_section_line(
                primary_line_id,
                member_line_ids,
                line_by_id,
            )
            aggregated_model = _aggregate_section_model_evidence(section)
            missing = [line_id for line_id in member_line_ids if line_id not in line_by_id]
            primary = _reconstruct_line(
                combined_line,
                model_by_line_id={primary_line_id: aggregated_model},
                duplicate_line_id=any(line_id_counts[line_id] > 1 for line_id in member_line_ids),
                game=game,
                image_width=image_width,
                force_column=True,
                extra_blocking_warnings=[
                    f"member_line_missing:{line_id}" for line_id in missing
                ],
            )
            _add_structure_identity(
                primary,
                structure_id=section["structure_id"],
                primary_line_id=primary_line_id,
                member_line_ids=member_line_ids,
                line_role="primary",
                game=game,
            )
            output.append(primary)
            handled_line_ids.add(primary_line_id)
            for line_id in member_line_ids[1:]:
                if line_id not in line_by_id:
                    continue
                output.append(_continuation_evidence(
                    line_by_id[line_id],
                    structure_id=section["structure_id"],
                    primary_line_id=primary_line_id,
                    member_line_ids=member_line_ids,
                    game=game,
                ))
                handled_line_ids.add(line_id)
            continue

        line = line_by_id[primary_line_id]
        evidence = _reconstruct_line(
            line,
            model_by_line_id=model_by_line_id,
            duplicate_line_id=line_id_counts[primary_line_id] > 1,
            game=game,
            image_width=image_width,
        )
        _add_structure_identity(
            evidence,
            structure_id=section["structure_id"],
            primary_line_id=primary_line_id,
            member_line_ids=member_line_ids,
            line_role="primary",
            game=game,
        )
        output.append(evidence)
        handled_line_ids.add(primary_line_id)

    for line_id in sorted(line_by_id):
        if line_id in handled_line_ids:
            continue
        evidence = _reconstruct_line(
            line_by_id[line_id],
            model_by_line_id=model_by_line_id,
            duplicate_line_id=line_id_counts[line_id] > 1,
            game=game,
            image_width=image_width,
        )
        _add_structure_identity(
            evidence,
            structure_id=line_id,
            primary_line_id=line_id,
            member_line_ids=[line_id],
            line_role="primary",
            game=game,
        )
        output.append(evidence)
    return output


def _reconstruct_line(
    line: Mapping[str, Any],
    *,
    model_by_line_id: Mapping[str, dict[str, Any]],
    duplicate_line_id: bool,
    game: str,
    image_width: float | None,
    force_column: bool = False,
    extra_blocking_warnings: list[str] | None = None,
) -> dict[str, Any]:
    line_id = str(line.get("line_id") or "")
    warnings: list[str] = []
    blocking: set[str] = set()
    unsupported = False

    if game not in {"539", "六合"}:
        _warn(warnings, blocking, "game_invalid")
    for warning in extra_blocking_warnings or []:
        _warn(warnings, blocking, warning)

    model_evidence = model_by_line_id.get(line_id) if line_id else None
    if model_evidence is None:
        _warn(warnings, blocking, "line_id_evidence_missing")
    if duplicate_line_id:
        _warn(warnings, blocking, "duplicate_line_id")

    model_candidate, model_compare, model_complete, model_unsupported = (
        _model_candidate(model_evidence, game=game)
    )
    unsupported = unsupported or model_unsupported
    if model_evidence is not None and not model_complete and not model_unsupported:
        _warn(warnings, blocking, "model_structure_incomplete")
    elif model_unsupported:
        warnings.append("model_layout_unsupported")

    raw_tokens = line.get("tokens")
    tokens = raw_tokens if isinstance(raw_tokens, list) else []
    token_evidence: list[dict[str, Any]] = []
    geometry_tokens: list[dict[str, Any]] = []
    number_tokens: list[dict[str, Any]] = []
    separator_tokens: list[dict[str, Any]] = []
    collision_tokens: list[dict[str, Any]] = []
    play_category_tokens: list[dict[str, Any]] = []
    complete_multiplier_texts: list[str] = []
    partial_multiplier_records: list[dict[str, Any]] = []
    invalid_multiplier_texts: list[str] = []
    coordinate_spaces: set[str] = set()

    for index, raw_token in enumerate(tokens, start=1):
        token = raw_token if isinstance(raw_token, Mapping) else {}
        token_id = str(token.get("token_id") or f"{line_id}-T{index:02d}")
        text = str(token.get("text") or "")
        source_line_id = str(token.get("_source_line_id") or line_id)
        bbox, coordinate_space, bbox_issue = _bbox_xyxy(token.get("bounding_box"))
        zone = _bbox_zone(
            bbox,
            coordinate_space=coordinate_space,
            image_width=image_width,
        )
        if coordinate_space:
            coordinate_spaces.add(coordinate_space)

        classification = "unknown"
        multiplier_classification: str | None = None
        number_valid = False

        if _TWO_DIGIT_RE.fullmatch(text):
            if zone == "play" and _PLAY_CATEGORY_RE.fullmatch(text):
                classification = "play_category_evidence"
                multiplier_classification = classify_multiplier_token(text)
            elif zone == "play":
                classification = "number_in_play_area_evidence"
                _warn(warnings, blocking, f"number_token_in_play_area:{token_id}")
            elif _number_is_valid(text, game=game):
                classification = "number"
                number_valid = True
            else:
                classification = "invalid_number"
                _warn(warnings, blocking, f"number_out_of_range:{token_id}")
        elif _SEPARATOR_RE.fullmatch(text):
            classification = "column_separator"
        elif _COLLISION_RE.fullmatch(text):
            classification = "collision_or_partial_multiplier_evidence"
            multiplier_classification = classify_multiplier_token(text)
        else:
            split_rules = split_complete_rules(text)
            text_partials = partial_tokens(text)
            multiplier_classification = classify_multiplier_token(text)
            if split_rules and not text_partials:
                classification = "complete_multiplier"
                complete_multiplier_texts.extend(split_rules)
            elif _looks_like_multiplier(text):
                classification = (
                    "partial_multiplier_evidence"
                    if multiplier_classification == PARTIAL
                    else "invalid_multiplier_evidence"
                )
                if multiplier_classification == PARTIAL:
                    partial_multiplier_records.append({
                        "token_id": token_id,
                        "text": text,
                        "bbox": bbox,
                        "coordinate_space": coordinate_space,
                        "source_line_id": source_line_id,
                        "zone": zone,
                    })
                else:
                    invalid_multiplier_texts.extend(text_partials or [text])
            elif _NUMBER_LIKE_RE.fullmatch(text):
                classification = "partial_number_evidence"
                _warn(warnings, blocking, f"partial_number_token:{token_id}")
            elif _is_supported_elsewhere(text):
                classification = "unsupported_structure_token"
                unsupported = True
                warnings.append(f"unsupported_structure_token:{token_id}")
            else:
                classification = "unknown"
                _warn(warnings, blocking, f"unknown_token:{token_id}")

        if bbox_issue is not None:
            _warn(warnings, blocking, bbox_issue)
            warnings.append(f"{bbox_issue}:{token_id}")

        evidence_item: dict[str, Any] = {
            "token_id": token_id,
            "text": text,
            "classification": classification,
            "coordinate_space": coordinate_space,
            "bbox": bbox,
            "zone": zone,
            "source_line_id": source_line_id,
        }
        if multiplier_classification is not None:
            evidence_item["multiplier_classification"] = multiplier_classification
        token_evidence.append(evidence_item)

        if bbox is None:
            continue
        geometry_token = {
            "text": text,
            "bbox": bbox,
            "token_id": token_id,
            "coordinate_space": coordinate_space,
            "source_line_id": source_line_id,
            "zone": zone,
        }
        if number_valid:
            number_tokens.append(geometry_token)
            geometry_tokens.append(geometry_token)
        elif classification == "column_separator":
            separator_tokens.append(geometry_token)
            geometry_tokens.append(geometry_token)
        elif classification == "collision_or_partial_multiplier_evidence":
            collision_tokens.append(geometry_token)
            geometry_tokens.append(geometry_token)
        elif classification == "play_category_evidence":
            play_category_tokens.append(geometry_token)
            partial_multiplier_records.append(geometry_token)

    if len(coordinate_spaces) > 1:
        _warn(warnings, blocking, "coordinate_space_mismatch")
    if not tokens:
        _warn(warnings, blocking, "line_tokens_missing")

    if _has_bbox_overlap(number_tokens):
        _warn(warnings, blocking, "number_bbox_overlap")

    grid = build_grid_from_rows(geometry_tokens)
    bbox_columns = build_columns_from_bbox(geometry_tokens)
    grid_groups = _columns_as_groups(grid)
    bbox_groups = _columns_as_groups(bbox_columns)
    row_count = len(_mapping(grid.get("_debug")).get("rows") or [])
    separator_between_numbers = _separator_between_numbers(
        separator_tokens,
        number_tokens,
    )
    model_layout = model_compare.get("layout")
    column_intent = force_column or row_count >= 2 or (
        len(number_tokens) >= 2 and separator_between_numbers
    )

    layout = "unknown"
    number_groups: list[list[str]] = []
    collision: str | None = None
    reconstruction_complete = True

    if column_intent:
        layout = "column_bet"
        number_groups = grid_groups
        collision_value = grid.get("collision_raw")
        collision = str(collision_value) if collision_value else None
        if len(number_groups) < 2:
            _warn(warnings, blocking, "column_count_insufficient")
        if any(not group for group in number_groups):
            _warn(warnings, blocking, "empty_column")
        if any(len(group) != len(set(group)) for group in number_groups):
            _warn(warnings, blocking, "duplicate_number_in_column")
        if grid_groups != bbox_groups:
            _warn(warnings, blocking, "column_geometry_disagreement")
        if len(number_tokens) == 2 and not separator_between_numbers:
            _warn(warnings, blocking, "column_separator_missing")
    elif number_tokens:
        if model_layout == "column_bet" and len(number_tokens) == 2:
            _warn(warnings, blocking, "column_separator_missing")
            reconstruction_complete = False
        else:
            layout = "normal_row"
            ordered = sorted(
                number_tokens,
                key=lambda token: (_center(token["bbox"])[0], _center(token["bbox"])[1]),
            )
            number_groups = [[str(token["text"]) for token in ordered]]
    elif unsupported:
        reconstruction_complete = False
    else:
        reconstruction_complete = False
        _warn(warnings, blocking, "number_structure_missing")

    if _number_in_play_area(number_tokens, collision_tokens, complete_multiplier_texts, token_evidence):
        _warn(warnings, blocking, "number_token_in_play_area")

    stacked_categories, stacked_category_issues = _stacked_collision_categories(
        collision_tokens,
    )
    for issue in stacked_category_issues:
        _warn(warnings, blocking, issue)

    derived_play_rules, resolved_partial_token_ids, play_rule_issues = _complete_play_rules(
        play_category_tokens + stacked_categories,
        partial_multiplier_records,
    )
    for issue in play_rule_issues:
        _warn(warnings, blocking, issue)
    if derived_play_rules:
        complete_multiplier_texts.extend(derived_play_rules)
        warnings.append("play_rule_reconstructed:" + "/".join(derived_play_rules))

    complete_rules = merge_complete_rules(complete_multiplier_texts)
    # Re-run the public splitter over the canonical inputs so only an entirely
    # complete set can enter the reconstructed field.
    if complete_rules:
        complete_rules = merge_complete_rules(
            split_complete_rules(" ".join(complete_rules))
        )

    collision_texts = {str(token["text"]) for token in collision_tokens}
    model_row = _mapping(model_evidence.get("row")) if model_evidence else {}
    unresolved_partial = [
        token for token in partial_multiplier_records
        if token["token_id"] not in resolved_partial_token_ids
        and str(token["text"]) not in collision_texts
    ]
    if unresolved_partial:
        _warn(warnings, blocking, "partial_multiplier_evidence")
    if invalid_multiplier_texts:
        _warn(warnings, blocking, "invalid_multiplier_evidence")
    if collision_texts:
        warnings.append("collision_play_evidence:" + "/".join(sorted(collision_texts)))
        if layout != "column_bet":
            _warn(warnings, blocking, "collision_multiplier_scope_ambiguous")
        elif "collision" not in model_row:
            _warn(warnings, blocking, "model_collision_evidence_missing")

    shared_multiplier: Any = None
    if model_candidate["shared_multiplier"] is not None:
        _warn(warnings, blocking, "shared_multiplier_scope_unresolved")

    if blocking:
        reconstruction_complete = False

    reconstructed_candidate = {
        "number_groups": number_groups,
        "multiplier_rules": complete_rules,
        "layout": layout,
        "collision": collision,
        "shared_multiplier": shared_multiplier,
    }

    reconstruction_compare = {
        "number_groups": number_groups,
        "column_count": len(number_groups) if layout == "column_bet" else 1 if number_groups else 0,
        "layout": layout,
        "multiplier_rules": complete_rules,
        "collision": collision,
        "shared_multiplier": shared_multiplier,
    }

    if blocking:
        status = "incomplete"
    elif unsupported or model_unsupported:
        status = "unsupported"
    elif not model_complete or not reconstruction_complete:
        status = "incomplete"
    elif model_compare == reconstruction_compare:
        status = "consistent"
    else:
        status = "divergent"

    bbox_debug = {
        "coordinate_spaces": sorted(coordinate_spaces),
        "zone_rule": _ZONE_RULE,
        "play_zone_ratio": _PLAY_ZONE_RATIO,
        "play_zone_boundary_x": _play_zone_boundary_x(
            coordinate_spaces,
            image_width=image_width,
        ),
        "image_width": image_width,
        "stacked_collision_categories": stacked_categories,
        "row_first": grid,
        "x_clustered": bbox_columns,
        "separator_between_numbers": separator_between_numbers,
        "combination_count": combination_count(
            [[int(value) for value in group] for group in number_groups]
        ) if number_groups else 0,
    }

    return {
        "line_id": line_id,
        "source": SOURCE,
        "model_candidate": model_candidate,
        "reconstructed_candidate": reconstructed_candidate,
        "status": status,
        "warnings": _unique(warnings),
        "evidence": {
            "tokens": token_evidence,
            "bbox_debug": bbox_debug,
            "rules_used": [
                "validate_number_token",
                "build_grid_from_rows",
                "build_columns_from_bbox",
                "classify_multiplier_token",
                "split_complete_rules",
                "merge_complete_rules",
                "partial_tokens",
                "combination_count",
            ],
        },
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _model_sections(value: Any) -> list[dict[str, Any]]:
    response = _mapping(value)
    sections = response.get("sections")
    if not isinstance(sections, list):
        return []
    output: list[dict[str, Any]] = []
    for section_index, raw_section in enumerate(sections, start=1):
        section = _mapping(raw_section)
        rows = section.get("rows")
        if not isinstance(rows, list) or not rows:
            continue
        structure_id = f"S{section_index:02d}"
        member_line_ids = [
            f"{structure_id}-L{row_index:02d}"
            for row_index in range(1, len(rows) + 1)
        ]
        output.append({
            "structure_id": structure_id,
            "primary_line_id": member_line_ids[0],
            "member_line_ids": member_line_ids,
            "rows": [_mapping(row) for row in rows],
            "shared_multiplier": section.get("shared_multiplier"),
        })
    return output


def _model_evidence_by_line_id(value: Any) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for section in _model_sections(value):
        for line_id, row in zip(section["member_line_ids"], section["rows"]):
            evidence[line_id] = {
                "row": row,
                "shared_multiplier": section["shared_multiplier"],
            }
    return evidence


def _aggregate_section_model_evidence(
    section: Mapping[str, Any],
) -> dict[str, Any]:
    rows = section.get("rows")
    model_rows = rows if isinstance(rows, list) else []
    row_cells: list[list[list[str]]] = []
    multiplier_parts: list[str] = []
    collision: Any = None

    for raw_row in model_rows:
        row = _mapping(raw_row)
        raw_numbers = row.get("numbers")
        groups = raw_numbers if isinstance(raw_numbers, list) else []
        cells: list[list[str]] = []
        if len(groups) == 1 and isinstance(groups[0], list) and len(groups[0]) > 1:
            cells = [[str(value)] for value in groups[0]]
        else:
            for raw_group in groups:
                cells.append(
                    [str(value) for value in raw_group]
                    if isinstance(raw_group, list)
                    else []
                )
        row_cells.append(cells)
        multiplier = row.get("multiplier")
        if isinstance(multiplier, str) and multiplier.strip():
            multiplier_parts.append(multiplier)
        if collision is None and row.get("collision") is not None:
            collision = row.get("collision")

    column_count = max((len(cells) for cells in row_cells), default=0)
    columns: list[list[str]] = [[] for _ in range(column_count)]
    for cells in row_cells:
        offset = column_count - len(cells)
        for index, cell in enumerate(cells):
            columns[offset + index].extend(cell)

    row: dict[str, Any] = {
        "numbers": columns,
        "multiplier": " ".join(multiplier_parts) if multiplier_parts else None,
        "layout_hint": "column_bet",
    }
    if collision is not None:
        row["collision"] = collision
    return {
        "row": row,
        "shared_multiplier": section.get("shared_multiplier"),
    }


def _combined_section_line(
    primary_line_id: str,
    member_line_ids: list[str],
    line_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    member_lines = [line_by_id[line_id] for line_id in member_line_ids if line_id in line_by_id]
    tokens: list[Any] = []
    for line in member_lines:
        raw_tokens = line.get("tokens")
        if isinstance(raw_tokens, list):
            source_line_id = str(line.get("line_id") or "")
            for raw_token in raw_tokens:
                token = dict(_mapping(raw_token))
                token["_source_line_id"] = source_line_id
                tokens.append(token)
    return {
        "line_id": primary_line_id,
        "text": "\n".join(str(line.get("text") or "") for line in member_lines),
        "tokens": tokens,
    }


def _add_structure_identity(
    record: dict[str, Any],
    *,
    structure_id: str,
    primary_line_id: str,
    member_line_ids: list[str],
    line_role: str,
    game: str,
) -> None:
    record.update({
        "structure_id": structure_id,
        "primary_line_id": primary_line_id,
        "member_line_ids": list(member_line_ids),
        "line_role": line_role,
        "game": game,
    })


def _continuation_evidence(
    line: Mapping[str, Any],
    *,
    structure_id: str,
    primary_line_id: str,
    member_line_ids: list[str],
    game: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    raw_tokens = line.get("tokens")
    tokens = raw_tokens if isinstance(raw_tokens, list) else []
    return {
        "line_id": str(line.get("line_id") or ""),
        "structure_id": structure_id,
        "primary_line_id": primary_line_id,
        "member_line_ids": list(member_line_ids),
        "line_role": "continuation",
        "source": SOURCE,
        "game": game,
        "status": "incomplete",
        "warnings": list(warnings or [f"continuation_of:{primary_line_id}"]),
        "evidence": {
            "tokens": [
                {
                    "token_id": str(_mapping(token).get("token_id") or ""),
                    "text": str(_mapping(token).get("text") or ""),
                    "bounding_box": _mapping(token).get("bounding_box"),
                }
                for token in tokens
            ],
            "bbox_debug": {},
            "rules_used": [],
        },
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _line_id_missing_evidence(
    line: Mapping[str, Any],
    *,
    game: str,
    image_width: float | None,
) -> dict[str, Any]:
    raw_tokens = line.get("tokens")
    tokens = raw_tokens if isinstance(raw_tokens, list) else []
    warnings = ["line_id_evidence_missing"]
    if game not in {"539", "六合"}:
        warnings.append("game_invalid")
    return {
        "line_id": "",
        "structure_id": None,
        "primary_line_id": None,
        "member_line_ids": [],
        "line_role": "unlinked",
        "source": SOURCE,
        "game": game,
        "status": "incomplete",
        "warnings": warnings,
        "evidence": {
            "tokens": [
                {
                    "token_id": str(_mapping(token).get("token_id") or ""),
                    "text": str(_mapping(token).get("text") or ""),
                    "bounding_box": _mapping(token).get("bounding_box"),
                }
                for token in tokens
            ],
            "bbox_debug": {
                "coordinate_spaces": [],
                "zone_rule": _ZONE_RULE,
                "play_zone_ratio": _PLAY_ZONE_RATIO,
                "play_zone_boundary_x": (
                    image_width * _PLAY_ZONE_RATIO if image_width is not None else None
                ),
                "image_width": image_width,
            },
            "rules_used": [],
        },
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _model_candidate(
    evidence: Mapping[str, Any] | None,
    *,
    game: str,
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    row = _mapping(evidence.get("row")) if evidence else {}
    shared = evidence.get("shared_multiplier") if evidence else None
    raw_numbers = row.get("numbers")
    numbers: list[list[str]] = []
    complete = evidence is not None
    unsupported = False

    if not isinstance(raw_numbers, list) or not raw_numbers:
        complete = False
    else:
        for raw_group in raw_numbers:
            if not isinstance(raw_group, list) or not raw_group:
                complete = False
                continue
            group: list[str] = []
            for raw_number in raw_group:
                number = raw_number if isinstance(raw_number, str) else ""
                group.append(number)
                if not _TWO_DIGIT_RE.fullmatch(number) or not _number_is_valid(number, game=game):
                    complete = False
            numbers.append(group)

    raw_layout = row.get("layout_hint") if evidence else None
    layout = _LAYOUT_ALIASES.get(str(raw_layout or "").strip().lower())
    if layout is None:
        complete = False
        unsupported = evidence is not None and raw_layout is not None
    if layout == "normal_row" and len(numbers) != 1:
        complete = False
    if layout == "column_bet" and (len(numbers) < 2 or any(not group for group in numbers)):
        complete = False

    raw_multiplier = row.get("multiplier") if evidence else None
    multiplier_rules: list[str] = []
    if raw_multiplier is not None:
        if not isinstance(raw_multiplier, str):
            complete = False
        elif raw_multiplier.strip():
            multiplier_rules = split_complete_rules(raw_multiplier)
            if not multiplier_rules or partial_tokens(raw_multiplier):
                complete = False

    if shared is not None:
        complete = False

    collision_present = "collision" in row
    raw_collision = row.get("collision") if collision_present else None
    if raw_collision is not None and not isinstance(raw_collision, str):
        complete = False

    candidate = {
        "numbers": numbers,
        "multiplier": raw_multiplier,
        "layout_hint": raw_layout,
        "shared_multiplier": shared,
    }
    compare = {
        "number_groups": numbers,
        "column_count": len(numbers) if layout == "column_bet" else 1 if numbers else 0,
        "layout": layout or "unknown",
        "multiplier_rules": merge_complete_rules(multiplier_rules),
        "collision": _canonical_collision(raw_collision),
        "shared_multiplier": shared,
    }
    return candidate, compare, complete, unsupported


def _bbox_xyxy(value: Any) -> tuple[list[float] | None, str | None, str | None]:
    bbox = _mapping(value)
    coordinate_space = bbox.get("coordinate_space")
    if not isinstance(coordinate_space, str) or not coordinate_space.strip():
        return None, None, "bbox_coordinate_space_invalid"
    coordinate_space = coordinate_space.strip()
    if coordinate_space not in _ALLOWED_COORDINATE_SPACES:
        return None, coordinate_space, "bbox_coordinate_space_unsupported"
    polygon = bbox.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 4:
        return None, coordinate_space, "bbox_polygon_invalid"
    points: list[tuple[float, float]] = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None, coordinate_space, "bbox_polygon_invalid"
        x, y = point
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            return None, coordinate_space, "bbox_coordinate_invalid"
        points.append((float(x), float(y)))
    if coordinate_space == "normalized" and any(
        coordinate < 0.0 or coordinate > 1.0
        for point in points
        for coordinate in point
    ):
        return None, coordinate_space, "bbox_normalized_out_of_range"
    x1 = min(point[0] for point in points)
    y1 = min(point[1] for point in points)
    x2 = max(point[0] for point in points)
    y2 = max(point[1] for point in points)
    if x2 <= x1 or y2 <= y1:
        return None, coordinate_space, "bbox_extents_invalid"
    return [x1, y1, x2, y2], coordinate_space, None


def _bbox_zone(
    bbox: list[float] | None,
    *,
    coordinate_space: str | None,
    image_width: float | None,
) -> str:
    if bbox is None:
        return "unknown"
    center_x = _center(bbox)[0]
    if coordinate_space == "normalized":
        return "play" if center_x >= _PLAY_ZONE_RATIO else "main"
    if image_width is not None:
        return "play" if center_x >= image_width * _PLAY_ZONE_RATIO else "main"
    return "unknown"


def _complete_play_rules(
    categories: list[dict[str, Any]],
    partials: list[dict[str, Any]],
) -> tuple[list[str], set[str], list[str]]:
    rules: list[str] = []
    resolved: set[str] = set()
    issues: list[str] = []
    value_tokens = [
        token for token in partials
        if _MULTIPLIER_VALUE_RE.fullmatch(str(token.get("text") or ""))
        and token.get("bbox") is not None
    ]
    category_matches: list[tuple[dict[str, Any], list[tuple[float, dict[str, Any]]]]] = []
    for category in categories:
        category_bbox = category.get("bbox")
        if category_bbox is None:
            continue
        nearby: list[tuple[float, dict[str, Any]]] = []
        for value in value_tokens:
            if _value_matches_category(category, value):
                horizontal_gap = value["bbox"][0] - category_bbox[2]
                nearby.append((abs(horizontal_gap), value))
        category_matches.append((category, nearby))

    value_match_counts = Counter(
        str(value.get("token_id") or "")
        for _, nearby in category_matches
        for _, value in nearby
    )
    for category, nearby in category_matches:
        if not nearby:
            if category.get("geometry") == "stacked_bbox":
                issues.append(
                    f"stacked_collision_value_missing:{category.get('token_id') or ''}"
                )
            continue
        value = nearby[0][1]
        if (
            len(nearby) != 1
            or value_match_counts[str(value.get("token_id") or "")] != 1
        ):
            issues.append(
                f"collision_multiplier_value_ambiguous:{category.get('token_id') or ''}"
            )
            continue
        category_text = canonical_category(str(category.get("text") or ""))
        value_text = normalize_rule(str(value.get("text") or ""))
        rule = f"{category_text}{value_text}"
        complete = split_complete_rules(rule)
        if not complete:
            continue
        rules.extend(complete)
        member_token_ids = category.get("member_token_ids")
        if isinstance(member_token_ids, list):
            resolved.update(str(token_id) for token_id in member_token_ids)
        else:
            resolved.add(str(category["token_id"]))
        resolved.add(str(value["token_id"]))
    return merge_complete_rules(rules), resolved, issues


def _stacked_collision_categories(
    collision_tokens: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    single_digits = [
        token for token in collision_tokens
        if re.fullmatch(r"[234]", str(token.get("text") or ""))
        and token.get("bbox") is not None
    ]
    clusters: list[list[dict[str, Any]]] = []
    for token in sorted(single_digits, key=lambda item: (_center(item["bbox"])[0], _center(item["bbox"])[1])):
        for cluster in clusters:
            if all(_horizontally_stacked(token, member) for member in cluster):
                cluster.append(token)
                break
        else:
            clusters.append([token])

    categories: list[dict[str, Any]] = []
    issues: list[str] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        token_ids = [str(token.get("token_id") or "") for token in cluster]
        category_id = "stacked:" + "+".join(token_ids)
        if len(cluster) > 3 or len({str(token.get("text") or "") for token in cluster}) != len(cluster):
            issues.append(f"stacked_collision_geometry_ambiguous:{category_id}")
            continue
        ordered = sorted(cluster, key=lambda token: _center(token["bbox"])[1])
        if not all(
            _vertically_stacked(first, second)
            for first, second in zip(ordered, ordered[1:])
        ):
            issues.append(f"stacked_collision_geometry_ambiguous:{category_id}")
            continue
        coordinate_spaces = {str(token.get("coordinate_space") or "") for token in cluster}
        if len(coordinate_spaces) != 1:
            issues.append(f"stacked_collision_geometry_ambiguous:{category_id}")
            continue
        bboxes = [token["bbox"] for token in ordered]
        category_text = canonical_category("/".join(str(token["text"]) for token in ordered))
        if not category_text:
            issues.append(f"stacked_collision_geometry_ambiguous:{category_id}")
            continue
        categories.append({
            "token_id": category_id,
            "text": category_text,
            "bbox": [
                min(bbox[0] for bbox in bboxes),
                min(bbox[1] for bbox in bboxes),
                max(bbox[2] for bbox in bboxes),
                max(bbox[3] for bbox in bboxes),
            ],
            "coordinate_space": next(iter(coordinate_spaces)),
            "source_line_ids": _unique([
                str(token.get("source_line_id") or "") for token in ordered
            ]),
            "member_token_ids": token_ids,
            "member_bboxes": bboxes,
            "reference_height": max(bbox[3] - bbox[1] for bbox in bboxes),
            "geometry": "stacked_bbox",
        })
    return categories, issues


def _value_matches_category(
    category: Mapping[str, Any],
    value: Mapping[str, Any],
) -> bool:
    category_bbox = category.get("bbox")
    value_bbox = value.get("bbox")
    if not isinstance(category_bbox, list) or not isinstance(value_bbox, list):
        return False
    category_space = str(category.get("coordinate_space") or "")
    value_space = str(value.get("coordinate_space") or "")
    if category_space and value_space and category_space != value_space:
        return False

    source_line_ids = category.get("source_line_ids")
    value_line_id = str(value.get("source_line_id") or "")
    if (
        isinstance(source_line_ids, list)
        and source_line_ids
        and value_line_id
        and value_line_id not in source_line_ids
    ):
        return False

    category_center = _center(category_bbox)
    value_center = _center(value_bbox)
    category_height = float(category.get("reference_height") or (
        category_bbox[3] - category_bbox[1]
    ))
    value_height = value_bbox[3] - value_bbox[1]
    geometry_unit = max(category_height, value_height)
    if geometry_unit <= 0:
        return False
    horizontal_gap = value_bbox[0] - category_bbox[2]
    if not (
        value_center[0] > category_center[0]
        and -geometry_unit <= horizontal_gap <= geometry_unit * 4
    ):
        return False

    member_bboxes = category.get("member_bboxes")
    if isinstance(member_bboxes, list) and member_bboxes:
        return min(
            abs(value_center[1] - _center(member_bbox)[1])
            for member_bbox in member_bboxes
            if isinstance(member_bbox, list)
        ) <= geometry_unit * 1.5
    return abs(value_center[1] - category_center[1]) <= geometry_unit * 1.5


def _horizontally_stacked(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    first_bbox = first["bbox"]
    second_bbox = second["bbox"]
    overlap = min(first_bbox[2], second_bbox[2]) - max(first_bbox[0], second_bbox[0])
    min_width = min(first_bbox[2] - first_bbox[0], second_bbox[2] - second_bbox[0])
    return overlap >= min_width * 0.5


def _vertically_stacked(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    first_bbox = first["bbox"]
    second_bbox = second["bbox"]
    center_gap = _center(second_bbox)[1] - _center(first_bbox)[1]
    reference_height = max(
        first_bbox[3] - first_bbox[1],
        second_bbox[3] - second_bbox[1],
    )
    return reference_height * 0.5 <= center_gap <= reference_height * 3


def _play_zone_boundary_x(
    coordinate_spaces: set[str],
    *,
    image_width: float | None,
) -> float | None:
    if coordinate_spaces == {"normalized"}:
        return _PLAY_ZONE_RATIO
    if coordinate_spaces == {"pixel"} and image_width is not None:
        return image_width * _PLAY_ZONE_RATIO
    return None


def _number_is_valid(text: str, *, game: str) -> bool:
    if not _TWO_DIGIT_RE.fullmatch(text):
        return False
    if game == "539":
        validation = validate_number_token(text)
        return (
            validation.canonical == text
            and not validation.requires_human_confirmation
            and 1 <= int(text) <= 39
        )
    if game == "六合":
        return 1 <= int(text) <= 49
    return False


def _looks_like_multiplier(text: str) -> bool:
    if not text:
        return False
    if classify_multiplier_token(text) in {COMPLETE, PARTIAL}:
        return True
    return bool(re.search(r"[xX×]", text))


def _canonical_collision(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return canonical_category(text) if _COLLISION_RE.fullmatch(text) else text


def _is_supported_elsewhere(text: str) -> bool:
    return any(marker in text for marker in ("尾", "各", "(", ")"))


def _columns_as_groups(value: Mapping[str, Any]) -> list[list[str]]:
    columns = value.get("columns")
    if not isinstance(columns, Mapping):
        return []
    groups: list[list[str]] = []
    for index in range(1, int(value.get("column_count") or 0) + 1):
        group = columns.get(str(index))
        groups.append([str(item) for item in group] if isinstance(group, list) else [])
    return groups


def _separator_between_numbers(
    separators: list[dict[str, Any]],
    numbers: list[dict[str, Any]],
) -> bool:
    if len(numbers) < 2:
        return False
    number_centers = sorted(_center(token["bbox"])[0] for token in numbers)
    left, right = number_centers[0], number_centers[-1]
    return any(left < _center(token["bbox"])[0] < right for token in separators)


def _has_bbox_overlap(tokens: list[dict[str, Any]]) -> bool:
    for index, first in enumerate(tokens):
        ax1, ay1, ax2, ay2 = first["bbox"]
        for second in tokens[index + 1:]:
            bx1, by1, bx2, by2 = second["bbox"]
            if min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1):
                return True
    return False


def _number_in_play_area(
    numbers: list[dict[str, Any]],
    collision_tokens: list[dict[str, Any]],
    complete_multiplier_texts: list[str],
    token_evidence: list[dict[str, Any]],
) -> bool:
    play_x: list[float] = [_center(token["bbox"])[0] for token in collision_tokens]
    if complete_multiplier_texts:
        play_x.extend(
            _center(item["bbox"])[0]
            for item in token_evidence
            if item.get("classification") == "complete_multiplier" and item.get("bbox")
        )
    if not play_x:
        return False
    boundary = min(play_x)
    return any(_center(token["bbox"])[0] > boundary for token in numbers)


def _center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric > 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _warn(warnings: list[str], blocking: set[str], warning: str) -> None:
    warnings.append(warning)
    blocking.add(warning)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
