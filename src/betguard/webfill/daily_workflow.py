from __future__ import annotations

from pathlib import Path
from typing import Any

from betguard.webfill.batch_audit import export_batch_audit
from betguard.webfill.batch_mock_queue import build_batch_mock_queue, save_queue_state
from betguard.webfill.review_console import write_review_console_html


def create_batch_from_text(
    raw_text: str,
    *,
    queue_path: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    path = Path(queue_path)
    if path.exists() and not overwrite:
        return {
            "mode": "new_batch",
            "status": "ERROR",
            "errors": [f"queue file already exists: {path}; use --overwrite to replace it"],
            "queue_path": str(path),
            "safety": _safety(),
        }
    queue = build_batch_mock_queue(raw_text)
    save_queue_state(queue, path)
    return {
        "mode": "new_batch",
        "status": "OK",
        "queue_path": str(path),
        "queue": queue,
        "warnings": ["existing queue overwritten"] if overwrite else [],
        "safety": _safety(),
    }


def create_review_package(
    queue: dict[str, Any],
    *,
    queue_path: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    review_html = out_path / "review.html"
    audit_json = out_path / "audit.json"
    summary_txt = out_path / "summary.txt"
    write_review_console_html(queue, review_html, queue_path=str(queue_path))
    audit = export_batch_audit(queue, audit_json)
    summary = build_review_package_summary(queue, queue_path=queue_path, audit=audit)
    summary_txt.write_text(summary, encoding="utf-8")
    return {
        "mode": "review_package",
        "status": "OK",
        "out_dir": str(out_path),
        "review_html_path": str(review_html),
        "audit_path": str(audit_json),
        "summary_path": str(summary_txt),
        "queue_status": queue.get("status"),
        "preprocessing_status": queue.get("preprocessing_status"),
        "safety": _safety(),
    }


def build_review_package_summary(queue: dict[str, Any], *, queue_path: str | Path, audit: dict[str, Any] | None = None) -> str:
    audit = audit or queue.get("audit", {})
    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    summary = queue.get("summary", {})
    queue_path_text = str(queue_path)
    return "\n".join(
        [
            "Betguard Daily Review Package",
            "",
            f"batch_id: {audit.get('batch_id')}",
            f"queue status: {queue.get('status')}",
            f"preprocessing status: {preprocessing.get('status') or queue.get('preprocessing_status')}",
            f"candidate count: {preprocessing_summary.get('candidate_count', summary.get('total', 0))}",
            f"valid count: {preprocessing_summary.get('valid_count', summary.get('ok', 0))}",
            f"invalid/review count: {preprocessing_summary.get('invalid_unsupported_count', 0)}",
            f"ignored metadata count: {preprocessing_summary.get('ignored_metadata_count', 0)}",
            "",
            "Next Suggested Command:",
            _next_command(queue, queue_path_text),
            "",
            "Safety:",
            "- real_site_operation=false",
            "- auto_submit=false",
            "- danger_buttons_clicked=[]",
            "- no live site operation",
            "- no submit",
            "- no click",
            "- human confirmation required for every item",
        ]
    )


def format_pretty_new_batch_result(result: dict[str, Any]) -> str:
    if result.get("status") != "OK":
        lines = ["New Batch", "", "Status: ERROR", ""]
        lines.extend(f"- {error}" for error in result.get("errors", []))
        lines.extend(["", "Safety:", "- real_site_operation=false", "- auto_submit=false", "- danger_buttons_clicked=[]"])
        return "\n".join(lines)
    queue = result.get("queue", {})
    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    summary = queue.get("summary", {})
    lines = [
        "New Batch",
        "",
        f"Queue: {result.get('queue_path')}",
        f"Queue Status: {queue.get('status')}",
        f"Preprocessing Status: {preprocessing.get('status') or queue.get('preprocessing_status')}",
        "",
        "Summary:",
        f"- candidates: {preprocessing_summary.get('candidate_count', summary.get('total', 0))}",
        f"- valid: {preprocessing_summary.get('valid_count', summary.get('ok', 0))}",
        f"- invalid/review: {preprocessing_summary.get('invalid_unsupported_count', 0)}",
        f"- ignored metadata: {preprocessing_summary.get('ignored_metadata_count', 0)}",
    ]
    if result.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.get("warnings", []))
    lines.extend(
        [
            "",
            "Next Suggested Command:",
            _next_command(queue, str(result.get("queue_path"))),
            "",
            "Safety:",
            "- real_site_operation=false",
            "- auto_submit=false",
            "- danger_buttons_clicked=[]",
        ]
    )
    return "\n".join(lines)


def format_pretty_review_package_result(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Review Package",
            "",
            f"Queue Status: {result.get('queue_status')}",
            f"Preprocessing Status: {result.get('preprocessing_status')}",
            "",
            "Files:",
            f"- review.html: {result.get('review_html_path')}",
            f"- audit.json: {result.get('audit_path')}",
            f"- summary.txt: {result.get('summary_path')}",
            "",
            "Safety:",
            "- real_site_operation=false",
            "- auto_submit=false",
            "- danger_buttons_clicked=[]",
        ]
    )


def _next_command(queue: dict[str, Any], queue_path: str) -> str:
    status = queue.get("status")
    if status == "NEEDS_REVIEW":
        return f"python -m betguard.webfill.cli --batch-review-accept-valid --queue {queue_path} --pretty"
    if status == "READY_FOR_QUEUE":
        return f"python -m betguard.webfill.cli --batch-mock-next --queue {queue_path} --pretty"
    if status == "WAITING_FOR_HUMAN_CONFIRM":
        return f"python -m betguard.webfill.cli --batch-mock-next --queue {queue_path} --pretty"
    if status == "BATCH_BLOCKED":
        return "fix input text, then create a new batch"
    return "review queue status before continuing"


def _safety() -> dict[str, Any]:
    return {
        "real_site_operation": False,
        "auto_submit": False,
        "danger_buttons_clicked": [],
    }
