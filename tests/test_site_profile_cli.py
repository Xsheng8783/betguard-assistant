from __future__ import annotations

import inspect
import json
import re
import sys

import pytest

from betguard.review import build_report
from betguard.webfill import cli as webfill_cli
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.site_profile import build_site_profile


NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))


def selector_candidate(label: str) -> dict:
    return {
        "tag": "button",
        "text": label,
        "candidate_selectors": [f"text={label}"],
    }


def complete_selector_report() -> dict:
    return {
        "market_state": {"can_probe_bet_page": True, "current_game_name": "539"},
        "number_candidates": {label: [selector_candidate(label)] for label in NUMBER_LABELS},
        "amount_field_candidates": {
            "二星": [selector_candidate("二星")],
            "三星": [selector_candidate("三星")],
            "四星": [selector_candidate("四星")],
        },
        "danger_candidates": [selector_candidate("送出注單")],
    }


def _new_cli_branch_source() -> str:
    source = inspect.getsource(webfill_cli)
    start = source.index("if args.save_site_profile:")
    end = source.index("if args.dry_run_pipeline:")
    return source[start:end]


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def test_save_site_profile_reads_selector_report_and_writes_profile(tmp_path, monkeypatch) -> None:
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--save-site-profile",
            "--selector-report",
            str(selector_report_path),
            "--out",
            str(profile_path),
            "--site-name",
            "local",
            "--page-name",
            "539",
        ],
    )
    webfill_cli.main()

    assert profile_path.exists()
    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["site_name"] == "local"
    assert saved["page_name"] == "539"


def test_saved_profile_content_matches_build_site_profile(tmp_path, monkeypatch) -> None:
    selector_report = complete_selector_report()
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report, ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--save-site-profile",
            "--selector-report",
            str(selector_report_path),
            "--out",
            str(profile_path),
            "--site-name",
            "local",
            "--page-name",
            "539",
            "--captured-at",
            "2026-07-03T00:00:00",
        ],
    )
    webfill_cli.main()

    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    expected = build_site_profile(
        selector_report,
        site_name="local",
        page_name="539",
        captured_at="2026-07-03T00:00:00",
    )
    assert saved == expected


def test_site_profile_report_with_complete_profile_reports_ok(tmp_path, monkeypatch, capsys) -> None:
    profile = build_site_profile(complete_selector_report(), site_name="local", page_name="539")
    profile_path = tmp_path / "site_profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--site-profile-report", "--profile", str(profile_path), "--pretty"],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Status: OK" in output


def test_site_profile_report_with_missing_danger_candidates_reports_blocked(tmp_path, monkeypatch, capsys) -> None:
    selector_report = complete_selector_report()
    selector_report["danger_candidates"] = []
    profile = build_site_profile(selector_report, site_name="local", page_name="539")
    profile_path = tmp_path / "site_profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--site-profile-report", "--profile", str(profile_path), "--pretty"],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Status: BLOCKED" in output
    assert "danger" in output.lower()


def test_site_profile_report_without_pretty_prints_json(tmp_path, monkeypatch, capsys) -> None:
    profile = build_site_profile(complete_selector_report(), site_name="local", page_name="539")
    profile_path = tmp_path / "site_profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--site-profile-report", "--profile", str(profile_path)],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["status"] == "OK"
    assert parsed["errors"] == []


def test_save_site_profile_missing_required_arguments_raises_parser_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--save-site-profile"])

    with pytest.raises(SystemExit):
        webfill_cli.main()

    error_output = capsys.readouterr().err
    assert "--save-site-profile requires" in error_output


def test_site_profile_report_missing_profile_argument_raises_parser_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--site-profile-report"])

    with pytest.raises(SystemExit):
        webfill_cli.main()

    error_output = capsys.readouterr().err
    assert "--site-profile-report requires --profile" in error_output


def test_save_site_profile_does_not_touch_queue_state(tmp_path, monkeypatch) -> None:
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--save-site-profile",
            "--selector-report",
            str(selector_report_path),
            "--out",
            str(profile_path),
            "--site-name",
            "local",
            "--page-name",
            "539",
        ],
    )
    webfill_cli.main()

    assert not (tmp_path / "queue_state.json").exists()
    written_files = {path.name for path in tmp_path.iterdir()}
    assert written_files == {"selector_report.json", "site_profile.json"}


def test_new_cli_branches_do_not_introduce_browser_automation_code() -> None:
    branch_source = _new_cli_branch_source()

    forbidden = re.compile(r"click|submit|playwright|selenium|browser", re.IGNORECASE)
    assert not forbidden.search(branch_source)


def test_cli_output_does_not_trigger_browser_automation(tmp_path, monkeypatch, capsys) -> None:
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "site_profile.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--save-site-profile",
            "--selector-report",
            str(selector_report_path),
            "--out",
            str(profile_path),
            "--site-name",
            "local",
            "--page-name",
            "539",
            "--pretty",
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    lowered = output.lower()
    assert "playwright" not in lowered
    assert "selenium" not in lowered
    assert ".click(" not in output


def test_map_dry_run_with_profile_matches_map_dry_run_with_selector_report(tmp_path, monkeypatch, capsys) -> None:
    selector_report = complete_selector_report()
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")

    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(selector_report, ensure_ascii=False), encoding="utf-8")

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
    report_from_selector_report = json.loads(capsys.readouterr().out)

    profile_path = tmp_path / "site_profile.json"
    profile = build_site_profile(selector_report, site_name="local", page_name="539")
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()
    report_from_profile = json.loads(capsys.readouterr().out)

    assert report_from_profile == report_from_selector_report
    assert report_from_profile["status"] == "SAFE"
    assert report_from_profile["final_decision"]["executable"] is False


def test_map_dry_run_rejects_both_selector_report_and_profile(tmp_path, monkeypatch, capsys) -> None:
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")
    selector_report_path = tmp_path / "selector_report.json"
    selector_report_path.write_text(json.dumps(complete_selector_report(), ensure_ascii=False), encoding="utf-8")
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
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--selector-report",
            str(selector_report_path),
            "--profile",
            str(profile_path),
        ],
    )

    with pytest.raises(SystemExit):
        webfill_cli.main()

    error_output = capsys.readouterr().err
    assert "only one of --selector-report or --profile" in error_output


def test_map_dry_run_rejects_missing_both_selector_report_and_profile(tmp_path, monkeypatch, capsys) -> None:
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--map-dry-run", "--fill-plan", str(fill_plan_path)],
    )

    with pytest.raises(SystemExit):
        webfill_cli.main()

    error_output = capsys.readouterr().err
    assert "--selector-report or --profile" in error_output


def test_map_dry_run_with_profile_missing_danger_candidates_stays_blocked(tmp_path, monkeypatch, capsys) -> None:
    selector_report = complete_selector_report()
    selector_report["danger_candidates"] = []
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")
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
            "--map-dry-run",
            "--fill-plan",
            str(fill_plan_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert {"type": "danger", "reason": "danger buttons not verified"} in report["missing"]
