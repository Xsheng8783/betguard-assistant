from __future__ import annotations

from copy import deepcopy
from typing import Any

from betguard.formatter import format_bet_summary
from betguard.webfill.fill_plan import build_fill_plan


QUEUE_MODE = "batch_assisted_fill_queue"
READY = "READY"
READY_FOR_QUEUE = READY
BATCH_BLOCKED = "BATCH_BLOCKED"
WAITING_FOR_HUMAN_CONFIRM = "WAITING_FOR_HUMAN_CONFIRM"
COMPLETED = "COMPLETED"

CURRENT = "CURRENT"
PENDING = "PENDING"
READY_TO_FILL = CURRENT
DONE = "DONE"
BLOCKED = "BLOCKED"

BLOCKED_REASON = "review result is not ok"
FINAL_DECISION = {
    "real_site_auto_submit": False,
    "human_required_each_item": True,
}


def build_batch_queue(review_result: dict[str, Any]) -> dict[str, Any]:
    can_continue = bool(review_result.get("can_continue"))
    items: list[dict[str, Any]] = []

    for index, item in enumerate(review_result.get("items", [])):
        result = item.get("result", {})
        summary = item.get("summary") or format_bet_summary(result)
        item_status = BLOCKED if not can_continue or result.get("status") != "ok" else PENDING
        if can_continue and index == 0:
            item_status = CURRENT
        items.append(
            {
                "index": index,
                "line_no": item.get("line_no"),
                "status": item_status,
                "original_text": item.get("raw", ""),
                "summary": summary,
                "parsed": result,
                "original": item.get("raw", ""),
                "parsed_summary": summary,
                "review_result": result,
                "fill_plan": build_fill_plan(result) if result.get("status") == "ok" else {},
                "warnings": list(result.get("warnings", [])),
                "errors": list(result.get("errors", [])),
            }
        )

    queue = {
        "mode": QUEUE_MODE,
        "status": READY if can_continue else BATCH_BLOCKED,
        "current_index": 0 if can_continue and items else None,
        "total": len(items),
        "done_count": 0,
        "items": items,
        "summary": {},
        "final_decision": dict(FINAL_DECISION),
    }
    if not can_continue:
        queue["reason"] = BLOCKED_REASON
    return _refresh_summary(queue)


def get_current_item(queue: dict[str, Any]) -> dict[str, Any] | None:
    if queue.get("status") != READY:
        return None
    return _find_item(queue, status=CURRENT)


def mark_current_waiting_for_human(queue: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(queue)
    if updated.get("status") != READY:
        raise ValueError("queue status must be READY")

    item = _find_item(updated, status=CURRENT)
    if item is None:
        raise ValueError("current item not found")

    item["status"] = WAITING_FOR_HUMAN_CONFIRM
    updated["status"] = WAITING_FOR_HUMAN_CONFIRM
    updated["current_index"] = item.get("index")
    return _refresh_summary(updated)


def mark_current_done_by_human(queue: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(queue)
    item = _find_item(updated, status=WAITING_FOR_HUMAN_CONFIRM)
    if item is None:
        raise ValueError("current item must be WAITING_FOR_HUMAN_CONFIRM before DONE")

    item["status"] = DONE
    # Append-only history write: this is the ONLY path that records
    # to runs/history/orders.jsonl.  Auto-batch fill (batch_fill_all)
    # sets item["status"] = "DONE" directly and is intentionally
    # skipped here.  See batch_fill_all._fill_loop for the comment.
    try:
        from betguard.webfill import history as _history
        _history.record_human_done(updated, item)
    except ImportError:
        # history module not available; skip silently (older builds)
        pass
    next_item = _next_pending_item(updated, int(item.get("index", -1)))
    if next_item is None:
        updated["status"] = COMPLETED
        updated["current_index"] = None
    else:
        next_item["status"] = CURRENT
        updated["status"] = READY
        updated["current_index"] = next_item.get("index")
    return _refresh_summary(updated)


def reset_batch_queue(queue: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(queue)
    if updated.get("status") == BATCH_BLOCKED:
        raise ValueError("batch is blocked")

    for index, item in enumerate(updated.get("items", [])):
        item["status"] = CURRENT if index == 0 else PENDING
    updated["status"] = READY if updated.get("items") else COMPLETED
    updated["current_index"] = 0 if updated.get("items") else None
    return _refresh_summary(updated)


def mark_item_waiting_for_human(queue: dict[str, Any], item_index: int) -> dict[str, Any]:
    current = get_current_item(queue)
    if current is None:
        raise ValueError("current item not found")
    if current.get("index") != item_index:
        raise ValueError("cannot skip current item")
    return mark_current_waiting_for_human(queue)


def mark_item_done_by_human(queue: dict[str, Any], item_index: int) -> dict[str, Any]:
    waiting = _find_item(queue, status=WAITING_FOR_HUMAN_CONFIRM)
    if waiting is None:
        raise ValueError("item must be WAITING_FOR_HUMAN_CONFIRM before DONE")
    if waiting.get("index") != item_index:
        raise ValueError("cannot skip current item")
    return mark_current_done_by_human(queue)


def format_pretty_batch_queue(queue: dict[str, Any]) -> str:
    if queue.get("status") == BATCH_BLOCKED:
        return "\n".join(
            [
                "Batch Queue BLOCKED",
                f"Reason: {queue.get('reason') or BLOCKED_REASON}",
            ]
        )

    lines = [
        f"Batch Queue {queue.get('status')}",
        f"Total: {queue.get('total', 0)}",
        f"Current: {_current_display(queue)}",
        "",
    ]
    for item in queue.get("items", []):
        display_index = int(item.get("index", 0)) + 1
        lines.append(f"[{display_index}] {item.get('status')} {item.get('summary', '')}")

    lines.extend(
        [
            "",
            "Safety:",
            "- Batch must be all OK before assisted fill",
            "- Each item stops before confirm",
            "- Human confirmation required per item",
            "- Auto submit: false",
        ]
    )
    return "\n".join(lines)


def _refresh_summary(queue: dict[str, Any]) -> dict[str, Any]:
    items = list(queue.get("items", []))
    blocked = sum(1 for item in items if item.get("status") == BLOCKED)
    done_count = sum(1 for item in items if item.get("status") == DONE)
    remaining = sum(
        1
        for item in items
        if item.get("status") in {CURRENT, PENDING, WAITING_FOR_HUMAN_CONFIRM}
    )
    queue["total"] = len(items)
    queue["done_count"] = done_count
    queue["current_index"] = _current_index(queue)
    queue["summary"] = {
        "total": len(items),
        "ok": len(items) - blocked,
        "blocked": blocked,
        "current_index": _legacy_current_index(queue),
        "remaining": remaining,
    }
    queue["final_decision"] = dict(FINAL_DECISION)
    return queue


def _current_index(queue: dict[str, Any]) -> int | None:
    for status in (CURRENT, WAITING_FOR_HUMAN_CONFIRM):
        item = _find_item(queue, status=status)
        if item is not None:
            return int(item.get("index", 0))
    return None


def _legacy_current_index(queue: dict[str, Any]) -> int:
    current = _current_index(queue)
    return int(current) + 1 if current is not None else 0


def _current_display(queue: dict[str, Any]) -> str:
    current = _current_index(queue)
    total = int(queue.get("total", 0))
    if current is None or total == 0:
        return "-"
    return f"{current + 1}/{total}"


def _next_pending_item(queue: dict[str, Any], after_index: int) -> dict[str, Any] | None:
    for item in queue.get("items", []):
        if int(item.get("index", -1)) > after_index and item.get("status") == PENDING:
            return item
    return None


def _find_item(
    queue: dict[str, Any],
    *,
    item_index: int | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    for item in queue.get("items", []):
        if item_index is not None and item.get("index") != item_index:
            continue
        if status is not None and item.get("status") != status:
            continue
        return item
    return None
