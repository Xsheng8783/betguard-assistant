"""Append-only order history (v1).

This module records orders that have been **manually confirmed DONE**
by a human operator.  It does NOT record auto-batch completion (e.g.
``--real-site-assisted-fill-all``), because the policy is:

    history v1 only records "human-confirmed per item DONE".
    Auto-batch fill ends an item as DONE without per-item human
    confirmation, so it is excluded from the official history.

Storage: ``runs/history/orders.jsonl`` (append-only JSONL).

Safety: this module is read-only from the perspective of triggering any
fill / submit / accept action.  The /history web route only ever reads
the file; it never offers a "fill" or "submit" button.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HISTORY_DIR = PROJECT_ROOT / "runs" / "history"
HISTORY_FILE = HISTORY_DIR / "orders.jsonl"

HARD_CODED_SAFETY_FLAGS = {
    "auto_submit": False,
    "auto_confirm": False,
    "auto_next": False,
    "danger_clicked": 0,
}

SAFETY_REMINDER = "系統只輔助填入，不會送出或確認"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_history_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _compute_history_id(
    queue_path: str | None,
    item_index: Any,
    original_text: str,
    completed_at: str,
) -> str:
    """Stable history_id = sha256(queue_path + item.index + original_text + completed_at)."""
    h = hashlib.sha256()
    h.update(str(queue_path or "").encode("utf-8"))
    h.update(b"|")
    h.update(str(item_index if item_index is not None else "").encode("utf-8"))
    h.update(b"|")
    h.update((original_text or "").encode("utf-8"))
    h.update(b"|")
    h.update((completed_at or "").encode("utf-8"))
    return "h-" + h.hexdigest()[:16]


def _coalesce_str(value: Any, default: str = "未指定") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def _build_record(
    queue: dict[str, Any],
    item: dict[str, Any],
    queue_path: str | Path | None,
    run_folder: str | Path | None,
) -> dict[str, Any]:
    """Build a history record dict.  All safety flags are hard-coded;
    they are NEVER read from queue/item."""
    item_index = item.get("index")
    original_text = _coalesce_str(
        item.get("original_text")
        or item.get("raw")
        or item.get("summary")
        or item.get("text")
        or "",
        default="",
    )
    completed_at = _coalesce_str(
        item.get("fill_completed_at")
        or item.get("completed_at")
        or item.get("done_at")
        or _now_iso(),
    )
    source = _coalesce_str(
        queue.get("source")
        or (queue.get("audit") or {}).get("source")
        or item.get("source"),
        default="未指定",
    )
    run_folder_str = (
        str(run_folder) if run_folder else ""
    )
    if not run_folder_str and queue_path:
        try:
            qp = Path(queue_path)
            if qp.parent.name and qp.parent.name != "history":
                run_folder_str = str(qp.parent)
        except Exception:
            run_folder_str = ""

    record: dict[str, Any] = {
        "history_id": _compute_history_id(
            str(queue_path) if queue_path else None,
            item_index,
            original_text,
            completed_at,
        ),
        "created_at": _now_iso(),
        "completed_at": completed_at,
        "source": source,
        "game": _coalesce_str(item.get("game") or queue.get("game"), default="未指定"),
        "play_type": _coalesce_str(
            item.get("play_type") or item.get("type"), default="未指定"
        ),
        "original_text": original_text,
        "numbers": list(item.get("numbers") or []),
        "columns": list(item.get("columns") or []) or None,
        "stars": list(item.get("stars") or []) or None,
        "amounts": dict(item.get("bets") or item.get("amounts") or {}),
        "unit": item.get("unit"),
        "money_total": item.get("money"),
        "queue_item_index": item_index,
        "status": "DONE",
        "safety_flags": dict(HARD_CODED_SAFETY_FLAGS),
        "queue_path": str(queue_path) if queue_path else None,
        "run_folder": run_folder_str or None,
        "confirmed_by_human": True,
    }
    # Normalize: drop None-valued columns if the list is empty
    if record["columns"] is None:
        record.pop("columns")
    if record["stars"] is None:
        record.pop("stars")
    return record


def _is_duplicate(record: dict[str, Any]) -> bool:
    """Return True if a record with the same history_id already exists."""
    if not HISTORY_FILE.exists():
        return False
    target_id = record.get("history_id")
    if not target_id:
        return False
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    other = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if other.get("history_id") == target_id:
                    return True
    except OSError:
        return False
    return False


def _append_record(record: dict[str, Any]) -> None:
    _ensure_history_dir()
    # Write to a temp file, then append+rename is not strictly atomic for
    # append-only JSONL; we use plain append, which is safe for single
    # writer.  Multi-writer is out of scope for v1.
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_human_done(
    queue: dict[str, Any],
    item: dict[str, Any],
    *,
    queue_path: str | Path | None = None,
    run_folder: str | Path | None = None,
) -> dict[str, Any] | None:
    """Record a single item as human-confirmed DONE.

    Returns the appended record, or None if:
      * the item is not in DONE state
      * a record with the same history_id already exists (idempotent)
    """
    if item.get("status") != "DONE":
        return None
    record = _build_record(queue, item, queue_path, run_folder)
    if _is_duplicate(record):
        return None
    try:
        _append_record(record)
    except OSError as exc:
        # Log to stderr but do NOT raise: the existing DONE transition
        # in batch_queue must continue to succeed even if the history
        # write fails (disk full, permission denied, etc.).  Operators
        # can backfill from queue audit later.
        print(
            f"[history] WARNING: failed to append to {HISTORY_FILE}: {exc}",
            file=sys.stderr,
        )
        return None
    # Tag the queue item so we can de-dupe on repeat calls without
    # scanning the JSONL each time.
    item["history_id"] = record["history_id"]
    item["history_recorded_at"] = record["created_at"]
    return record


def load_all_records() -> list[dict[str, Any]]:
    """Load all history records, skipping malformed lines (best effort)."""
    if not HISTORY_FILE.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def filter_records(
    records: list[dict[str, Any]],
    *,
    query: str | None = None,
    number: int | str | None = None,
    game: str | None = None,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """Return records matching all of the given filters (AND semantics)."""
    q = (query or "").strip()
    n = str(number) if number is not None else ""
    g = (game or "").strip()
    d = (date or "").strip()
    out: list[dict[str, Any]] = []
    for r in records:
        if q and q not in str(r.get("original_text", "")):
            continue
        if n:
            numbers = r.get("numbers") or []
            if n not in [str(x) for x in numbers]:
                continue
        if g and str(r.get("game", "")) != g:
            continue
        if d:
            ca = str(r.get("completed_at", ""))[:10]
            if ca != d:
                continue
        out.append(r)
    return out


def summarize_records_for_date(
    records: list[dict[str, Any]],
    *,
    date: str,
    needs_review_count: int = 0,
    invalid_count: int = 0,
    watchlist_count: int = 0,
    expected_total: int | None = None,
) -> dict[str, Any]:
    """Build a read-only daily check summary from local history records."""
    today_records = filter_records(records, date=date)
    assisted_count = sum(1 for record in today_records if record.get("status") == "DONE")
    total = expected_total if expected_total is not None else len(today_records)
    return {
        "date": date,
        "total_count": total,
        "assisted_count": assisted_count,
        "unprocessed_count": max(total - assisted_count, 0),
        "needs_review_count": int(needs_review_count),
        "invalid_count": int(invalid_count),
        "watchlist_count": int(watchlist_count),
        "safety_reminder": SAFETY_REMINDER,
    }


def export_records_csv(records: list[dict[str, Any]]) -> str:
    """Return a human-checkable CSV export for local history records."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "日期",
        "時間",
        "序號",
        "原始片段",
        "號碼",
        "二星金額",
        "三星金額",
        "四星金額",
        "狀態",
        "人工核對提醒",
    ])
    for record in records:
        completed_at = str(record.get("completed_at") or record.get("created_at") or "")
        amounts = record.get("amounts") or {}
        numbers = " ".join(_format_number(number) for number in record.get("numbers") or [])
        writer.writerow([
            completed_at[:10],
            completed_at[11:19] if len(completed_at) >= 19 else "",
            record.get("queue_item_index", ""),
            record.get("original_text", ""),
            numbers,
            amounts.get("2", ""),
            amounts.get("3", ""),
            amounts.get("4", ""),
            record.get("status", ""),
            SAFETY_REMINDER,
        ])
    return output.getvalue()


def _format_number(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:02d}" if 0 <= number < 10 else str(number)


__all__ = [
    "HISTORY_DIR",
    "HISTORY_FILE",
    "HARD_CODED_SAFETY_FLAGS",
    "record_human_done",
    "load_all_records",
    "filter_records",
    "summarize_records_for_date",
    "export_records_csv",
]
