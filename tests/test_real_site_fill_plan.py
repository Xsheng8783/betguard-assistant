import json
import sys

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
    mark_item_waiting_for_human,
)
from betguard.webfill.real_site_fill_plan import (
    build_real_site_assisted_fill_plan,
    format_pretty_real_site_fill_plan,
)


TWO_THREE = "\u4e8c\u4e09"


def queue_for(text: str) -> dict:
    return build_batch_queue(attach_summaries(review_text(text).to_dict()))


def candidate(selector: str, text: str, frame: str = "mainFrame") -> dict:
    return {
        "tag": "button",
        "text": text,
        "frame_name": frame,
        "candidate_selectors": [selector],
    }


def full_selector_report() -> dict:
    return {
        "number_candidates": {
            "06": [candidate('button[data-number="06"]', "06")],
            "13": [candidate('button[data-number="13"]', "13")],
            "23": [candidate('button[data-number="23"]', "23")],
            "22": [candidate('button[data-number="22"]', "22")],
        },
        "amount_field_candidates": {
            "\u4e8c\u661f": [candidate('input[data-amount-field="二星"]', "\u4e8c\u661f")],
            "\u4e09\u661f": [candidate('input[data-amount-field="三星"]', "\u4e09\u661f")],
        },
        "danger_candidates": [
            candidate('button[data-danger="true"]', "\u9001\u51fa\u6ce8\u55ae"),
            candidate('button[data-danger="true"]', "\u78ba\u8a8d"),
        ],
    }


def test_ready_queue_with_full_selectors_builds_human_review_plan() -> None:
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")

    report = build_real_site_assisted_fill_plan(queue, full_selector_report())

    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    assert report["item"]["index"] == 1
    assert report["item"]["original"] == f"06.13.23.22 {TWO_THREE}50"
    assert report["danger_check"]["danger_candidates_found"] is True
    assert report["danger_check"]["will_click_danger"] is False
    assert all(action["execute"] is False for action in report["planned_actions"])
    assert [action["type"] for action in report["planned_actions"]] == [
        "SELECT_NUMBER",
        "SELECT_NUMBER",
        "SELECT_NUMBER",
        "SELECT_NUMBER",
        "SET_AMOUNT",
        "SET_AMOUNT",
    ]


def test_batch_blocked_queue_blocks_plan() -> None:
    queue = queue_for(f"13.13 {TWO_THREE}100")

    report = build_real_site_assisted_fill_plan(queue, full_selector_report())

    assert queue["status"] == BATCH_BLOCKED
    assert report["status"] == "BLOCKED"
    assert "queue status must be READY_FOR_QUEUE" in report["errors"][0]


def test_waiting_for_human_queue_blocks_plan() -> None:
    queue = mark_item_waiting_for_human(queue_for(f"06.13.23.22 {TWO_THREE}50"), 1)

    report = build_real_site_assisted_fill_plan(queue, full_selector_report())

    assert queue["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["status"] == "BLOCKED"
    assert "queue status must be READY_FOR_QUEUE" in report["errors"][0]


def test_missing_number_selector_blocks_plan() -> None:
    selector_report = full_selector_report()
    selector_report["number_candidates"].pop("06")

    report = build_real_site_assisted_fill_plan(queue_for(f"06.13.23.22 {TWO_THREE}50"), selector_report)

    assert report["status"] == "BLOCKED"
    assert "number 06 selector missing" in report["missing"]


def test_missing_amount_selector_blocks_plan() -> None:
    selector_report = full_selector_report()
    selector_report["amount_field_candidates"].pop("\u4e8c\u661f")

    report = build_real_site_assisted_fill_plan(queue_for(f"06.13.23.22 {TWO_THREE}50"), selector_report)

    assert report["status"] == "BLOCKED"
    assert "amount field \u4e8c\u661f missing" in report["missing"]


def test_empty_danger_candidates_blocks_plan() -> None:
    selector_report = full_selector_report()
    selector_report["danger_candidates"] = []

    report = build_real_site_assisted_fill_plan(queue_for(f"06.13.23.22 {TWO_THREE}50"), selector_report)

    assert report["status"] == "BLOCKED"
    assert report["danger_check"]["danger_candidates_found"] is False
    assert "danger candidates missing" in report["missing"]
    assert "danger candidates not found" in report["errors"]


def test_final_decision_never_executes_or_auto_submits() -> None:
    report = build_real_site_assisted_fill_plan(queue_for(f"06.13.23.22 {TWO_THREE}50"), full_selector_report())

    assert report["final_decision"]["real_site_execute"] is False
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["final_decision"]["human_review_required"] is True


def test_pretty_report_contains_plan_sections() -> None:
    report = build_real_site_assisted_fill_plan(queue_for(f"06.13.23.22 {TWO_THREE}50"), full_selector_report())

    pretty = format_pretty_real_site_fill_plan(report)

    assert "Real-site Assisted Fill Plan" in pretty
    assert "Status: READY_FOR_HUMAN_REVIEW" in pretty
    assert "SELECT_NUMBER 06" in pretty
    assert "execute: false" in pretty
    assert "will_click_danger: false" in pretty
    assert "real_site_auto_submit: false" in pretty


def test_cli_real_site_fill_plan_reads_queue_and_selector_report(capsys, monkeypatch, tmp_path) -> None:
    queue_path = tmp_path / "queue.json"
    selector_path = tmp_path / "selector_report.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")
    selector_path.write_text(json.dumps(full_selector_report(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--selector-report",
            str(selector_path),
        ],
    )

    webfill_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "READY_FOR_HUMAN_REVIEW"
    assert output["final_decision"]["real_site_execute"] is False
    assert output["final_decision"]["real_site_auto_submit"] is False

