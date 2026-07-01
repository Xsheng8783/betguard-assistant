from __future__ import annotations

import json
import sys

from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_queue import (
    COMPLETED,
    NEEDS_REVIEW,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    advance_queue_after_human_confirm,
    build_batch_mock_queue,
    format_pretty_batch_mock_queue,
    reject_batch_review,
    run_current_mock_queue_item,
)


TWO_THREE = "\u4e8c\u4e09"
CAR = "\u8eca"
YUAN = "\u5143"


def test_all_valid_batch_has_audit_original_preprocessing_items_and_safety() -> None:
    text = f"06.13.23.22 {TWO_THREE}50..32{CAR}10{YUAN}"
    queue = build_batch_mock_queue(text)
    audit = queue["audit"]

    assert audit["batch_id"].startswith("batch_")
    assert audit["original_text"] == text
    assert audit["preprocessing"]["status"] == "READY"
    assert audit["preprocessing"]["candidate_count"] == 2
    assert audit["preprocessing"]["valid_count"] == 2
    assert len(audit["items"]) == 2
    assert audit["safety"]["real_site_operation"] is False
    assert audit["safety"]["auto_submit"] is False
    assert audit["safety"]["danger_buttons_clicked"] == []


def test_mixed_valid_invalid_audit_needs_review_preserves_fragments_without_mock_fill() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")
    audit = queue["audit"]

    assert queue["status"] == NEEDS_REVIEW
    assert audit["preprocessing"]["status"] == "NEEDS_REVIEW"
    assert audit["preprocessing"]["invalid_fragments"][0]["raw"] == f"40{CAR}10{YUAN}"
    assert [item["original_fragment"] for item in audit["items"]] == [
        f"06.13.23.22 {TWO_THREE}50",
        f"40{CAR}10{YUAN}",
        f"32{CAR}10{YUAN}",
    ]
    assert all(item["mock_result_summary"]["danger_buttons_clicked"] == [] for item in audit["items"])


def test_accept_valid_updates_audit_and_records_first_mock_result() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")

    accepted = accept_valid_candidates_for_mock_queue(queue, run_first=True)
    audit = accepted["audit"]

    assert audit["review"]["action"] == "accept_valid"
    assert audit["review"]["accepted_valid_count"] == 2
    assert audit["preprocessing"]["invalid_count"] == 1
    assert audit["preprocessing"]["invalid_fragments"][0]["raw"] == f"40{CAR}10{YUAN}"
    assert audit["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert audit["items"][0]["human_confirmation_status"] == "waiting_for_human_confirm"
    assert audit["items"][0]["mock_result_summary"]["selected_numbers"] == ["06", "13", "23", "22"]
    assert audit["safety"]["danger_buttons_clicked"] == []


def test_reject_updates_audit_without_mock_fill() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}")

    rejected = reject_batch_review(queue)
    audit = rejected["audit"]

    assert rejected["status"] == "REJECTED"
    assert audit["review"]["action"] == "reject"
    assert audit["review"]["rejected_reason"] == "batch review rejected by user"
    assert all(item["mock_result_summary"]["selected_numbers"] == [] for item in audit["items"])


def test_cli_batch_audit_export_writes_readable_json(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    audit_path = tmp_path / "audit.json"
    queue = run_current_mock_queue_item(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50"))
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--batch-audit-export",
            "--queue",
            str(queue_path),
            "--out",
            str(audit_path),
            "--pretty",
        ],
    )
    webfill_cli.main()

    exported = json.loads(audit_path.read_text(encoding="utf-8"))
    assert exported["batch_id"]
    assert exported["original_text"]
    assert exported["items"]
    assert exported["preprocessing"]
    assert exported["review"]
    assert exported["safety"]["real_site_operation"] is False
    assert exported["safety"]["auto_submit"] is False
    payload = json.dumps(exported, ensure_ascii=False)
    assert "selector" not in payload
    assert "submit_clicked" not in payload
    assert "confirm_clicked" not in payload


def test_completed_queue_updates_audit_completed_status() -> None:
    queue = run_current_mock_queue_item(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n32{CAR}10{YUAN}"))
    queue = advance_queue_after_human_confirm(queue)
    queue = advance_queue_after_human_confirm(queue)

    assert queue["status"] == COMPLETED
    assert queue["audit"]["queue_status"] == COMPLETED
    assert [item["human_confirmation_status"] for item in queue["audit"]["items"]] == [
        "confirmed_done_by_human",
        "confirmed_done_by_human",
    ]


def test_pretty_report_contains_short_audit_summary() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50")
    pretty = format_pretty_batch_mock_queue(queue)

    assert "Audit Summary" in pretty
    assert "- batch_id: batch_" in pretty
    assert "- preprocessing status: READY" in pretty
    assert "- real_site_operation: false" in pretty
    assert "- auto_submit: false" in pretty
