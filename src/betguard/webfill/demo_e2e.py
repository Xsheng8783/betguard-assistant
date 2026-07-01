from __future__ import annotations

from pathlib import Path
from typing import Any

from betguard.webfill.batch_audit import export_batch_audit
from betguard.webfill.batch_mock_queue import build_batch_mock_queue, save_queue_state
from betguard.webfill.review_console import write_review_console_html


DEFAULT_SAMPLE_PATH = Path("examples/line_paste_demo.txt")


def run_e2e_demo(
    *,
    out_dir: str | Path,
    sample_path: str | Path = DEFAULT_SAMPLE_PATH,
) -> dict[str, Any]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    sample_file = Path(sample_path)
    raw_text = sample_file.read_text(encoding="utf-8")
    queue = build_batch_mock_queue(raw_text)

    queue_path = out_path / "queue_state.json"
    review_html_path = out_path / "review.html"
    audit_path = out_path / "audit.json"
    summary_path = out_path / "summary.txt"

    save_queue_state(queue, queue_path)
    write_review_console_html(queue, review_html_path, queue_path=str(queue_path))
    audit = export_batch_audit(queue, audit_path)
    summary = build_demo_summary(queue, queue_path=queue_path, audit_path=audit_path, review_html_path=review_html_path)
    summary_path.write_text(summary, encoding="utf-8")

    return {
        "mode": "e2e_demo_pack",
        "sample_path": str(sample_file),
        "out_dir": str(out_path),
        "queue_path": str(queue_path),
        "review_html_path": str(review_html_path),
        "audit_path": str(audit_path),
        "summary_path": str(summary_path),
        "queue_status": queue.get("status"),
        "preprocessing_status": queue.get("preprocessing_status"),
        "audit_batch_id": audit.get("batch_id"),
        "safety": {
            "real_site_operation": False,
            "auto_submit": False,
            "danger_buttons_clicked": [],
        },
    }


def build_demo_summary(
    queue: dict[str, Any],
    *,
    queue_path: str | Path,
    audit_path: str | Path,
    review_html_path: str | Path,
) -> str:
    summary = queue.get("summary", {})
    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    queue_path_text = str(queue_path)
    lines = [
        "Betguard End-to-End Demo Pack",
        "",
        "Generated Files:",
        f"- queue_state: {queue_path_text}",
        f"- review_html: {review_html_path}",
        f"- audit_json: {audit_path}",
        "",
        "Preprocessing:",
        f"- status: {preprocessing.get('status') or queue.get('preprocessing_status')}",
        f"- candidate count: {preprocessing_summary.get('candidate_count', summary.get('total', 0))}",
        f"- valid count: {preprocessing_summary.get('valid_count', summary.get('ok', 0))}",
        f"- invalid/review count: {preprocessing_summary.get('invalid_unsupported_count', 0)}",
        f"- ignored metadata count: {preprocessing_summary.get('ignored_metadata_count', 0)}",
        "",
        "Queue:",
        f"- status: {queue.get('status')}",
        f"- item count: {summary.get('total', 0)}",
        f"- current index: {summary.get('current_index', 0)}",
        "",
        "Next Suggested Command:",
        f"python -m betguard.webfill.cli --batch-review-accept-valid --queue {queue_path_text} --pretty",
        "",
        "After Accepting Valid Candidates:",
        f"python -m betguard.webfill.cli --batch-mock-next --queue {queue_path_text} --pretty",
        f"python -m betguard.webfill.cli --review-report-html --queue {queue_path_text} --out {Path(queue_path).parent / 'review_after_next.html'} --pretty",
        f"python -m betguard.webfill.cli --batch-audit-export --queue {queue_path_text} --out {Path(queue_path).parent / 'audit_after_next.json'} --pretty",
        "",
        "Safety:",
        "- real_site_operation=false",
        "- auto_submit=false",
        "- danger_buttons_clicked=[]",
        "- no live site operation",
        "- no submit",
        "- no danger button click",
    ]
    return "\n".join(lines)


def format_pretty_demo_result(result: dict[str, Any]) -> str:
    lines = [
        "End-to-End Demo Pack",
        "",
        f"Status: {result.get('queue_status')}",
        f"Preprocessing: {result.get('preprocessing_status')}",
        "",
        "Files:",
        f"- queue_state.json: {result.get('queue_path')}",
        f"- review.html: {result.get('review_html_path')}",
        f"- audit.json: {result.get('audit_path')}",
        f"- summary.txt: {result.get('summary_path')}",
        "",
        "Safety:",
        "- real_site_operation=false",
        "- auto_submit=false",
        "- danger_buttons_clicked=[]",
    ]
    return "\n".join(lines)
