from __future__ import annotations

import inspect
import json
import re
import sys

import pytest

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_queue import build_batch_queue
from betguard.webfill.site_profile import build_site_profile


TWO_THREE = "二三"
TWO_STAR = "二星"
THREE_STAR = "三星"
FOUR_STAR = "四星"
NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))


def queue_for(text: str) -> dict:
    return build_batch_queue(attach_summaries(review_text(text).to_dict()))


def candidate(selector: str, text: str, frame: str = "mainFrame") -> dict:
    return {
        "tag": "button",
        "text": text,
        "frame_name": frame,
        "candidate_selectors": [selector],
    }


def complete_selector_report() -> dict:
    return {
        "market_state": {"can_probe_bet_page": True, "current_game_name": "539"},
        "number_candidates": {
            label: [candidate(f'button[data-number="{label}"]', label)] for label in NUMBER_LABELS
        },
        "amount_field_candidates": {
            TWO_STAR: [candidate('input[data-amount-field="two"]', TWO_STAR)],
            THREE_STAR: [candidate('input[data-amount-field="three"]', THREE_STAR)],
            FOUR_STAR: [candidate('input[data-amount-field="four"]', FOUR_STAR)],
        },
        "danger_candidates": [
            candidate('button[data-danger="submit"]', "送出注單"),
            candidate('button[data-danger="confirm"]', "確認"),
        ],
    }


def _real_site_fill_plan_block_source() -> str:
    source = inspect.getsource(webfill_cli.main)
    start = source.index("if args.real_site_fill_plan:")
    end = source.index("if args.real_site_assisted_fill:", start)
    return source[start:end]


def test_profile_path_produces_same_plan_as_selector_report(tmp_path, monkeypatch, capsys) -> None:
    selector_report = complete_selector_report()
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")

    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    selector_path = tmp_path / "selector_report.json"
    selector_path.write_text(json.dumps(selector_report, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--selector-report",
            str(selector_path),
        ],
    )
    webfill_cli.main()
    plan_from_selector_report = json.loads(capsys.readouterr().out)

    profile_path = tmp_path / "site_profile.json"
    profile = build_site_profile(selector_report, site_name="local", page_name="539")
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()
    plan_from_profile = json.loads(capsys.readouterr().out)

    assert plan_from_profile == plan_from_selector_report
    assert plan_from_profile["status"] == "READY_FOR_HUMAN_REVIEW"
    assert plan_from_profile["final_decision"]["real_site_execute"] is False
    assert plan_from_profile["final_decision"]["real_site_auto_submit"] is False


def test_rejects_both_selector_report_and_profile(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")
    selector_path = tmp_path / "selector_report.json"
    selector_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"
    profile_path.write_text(
        json.dumps(build_site_profile(complete_selector_report(), site_name="local", page_name="539"), ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--selector-report",
            str(selector_path),
            "--profile",
            str(profile_path),
        ],
    )

    with pytest.raises(SystemExit):
        webfill_cli.main()


def test_rejects_missing_both_selector_report_and_profile(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--real-site-fill-plan", "--queue", str(queue_path)],
    )

    with pytest.raises(SystemExit):
        webfill_cli.main()


def test_invalid_profile_is_blocked_before_planning(tmp_path, monkeypatch, capsys) -> None:
    selector_report = complete_selector_report()
    selector_report["danger_candidates"] = []
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"
    profile_path.write_text(
        json.dumps(build_site_profile(selector_report, site_name="local", page_name="539"), ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert any("site profile validation failed" in error for error in report["errors"])
    assert any("danger" in error for error in report["errors"])
    assert report["final_decision"]["real_site_execute"] is False


def test_existing_selector_report_path_still_works(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")
    selector_path = tmp_path / "selector_report.json"
    selector_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-plan",
            "--queue",
            str(queue_path),
            "--selector-report",
            str(selector_path),
        ],
    )
    webfill_cli.main()

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    assert report["final_decision"]["real_site_execute"] is False
    assert report["final_decision"]["real_site_auto_submit"] is False


def test_profile_path_does_not_introduce_click_fill_submit_code() -> None:
    block_source = _real_site_fill_plan_block_source()

    forbidden = re.compile(r"\.click\(|\.fill\(|submit|press|\.type\(|playwright|selenium|browser", re.IGNORECASE)
    assert not forbidden.search(block_source)
