"""Read-only fill preview built from the human-approved fill queue.

The preview never clicks, submits, or operates a real website. It only shows
what a later assisted fill step would prepare, using approved_fill_queue as
the single source. Raw valid_candidates are never read directly.
"""

from __future__ import annotations

import copy
from typing import Any


ACCEPT_VALID_REQUIRED_MESSAGE = "Run accept-valid first."

PREVIEW_SAFETY = {
    "real_site_operation": False,
    "auto_submit": False,
    "danger_buttons_clicked": [],
    "human_required_each_item": True,
}


def build_fill_preview(queue: dict[str, Any]) -> dict[str, Any]:
    approved = queue.get("approved_fill_queue")
    if not isinstance(approved, list):
        raise ValueError(ACCEPT_VALID_REQUIRED_MESSAGE)
    accepted_entries = [entry for entry in approved if entry.get("accepted_by_human") is True]
    if not accepted_entries:
        raise ValueError(ACCEPT_VALID_REQUIRED_MESSAGE)

    entries = [
        _preview_entry(index, entry)
        for index, entry in enumerate(accepted_entries, start=1)
    ]
    return {
        "mode": "fill_preview",
        "source": "approved_fill_queue",
        "count": len(entries),
        "entries": entries,
        "safety": dict(PREVIEW_SAFETY),
    }


def format_pretty_fill_preview(preview: dict[str, Any]) -> str:
    lines = [
        "Fill Preview",
        "",
        f"Source: {preview.get('source')}",
        f"Entries: {preview.get('count')}",
        "",
    ]
    for entry in preview.get("entries", []):
        lines.append(f"[{entry.get('index')}] {entry.get('action_summary')}")
        lines.append(f"    original: {entry.get('original_fragment')}")
    safety = preview.get("safety", {})
    lines += [
        "",
        "Safety:",
        f"- real_site_operation={str(safety.get('real_site_operation')).lower()}",
        f"- auto_submit={str(safety.get('auto_submit')).lower()}",
        f"- danger_buttons_clicked={safety.get('danger_buttons_clicked', [])}",
        f"- human_required_each_item={str(safety.get('human_required_each_item')).lower()}",
    ]
    return "\n".join(lines)


def _preview_entry(index: int, entry: dict[str, Any]) -> dict[str, Any]:
    bet_type = entry.get("bet_type")
    preview: dict[str, Any] = {
        "index": index,
        "source_index": entry.get("index"),
        "bet_type": bet_type,
        "original_fragment": entry.get("original_fragment"),
        "original_lines": list(entry.get("original_lines", [])),
    }
    if bet_type == "car":
        preview.update(
            {
                "number": entry.get("number"),
                "car_units": entry.get("car_units"),
                "money": entry.get("money"),
            }
        )
    elif bet_type == "column":
        preview.update(
            {
                "columns": copy.deepcopy(entry.get("columns")),
                "stars": list(entry.get("stars") or []),
                "unit": entry.get("unit"),
                "money": entry.get("money"),
                "star_amounts": copy.deepcopy(entry.get("star_amounts") or {}),
            }
        )
    else:
        preview.update(
            {
                "numbers": list(entry.get("numbers") or []),
                "stars": list(entry.get("stars") or []),
                "unit": entry.get("unit"),
                "money": entry.get("money"),
                "star_amounts": copy.deepcopy(entry.get("star_amounts") or {}),
            }
        )
    preview["action_summary"] = _action_summary(preview)
    return preview


def _action_summary(preview: dict[str, Any]) -> str:
    bet_type = preview.get("bet_type")
    if bet_type == "car":
        return (
            f"car: select number {preview.get('number')}; "
            f"car_units {preview.get('car_units')}; amount {preview.get('money')}"
        )
    if bet_type == "column":
        columns = preview.get("columns") or []
        column_text = " | ".join(
            ",".join(str(number) for number in column) for column in _column_number_lists(columns)
        )
        stars_text = "/".join(str(star) for star in preview.get("stars") or [])
        return (
            f"column: select columns {column_text}; fill {stars_text} stars; "
            f"{_amount_summary(preview)}"
        )
    numbers_text = ",".join(str(number) for number in preview.get("numbers") or [])
    stars_text = "/".join(str(star) for star in preview.get("stars") or [])
    return (
        f"normal: select numbers {numbers_text}; fill {stars_text} stars; "
        f"{_amount_summary(preview)}"
    )


def _amount_summary(preview: dict[str, Any]) -> str:
    star_amounts = preview.get("star_amounts") or {}
    money_values = {amount.get("money") for amount in star_amounts.values() if isinstance(amount, dict)}
    if star_amounts and len(money_values) > 1:
        parts = ", ".join(
            f"{star}星 {amount.get('money')}"
            for star, amount in sorted(star_amounts.items())
            if isinstance(amount, dict)
        )
        return f"amounts {parts}"
    return f"amount {preview.get('money')}"


def _column_number_lists(columns: Any) -> list[list[Any]]:
    result = []
    for column in columns or []:
        if isinstance(column, dict):
            result.append(list(column.get("numbers", [])))
        elif isinstance(column, list):
            result.append(list(column))
        else:
            result.append([column])
    return result
