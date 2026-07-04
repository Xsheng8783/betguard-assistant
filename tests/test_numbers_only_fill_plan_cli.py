from __future__ import annotations

import json
import sys

import pytest

from betguard.review import build_report
from betguard.webfill import cli as webfill_cli
from betguard.webfill.fill_plan import NUMBERS_ONLY_MODE, build_fill_plan, to_numbers_only_plan


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def test_numbers_only_fill_plan_cli_writes_expected_json(tmp_path, monkeypatch) -> None:
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "numbers_only.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--numbers-only-fill-plan",
            "--fill-plan",
            str(fill_plan_path),
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["mode"] == NUMBERS_ONLY_MODE
    assert saved["executable"] is False
    assert saved["amount_manual_required"] is True
    assert all(step.get("type") != "set_amount" for step in saved["planned_steps"])
    assert len(saved["amount_steps_removed"]) == 3


def test_numbers_only_fill_plan_cli_matches_pure_transform(tmp_path, monkeypatch) -> None:
    plan = normal_fill_plan()
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "numbers_only.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--numbers-only-fill-plan",
            "--fill-plan",
            str(fill_plan_path),
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved == to_numbers_only_plan(plan)


def test_numbers_only_fill_plan_cli_without_out_prints_json(tmp_path, monkeypatch, capsys) -> None:
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--numbers-only-fill-plan", "--fill-plan", str(fill_plan_path)],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["mode"] == NUMBERS_ONLY_MODE
    assert parsed["amount_manual_required"] is True


def test_numbers_only_fill_plan_cli_missing_fill_plan_raises_parser_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--numbers-only-fill-plan"])

    with pytest.raises(SystemExit):
        webfill_cli.main()

    error_output = capsys.readouterr().err
    assert "--numbers-only-fill-plan requires --fill-plan" in error_output


def test_numbers_only_fill_plan_cli_does_not_touch_queue_state(tmp_path, monkeypatch) -> None:
    fill_plan_path = tmp_path / "fill_plan.json"
    fill_plan_path.write_text(json.dumps(normal_fill_plan(), ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "numbers_only.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--numbers-only-fill-plan",
            "--fill-plan",
            str(fill_plan_path),
            "--out",
            str(out_path),
        ],
    )
    webfill_cli.main()

    assert not (tmp_path / "queue_state.json").exists()
    written_files = {path.name for path in tmp_path.iterdir()}
    assert written_files == {"fill_plan.json", "numbers_only.json"}
