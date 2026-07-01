from __future__ import annotations

import json
import sys
from pathlib import Path

from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_queue import (
    BATCH_BLOCKED,
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_mock_queue,
    format_pretty_batch_mock_queue,
)


TWO_THREE = "\u4e8c\u4e09"
CAR = "\u8eca"
YUAN = "\u5143"


def test_all_valid_preprocessing_ready_can_queue() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..32{CAR}10{YUAN}")

    assert queue["status"] == READY_FOR_QUEUE
    assert queue["preprocessing_status"] == "READY"
    assert queue["preprocessing"]["summary"]["valid_count"] == 2
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 0


def test_mixed_valid_invalid_needs_review_and_does_not_auto_fill() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")

    assert queue["status"] == NEEDS_REVIEW
    assert queue["preprocessing_status"] == "NEEDS_REVIEW"
    assert queue["preprocessing"]["summary"]["valid_count"] == 2
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 1
    assert all(item["danger_buttons_clicked"] == [] for item in queue["items"])
    assert all(item["selected_numbers"] == [] for item in queue["items"])


def test_pretty_report_for_needs_review_shows_valid_invalid_and_metadata() -> None:
    text = "\n".join(
        [
            "\u4e0a\u534812:04",
            f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}",
        ]
    )

    pretty = format_pretty_batch_mock_queue(build_batch_mock_queue(text))

    assert "Status: NEEDS_REVIEW" in pretty
    assert "Valid Candidates:" in pretty
    assert f"06.13.23.22 {TWO_THREE}50" in pretty
    assert f"32{CAR}10{YUAN}" in pretty
    assert "Invalid / Unsupported:" in pretty
    assert f"40{CAR}10{YUAN}" in pretty
    assert "number out of range 40" in pretty
    assert "Ignored Metadata:" in pretty
    assert "- count: 1" in pretty
    assert "real site operation: false" in pretty
    assert "auto submit: false" in pretty


def test_no_valid_candidates_is_blocked() -> None:
    queue = build_batch_mock_queue(f"\u4e0a\u534812:04\n40{CAR}10{YUAN}")

    assert queue["status"] == BATCH_BLOCKED
    assert queue["preprocessing_status"] == "BLOCKED"
    assert queue["preprocessing"]["summary"]["valid_count"] == 0


def test_cli_accept_valid_preserves_invalid_audit_and_runs_first_item(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--batch-review-accept-valid", "--queue", str(queue_path), "--pretty"],
    )
    webfill_cli.main()

    saved = json.loads(queue_path.read_text(encoding="utf-8"))
    assert saved["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert [item["original"] for item in saved["items"]] == [f"06.13.23.22 {TWO_THREE}50", f"32{CAR}10{YUAN}"]
    assert saved["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert saved["preprocessing"]["original_review_audit"]["invalid_fragments"][0]["raw"] == f"40{CAR}10{YUAN}"
    assert saved["final_decision"]["real_site_operation"] is False
    assert saved["final_decision"]["auto_submit"] is False


def test_cli_reject_marks_queue_rejected(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}")
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--batch-review-reject", "--queue", str(queue_path), "--pretty"],
    )
    webfill_cli.main()

    saved = json.loads(queue_path.read_text(encoding="utf-8"))
    assert saved["status"] == "REJECTED"
    assert saved["errors"] == ["batch review rejected by user"]
