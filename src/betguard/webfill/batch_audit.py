from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SAFETY = {
    "real_site_operation": False,
    "auto_submit": False,
    "danger_buttons_clicked": [],
}


def ensure_batch_audit(queue: dict[str, Any]) -> dict[str, Any]:
    audit = queue.get("audit")
    if not isinstance(audit, dict):
        audit = _new_audit(queue)
        queue["audit"] = audit
    sync_batch_audit(queue)
    return audit


def sync_batch_audit(queue: dict[str, Any]) -> dict[str, Any]:
    audit = queue.setdefault("audit", _new_audit(queue))
    audit.setdefault("batch_id", f"batch_{uuid4().hex}")
    audit.setdefault("created_at", _now_iso())
    audit["updated_at"] = _now_iso()
    audit["original_text"] = queue.get("raw_text", audit.get("original_text", ""))
    audit["preprocessing"] = _preprocessing_audit(queue)
    audit["review"] = _review_audit(queue, audit)
    audit["items"] = [_item_audit(item) for item in queue.get("items", [])]
    audit["approved_fill_queue"] = _approved_fill_audit(queue, audit)
    audit["safety"] = _safety_audit(queue)
    audit["queue_status"] = queue.get("status")
    return audit


def record_review_action(
    queue: dict[str, Any],
    action: str,
    *,
    accepted_valid_count: int | None = None,
    rejected_reason: str | None = None,
) -> dict[str, Any]:
    audit = ensure_batch_audit(queue)
    review = audit.setdefault("review", {})
    review["action"] = action
    if action == "accept_valid":
        review["accepted_at"] = _now_iso()
        review["accepted_valid_count"] = int(accepted_valid_count or 0)
    if action == "reject":
        review["rejected_at"] = _now_iso()
        review["rejected_reason"] = rejected_reason or "batch review rejected by user"
    return sync_batch_audit(queue)


def export_batch_audit(queue: dict[str, Any], path: str | Path) -> dict[str, Any]:
    audit = sync_batch_audit(queue)
    Path(path).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def format_pretty_audit_summary(queue_or_audit: dict[str, Any]) -> str:
    audit = queue_or_audit.get("audit") if "audit" in queue_or_audit else queue_or_audit
    audit = audit or {}
    preprocessing = audit.get("preprocessing", {})
    review = audit.get("review", {})
    safety = audit.get("safety", {})
    lines = [
        "Audit Summary",
        f"- batch_id: {audit.get('batch_id')}",
        f"- created_at: {audit.get('created_at')}",
        f"- original_text: {'yes' if audit.get('original_text') else 'no'}",
        f"- preprocessing status: {preprocessing.get('status')}",
        f"- review action: {review.get('action') or 'none'}",
        f"- item count: {len(audit.get('items', []))}",
        f"- invalid fragments: {preprocessing.get('invalid_count', 0)}",
        f"- current queue status: {audit.get('queue_status') or review.get('status')}",
        f"- approved_fill_queue_count: {audit.get('approved_fill_queue', {}).get('approved_fill_queue_count', 0)}",
        f"- real_site_operation: {str(safety.get('real_site_operation')).lower()}",
        f"- auto_submit: {str(safety.get('auto_submit')).lower()}",
        f"- danger_buttons_clicked: {safety.get('danger_buttons_clicked', [])}",
    ]
    return "\n".join(lines)


def _new_audit(queue: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    return {
        "batch_id": f"batch_{uuid4().hex}",
        "created_at": now,
        "updated_at": now,
        "original_text": queue.get("raw_text", ""),
        "preprocessing": {},
        "review": {},
        "items": [],
        "safety": dict(SAFETY),
    }


def _preprocessing_audit(queue: dict[str, Any]) -> dict[str, Any]:
    preprocessing = queue.get("preprocessing", {})
    summary = preprocessing.get("summary", {})
    original_audit = preprocessing.get("original_review_audit", {})
    invalid_fragments = list(preprocessing.get("invalid_fragments", []))
    if not invalid_fragments and original_audit:
        invalid_fragments = list(original_audit.get("invalid_fragments", []))
    ignored_metadata = list(preprocessing.get("ignored_metadata_lines", []))
    if not ignored_metadata and original_audit:
        ignored_metadata = list(original_audit.get("ignored_metadata_lines", []))
    return {
        "status": preprocessing.get("status") or queue.get("preprocessing_status"),
        "candidate_count": int(summary.get("candidate_count", len(queue.get("items", [])))),
        "valid_count": int(summary.get("valid_count", queue.get("summary", {}).get("ok", 0))),
        "invalid_count": max(int(summary.get("invalid_unsupported_count", 0)), len(invalid_fragments)),
        "ignored_metadata_count": int(summary.get("ignored_metadata_count", len(ignored_metadata))),
        "warnings_count": int(summary.get("warnings_count", 0)),
        "ignored_metadata_lines": ignored_metadata,
        "invalid_fragments": invalid_fragments,
    }


def _review_audit(queue: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    existing = dict(audit.get("review", {}))
    existing.setdefault("action", queue.get("review_action"))
    existing["status"] = queue.get("status")
    existing.setdefault("accepted_at", None)
    existing.setdefault("rejected_at", None)
    existing.setdefault("accepted_valid_count", 0)
    existing.setdefault("rejected_reason", None)
    return existing


def _approved_fill_audit(queue: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    entries = queue.get("approved_fill_queue")
    entries = entries if isinstance(entries, list) else []
    human_accepted = [entry for entry in entries if entry.get("accepted_by_human") is True]
    review = audit.get("review", {})
    preprocessing = audit.get("preprocessing", {})
    return {
        "approved_fill_queue_count": len(entries),
        "human_accepted_count": len(human_accepted),
        "accepted_valid_count": int(review.get("accepted_valid_count", 0) or 0),
        "excluded_invalid_count": int(preprocessing.get("invalid_count", 0) or 0),
        "human_accepted_only": len(human_accepted) == len(entries),
        "real_site_operation": False,
        "auto_submit": False,
    }


def _item_audit(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": item.get("index"),
        "original_fragment": item.get("original_fragment") or item.get("original"),
        "original_line": item.get("original_line"),
        "original_lines": list(item.get("original_lines", [])),
        "preprocessing_notes": list(item.get("preprocessing_notes", [])),
        "bet_type": item.get("bet_type") or item.get("review_result", {}).get("type"),
        "parsed_summary": item.get("parsed_summary"),
        "star_amounts": _star_amounts(item.get("review_result", {})),
        "status": item.get("status"),
        "approved_source": dict(item["approved_source"]) if isinstance(item.get("approved_source"), dict) else None,
        "mock_result_summary": _mock_result_summary(item),
        "human_confirmation_status": _human_status(item),
    }


def _mock_result_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_numbers": list(item.get("selected_numbers", [])),
        "selected_columns": list(item.get("selected_columns", [])),
        "selected_car_number": item.get("selected_car_number"),
        "car_units": item.get("car_units"),
        "filled_amount": item.get("filled_amount"),
        "filled_amounts": dict(item.get("filled_amounts", {})),
        "danger_buttons_detected": list(item.get("danger_buttons_detected", [])),
        "danger_buttons_clicked": list(item.get("danger_buttons_clicked", [])),
        "errors": list(item.get("errors", [])),
        "warnings": list(item.get("warnings", [])),
    }


def _star_amounts(result: dict[str, Any]) -> dict[str, Any]:
    bets = result.get("bets")
    if not isinstance(bets, dict):
        return {}
    return {
        str(star): dict(amount)
        for star, amount in bets.items()
        if isinstance(amount, dict)
    }


def _human_status(item: dict[str, Any]) -> str:
    status = item.get("status")
    if status == "WAITING_FOR_HUMAN_CONFIRM":
        return "waiting_for_human_confirm"
    if status == "DONE":
        return "confirmed_done_by_human"
    if status == "PENDING":
        return "not_started"
    if status == "BLOCKED":
        return "blocked"
    if status == "MOCK_FILL_FAILED":
        return "mock_fill_failed"
    return str(status or "unknown")


def _safety_audit(queue: dict[str, Any]) -> dict[str, Any]:
    clicked = [
        click
        for item in queue.get("items", [])
        for click in item.get("danger_buttons_clicked", [])
    ]
    return {
        "real_site_operation": False,
        "auto_submit": False,
        "danger_buttons_clicked": clicked,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
