"""Geometry-based column grouping for 柱碰 (column bets).

Flattened OCR text loses the spatial structure; this layer rebuilds columns
from per-token bounding boxes:

    tokens + bbox
      -> classify (number / separator-x / collision / multiplier)
      -> cluster numbers by center_x -> columns
      -> sort each column by center_y (top -> bottom)
      -> parse collision (e.g. 4/3 -> 四三碰) and multiplier (x0.1) separately
      -> drop handwritten "x" separator strokes and isolated stray digits

Example (fixed regression case):
    columns = [[24,34], [03,23], [17,27,37], [20,30,35]]
    collision_raw = "4/3", collision = "四三碰", multiplier = 0.1
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Token:
    text: str
    bbox: list[int]  # [x1, y1, x2, y2]

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def center_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


COLLISION_MAP = {
    frozenset({2, 3}): ("2/3", "二三碰"),
    frozenset({3, 4}): ("4/3", "四三碰"),
    frozenset({2, 4}): ("2/4", "二四碰"),
    frozenset({2}): ("2", "二碰"),
    frozenset({3}): ("3", "三碰"),
    frozenset({4}): ("4", "四碰"),
}


def token_from_dict(d: dict[str, Any]) -> Token:
    return Token(text=str(d.get("text") or "").strip(), bbox=[int(x) for x in d.get("bbox")])


def classify_tokens(tokens: list[Token]) -> dict[str, list[Token]]:
    numbers: list[Token] = []
    separators: list[Token] = []
    collision: list[Token] = []
    multiplier: list[Token] = []
    for t in tokens:
        if re.fullmatch(r"[xX×]", t.text):
            separators.append(t)
        elif re.fullmatch(r"\d{2}", t.text) and 1 <= int(t.text) <= 49:
            numbers.append(t)
        elif re.fullmatch(r"[234](?:/[234])?", t.text):
            collision.append(t)
        elif re.fullmatch(r"x?0?\.?\d+(?:\.\d+)?", t.text) and ("." in t.text or len(t.text) <= 3):
            multiplier.append(t)
        else:
            separators.append(t)  # unknown / stray stroke -> drop
    return {
        "numbers": numbers,
        "separators": separators,
        "collision": collision,
        "multiplier": multiplier,
    }


def cluster_by_x(tokens: list[Token], gap_threshold: float = 65.0) -> list[list[Token]]:
    """Group tokens by center_x proximity (column = same X band)."""
    ordered = sorted(tokens, key=lambda t: t.center_x)
    clusters: list[list[Token]] = []
    for t in ordered:
        if clusters and t.center_x - clusters[-1][-1].center_x <= gap_threshold:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    return clusters


def parse_collision(tokens: list[Token]) -> tuple[str, str] | None:
    """Collision zone: stacked category digits (e.g. 4/3)."""
    digits = set()
    for t in tokens:
        for ch in re.findall(r"[234]", t.text):
            digits.add(int(ch))
    if not digits:
        return None
    # prefer the largest matching pair ({3,4} before {2,3}) so a stray "2"
    # does not turn 四三碰 into 二三碰
    for key, (raw, zh) in sorted(
        COLLISION_MAP.items(), key=lambda kv: (len(kv[0]), sum(kv[0])), reverse=True
    ):
        if key <= digits:
            return raw, zh
    return None


def parse_multiplier(tokens: list[Token]) -> float | None:
    """Reconstruct the multiplier value from zone tokens (X order).

    Handles the VLM splitting "x0.1" into tokens "x", "0", "1": the joined
    text "01" is interpreted as 0.1 (leading-zero two-digit pattern).
    """
    ordered = sorted(tokens, key=lambda t: t.center_x)
    deduped: list[Token] = []
    for t in ordered:
        if not deduped or deduped[-1].text != t.text:
            deduped.append(t)
    joined = "".join(t.text for t in deduped)
    joined = re.sub(r"^[xX×]+", "", joined)
    # take the FIRST complete multiplier value (duplicates across rows may
    # join into "0101" etc.)
    m = re.search(r"\d\.\d+", joined)
    if m:
        return float(m.group(0))
    m = re.search(r"0\d", joined)
    if m:
        return float(f"0.{m.group(0)[1]}")
    m = re.search(r"\d+", joined)
    if m:
        return float(m.group(0))
    return None


def build_grid_from_rows(token_dicts: list[dict[str, Any]], *, row_threshold: float = 18.0) -> dict[str, Any]:
    """Rebuild columns by ROW first, then right-align partial rows.

    More robust to per-token X drift: numbers on the same horizontal line
    (similar center_y) are grouped into a row, sorted by center_x; a row with
    fewer numbers right-aligns to the last columns (real slips' pattern).
    """
    tokens = [token_from_dict(d) for d in token_dicts]
    classified = classify_tokens(tokens)
    nums = sorted(classified["numbers"], key=lambda t: (t.center_y, t.center_x))
    rows: list[list[Token]] = []
    for t in nums:
        if rows and abs(t.center_y - rows[-1][0].center_y) <= row_threshold:
            rows[-1].append(t)
        else:
            rows.append([t])
    for row in rows:
        row.sort(key=lambda t: t.center_x)
    n_cols = max(len(r) for r in rows) if rows else 0
    columns: list[list[str]] = [[] for _ in range(n_cols)]
    for row in rows:
        offset = n_cols - len(row)
        for i, t in enumerate(row):
            columns[offset + i].append(t.text)
    for col in columns:
        col.sort(key=lambda _: 0)  # preserve top->bottom row order already
    collision_raw, collision = parse_collision(classified["collision"]) or (None, None)
    multiplier = parse_multiplier(classified["multiplier"])
    return {
        "type": "column",
        "column_count": n_cols,
        "columns": {str(i + 1): col for i, col in enumerate(columns)},
        "collision": collision,
        "collision_raw": collision_raw,
        "multiplier": multiplier,
        "_debug": {
            "rows": [[(t.text, t.bbox, round(t.center_x), round(t.center_y)) for t in r] for r in rows],
            "collision_tokens": [(t.text, t.bbox) for t in classified["collision"]],
            "multiplier_tokens": [(t.text, t.bbox) for t in classified["multiplier"]],
            "separator_tokens": [t.text for t in classified["separators"]],
        },
    }


def _x_range(tokens: list[Token]) -> tuple[float, float]:
    xs = [t.center_x for t in tokens]
    return min(xs), max(xs)


def rows_overlap(a: list[Token], b: list[Token], *, min_overlap: float = 30.0) -> bool:
    """Two rows belong to the same bet when their X ranges overlap."""
    a1, a2 = _x_range(a)
    b1, b2 = _x_range(b)
    overlap = min(a2, b2) - max(a1, b1)
    return overlap >= min_overlap


def segment_rows_into_bets(rows: list[list[Token]]) -> list[list[list[Token]]]:
    """Group consecutive rows into bets by X-range overlap (NOT vertical gaps).

    Rows of the same 柱碰 bet share the same column X bands; rows of different
    bets (different column positions) do not overlap horizontally.
    """
    bets: list[list[list[Token]]] = []
    for row in rows:
        if bets and rows_overlap(bets[-1][-1], row):
            bets[-1].append(row)
        else:
            bets.append([row])
    return bets


def build_page_bets(token_dicts: list[dict[str, Any]], *, row_threshold: float = 18.0) -> list[dict[str, Any]]:
    """Whole-page rule layer: classify -> rows -> segment by X-overlap ->
    per-bet X-clustering -> structured column bets."""
    tokens = [token_from_dict(d) for d in token_dicts]
    classified = classify_tokens(tokens)
    nums = sorted(classified["numbers"], key=lambda t: (t.center_y, t.center_x))
    rows: list[list[Token]] = []
    for t in nums:
        if rows and abs(t.center_y - rows[-1][0].center_y) <= row_threshold:
            rows[-1].append(t)
        else:
            rows.append([t])
    bets = segment_rows_into_bets(rows)
    out: list[dict[str, Any]] = []
    for bet in bets:
        flat = [t for row in bet for t in row]
        if len(flat) < 3:
            continue
        bet_x1 = min(t.center_x for t in flat) - 25
        bet_x2 = max(t.center_x for t in flat) + 110
        bet_y1 = min(t.center_y for t in flat) - 20
        bet_y2 = max(t.center_y for t in flat) + 35
        zone = (
            [{"text": t.text, "bbox": t.bbox} for t in flat]
            + [
                {"text": t.text, "bbox": t.bbox}
                for t in classified["collision"] + classified["multiplier"] + classified["separators"]
                if bet_x1 <= t.center_x <= bet_x2 and bet_y1 <= t.center_y <= bet_y2
            ]
        )
        res = build_columns_from_bbox(
            zone,
        )
        if res["column_count"] >= 2:
            res["_rows"] = len(bet)
            out.append(res)
    return out


def build_columns_from_bbox(
    token_dicts: list[dict[str, Any]],
    *,
    gap_threshold: float = 60.0,
) -> dict[str, Any]:
    """Rebuild the 柱碰 structure from tokens + bboxes (X-clustering first).

    Pipeline:
      1. classify tokens: number / separator / collision (碰法) / multiplier
      2. cluster number tokens by center_x (columns share an X band)
      3. sort each column by center_y (top -> bottom)
      4. parse collision (e.g. 4/3 -> 四三碰) and multiplier (x0.1 -> 0.1)
      5. NEVER assign columns by OCR token order / nearest empty slot / Y only
    """
    tokens = [token_from_dict(d) for d in token_dicts]
    classified = classify_tokens(tokens)
    numbers = sorted(classified["numbers"], key=lambda t: t.center_x)
    clusters: list[list[Token]] = []
    for t in numbers:
        if clusters and t.center_x - clusters[-1][-1].center_x <= gap_threshold:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    columns: list[list[str]] = []
    for cluster in clusters:
        ordered = sorted(cluster, key=lambda t: t.center_y)
        columns.append([t.text for t in ordered])

    collision_raw, collision = parse_collision(classified["collision"]) or (None, None)
    multiplier = parse_multiplier(classified["multiplier"])
    centers = [
        round(sum(t.center_x for t in c) / len(c), 1) if c else None
        for c in clusters
    ]

    return {
        "type": "column",
        "column_count": len(columns),
        "columns": {str(i + 1): col for i, col in enumerate(columns)},
        "collision": collision,
        "collision_raw": collision_raw,
        "multiplier": multiplier,
        "_debug": {
            "number_tokens": [(t.text, t.bbox, round(t.center_x, 1), round(t.center_y, 1)) for t in numbers],
            "separator_tokens": [t.text for t in classified["separators"]],
            "collision_tokens": [(t.text, t.bbox, round(t.center_x, 1), round(t.center_y, 1)) for t in classified["collision"]],
            "multiplier_tokens": [(t.text, t.bbox, round(t.center_x, 1), round(t.center_y, 1)) for t in classified["multiplier"]],
            "x_clusters": [
                [(t.text, round(t.center_x, 1), round(t.center_y, 1)) for t in c]
                for c in clusters
            ],
            "column_centers_x": centers,
        },
    }
