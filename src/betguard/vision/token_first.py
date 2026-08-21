"""Literal token retention and geometry-only grouping for Phase 1B.

The functions in this module operate after whole-image OCR.  They never infer
betting semantics and never discard OCR evidence when grouping is uncertain.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from statistics import median
from typing import Any, Iterable

from betguard.vision.cell_first import (
    SCHEMA_VERSION,
    evidence_authority,
    make_token,
    reconstruct_visible_rows,
)


TOKEN_FIRST_SCHEMA_VERSION = SCHEMA_VERSION.replace("cell-first", "token-first")
TOKEN_GROUP_CONFLICT = "TOKEN_GROUP_CONFLICT"
COMPONENT_UNION_BLOCKED_BY_SEPARATOR = "COMPONENT_UNION_BLOCKED_BY_SEPARATOR"
COMPONENT_SPLIT_BY_SEPARATOR = "COMPONENT_SPLIT_BY_SEPARATOR"
AI_UNCERTAIN = "AI_UNCERTAIN"

_LITERAL_RE = re.compile(r"半車|尾|車|各|[0-9]+[.．][0-9]+|[xX×]|[0-9]+|[^\s]")


def literal_tokens_from_regions(regions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain every OCR region and deterministically expose literal spans.

    PP-OCR returns text-line regions rather than character boxes.  A literal
    token therefore carries its parent region id and a deterministic horizontal
    partition of that region's bbox.  The original region text/bbox remains the
    authoritative OCR evidence; no characters are corrected or synthesized.
    """
    tokens: list[dict[str, Any]] = []
    for region_index, source in enumerate(regions, 1):
        region = _validated_region(source, region_index)
        text = region["text"]
        matches = list(_LITERAL_RE.finditer(text))
        for match in matches:
            bbox = _partition_bbox(region["bbox"], match.start(), match.end(), len(text))
            token = make_token(
                token_id=f"TOKEN-{len(tokens) + 1:05d}",
                cell_id=None,
                text_raw=match.group(0),
                confidence=region["confidence"],
                bbox=bbox,
            )
            token.update({
                "source_region_id": region["region_id"],
                "source_region_text_raw": text,
                "source_region_bbox": region["bbox"],
                "source_character_span": [match.start(), match.end()],
                "bbox_origin": "deterministic_partition_of_ppocr_region",
            })
            tokens.append(token)
    return tokens


def group_regions_with_separators(
    regions: Iterable[dict[str, Any]],
    tokens: Iterable[dict[str, Any]],
    separators: Iterable[dict[str, Any]],
    *,
    image_size: tuple[int, int],
) -> dict[str, Any]:
    """Build conservative region adjacency groups with red-line barriers."""
    width, height = image_size
    normalized_regions = [
        _validated_region(region, index)
        for index, region in enumerate(regions, 1)
    ]
    normalized_tokens = [deepcopy(token) for token in tokens]
    normalized_separators = [deepcopy(separator) for separator in separators]
    region_by_id = {region["region_id"]: region for region in normalized_regions}
    tokens_by_region: dict[str, list[dict[str, Any]]] = {
        region_id: [] for region_id in region_by_id
    }
    for token in normalized_tokens:
        region_id = str(token.get("source_region_id") or "")
        if region_id in tokens_by_region:
            tokens_by_region[region_id].append(token)

    if not normalized_regions:
        return {
            "groups": [],
            "rows": [],
            "token_group_conflicts": [],
            "ungrouped_token_ids": [token["token_id"] for token in normalized_tokens],
            "retained_token_ids": [token["token_id"] for token in normalized_tokens],
            "retention_rate": 1.0 if normalized_tokens else 1.0,
            "cross_separator_merge_count": 0,
            "union_attempt_count": 0,
            "successful_union_count": 0,
            "union_noop_same_component_count": 0,
            "component_union_blocked_by_separator_count": 0,
            "component_union_blocks": [],
            "component_split_count": 0,
            "component_split_created_count": 0,
            "component_split_events": [],
            "adjacency": {
                "strong_edges": [],
                "ambiguous_edges": [],
                "blocked_edges": [],
            },
            **evidence_authority(),
        }

    typical_height = median(
        max(1.0, float(region["bbox"][3]) - float(region["bbox"][1]))
        for region in normalized_regions
    )
    strong_edges: list[dict[str, Any]] = []
    ambiguous_edges: list[dict[str, Any]] = []
    blocked_edges: list[dict[str, Any]] = []
    for left_index, left in enumerate(normalized_regions):
        for right in normalized_regions[left_index + 1:]:
            score, relation = _adjacency_score(
                left, right, typical_height, width, height
            )
            if score < 0.44:
                continue
            crossing = separator_crossings(left, right, normalized_separators)
            edge = {
                "left_region_id": left["region_id"],
                "right_region_id": right["region_id"],
                "score": round(score, 6),
                "relation": relation,
                "crossing_separator_ids": [item["separator_id"] for item in crossing],
            }
            if crossing:
                edge["blocked"] = True
                blocked_edges.append(edge)
            elif score >= 0.64:
                edge["blocked"] = False
                strong_edges.append(edge)
            else:
                edge["blocked"] = False
                ambiguous_edges.append(edge)

    disjoint = _DisjointSet(region_by_id)
    union_attempt_count = 0
    successful_union_count = 0
    union_noop_same_component_count = 0
    component_union_blocks: list[dict[str, Any]] = []
    for edge in sorted(
        strong_edges,
        key=lambda item: (
            -float(item["score"]),
            str(item["left_region_id"]),
            str(item["right_region_id"]),
        ),
    ):
        union_attempt_count += 1
        left_root = disjoint.find(edge["left_region_id"])
        right_root = disjoint.find(edge["right_region_id"])
        if left_root == right_root:
            union_noop_same_component_count += 1
            continue
        left_members = _component_members(disjoint, region_by_id, left_root)
        right_members = _component_members(disjoint, region_by_id, right_root)
        crossing_ids = _component_crossing_separator_ids(
            left_members,
            right_members,
            region_by_id,
            normalized_separators,
        )
        if crossing_ids:
            component_union_blocks.append({
                "code": COMPONENT_UNION_BLOCKED_BY_SEPARATOR,
                "left_component_region_ids": left_members,
                "right_component_region_ids": right_members,
                "trigger_edge": deepcopy(edge),
                "separator_ids": crossing_ids,
            })
            continue
        disjoint.union(edge["left_region_id"], edge["right_region_id"])
        successful_union_count += 1

    component_regions: dict[str, list[str]] = {}
    for region_id in region_by_id:
        component_regions.setdefault(disjoint.find(region_id), []).append(region_id)
    component_split_events: list[dict[str, Any]] = []
    separator_safe_components: list[list[str]] = []
    for members in component_regions.values():
        violations = _internal_crossing_separator_ids(
            members, region_by_id, normalized_separators
        )
        if not violations:
            separator_safe_components.append(sorted(members))
            continue
        split_components = _split_separator_violating_component(
            members,
            region_by_id,
            strong_edges,
            normalized_separators,
        )
        component_split_events.append({
            "code": COMPONENT_SPLIT_BY_SEPARATOR,
            "source_region_ids": sorted(members),
            "separator_ids": violations,
            "result_components": split_components,
        })
        separator_safe_components.extend(split_components)

    for members in separator_safe_components:
        remaining = _internal_crossing_separator_ids(
            members, region_by_id, normalized_separators
        )
        if remaining:
            raise RuntimeError("SEPARATOR_SAFE_COMPONENT_INVARIANT_BROKEN")

    ordered_components = sorted(
        separator_safe_components,
        key=lambda members: _union_bbox(region_by_id[item]["bbox"] for item in members)[1::-1],
    )
    group_id_by_region: dict[str, str] = {}
    for group_index, members in enumerate(ordered_components, 1):
        for region_id in members:
            group_id_by_region[region_id] = f"GROUP-{group_index:04d}"

    conflicts: list[dict[str, Any]] = []
    for edge in ambiguous_edges:
        left_group = group_id_by_region[edge["left_region_id"]]
        right_group = group_id_by_region[edge["right_region_id"]]
        if left_group == right_group:
            continue
        token_ids = [
            token["token_id"]
            for region_id in (edge["left_region_id"], edge["right_region_id"])
            for token in tokens_by_region.get(region_id, [])
        ]
        conflicts.append({
            "code": TOKEN_GROUP_CONFLICT,
            "token_ids": token_ids,
            "candidate_group_ids": [left_group, right_group],
            "adjacency_score": edge["score"],
            "relation": edge["relation"],
        })

    conflicted_groups = {
        group_id
        for conflict in conflicts
        for group_id in conflict["candidate_group_ids"]
    }
    groups: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    cross_separator_merge_count = 0
    for members in ordered_components:
        group_id = group_id_by_region[members[0]]
        group_regions = [region_by_id[region_id] for region_id in members]
        group_tokens = [
            token
            for region_id in members
            for token in tokens_by_region.get(region_id, [])
        ]
        internal_edges = [
            edge for edge in strong_edges
            if edge["left_region_id"] in members and edge["right_region_id"] in members
        ]
        crossing_ids: set[str] = set()
        for left_index, left in enumerate(group_regions):
            for right in group_regions[left_index + 1:]:
                crossing_ids.update(
                    item["separator_id"]
                    for item in separator_crossings(left, right, normalized_separators)
                )
        cross_separator_merge_count += len(crossing_ids)
        blocked_near_group = {
            separator_id
            for edge in blocked_edges
            if edge["left_region_id"] in members or edge["right_region_id"] in members
            for separator_id in edge["crossing_separator_ids"]
        }
        if internal_edges:
            confidence = sum(edge["score"] for edge in internal_edges) / len(internal_edges)
        else:
            confidence = 0.58
        grouping_uncertain = bool(
            group_id in conflicted_groups or crossing_ids or confidence < 0.68
        )
        if grouping_uncertain:
            confidence = min(confidence, 0.67)
        assigned = []
        for token in group_tokens:
            clone = deepcopy(token)
            clone["cell_id"] = group_id
            assigned.append(clone)
        row_result = reconstruct_visible_rows(group_id, assigned)
        normalized_assigned = row_result.pop("tokens", assigned)
        row_result["grouping_uncertain"] = grouping_uncertain
        rows.append(row_result)
        groups.append({
            "group_id": group_id,
            "token_ids": [token["token_id"] for token in group_tokens],
            "source_region_ids": members,
            "bbox": _union_bbox(region["bbox"] for region in group_regions),
            "grouping_confidence": round(max(0.0, min(1.0, confidence)), 6),
            "separator_evidence": {
                "blocked_separator_ids": sorted(blocked_near_group),
                "internal_crossing_separator_ids": sorted(crossing_ids),
                "accepted_adjacency_edge_count": len(internal_edges),
            },
            "grouping_uncertain": grouping_uncertain,
            "status": AI_UNCERTAIN if grouping_uncertain else "GROUPED",
            "assigned_tokens": normalized_assigned,
            **evidence_authority(),
        })

    retained_ids = [token["token_id"] for token in normalized_tokens]
    assigned_ids = {
        token_id for group in groups for token_id in group["token_ids"]
    }
    return {
        "groups": groups,
        "rows": rows,
        "token_group_conflicts": conflicts,
        "ungrouped_token_ids": [
            token_id for token_id in retained_ids if token_id not in assigned_ids
        ],
        "retained_token_ids": retained_ids,
        "retention_rate": len(retained_ids) / len(normalized_tokens) if normalized_tokens else 1.0,
        "cross_separator_merge_count": cross_separator_merge_count,
        "union_attempt_count": union_attempt_count,
        "successful_union_count": successful_union_count,
        "union_noop_same_component_count": union_noop_same_component_count,
        "component_union_blocked_by_separator_count": len(component_union_blocks),
        "component_union_blocks": component_union_blocks,
        "component_split_count": len(component_split_events),
        "component_split_created_count": sum(
            max(0, len(event["result_components"]) - 1)
            for event in component_split_events
        ),
        "component_split_events": component_split_events,
        "adjacency": {
            "strong_edges": strong_edges,
            "ambiguous_edges": ambiguous_edges,
            "blocked_edges": blocked_edges,
        },
        **evidence_authority(),
    }


def separator_crossings(
    left: dict[str, Any],
    right: dict[str, Any],
    separators: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return high-confidence separator segments crossed by a center-to-center edge."""
    first = [float(left["center_x"]), float(left["center_y"])]
    second = [float(right["center_x"]), float(right["center_y"])]
    typical_height = min(float(left["height"]), float(right["height"]))
    tolerance = max(2.0, typical_height * 0.18)
    result = []
    for separator in separators:
        if float(separator.get("confidence", 0.0)) < 0.52:
            continue
        start = [float(value) for value in separator["start"]]
        end = [float(value) for value in separator["end"]]
        if _segments_intersect_or_close(first, second, start, end, tolerance):
            result.append(separator)
    return result


def _component_members(
    disjoint: "_DisjointSet",
    region_by_id: dict[str, dict[str, Any]],
    root: str,
) -> list[str]:
    return sorted(
        region_id
        for region_id in region_by_id
        if disjoint.find(region_id) == root
    )


def _component_crossing_separator_ids(
    left_members: Iterable[str],
    right_members: Iterable[str],
    region_by_id: dict[str, dict[str, Any]],
    separators: Iterable[dict[str, Any]],
) -> list[str]:
    crossing_ids = {
        separator["separator_id"]
        for left_id in left_members
        for right_id in right_members
        for separator in separator_crossings(
            region_by_id[left_id], region_by_id[right_id], separators
        )
    }
    return sorted(crossing_ids)


def _internal_crossing_separator_ids(
    members: Iterable[str],
    region_by_id: dict[str, dict[str, Any]],
    separators: Iterable[dict[str, Any]],
) -> list[str]:
    ordered = sorted(members)
    crossing_ids = {
        separator["separator_id"]
        for left_index, left_id in enumerate(ordered)
        for right_id in ordered[left_index + 1:]
        for separator in separator_crossings(
            region_by_id[left_id], region_by_id[right_id], separators
        )
    }
    return sorted(crossing_ids)


def _split_separator_violating_component(
    members: Iterable[str],
    region_by_id: dict[str, dict[str, Any]],
    strong_edges: Iterable[dict[str, Any]],
    separators: Iterable[dict[str, Any]],
) -> list[list[str]]:
    """Greedily create all-pairs separator-safe connected subcomponents."""
    edge_scores: dict[frozenset[str], float] = {
        frozenset((edge["left_region_id"], edge["right_region_id"])): float(edge["score"])
        for edge in strong_edges
    }
    ordered = sorted(
        members,
        key=lambda region_id: (
            float(region_by_id[region_id]["center_y"]),
            float(region_by_id[region_id]["center_x"]),
            region_id,
        ),
    )
    components: list[list[str]] = []
    for region_id in ordered:
        candidates: list[tuple[float, int]] = []
        for component_index, component in enumerate(components):
            if _component_crossing_separator_ids(
                [region_id], component, region_by_id, separators
            ):
                continue
            connecting_scores = [
                edge_scores.get(frozenset((region_id, member)), 0.0)
                for member in component
            ]
            best_score = max(connecting_scores, default=0.0)
            if best_score > 0.0:
                candidates.append((best_score, component_index))
        if candidates:
            _score, component_index = max(
                candidates, key=lambda item: (item[0], -item[1])
            )
            components[component_index].append(region_id)
        else:
            components.append([region_id])
    return [sorted(component) for component in components]


def _validated_region(source: dict[str, Any], index: int) -> dict[str, Any]:
    text = str(source.get("text") or "")
    confidence = float(source.get("confidence", 0.0))
    bbox = [float(value) for value in source.get("bbox", [])]
    if not text or len(bbox) != 4:
        raise ValueError("OCR region requires non-empty text and bbox")
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("OCR region confidence is invalid")
    if not all(math.isfinite(value) for value in bbox):
        raise ValueError("OCR region bbox is invalid")
    x1, y1, x2, y2 = bbox
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
        raise ValueError("OCR region bbox is invalid")
    return {
        **deepcopy(source),
        "region_id": str(source.get("region_id") or f"PPREGION-{index:05d}"),
        "text": text,
        "confidence": confidence,
        "bbox": bbox,
        "center_x": (x1 + x2) / 2.0,
        "center_y": (y1 + y2) / 2.0,
        "width": x2 - x1,
        "height": y2 - y1,
    }


def _partition_bbox(
    bbox: list[float], start: int, end: int, text_length: int
) -> list[float]:
    x1, y1, x2, y2 = bbox
    divisor = max(1, text_length)
    left = x1 + (x2 - x1) * start / divisor
    right = x1 + (x2 - x1) * end / divisor
    if right <= left:
        right = min(x2, left + max(0.001, (x2 - x1) / divisor))
    return [left, y1, right, y2]


def _adjacency_score(
    left: dict[str, Any],
    right: dict[str, Any],
    typical_height: float,
    image_width: int,
    image_height: int,
) -> tuple[float, str]:
    lx1, ly1, lx2, ly2 = left["bbox"]
    rx1, ry1, rx2, ry2 = right["bbox"]
    horizontal_gap = max(0.0, max(lx1, rx1) - min(lx2, rx2))
    vertical_gap = max(0.0, max(ly1, ry1) - min(ly2, ry2))
    vertical_overlap = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    horizontal_overlap = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    min_height = max(1.0, min(left["height"], right["height"]))
    min_width = max(1.0, min(left["width"], right["width"]))
    row_alignment = vertical_overlap / min_height
    column_alignment = horizontal_overlap / min_width
    center_y_distance = abs(left["center_y"] - right["center_y"])
    center_x_distance = abs(left["center_x"] - right["center_x"])

    row_limit = max(typical_height * 5.5, image_width * 0.16)
    if row_alignment >= 0.32 and horizontal_gap <= row_limit:
        distance_score = 1.0 - min(1.0, horizontal_gap / row_limit)
        alignment_score = min(1.0, row_alignment)
        return 0.48 + 0.30 * alignment_score + 0.22 * distance_score, "same_visible_row"

    vertical_limit = max(typical_height * 2.8, image_height * 0.045)
    x_limit = max(typical_height * 2.0, image_width * 0.05)
    if vertical_gap <= vertical_limit and (
        column_alignment >= 0.28 or center_x_distance <= x_limit
    ):
        distance_score = 1.0 - min(1.0, vertical_gap / vertical_limit)
        alignment_score = max(
            min(1.0, column_alignment),
            1.0 - min(1.0, center_x_distance / x_limit),
        )
        return 0.38 + 0.32 * alignment_score + 0.30 * distance_score, "vertical_continuation"

    if horizontal_gap == 0.0 and vertical_gap == 0.0:
        return 0.75, "overlapping_ocr_regions"
    return 0.0, "not_adjacent"


def _segments_intersect_or_close(
    first: list[float],
    second: list[float],
    third: list[float],
    fourth: list[float],
    tolerance: float,
) -> bool:
    if _segments_intersect(first, second, third, fourth):
        return True
    return min(
        _point_segment_distance(first, third, fourth),
        _point_segment_distance(second, third, fourth),
        _point_segment_distance(third, first, second),
        _point_segment_distance(fourth, first, second),
    ) <= tolerance


def _segments_intersect(
    first: list[float],
    second: list[float],
    third: list[float],
    fourth: list[float],
) -> bool:
    def orientation(a: list[float], b: list[float], c: list[float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    one = orientation(first, second, third)
    two = orientation(first, second, fourth)
    three = orientation(third, fourth, first)
    four = orientation(third, fourth, second)
    return (
        (one == 0.0 or two == 0.0 or one * two < 0.0)
        and (three == 0.0 or four == 0.0 or three * four < 0.0)
        and max(min(first[0], second[0]), min(third[0], fourth[0]))
        <= min(max(first[0], second[0]), max(third[0], fourth[0])) + 1e-6
        and max(min(first[1], second[1]), min(third[1], fourth[1]))
        <= min(max(first[1], second[1]), max(third[1], fourth[1])) + 1e-6
    )


def _point_segment_distance(
    point: list[float], start: list[float], end: list[float]
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared <= 1e-9:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared),
    )
    projection = [start[0] + fraction * dx, start[1] + fraction * dy]
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def _union_bbox(values: Iterable[Iterable[float]]) -> list[float]:
    boxes = [list(value) for value in values]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


class _DisjointSet:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root
