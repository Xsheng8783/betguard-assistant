from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_queue import NEEDS_REVIEW, WAITING_FOR_HUMAN_CONFIRM, load_queue_state
from betguard.webfill.daily_workflow import create_batch_from_text, create_review_package


TWO_THREE = "\u4e8c\u4e09"
CAR = "\u8eca"
YUAN = "\u5143"


def _mixed_text() -> str:
    return f"15:19 USER_A 天天樂\n06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}"


def test_new_batch_from_file_creates_queue_with_mixed_review(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "input.txt"
    queue_path = tmp_path / "queue_state.json"
    input_path.write_text(_mixed_text(), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--new-batch-from-file",
            str(input_path),
            "--queue",
            str(queue_path),
            "--pretty",
        ],
    )
    webfill_cli.main()

    queue = load_queue_state(queue_path)
    assert queue["status"] == NEEDS_REVIEW
    assert queue["preprocessing"]["summary"]["valid_count"] == 2
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 1
    assert queue["preprocessing"]["valid_candidates"]
    assert queue["preprocessing"]["invalid_fragments"]


def test_new_batch_from_stdin_matches_file_behavior(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    monkeypatch.setattr(sys, "stdin", io.StringIO(_mixed_text()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--new-batch-from-stdin", "--queue", str(queue_path), "--pretty"],
    )

    webfill_cli.main()

    queue = load_queue_state(queue_path)
    assert queue["status"] == NEEDS_REVIEW
    assert queue["preprocessing"]["summary"]["valid_count"] == 2
    assert queue["preprocessing"]["summary"]["invalid_unsupported_count"] == 1


def test_review_package_generates_html_audit_and_summary(tmp_path) -> None:
    queue_path = tmp_path / "queue_state.json"
    out_dir = tmp_path / "review_out"
    result = create_batch_from_text(_mixed_text(), queue_path=queue_path)
    queue = result["queue"]

    package = create_review_package(queue, queue_path=queue_path, out_dir=out_dir)

    assert Path(package["review_html_path"]).exists()
    assert Path(package["audit_path"]).exists()
    assert Path(package["summary_path"]).exists()
    summary = Path(package["summary_path"]).read_text(encoding="utf-8")
    assert "Next Suggested Command:" in summary
    assert "real_site_operation=false" in summary
    assert "auto_submit=false" in summary
    assert "danger_buttons_clicked=[]" in summary
    html = Path(package["review_html_path"]).read_text(encoding="utf-8")
    assert "live selector" not in html.lower()


def test_queue_overwrite_protection_blocks_without_overwrite(tmp_path) -> None:
    queue_path = tmp_path / "queue_state.json"
    first = create_batch_from_text(f"06.13.23.22 {TWO_THREE}50", queue_path=queue_path)
    second = create_batch_from_text(f"32{CAR}10{YUAN}", queue_path=queue_path)

    assert first["status"] == "OK"
    assert second["status"] == "ERROR"
    assert "use --overwrite" in second["errors"][0]
    queue = load_queue_state(queue_path)
    assert queue["items"][0]["original"] == f"06.13.23.22 {TWO_THREE}50"


def test_queue_overwrite_allows_explicit_replace(tmp_path) -> None:
    queue_path = tmp_path / "queue_state.json"
    create_batch_from_text(f"06.13.23.22 {TWO_THREE}50", queue_path=queue_path)
    second = create_batch_from_text(f"32{CAR}10{YUAN}", queue_path=queue_path, overwrite=True)

    assert second["status"] == "OK"
    assert "existing queue overwritten" in second["warnings"]
    queue = load_queue_state(queue_path)
    assert queue["items"][0]["original"] == f"32{CAR}10{YUAN}"


def test_daily_workflow_end_to_end_stops_at_waiting_for_human(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "input.txt"
    queue_path = tmp_path / "queue_state.json"
    out_dir = tmp_path / "review_out"
    input_path.write_text(_mixed_text(), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--new-batch-from-file", str(input_path), "--queue", str(queue_path), "--pretty"],
    )
    webfill_cli.main()

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--review-package", "--queue", str(queue_path), "--out-dir", str(out_dir), "--pretty"],
    )
    webfill_cli.main()

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--batch-review-accept-valid", "--queue", str(queue_path), "--pretty"],
    )
    webfill_cli.main()

    queue = load_queue_state(queue_path)
    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--review-package", "--queue", str(queue_path), "--out-dir", str(out_dir), "--pretty"],
    )
    webfill_cli.main()

    refreshed = load_queue_state(queue_path)
    assert refreshed["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert refreshed["final_decision"]["real_site_operation"] is False
    assert refreshed["final_decision"]["auto_submit"] is False
    assert refreshed["items"][0]["danger_buttons_clicked"] == []


def test_cli_new_batch_existing_queue_reports_clean_error_without_traceback(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue_state.json"
    queue_path.write_text("{}", encoding="utf-8")
    input_path = tmp_path / "input.txt"
    input_path.write_text(f"06.13.23.22 {TWO_THREE}50", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--new-batch-from-file", str(input_path), "--queue", str(queue_path), "--pretty"],
    )
    webfill_cli.main()
    output = capsys.readouterr().out

    assert "Status: ERROR" in output
    assert "use --overwrite" in output
    assert "Traceback" not in output
