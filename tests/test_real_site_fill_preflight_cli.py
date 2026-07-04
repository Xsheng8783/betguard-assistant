from __future__ import annotations

import json
import sys

from betguard.review import build_report
from betguard.webfill import cli as webfill_cli


def clean_profile() -> dict:
    number_candidates = {
        f"{n:02d}": [
            {
                "tag": "td",
                "text": f"{n:02d}",
                "frame_name": "mainFrame",
                "candidate_selectors": [f"text={n:02d}"],
            }
        ]
        for n in range(1, 40)
    }

    def pengbet(left: float) -> dict:
        return {
            "tag": "input",
            "visible": True,
            "hidden": False,
            "id": "",
            "outerHTML": '<input data-bind="value: PengBet.Value" />',
            "box": {"top": 245, "left": left, "w": 60, "h": 20},
            "candidate_selectors": [],
            "frame_name": "mainFrame",
        }

    return {
        "profile_version": 1,
        "site_name": "gts362",
        "page_name": "539",
        "captured_at": "2026-07-05",
        "number_candidates": number_candidates,
        "amount_field_candidates": {
            "二星": [pengbet(65)],
            "三星": [pengbet(138)],
            "四星": [pengbet(211)],
        },
        "danger_candidates": [
            {
                "text": "送出注單",
                "tag": "button",
                "frame_name": "mainFrame",
                "candidate_selectors": ["text=送出注單"],
            }
        ],
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
    }


def approved_queue() -> dict:
    return {
        "approved_fill_queue": [
            {
                "index": 0,
                "original_fragment": "06-13-23-22/50",
                "bet_type": "normal",
                "review_result": build_report("06-13-23-22/50").to_dict(),
                "accepted_by_human": True,
            }
        ]
    }


def test_real_site_fill_preflight_concise_cli(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(approved_queue(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(clean_profile(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-preflight",
            "--queue",
            str(queue_path),
            "--profile",
            str(profile_path),
            "--item-index",
            "0",
            "--concise",
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Real-site Assisted Fill Preflight (concise)" in output
    assert "Status: READY_FOR_HUMAN_REVIEW" in output
    assert "executable: false" in output


def test_real_site_fill_preflight_json_default(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(approved_queue(), ensure_ascii=False), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(clean_profile(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-preflight",
            "--queue",
            str(queue_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["status"] == "READY_FOR_HUMAN_REVIEW"
    assert parsed["final_decision"]["executable"] is False


def test_real_site_fill_preflight_requires_queue_and_profile(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--real-site-fill-preflight"])
    try:
        webfill_cli.main()
        assert False, "expected SystemExit"
    except SystemExit:
        pass
    error_output = capsys.readouterr().err
    assert "--real-site-fill-preflight requires --queue and --profile" in error_output
