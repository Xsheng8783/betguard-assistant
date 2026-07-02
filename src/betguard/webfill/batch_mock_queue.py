from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from betguard.formatter import attach_summaries
from betguard.input_preprocessor import preprocess_batch_input
from betguard.review import review_lines, review_text
from betguard.webfill.assisted_fill_mock import build_mock_fill_report
from betguard.webfill.batch_audit import ensure_batch_audit, format_pretty_audit_summary, record_review_action, sync_batch_audit


READY_FOR_QUEUE = "READY_FOR_QUEUE"
BATCH_BLOCKED = "BATCH_BLOCKED"
NEEDS_REVIEW = "NEEDS_REVIEW"
REJECTED = "REJECTED"
WAITING_FOR_HUMAN_CONFIRM = "WAITING_FOR_HUMAN_CONFIRM"
MOCK_FILL_FAILED = "MOCK_FILL_FAILED"
COMPLETED = "COMPLETED"
PENDING = "PENDING"
DONE = "DONE"
BLOCKED = "BLOCKED"

FINAL_DECISION = {
    "real_site_operation": False,
    "auto_submit": False,
    "human_required_each_item": True,
}


def build_batch_mock_queue(text_or_lines: str | Iterable[str]) -> dict[str, Any]:
    raw_text = "\n".join(text_or_lines) if not isinstance(text_or_lines, str) else text_or_lines
    preprocessing = preprocess_batch_input(text_or_lines)
    candidates = list(preprocessing.get("candidate_bet_lines", []))
    review_result = _review_candidates(candidates)
    can_continue = bool(review_result.get("can_continue"))
    items = []
    for index, review_item in enumerate(review_result.get("items", []), start=1):
        candidate = candidates[index - 1] if index - 1 < len(candidates) else {}
        result = _apply_preprocessing_warnings(review_item.get("result", {}), candidate)
        review_item["result"] = result
        if result.get("status") == "warning":
            review_item["summary"] = review_item.get("summary", "")
        status = PENDING if can_continue else BLOCKED
        items.append(
            {
                "index": index,
                "original": review_item.get("raw", ""),
                "original_fragment": review_item.get("raw", ""),
                "original_line": candidate.get("original_line"),
                "original_lines": list(candidate.get("original_lines", [])),
                "source_line_no": candidate.get("line_no"),
                "fragment_index": candidate.get("fragment_index"),
                "preprocessing_notes": list(candidate.get("preprocessing_notes", [])),
                "parsed_summary": review_item.get("summary", ""),
                "status": status,
                "selected_numbers": [],
                "selected_columns": [],
                "selected_car_number": None,
                "car_units": None,
                "filled_amount": None,
                "filled_amounts": {},
                "danger_buttons_detected": [],
                "danger_buttons_clicked": [],
                "review_result": result,
                "warnings": list(result.get("warnings", [])),
                "errors": list(result.get("errors", [])),
            }
        )

    blocked = 0 if can_continue else len(items)
    preprocessing_summary = dict(preprocessing.get("summary", {}))
    invalid_count = int(review_result.get("error", 0)) + int(review_result.get("warning", 0))
    valid_count = int(review_result.get("ok", 0))
    preprocessing_status = _preprocessing_status(valid_count, invalid_count, len(items))
    preprocessing_summary["valid_count"] = valid_count
    preprocessing_summary["invalid_unsupported_count"] = invalid_count
    preprocessing_summary["warnings_count"] = int(review_result.get("warning", 0))
    preprocessing["valid_candidates"] = _candidate_review_items(review_result, candidates, valid=True)
    preprocessing["invalid_fragments"] = _candidate_review_items(review_result, candidates, valid=False)
    preprocessing["status"] = preprocessing_status
    queue_status = _queue_status_for_preprocessing(preprocessing_status)
    can_build_ready_queue = preprocessing_status == "READY"
    blocked = 0 if can_build_ready_queue else invalid_count
    queue = {
        "mode": "batch_mock_assisted_fill_queue",
        "status": queue_status,
        "preprocessing_status": preprocessing_status,
        "summary": {
            "total": len(items),
            "ok": valid_count,
            "blocked": blocked,
            "failed": 0,
            "done": 0,
            "waiting": 0,
            "pending": len(items) if can_build_ready_queue else 0,
            "current_index": 1 if can_build_ready_queue and items else 0,
            "remaining": len(items) if can_build_ready_queue else 0,
            "candidate_count": preprocessing_summary.get("candidate_count", len(items)),
            "ignored_metadata_count": preprocessing_summary.get("ignored_metadata_count", 0),
            "invalid_unsupported_count": preprocessing_summary.get("invalid_unsupported_count", 0),
            "valid_count": valid_count,
            "warnings_count": preprocessing_summary.get("warnings_count", 0),
        },
        "items": items,
        "raw_text": raw_text,
        "preprocessing": {
            **preprocessing,
            "summary": preprocessing_summary,
        },
        "review_result": review_result,
        "final_decision": dict(FINAL_DECISION),
        "warnings": _queue_warnings_for_status(preprocessing_status),
        "errors": _queue_errors_for_status(preprocessing_status),
    }
    ensure_batch_audit(queue)
    return queue


def run_current_mock_queue_item(queue: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(queue)
    if updated.get("status") not in {READY_FOR_QUEUE}:
        raise ValueError("queue is not ready for current mock fill")

    item = _first_item_with_status(updated, PENDING)
    if item is None:
        raise ValueError("no pending item available")

    report = build_mock_fill_report(str(item.get("original") or ""))
    if report.get("status") != "COMPLETED_MOCK_ONLY":
        item["status"] = MOCK_FILL_FAILED
        item["errors"] = [_mock_fill_failure_reason(item, report)]
        item["danger_buttons_clicked"] = list(report.get("danger_buttons_clicked", []))
        updated["status"] = MOCK_FILL_FAILED
        _refresh_summary(updated)
        sync_batch_audit(updated)
        return updated

    item["status"] = WAITING_FOR_HUMAN_CONFIRM
    item["bet_type"] = report.get("bet_type") or item.get("review_result", {}).get("type")
    item["selected_numbers"] = list(report.get("selected_numbers", []))
    item["selected_columns"] = list(report.get("selected_columns", []))
    item["selected_car_number"] = report.get("selected_car_number")
    item["car_units"] = report.get("car_units")
    item["filled_amount"] = report.get("filled_amount")
    item["filled_amounts"] = dict(report.get("filled_amounts", {}))
    item["danger_buttons_detected"] = list(report.get("danger_buttons_detected", []))
    item["danger_buttons_clicked"] = list(report.get("danger_buttons_clicked", []))
    item["warnings"] = list(report.get("warnings", []))
    item["errors"] = list(report.get("errors", []))
    updated["status"] = WAITING_FOR_HUMAN_CONFIRM
    _refresh_summary(updated)
    sync_batch_audit(updated)
    return updated


def mark_current_item_done_by_human(queue: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(queue)
    waiting_items = [item for item in updated.get("items", []) if item.get("status") == WAITING_FOR_HUMAN_CONFIRM]
    if not waiting_items:
        raise ValueError("no item is waiting for human confirmation")
    if len(waiting_items) > 1:
        raise ValueError("queue has multiple waiting items")

    item = waiting_items[0]
    item["status"] = DONE
    if _first_item_with_status(updated, PENDING) is None:
        updated["status"] = COMPLETED
    else:
        updated["status"] = READY_FOR_QUEUE
    _refresh_summary(updated)
    sync_batch_audit(updated)
    return updated


def advance_queue_after_human_confirm(queue: dict[str, Any]) -> dict[str, Any]:
    if queue.get("status") == MOCK_FILL_FAILED:
        raise ValueError("queue is stopped at MOCK_FILL_FAILED; fix or skip is not implemented yet")
    if queue.get("status") == READY_FOR_QUEUE and not any(
        item.get("status") == WAITING_FOR_HUMAN_CONFIRM for item in queue.get("items", [])
    ):
        return run_current_mock_queue_item(queue)
    updated = mark_current_item_done_by_human(queue)
    if updated.get("status") == READY_FOR_QUEUE:
        return run_current_mock_queue_item(updated)
    _refresh_summary(updated)
    sync_batch_audit(updated)
    return updated


def accept_valid_candidates_for_mock_queue(queue: dict[str, Any], *, run_first: bool = False) -> dict[str, Any]:
    if queue.get("status") != NEEDS_REVIEW:
        raise ValueError("batch review accept is only allowed when status is NEEDS_REVIEW")
    valid_candidates = list(queue.get("preprocessing", {}).get("valid_candidates", []))
    if not valid_candidates:
        raise ValueError("no valid candidates available to accept")

    accepted = build_batch_mock_queue([str(candidate.get("raw", "")) for candidate in valid_candidates])
    accepted["review_action"] = "accept_valid"
    accepted["accepted_from_queue_status"] = NEEDS_REVIEW
    accepted["preprocessing"]["original_review_audit"] = {
        "status": queue.get("status"),
        "preprocessing_status": queue.get("preprocessing_status"),
        "invalid_fragments": list(queue.get("preprocessing", {}).get("invalid_fragments", [])),
        "ignored_metadata_lines": list(queue.get("preprocessing", {}).get("ignored_metadata_lines", [])),
        "original_text": queue.get("raw_text", ""),
    }
    accepted["warnings"] = ["accepted valid candidates after manual review"]
    record_review_action(accepted, "accept_valid", accepted_valid_count=len(valid_candidates))
    if run_first and accepted.get("status") == READY_FOR_QUEUE:
        accepted = run_current_mock_queue_item(accepted)
        accepted["review_action"] = "accept_valid"
        record_review_action(accepted, "accept_valid", accepted_valid_count=len(valid_candidates))
    return accepted


def reject_batch_review(queue: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(queue)
    updated["status"] = REJECTED
    updated["review_action"] = "reject"
    updated["warnings"] = []
    updated["errors"] = ["batch review rejected by user"]
    for item in updated.get("items", []):
        if item.get("status") not in {DONE, WAITING_FOR_HUMAN_CONFIRM}:
            item["status"] = BLOCKED
    _refresh_summary(updated)
    updated["status"] = REJECTED
    updated["summary"]["pending"] = 0
    updated["summary"]["remaining"] = 0
    record_review_action(updated, "reject", rejected_reason="batch review rejected by user")
    return updated


def save_queue_state(queue: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def load_queue_state(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_pretty_batch_mock_queue(queue: dict[str, Any]) -> str:
    summary = queue.get("summary", {})
    current = _current_display_item(queue)
    lines = [
        "Batch Mock Assisted Fill Queue",
        "",
        f"Status: {queue.get('status')}",
        "",
        "Summary:",
        f"- total: {summary.get('total', 0)}",
        f"- ok: {summary.get('ok', 0)}",
        f"- blocked: {summary.get('blocked', 0)}",
        f"- current item: {summary.get('current_index', 0)}",
        f"- remaining: {summary.get('remaining', 0)}",
    ]

    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    if preprocessing:
        lines.extend(
            [
                "",
                "Preprocessing:",
                f"- candidates: {preprocessing_summary.get('candidate_count', summary.get('total', 0))}",
                f"- ignored metadata: {preprocessing_summary.get('ignored_metadata_count', 0)}",
                f"- invalid/unsupported: {preprocessing_summary.get('invalid_unsupported_count', 0)}",
            ]
        )
        ignored = preprocessing.get("ignored_metadata_lines", [])
        if ignored:
            preview = ", ".join(str(line.get("raw", "")) for line in ignored[:3])
            suffix = " ..." if len(ignored) > 3 else ""
            lines.append(f"- ignored preview: {preview}{suffix}")

    if queue.get("audit"):
        lines.extend(["", format_pretty_audit_summary(queue)])

    if queue.get("status") == NEEDS_REVIEW:
        _extend_preprocessing_review_sections(lines, queue)

    if queue.get("items"):
        lines.extend(["", "Items:"])
        for item in queue.get("items", []):
            lines.append(f"[{item.get('index')}] {item.get('status')} {item.get('original', '')}")

    if current:
        lines.extend(
            [
                "",
                "Current Item:",
                f"[{current.get('index')}] {current.get('status')}",
                "Original:",
                str(current.get("original", "")),
            ]
        )
        if current.get("selected_numbers"):
            lines.extend(["", "Selected Numbers:", ", ".join(current.get("selected_numbers", []))])
        if current.get("selected_columns"):
            lines.extend(["", "Selected Columns:"])
            for column in current.get("selected_columns", []):
                numbers = ", ".join(str(number) for number in column.get("numbers", []))
                lines.append(f"- Column {column.get('column')}: {numbers}")
        if current.get("bet_type") == "car":
            lines.extend(
                [
                    "",
                    "Car:",
                    f"- Number: {current.get('selected_car_number')}",
                    f"- Units: {current.get('car_units')}",
                    f"- Amount: {current.get('filled_amount')}",
                ]
            )
        if current.get("filled_amounts"):
            lines.extend(["", "Filled Amounts:"])
            for star, amount in current.get("filled_amounts", {}).items():
                lines.append(f"- {star}: {amount}")
        if current.get("errors"):
            lines.extend(["", "Reason:"])
            lines.extend(str(error) for error in current.get("errors", []))
        lines.extend(["", "Danger Buttons:"])
        detected = current.get("danger_buttons_detected", [])
        clicked = current.get("danger_buttons_clicked", [])
        lines.append(f"- detected: {', '.join(detected) if detected else 'none'}")
        lines.append(f"- clicked: {', '.join(clicked) if clicked else 'none'}")

    if queue.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in queue.get("errors", []))

    lines.extend(["", "Next Action:"])
    if queue.get("status") == NEEDS_REVIEW:
        lines.extend(
            [
                "人工審核後二選一：",
                "python -m betguard.webfill.cli --batch-review-accept-valid --queue queue_state.json --pretty",
                "python -m betguard.webfill.cli --batch-review-reject --queue queue_state.json --pretty",
            ]
        )
    else:
        lines.extend(
            [
                "人工確認後，執行：",
                "python -m betguard.webfill.cli --batch-mock-next --queue queue_state.json --pretty",
            ]
        )
    lines.extend(
        [
            "",
            "Final:",
            f"- real site operation: {str(queue.get('final_decision', {}).get('real_site_operation')).lower()}",
            f"- auto submit: {str(queue.get('final_decision', {}).get('auto_submit')).lower()}",
            f"- human required each item: {str(queue.get('final_decision', {}).get('human_required_each_item')).lower()}",
        ]
    )
    return "\n".join(lines)


def _review_input(text_or_lines: str | Iterable[str]) -> dict[str, Any]:
    if isinstance(text_or_lines, str):
        return attach_summaries(review_text(text_or_lines).to_dict())
    return attach_summaries(review_lines([str(line) for line in text_or_lines]).to_dict())


def _review_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = attach_summaries(review_lines([str(candidate.get("raw", "")) for candidate in candidates]).to_dict())
    counts = {"ok": 0, "warning": 0, "error": 0}
    for index, item in enumerate(reviewed.get("items", [])):
        candidate = candidates[index] if index < len(candidates) else {}
        item["result"] = _apply_preprocessing_warnings(item.get("result", {}), candidate)
        counts[item["result"].get("status", "error")] += 1
    reviewed["ok"] = counts["ok"]
    reviewed["warning"] = counts["warning"]
    reviewed["error"] = counts["error"]
    reviewed["can_continue"] = counts["warning"] == 0 and counts["error"] == 0
    return reviewed


def _apply_preprocessing_warnings(result: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    notes = [
        note
        for note in candidate.get("preprocessing_notes", [])
        if "requires manual review" in str(note)
    ]
    if not notes:
        return result
    updated = dict(result)
    warnings = list(updated.get("warnings", []))
    for note in notes:
        if note not in warnings:
            warnings.append(str(note))
    updated["warnings"] = warnings
    if updated.get("status") == "ok":
        updated["status"] = "warning"
    return updated


def _preprocessing_status(valid_count: int, invalid_count: int, total_count: int) -> str:
    if total_count == 0 or valid_count == 0:
        return "BLOCKED"
    if invalid_count > 0:
        return "NEEDS_REVIEW"
    return "READY"


def _queue_status_for_preprocessing(status: str) -> str:
    if status == "READY":
        return READY_FOR_QUEUE
    if status == "NEEDS_REVIEW":
        return NEEDS_REVIEW
    return BATCH_BLOCKED


def _queue_warnings_for_status(status: str) -> list[str]:
    if status == "NEEDS_REVIEW":
        return ["batch contains invalid or warning fragments; manual review required"]
    return []


def _queue_errors_for_status(status: str) -> list[str]:
    if status == "BLOCKED":
        return ["no valid candidates available"]
    return []


def _candidate_review_items(
    review_result: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    valid: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, review_item in enumerate(review_result.get("items", []), start=1):
        bet_result = review_item.get("result", {})
        is_valid = bet_result.get("status") == "ok"
        if is_valid != valid:
            continue
        candidate = candidates[index - 1] if index - 1 < len(candidates) else {}
        result.append(
            {
                "index": index,
                "raw": review_item.get("raw", ""),
                "original_fragment": review_item.get("raw", ""),
                "line_no": candidate.get("line_no"),
                "fragment_index": candidate.get("fragment_index"),
                "summary": review_item.get("summary", ""),
                "status": bet_result.get("status"),
                "warnings": list(bet_result.get("warnings", [])),
                "errors": list(bet_result.get("errors", [])),
                "result": bet_result,
            }
        )
    return result


def _extend_preprocessing_review_sections(lines: list[str], queue: dict[str, Any]) -> None:
    preprocessing = queue.get("preprocessing", {})
    valid_candidates = preprocessing.get("valid_candidates", [])
    invalid_fragments = preprocessing.get("invalid_fragments", [])
    ignored = preprocessing.get("ignored_metadata_lines", [])

    lines.extend(["", "Valid Candidates:"])
    if valid_candidates:
        for candidate in valid_candidates:
            summary = candidate.get("summary") or ""
            lines.append(f"[{candidate.get('index')}] {candidate.get('raw')}")
            if summary:
                lines.append(f"    {summary}")
    else:
        lines.append("- none")

    lines.extend(["", "Invalid / Unsupported:"])
    if invalid_fragments:
        for fragment in invalid_fragments:
            reasons = list(fragment.get("errors", [])) + list(fragment.get("warnings", []))
            reason_text = "; ".join(str(reason) for reason in reasons) or "review required"
            lines.append(f"[{fragment.get('index')}] {fragment.get('raw')}")
            lines.append(f"    reason: {reason_text}")
    else:
        lines.append("- none")

    lines.extend(["", "Ignored Metadata:"])
    lines.append(f"- count: {len(ignored)}")
    for item in ignored[:5]:
        lines.append(f"- {item.get('raw')}")
    if len(ignored) > 5:
        lines.append("- ...")


def _first_item_with_status(queue: dict[str, Any], status: str) -> dict[str, Any] | None:
    for item in queue.get("items", []):
        if item.get("status") == status:
            return item
    return None


def _current_display_item(queue: dict[str, Any]) -> dict[str, Any] | None:
    return (
        _first_item_with_status(queue, WAITING_FOR_HUMAN_CONFIRM)
        or _first_item_with_status(queue, MOCK_FILL_FAILED)
        or _first_item_with_status(queue, PENDING)
        or _last_item_with_status(queue, DONE)
    )


def _last_item_with_status(queue: dict[str, Any], status: str) -> dict[str, Any] | None:
    for item in reversed(queue.get("items", [])):
        if item.get("status") == status:
            return item
    return None


def _refresh_summary(queue: dict[str, Any]) -> None:
    items = queue.get("items", [])
    pending = [item for item in items if item.get("status") == PENDING]
    blocked = [item for item in items if item.get("status") == BLOCKED]
    failed = [item for item in items if item.get("status") == MOCK_FILL_FAILED]
    done = [item for item in items if item.get("status") == DONE]
    waiting = [item for item in items if item.get("status") == WAITING_FOR_HUMAN_CONFIRM]
    current = _current_display_item(queue)
    queue["summary"] = {
        "total": len(items),
        "ok": sum(1 for item in items if item.get("status") in {PENDING, WAITING_FOR_HUMAN_CONFIRM, DONE}),
        "blocked": len(blocked),
        "failed": len(failed),
        "done": len(done),
        "waiting": len(waiting),
        "pending": len(pending),
        "current_index": int(current.get("index", 0)) if current else 0,
        "remaining": len(pending),
        "candidate_count": queue.get("preprocessing", {}).get("summary", {}).get("candidate_count", len(items)),
        "ignored_metadata_count": queue.get("preprocessing", {}).get("summary", {}).get("ignored_metadata_count", 0),
        "invalid_unsupported_count": queue.get("preprocessing", {}).get("summary", {}).get(
            "invalid_unsupported_count", len(failed) + len(blocked)
        ),
    }
    queue["final_decision"] = dict(FINAL_DECISION)


def _mock_fill_failure_reason(item: dict[str, Any], report: dict[str, Any]) -> str:
    bet_type = item.get("review_result", {}).get("type")
    if bet_type == "column":
        return "mock assisted fill does not support column bets yet"
    if bet_type == "car":
        return "mock assisted fill does not support car bets yet"
    return "; ".join(str(error) for error in report.get("errors", [])) or "mock fill failed"
