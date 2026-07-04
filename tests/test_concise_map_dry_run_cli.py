from __future__ import annotations

import json
import sys

from betguard.review import build_report
from betguard.webfill import cli as webfill_cli
from betguard.webfill.fill_plan import build_fill_plan, to_numbers_only_plan


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def candidate(label: str, *, frame: str = "mainFrame") -> dict:
    return {
        "tag": "button",
        "text": label,
        "frame_name": frame,
        "candidate_selectors": [f"text={label}"],
    }


def selector_report_for(plan: dict) -> dict:
    return {
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
        "number_candidates": {number: [candidate(number)] for number in plan.get("numbers", [])},
        "amount_field_candidates": {
            star: [candidate(star, frame="amountFrame")] for star in plan.get("stars", [])
        },
        "danger_candidates": [candidate("送出注單"), candidate("確認")],
    }


def test_map_dry_run_concise_flag_prints_short_summary(tmp_path, monkeypatch, capsys) -> None:
    plan = normal_fill_plan()
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report_for(plan), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--selector-report",
            str(selector_report_path),
            "--concise",
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Assisted Fill Dry-run Mapping (concise)" in output
    assert "Status: SAFE" in output
    assert "Numbers:" in output
    assert "Amounts:" in output
    assert "Safety:" in output
    assert len(output.splitlines()) < 40


def test_map_dry_run_concise_takes_precedence_over_pretty(tmp_path, monkeypatch, capsys) -> None:
    plan = normal_fill_plan()
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report_for(plan), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--selector-report",
            str(selector_report_path),
            "--pretty",
            "--concise",
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "(concise)" in output


def test_map_dry_run_without_concise_still_prints_json_by_default(tmp_path, monkeypatch, capsys) -> None:
    plan = normal_fill_plan()
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report_for(plan), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--selector-report",
            str(selector_report_path),
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["status"] == "SAFE"


def test_map_dry_run_concise_numbers_only_shows_manual_required(tmp_path, monkeypatch, capsys) -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(numbers_only, ensure_ascii=False), encoding="utf-8")
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report_for(plan), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--selector-report",
            str(selector_report_path),
            "--concise",
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Status: SAFE" in output
    assert "amount_manual_required: true" in output
    assert "Amounts:" not in output
