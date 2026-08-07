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
_SINGLE_DIGIT_RE = re.compile(r"\d")
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
    fragmented_multiplier_tokens: list[dict[str, Any]] = []
    invalid_multiplier_texts: list[str] = []
    coordinate_spaces: set[str] = set()

    prepared_tokens: list[dict[str, Any]] = []
    for index, raw_token in enumerate(tokens, start=1):
        token = raw_token if isinstance(raw_token, Mapping) else {}
        token_id = str(token.get("token_id") or f"{line_id}-T{index:02d}")
        text = str(token.get("text") or "")
        source_line_id = str(token.get("_source_line_id") or line_id)
        bbox, coordinate_space, bbox_issue = _bbox_xyxy(token.get("bounding_box"))
        global_zone = _bbox_zone(
            bbox,
            coordinate_space=coordinate_space,
            image_width=image_width,
        )
        if coordinate_space:
            coordinate_spaces.add(coordinate_space)
        prepared_tokens.append({
            "token_id": token_id,
            "text": text,
            "source_line_id": source_line_id,
            "bbox": bbox,
            "coordinate_space": coordinate_space,
            "bbox_issue": bbox_issue,
            "global_zone": global_zone,
        })

    preliminary_numbers = [
        token for token in prepared_tokens
        if token["bbox"] is not None
        and _TWO_DIGIT_RE.fullmatch(str(token["text"]))
        and _number_is_valid(str(token["text"]), game=game)
        and token["global_zone"] != "play"
    ]
    play_boundary = _structure_play_boundary(
        preliminary_numbers,
        coordinate_spaces=coordinate_spaces,
        image_width=image_width,
    )

    for prepared in prepared_tokens:
        token_id = str(prepared["token_id"])
        text = str(prepared["text"])
        source_line_id = str(prepared["source_line_id"])
        bbox = prepared["bbox"]
        coordinate_space = prepared["coordinate_space"]
        bbox_issue = prepared["bbox_issue"]
        global_zone = str(prepared["global_zone"])
        zone = _structure_token_zone(
            bbox,
            coordinate_space=coordinate_space,
            global_zone=global_zone,
            play_boundary=play_boundary,
        )

        classification = "unknown"
        multiplier_classification: str | None = None
        number_valid = False

        if not text.strip():
            classification = "blank_geometry_token"
        elif _TWO_DIGIT_RE.fullmatch(text):
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
        elif zone == "play" and text in {"/", "."}:
            classification = "multiplier_fragment"
        elif zone == "play" and _SINGLE_DIGIT_RE.fullmatch(text):
            classification = "multiplier_fragment_candidate"
            if text in {"2", "3", "4"}:
                multiplier_classification = classify_multiplier_token(text)
        elif zone == "play" and _SEPARATOR_RE.fullmatch(text):
            classification = "multiplier_operator_candidate"
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
            "global_zone": global_zone,
            "play_boundary_source": play_boundary["source"],
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
        elif classification in {
            "multiplier_fragment",
            "multiplier_fragment_candidate",
            "multiplier_operator_candidate",
        }:
            fragmented_multiplier_tokens.append(geometry_token)
            if classification == "multiplier_fragment_candidate" and text in {"2", "3", "4"}:
                collision_tokens.append(geometry_token)
                geometry_tokens.append(geometry_token)

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

    (
        fragmented_rules,
        resolved_fragment_token_ids,
        fragmented_rule_issues,
        fragmented_rule_debug,
    ) = _compose_fragmented_multiplier_rules(
        fragmented_multiplier_tokens,
        collision_tokens=collision_tokens,
    )
    for issue in fragmented_rule_issues:
        _warn(warnings, blocking, issue)
    if fragmented_rules:
        complete_multiplier_texts.extend(fragmented_rules)
        warnings.append("fragmented_play_rule_reconstructed:" + "/".join(fragmented_rules))

    resolved_multiplier_token_ids = (
        resolved_partial_token_ids | resolved_fragment_token_ids
    )
    unresolved_fragments = [
        token for token in fragmented_multiplier_tokens
        if str(token.get("token_id") or "") not in resolved_multiplier_token_ids
    ]
    if unresolved_fragments and "fragment_multiplier_ambiguous" not in blocking:
        _warn(warnings, blocking, "fragment_multiplier_incomplete")

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
        if token["token_id"] not in resolved_multiplier_token_ids
        and str(token["text"]) not in collision_texts
    ]
    if unresolved_partial:
        _warn(warnings, blocking, "partial_multiplier_evidence")
    if invalid_multiplier_texts:
        _warn(warnings, blocking, "invalid_multiplier_evidence")
    collision_token_ids = {
        str(token.get("token_id") or "") for token in collision_tokens
    }
    collision_fully_composed = bool(collision_token_ids) and (
        collision_token_ids <= resolved_multiplier_token_ids
    )
    if collision_texts:
        warnings.append("collision_play_evidence:" + "/".join(sorted(collision_texts)))
        if collision_fully_composed:
            warnings.append("collision_composed_into_multiplier")
        elif layout != "column_bet":
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

    comparison_collision = (
        collision
        if model_compare.get("collision") is not None or not collision_fully_composed
        else None
    )
    reconstruction_compare = {
        "number_groups": number_groups,
        "column_count": len(number_groups) if layout == "column_bet" else 1 if number_groups else 0,
        "layout": layout,
        "multiplier_rules": complete_rules,
        "collision": comparison_collision,
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
        "zone_rule": play_boundary["zone_rule"],
        "play_zone_ratio": _PLAY_ZONE_RATIO,
        "play_zone_boundary_x": play_boundary["boundary_x"],
        "play_zone_boundary_source": play_boundary["source"],
        "global_play_zone_boundary_x": play_boundary["global_boundary_x"],
        "rightmost_number_x2": play_boundary["rightmost_number_x2"],
        "rightmost_number_token_ids": play_boundary["rightmost_number_token_ids"],
        "image_width": image_width,
        "stacked_collision_categories": stacked_categories,
        "fragmented_multiplier": fragmented_rule_debug,
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
                "structure_relative_play_boundary",
                "deterministic_fragmented_multiplier_v1",
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


def _structure_play_boundary(
    number_tokens: list[dict[str, Any]],
    *,
    coordinate_spaces: set[str],
    image_width: float | None,
) -> dict[str, Any]:
    global_boundary = _play_zone_boundary_x(
        coordinate_spaces,
        image_width=image_width,
    )
    number_spaces = {
        str(token.get("coordinate_space") or "")
        for token in number_tokens
        if token.get("bbox") is not None
    }
    if number_tokens and len(number_spaces) == 1 and "" not in number_spaces:
        rightmost_x2 = max(float(token["bbox"][2]) for token in number_tokens)
        return {
            "boundary_x": rightmost_x2,
            "coordinate_space": next(iter(number_spaces)),
            "source": "structure_relative_rightmost_number_x2",
            "zone_rule": "token_bbox_x1_gt_structure_rightmost_number_x2",
            "global_boundary_x": global_boundary,
            "rightmost_number_x2": rightmost_x2,
            "rightmost_number_token_ids": [
                str(token.get("token_id") or "")
                for token in number_tokens
                if float(token["bbox"][2]) == rightmost_x2
            ],
        }
    return {
        "boundary_x": global_boundary,
        "coordinate_space": (
            next(iter(coordinate_spaces)) if len(coordinate_spaces) == 1 else None
        ),
        "source": "global_image_ratio_fallback",
        "zone_rule": _ZONE_RULE,
        "global_boundary_x": global_boundary,
        "rightmost_number_x2": None,
        "rightmost_number_token_ids": [],
    }


def _structure_token_zone(
    bbox: list[float] | None,
    *,
    coordinate_space: str | None,
    global_zone: str,
    play_boundary: Mapping[str, Any],
) -> str:
    boundary_x = play_boundary.get("boundary_x")
    boundary_space = play_boundary.get("coordinate_space")
    if (
        play_boundary.get("source") == "structure_relative_rightmost_number_x2"
        and bbox is not None
        and isinstance(boundary_x, (int, float))
        and coordinate_space == boundary_space
    ):
        return "play" if bbox[0] > float(boundary_x) else "main"
    return global_zone


def _compose_fragmented_multiplier_rules(
    fragments: list[dict[str, Any]],
    *,
    collision_tokens: list[dict[str, Any]],
) -> tuple[list[str], set[str], list[str], dict[str, Any]]:
    usable = [token for token in fragments if token.get("bbox") is not None]
    issues: list[str] = []
    if any(not str(token.get("source_line_id") or "") for token in usable):
        issues.append("fragment_multiplier_scope_ambiguous")

    by_source_line: dict[str, list[dict[str, Any]]] = {}
    for token in usable:
        source_line_id = str(token.get("source_line_id") or "")
        by_source_line.setdefault(source_line_id, []).append(token)

    candidates: list[dict[str, Any]] = []
    for source_line_id in sorted(by_source_line):
        ordered = sorted(
            by_source_line[source_line_id],
            key=lambda token: (
                _center(token["bbox"])[0],
                _center(token["bbox"])[1],
                str(token.get("token_id") or ""),
            ),
        )
        for index in range(len(ordered)):
            if _category_start_is_continuation(ordered, index):
                continue
            candidate = _parse_fragment_rule(ordered, index)
            if candidate is None:
                continue
            extended, extension_issue = _extend_fragment_category(
                candidate,
                collision_tokens=collision_tokens,
            )
            if extension_issue is not None:
                issues.append(extension_issue)
                continue
            candidates.append(extended)

    unique_candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, tuple[str, ...]]] = set()
    for candidate in candidates:
        key = (
            str(candidate["rule"]),
            tuple(sorted(str(token_id) for token_id in candidate["token_ids"])),
        )
        if key not in seen_candidates:
            seen_candidates.add(key)
            unique_candidates.append(candidate)

    token_use_counts = Counter(
        str(token_id)
        for candidate in unique_candidates
        for token_id in candidate["token_ids"]
    )
    category_use_counts = Counter(
        str(candidate["category"])
        for candidate in unique_candidates
    )
    ambiguous = any(count > 1 for count in token_use_counts.values()) or any(
        count > 1 for count in category_use_counts.values()
    )
    if ambiguous:
        issues.append("fragment_multiplier_ambiguous")
        selected: list[dict[str, Any]] = []
    else:
        selected = unique_candidates

    resolved = {
        str(token_id)
        for candidate in selected
        for token_id in candidate["token_ids"]
    }
    resolved.update(_fragment_rule_divider_ids(usable, selected))
    unresolved_token_ids = {
        str(token.get("token_id") or "") for token in usable
    } - resolved
    if selected and unresolved_token_ids:
        issues.append("fragment_multiplier_ambiguous")
        selected = []
        resolved = set()
    rules = merge_complete_rules([str(candidate["rule"]) for candidate in selected])
    debug = {
        "composer": "deterministic_fragmented_multiplier_v1",
        "input_token_ids": [str(token.get("token_id") or "") for token in usable],
        "candidate_rules": [
            {
                "rule": candidate["rule"],
                "base_rule": candidate["base_rule"],
                "category": candidate["category"],
                "value": candidate["value"],
                "source_line_id": candidate["source_line_id"],
                "token_ids": list(candidate["token_ids"]),
                "stacked_category_token_ids": list(
                    candidate["stacked_category_token_ids"]
                ),
            }
            for candidate in unique_candidates
        ],
        "selected_rules": rules,
        "resolved_token_ids": sorted(resolved),
        "unresolved_token_ids": sorted(unresolved_token_ids),
        "issues": _unique(issues),
    }
    return rules, resolved, _unique(issues), debug


def _parse_fragment_rule(
    ordered: list[dict[str, Any]],
    start: int,
) -> dict[str, Any] | None:
    first = ordered[start]
    if str(first.get("text") or "") not in {"2", "3", "4"}:
        return None

    category_tokens = [first]
    cursor = start + 1
    while (
        cursor + 1 < len(ordered)
        and str(ordered[cursor].get("text") or "") == "/"
        and str(ordered[cursor + 1].get("text") or "") in {"2", "3", "4"}
    ):
        category_tokens.extend([ordered[cursor], ordered[cursor + 1]])
        cursor += 2

    if cursor >= len(ordered) or not _SEPARATOR_RE.fullmatch(
        str(ordered[cursor].get("text") or "")
    ):
        return None
    operator = ordered[cursor]
    cursor += 1
    if cursor >= len(ordered) or not _SINGLE_DIGIT_RE.fullmatch(
        str(ordered[cursor].get("text") or "")
    ):
        return None

    value_tokens: list[dict[str, Any]] = []
    while cursor < len(ordered) and _SINGLE_DIGIT_RE.fullmatch(
        str(ordered[cursor].get("text") or "")
    ):
        if (
            value_tokens
            and str(ordered[cursor].get("text") or "") in {"2", "3", "4"}
            and cursor + 1 < len(ordered)
            and _SEPARATOR_RE.fullmatch(str(ordered[cursor + 1].get("text") or ""))
        ):
            break
        value_tokens.append(ordered[cursor])
        cursor += 1

    if cursor < len(ordered) and str(ordered[cursor].get("text") or "") == ".":
        decimal_point = ordered[cursor]
        cursor += 1
        decimal_digits: list[dict[str, Any]] = []
        while cursor < len(ordered) and _SINGLE_DIGIT_RE.fullmatch(
            str(ordered[cursor].get("text") or "")
        ):
            if (
                decimal_digits
                and str(ordered[cursor].get("text") or "") in {"2", "3", "4"}
                and cursor + 1 < len(ordered)
                and _SEPARATOR_RE.fullmatch(str(ordered[cursor + 1].get("text") or ""))
            ):
                break
            decimal_digits.append(ordered[cursor])
            cursor += 1
        if not decimal_digits:
            return None
        value_tokens.extend([decimal_point, *decimal_digits])

    used_tokens = [*category_tokens, operator, *value_tokens]
    if not all(
        _fragment_pair_adjacent(first_token, second_token)
        for first_token, second_token in zip(used_tokens, used_tokens[1:])
    ):
        return None

    category_digits = [
        str(token.get("text") or "")
        for token in category_tokens
        if str(token.get("text") or "") in {"2", "3", "4"}
    ]
    category = canonical_category("/".join(category_digits))
    value = "".join(str(token.get("text") or "") for token in value_tokens)
    base_rule = f"{category}X{value}"
    validated = split_complete_rules(base_rule)
    if len(validated) != 1:
        return None
    all_bboxes = [token["bbox"] for token in used_tokens]
    return {
        "rule": validated[0],
        "base_rule": validated[0],
        "category": category,
        "category_digit_tokens": [
            token for token in category_tokens
            if str(token.get("text") or "") in {"2", "3", "4"}
        ],
        "value": value,
        "source_line_id": str(first.get("source_line_id") or ""),
        "token_ids": [str(token.get("token_id") or "") for token in used_tokens],
        "stacked_category_token_ids": [],
        "bbox": _bbox_union(all_bboxes),
    }


def _extend_fragment_category(
    candidate: dict[str, Any],
    *,
    collision_tokens: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    used_ids = {str(token_id) for token_id in candidate["token_ids"]}
    category_tokens = list(candidate["category_digit_tokens"])
    category_digits = {
        str(token.get("text") or "") for token in category_tokens
    }
    plausible: list[dict[str, Any]] = []
    for token in collision_tokens:
        token_id = str(token.get("token_id") or "")
        text = str(token.get("text") or "")
        if token_id in used_ids or text not in {"2", "3", "4"}:
            continue
        if any(_tokens_are_stacked(token, member) for member in category_tokens):
            plausible.append(token)

    if not plausible:
        return candidate, None
    plausible_digits = [str(token.get("text") or "") for token in plausible]
    if (
        len(plausible_digits) != len(set(plausible_digits))
        or any(digit in category_digits for digit in plausible_digits)
        or len(category_digits | set(plausible_digits)) > 3
    ):
        return candidate, "fragment_multiplier_ambiguous"

    expanded = dict(candidate)
    expanded_category = canonical_category(
        "/".join([*sorted(category_digits), *plausible_digits])
    )
    expanded_rule = f"{expanded_category}X{candidate['value']}"
    validated = split_complete_rules(expanded_rule)
    if len(validated) != 1:
        return candidate, "fragment_multiplier_ambiguous"
    expanded["rule"] = validated[0]
    expanded["category"] = expanded_category
    expanded["token_ids"] = [
        *candidate["token_ids"],
        *(str(token.get("token_id") or "") for token in plausible),
    ]
    expanded["stacked_category_token_ids"] = [
        str(token.get("token_id") or "") for token in plausible
    ]
    return expanded, None


def _tokens_are_stacked(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    if first.get("coordinate_space") != second.get("coordinate_space"):
        return False
    if not _horizontally_stacked(first, second):
        return False
    ordered = sorted([first, second], key=lambda token: _center(token["bbox"])[1])
    return _vertically_stacked(ordered[0], ordered[1])


def _fragment_pair_adjacent(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    if first.get("coordinate_space") != second.get("coordinate_space"):
        return False
    first_bbox = first["bbox"]
    second_bbox = second["bbox"]
    first_center = _center(first_bbox)
    second_center = _center(second_bbox)
    geometry_unit = max(
        first_bbox[3] - first_bbox[1],
        second_bbox[3] - second_bbox[1],
    )
    horizontal_gap = second_bbox[0] - first_bbox[2]
    return (
        second_center[0] > first_center[0]
        and -geometry_unit * 0.5 <= horizontal_gap <= geometry_unit * 1.75
        and abs(second_center[1] - first_center[1]) <= geometry_unit * 1.5
    )


def _category_start_is_continuation(
    ordered: list[dict[str, Any]],
    index: int,
) -> bool:
    if index == 0:
        return False
    previous_text = str(ordered[index - 1].get("text") or "")
    if _SEPARATOR_RE.fullmatch(previous_text):
        return True
    if previous_text != "/":
        return False
    for prior in reversed(ordered[: index - 1]):
        text = str(prior.get("text") or "")
        if _SEPARATOR_RE.fullmatch(text):
            return False
        if text == "/":
            return True
    return True


def _fragment_rule_divider_ids(
    fragments: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> set[str]:
    resolved: set[str] = set()
    for token in fragments:
        if str(token.get("text") or "") != "/":
            continue
        token_center = _center(token["bbox"])
        source_line_id = str(token.get("source_line_id") or "")
        left = [
            candidate for candidate in candidates
            if candidate["source_line_id"] == source_line_id
            and candidate["bbox"][2] < token_center[0]
        ]
        right = [
            candidate for candidate in candidates
            if candidate["source_line_id"] == source_line_id
            and candidate["bbox"][0] > token_center[0]
        ]
        if left and right:
            resolved.add(str(token.get("token_id") or ""))
    return resolved


def _bbox_union(bboxes: list[list[float]]) -> list[float]:
    return [
        min(bbox[0] for bbox in bboxes),
        min(bbox[1] for bbox in bboxes),
        max(bbox[2] for bbox in bboxes),
        max(bbox[3] for bbox in bboxes),
    ]


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
