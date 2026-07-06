from __future__ import annotations

import json
import sys
from pathlib import Path

from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_queue import (
    NEEDS_REVIEW,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    load_queue_state,
)
from betguard.webfill.demo_e2e import DEFAULT_SAMPLE_PATH, run_e2e_demo


def test_demo_sample_file_exists() -> None:
    assert DEFAULT_SAMPLE_PATH.exists()
    assert "12\u534a\u8eca" in DEFAULT_SAMPLE_PATH.read_text(encoding="utf-8")


def test_demo_command_generates_expected_output_files(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)

    assert Path(result["queue_path"]).exists()
    assert Path(result["review_html_path"]).exists()
    assert Path(result["audit_path"]).exists()
    assert Path(result["summary_path"]).exists()
    assert result["queue_status"] == NEEDS_REVIEW
    assert result["safety"]["real_site_operation"] is False
    assert result["safety"]["auto_submit"] is False


def test_generated_queue_state_starts_needs_review_without_auto_accept_or_mock(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)
    queue = load_queue_state(result["queue_path"])

    assert queue["status"] == NEEDS_REVIEW
    assert queue.get("review_action") is None
    assert all(item["selected_numbers"] == [] for item in queue["items"])
    assert all(item["danger_buttons_clicked"] == [] for item in queue["items"])


def test_generated_review_html_contains_review_sections_and_safety(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)
    html = Path(result["review_html_path"]).read_text(encoding="utf-8")

    assert "正確候選" in html
    assert "需人工確認" in html
    assert "未連真網站" in html
    assert "未送出" in html
    assert "未填寫" in html
    assert "live selector" not in html.lower()


def test_generated_audit_contains_original_fragments_summary_and_safety(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)
    audit = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))

    assert audit["original_text"]
    assert audit["items"]
    assert all(item["original_fragment"] for item in audit["items"])
    assert audit["preprocessing"]["candidate_count"] >= 5
    assert audit["preprocessing"]["invalid_count"] >= 1
    assert audit["safety"]["real_site_operation"] is False
    assert audit["safety"]["auto_submit"] is False
    assert audit["safety"]["danger_buttons_clicked"] == []


def test_generated_summary_contains_counts_next_command_and_safety(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)
    summary = Path(result["summary_path"]).read_text(encoding="utf-8")

    assert "candidate count:" in summary
    assert "valid count:" in summary
    assert "invalid/review count:" in summary
    assert "--batch-review-accept-valid" in summary
    assert "real_site_operation=false" in summary
    assert "auto_submit=false" in summary


def test_after_manual_accept_valid_queue_stops_at_waiting_for_human_confirm(tmp_path) -> None:
    result = run_e2e_demo(out_dir=tmp_path)
    queue = load_queue_state(result["queue_path"])

    accepted = accept_valid_candidates_for_mock_queue(queue, run_first=True)

    assert accepted["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert accepted["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert accepted["preprocessing"]["original_review_audit"]["invalid_fragments"]
    assert accepted["final_decision"]["real_site_operation"] is False
    assert accepted["final_decision"]["auto_submit"] is False


def test_cli_demo_e2e_generates_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--demo-e2e", "--out-dir", str(tmp_path), "--pretty"],
    )

    webfill_cli.main()

    assert (tmp_path / "queue_state.json").exists()
    assert (tmp_path / "review.html").exists()
    assert (tmp_path / "audit.json").exists()
    assert (tmp_path / "summary.txt").exists()
