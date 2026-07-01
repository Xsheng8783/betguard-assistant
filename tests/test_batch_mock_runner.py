import inspect
import json
import sys
from pathlib import Path

from betguard.webfill import batch_mock_runner
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_runner import (
    build_batch_mock_runner_report,
    format_pretty_batch_mock_report,
)
from betguard.webfill.batch_queue import BATCH_BLOCKED, DONE, WAITING_FOR_HUMAN_CONFIRM
from betguard.webfill.mock_page import DANGER_BUTTONS, STAR_FIELDS


TWO_THREE = "\u4e8c\u4e09"
TWO_THREE_FOUR = "\u4e8c\u4e09\u56db"


def test_all_ok_auto_confirm_runs_items_in_order_to_completed() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )

    report = build_batch_mock_runner_report(text, auto_confirm_mock=True)

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["summary"]["total"] == 2
    assert report["summary"]["completed"] == 2
    assert [item["status"] for item in report["items"]] == [DONE, DONE]
    assert report["items"][0]["status_history"] == ["READY_TO_FILL", WAITING_FOR_HUMAN_CONFIRM, DONE]
    assert report["items"][1]["status_history"] == ["READY_TO_FILL", WAITING_FOR_HUMAN_CONFIRM, DONE]


def test_without_auto_confirm_stops_at_waiting_for_human() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE}50")

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["items"][0]["stopped_before_confirm"] is True
    assert report["summary"]["completed"] == 0


def test_duplicate_blocks_batch_and_does_not_open_mock_page() -> None:
    report = build_batch_mock_runner_report(f"06.13.23/50\n13.13 {TWO_THREE}100", auto_confirm_mock=True)

    assert report["status"] == BATCH_BLOCKED
    assert report["mock_page_opened"] is False
    assert report["summary"]["blocked"] == 1
    assert all(item["selected_numbers"] == [] for item in report["items"])
    assert all(item["filled_amounts"] == {} for item in report["items"])


def test_two_three_fills_two_three_only() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE}50", auto_confirm_mock=True)

    assert report["items"][0]["selected_numbers"] == ["06", "13", "23", "22"]
    assert report["items"][0]["filled_amounts"] == {
        STAR_FIELDS[0]: 50,
        STAR_FIELDS[1]: 50,
    }
    assert STAR_FIELDS[2] in report["items"][0]["skipped"]


def test_two_three_four_fills_all_three_amounts() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE_FOUR}100", auto_confirm_mock=True)

    assert report["items"][0]["filled_amounts"] == {
        STAR_FIELDS[0]: 100,
        STAR_FIELDS[1]: 100,
        STAR_FIELDS[2]: 100,
    }
    assert report["items"][0]["skipped"] == []


def test_column_item_can_be_mock_filled_by_flattening_columns() -> None:
    report = build_batch_mock_runner_report(f"17.20/28/34 {TWO_THREE}1", auto_confirm_mock=True)

    assert report["items"][0]["selected_numbers"] == ["17", "20", "28", "34"]
    assert report["items"][0]["filled_amounts"] == {
        STAR_FIELDS[0]: 100,
        STAR_FIELDS[1]: 100,
    }


def test_danger_buttons_clicked_is_always_empty() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE}50", auto_confirm_mock=True)

    assert report["danger_buttons_detected"] == DANGER_BUTTONS
    assert report["danger_buttons_clicked"] == []
    assert report["items"][0]["danger_buttons_clicked"] == []


def test_real_site_auto_submit_is_always_false() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE}50", auto_confirm_mock=True)

    assert report["summary"]["real_site_auto_submit"] is False
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["final_decision"]["human_required_each_item"] is True


def test_batch_mock_runner_source_contains_no_real_site_url() -> None:
    source = inspect.getsource(batch_mock_runner)

    assert "gts362" not in source
    assert "http://" not in source
    assert "https://" not in source


def test_batch_mock_runner_does_not_click_danger_buttons() -> None:
    source = Path("src/betguard/webfill/batch_mock_runner.py").read_text(encoding="utf-8")
    click_lines = [line for line in source.splitlines() if ".click(" in line]

    assert click_lines
    assert all("data-danger" not in line for line in click_lines)
    assert all("\u9001\u51fa" not in line for line in click_lines)
    assert all("\u78ba\u8a8d" not in line for line in click_lines)


def test_pretty_report_contains_expected_sections() -> None:
    report = build_batch_mock_runner_report(f"06.13.23.22 {TWO_THREE}50", auto_confirm_mock=True)

    pretty = format_pretty_batch_mock_report(report)

    assert "Batch Mock Runner Report" in pretty
    assert "Status: COMPLETED_MOCK_ONLY" in pretty
    assert "- real_site_auto_submit: false" in pretty
    assert f"- {DANGER_BUTTONS[0]} detected, not clicked" in pretty


def test_cli_batch_mock_run_uses_mock_runner(capsys, monkeypatch, tmp_path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text(f"06.13.23.22 {TWO_THREE}50", encoding="utf-8")
    fake_report = build_batch_mock_runner_report(input_file.read_text(encoding="utf-8"), auto_confirm_mock=True)
    monkeypatch.setattr(webfill_cli, "run_batch_mock_runner_from_file", lambda *args, **kwargs: fake_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--batch-mock-run",
            "--file",
            str(input_file),
            "--auto-confirm-mock",
        ],
    )

    webfill_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "COMPLETED_MOCK_ONLY"
    assert output["final_decision"]["real_site_auto_submit"] is False

