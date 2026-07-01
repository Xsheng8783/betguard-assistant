from __future__ import annotations

from copy import deepcopy
from typing import Any

from betguard.formatter import format_bet_summary
from betguard.webfill.fill_plan import build_fill_plan


QUEUE_MODE = "batch_assisted_fill_queue"
READY_FOR_QUEUE = "READY_FOR_QUEUE"
BATCH_BLOCKED = "BATCH_BLOCKED"
WAITING_FOR_HUMAN_CONFIRM = "WAITING_FOR_HUMAN_CONFIRM"
COMPLETED = "COMPLETED"

PENDING = "PENDING"
READY_TO_FILL = "READY_TO_FILL"
DONE = "DONE"
BLOCKED = "BLOCKED"

FINAL_DECISION = {
    "real_site_auto_submit": False,
    "human_required_each_item": True,
}


def build_batch_queue(review_result: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    blocked_count = 0

    for position, item in enumerate(review_result.get("items", []), start=1):
        result = item.get("result", {})
        status = result.get("status")
        item_status = PENDING if status == "ok" else BLOCKED
        if item_status == BLOCKED:
            blocked_count += 1
        items.append(
            {
                "index": position,
                "line_no": item.get("line_no"),
                "original": item.get("raw", ""),
                "parsed_summary": item.get("summary") or format_bet_summary(result),
                "status": item_status,
                "review_result": result,
                "fill_plan": build_fill_plan(result) if status == "ok" else {},
                "warnings": list(result.get("warnings", [])),
                "errors": list(result.get("errors", [])),
            }
        )

    queue_status = BATCH_BLOCKED if blocked_count else READY_FOR_QUEUE
    queue = {
        "mode": QUEUE_MODE,
        "status": queue_status,
        "summary": {},
        "items": items,
        "final_decision": dict(FINAL_DECISION),
    }
    return _refresh_summary(queue)


def get_current_item(queue: dict[str, Any]) -> dict[str, Any] | None:
    if queue.get("status") in {BATCH_BLOCKED, COMPLETED, WAITING_FOR_HUMAN_CONFIRM}:
        return None
    for item in queue.get("items", []):
        if item.get("status") in {PENDING, READY_TO_FILL}:
            return item
    return None


def mark_item_waiting_for_human(queue: dict[str, Any], item_index: int) -> dict[str, Any]:
    updated = deepcopy(queue)
    if updated.get("status") == BATCH_BLOCKED:
        raise ValueError("batch is blocked")
    if _find_item(updated, status=WAITING_FOR_HUMAN_CONFIRM) is not None:
        raise ValueError("another item is already waiting for human confirmation")

    item = _find_item(updated, item_index=item_index)
    if item is None:
        raise ValueError(f"item {item_index} not found")
    if item.get("status") not in {PENDING, READY_TO_FILL}:
        raise ValueError(f"item {item_index} is not ready to fill")

    item["status"] = WAITING_FOR_HUMAN_CONFIRM
    updated["status"] = WAITING_FOR_HUMAN_CONFIRM
    return _refresh_summary(updated)


def mark_item_done_by_human(queue: dict[str, Any], item_index: int) -> dict[str, Any]:
    updated = deepcopy(queue)
    item = _find_item(updated, item_index=item_index)
    if item is None:
        raise ValueError(f"item {item_index} not found")
    if item.get("status") != WAITING_FOR_HUMAN_CONFIRM:
        raise ValueError("item must be WAITING_FOR_HUMAN_CONFIRM before DONE")

    item["status"] = DONE
    if any(existing.get("status") in {PENDING, READY_TO_FILL} for existing in updated.get("items", [])):
        updated["status"] = READY_FOR_QUEUE
    else:
        updated["status"] = COMPLETED
    return _refresh_summary(updated)


def format_pretty_batch_queue(queue: dict[str, Any]) -> str:
    summary = queue.get("summary", {})
    lines = [
        "Batch Assisted Fill Queue",
        "",
        f"Status: {queue.get('status')}",
        "",
        "Summary:",
        f"- total: {summary.get('total', 0)}",
        f"- ok: {summary.get('ok', 0)}",
        f"- blocked: {summary.get('blocked', 0)}",
        f"- current: {summary.get('current_index', 0)}",
        f"- remaining: {summary.get('remaining', 0)}",
        "",
        "Items:",
    ]
    for item in queue.get("items", []):
        lines.extend(
            [
                f"[{item.get('index')}] {item.get('status')}",
                f"    {item.get('original', '')}",
                f"    {item.get('parsed_summary', '')}",
            ]
        )
        for error in item.get("errors", []):
            lines.append(f"    - {error}")
        for warning in item.get("warnings", []):
            lines.append(f"    - {warning}")

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
    ok = len(items) - blocked
    remaining = sum(
        1
        for item in items
        if item.get("status") in {PENDING, READY_TO_FILL, WAITING_FOR_HUMAN_CONFIRM}
    )
    queue["summary"] = {
        "total": len(items),
        "ok": ok,
        "blocked": blocked,
        "current_index": _current_index(queue),
        "remaining": remaining,
    }
    queue["final_decision"] = dict(FINAL_DECISION)
    return queue


def _current_index(queue: dict[str, Any]) -> int:
    if queue.get("status") == BATCH_BLOCKED:
        return 0
    waiting = _find_item(queue, status=WAITING_FOR_HUMAN_CONFIRM)
    if waiting is not None:
        return int(waiting.get("index", 0))
    current = get_current_item(queue)
    return int(current.get("index", 0)) if current else 0


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
