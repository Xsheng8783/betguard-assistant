from __future__ import annotations

import inspect
import json
import re
import sys

from betguard.webfill import cli as webfill_cli


NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))


def selector_candidate(label: str) -> dict:
    return {
        "tag": "button",
        "text": label,
        "candidate_selectors": [f"text={label}"],
    }


def fake_discovery_report() -> dict:
    return {
        "url": "http://localhost/fake",
        "mode": "selector_discovery",
        "current_game_guess": "539",
        "market_state": {"can_probe_bet_page": True, "current_game_name": "539"},
        "number_candidates": {label: [selector_candidate(label)] for label in NUMBER_LABELS},
        "amount_field_candidates": {
            "二星": [selector_candidate("二星")],
            "三星": [selector_candidate("三星")],
            "四星": [selector_candidate("四星")],
        },
        "danger_candidates": [selector_candidate("送出注單")],
        "warnings": [],
        "errors": [],
    }


def _patch_discovery(monkeypatch) -> dict:
    report = fake_discovery_report()
    monkeypatch.setattr(webfill_cli, "run_selector_discovery", lambda url: report)
    return report


def test_discover_selectors_with_out_writes_json_file(tmp_path, monkeypatch, capsys) -> None:
    report = _patch_discovery(monkeypatch)
    out_path = tmp_path / "selector_report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--dry-run",
            "--discover-selectors",
            "--url",
            "http://localhost/fake",
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded == report


def test_written_file_has_no_utf8_bom(tmp_path, monkeypatch) -> None:
    _patch_discovery(monkeypatch)
    out_path = tmp_path / "selector_report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--dry-run",
            "--discover-selectors",
            "--url",
            "http://localhost/fake",
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    raw = out_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert not raw.startswith(b"\xff\xfe")
    assert not raw.startswith(b"\xfe\xff")


def test_stdout_contains_only_confirmation_when_out_is_used(tmp_path, monkeypatch, capsys) -> None:
    _patch_discovery(monkeypatch)
    out_path = tmp_path / "selector_report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--dry-run",
            "--discover-selectors",
            "--url",
            "http://localhost/fake",
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Selector report written:" in output
    assert str(out_path) in output
    assert "number_candidates" not in output


def test_discover_selectors_without_out_prints_full_json_to_stdout(monkeypatch, capsys) -> None:
    report = _patch_discovery(monkeypatch)

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--dry-run", "--discover-selectors", "--url", "http://localhost/fake"],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert json.loads(output) == report


def test_written_report_works_with_save_site_profile(tmp_path, monkeypatch, capsys) -> None:
    _patch_discovery(monkeypatch)
    report_path = tmp_path / "selector_report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--dry-run",
            "--discover-selectors",
            "--url",
            "http://localhost/fake",
            "--out",
            str(report_path),
        ],
    )
    webfill_cli.main()
    capsys.readouterr()

    profile_path = tmp_path / "site_profile.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--save-site-profile",
            "--selector-report",
            str(report_path),
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
    assert "Status: OK" in output
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["metadata"]["number_count"] == 39
    assert profile["metadata"]["danger_candidate_count"] == 1


def test_new_output_path_does_not_introduce_browser_automation_code() -> None:
    source = inspect.getsource(webfill_cli.main)
    start = source.index("if args.discover_selectors and args.output_path:")
    end = source.index("print(json.dumps(report, ensure_ascii=False, indent=2))", start)
    new_block = source[start:end]

    forbidden = re.compile(r"click|submit|\.fill\(|press|\.type\(|playwright|selenium|browser", re.IGNORECASE)
    assert not forbidden.search(new_block)
