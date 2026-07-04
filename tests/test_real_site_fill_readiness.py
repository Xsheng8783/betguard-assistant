from __future__ import annotations

import copy
import json
import sys

import betguard.webfill.real_site_fill_readiness as real_site_fill_readiness_module
from betguard.webfill import cli as webfill_cli
from betguard.webfill.real_site_fill_preflight import build_real_site_fill_preflight_report
from betguard.webfill.real_site_fill_readiness import (
    BLOCKED,
    PASS,
    build_readiness_checklist,
    format_concise_readiness_checklist,
    format_pretty_readiness_checklist,
)
from tests.test_real_site_assisted_fill import (
    TWO_THREE,
    approved_queue_for,
    clean_profile,
    queue_for,
)


def _report_returner(report: dict):
    def _return(*args, **kwargs):
        return report

    return _return


def ready_v1_report() -> dict:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    return report


def test_pass_on_clean_ready_data() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == PASS
    assert all(report["checklist"].values())
    assert report["execution_action_summary"]


def test_blocked_when_approved_fill_queue_missing() -> None:
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")  # no approved_fill_queue
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["approved_fill_queue_present"] is False


def test_blocked_when_item_index_missing() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_readiness_checklist(queue, clean_profile(), item_index=99)

    assert report["status"] == BLOCKED
    assert report["checklist"]["item_found"] is False


def test_blocked_when_accepted_by_human_false() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    queue["approved_fill_queue"][0]["accepted_by_human"] = False
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["human_accepted"] is False


def test_blocked_for_needs_review_invalid_watchlist_states() -> None:
    for state in ("NEEDS_REVIEW", "INVALID", "WATCHLIST"):
        queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
        queue["approved_fill_queue"][0]["status"] = state
        report = build_readiness_checklist(queue, clean_profile(), item_index=0)

        assert report["status"] == BLOCKED, state
        assert report["checklist"]["review_state_excluded"] is False, state


def test_blocked_when_profile_validation_fails() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    broken_profile = clean_profile()
    broken_profile["danger_candidates"] = []
    report = build_readiness_checklist(queue, broken_profile, item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["profile_validation_ok"] is False


def test_blocked_when_profile_not_supplied() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_readiness_checklist(queue, None, item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["profile_supplied"] is False
    assert report["checklist"]["execution_actions_buildable"] is False


def test_blocked_when_preflight_is_not_ready() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    profile = clean_profile()
    # Break the position-verified triple so map-dry-run stays BLOCKED even
    # though the profile itself validates structurally (all 3 stars present).
    profile["amount_field_candidates"]["四星"] = [
        {
            "tag": "input",
            "className": "BDAll",
            "frame_name": "mainFrame",
            "candidate_selectors": ["input.BDAll"],
        }
    ]
    report = build_readiness_checklist(queue, profile, item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["preflight_status_ready"] is False


def test_blocked_when_execution_actions_cannot_be_built(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = ready_v1_report()
    tampered = copy.deepcopy(good_report)
    tampered["numbers"][0]["selector"] = ""  # converter refuses: no selector

    monkeypatch.setattr(
        real_site_fill_readiness_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["execution_actions_buildable"] is False


def test_blocked_when_groupset_value_appears(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = ready_v1_report()
    tampered = copy.deepcopy(good_report)
    tampered["amounts"][0]["selector"] = "#GroupSet_Value"

    monkeypatch.setattr(
        real_site_fill_readiness_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["groupset_value_excluded"] is False
    assert any("GroupSet_Value" in error for error in report["errors"])


def test_blocked_when_amount_not_position_verified(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = ready_v1_report()
    tampered = copy.deepcopy(good_report)
    tampered["amounts"][0]["position_verified"] = False

    monkeypatch.setattr(
        real_site_fill_readiness_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["amounts_position_verified"] is False


def test_blocked_when_forbidden_step_appears(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = ready_v1_report()
    for forbidden in ("submit", "confirm", "send_bet", "click_danger_button"):
        tampered = copy.deepcopy(good_report)
        tampered["amounts"][0]["type"] = forbidden

        monkeypatch.setattr(
            real_site_fill_readiness_module,
            "build_real_site_fill_preflight_report",
            _report_returner(tampered),
        )
        report = build_readiness_checklist(queue, clean_profile(), item_index=0)

        assert report["status"] == BLOCKED, forbidden
        assert report["checklist"]["no_forbidden_steps"] is False, forbidden


def test_blocked_when_final_flags_unsafe(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = ready_v1_report()

    for field, bad_value in (
        ("real_site_execute", True),
        ("auto_submit", True),
        ("executable", True),
        ("human_required_each_item", False),
    ):
        tampered = copy.deepcopy(good_report)
        tampered["final_decision"][field] = bad_value

        monkeypatch.setattr(
            real_site_fill_readiness_module,
            "build_real_site_fill_preflight_report",
            _report_returner(tampered),
        )
        report = build_readiness_checklist(queue, clean_profile(), item_index=0)

        assert report["status"] == BLOCKED, field
        assert report["checklist"]["final_flags_safe"] is False, field

    # Readiness report's own final_decision must always stay the safe constant
    # regardless of what the (tampered) upstream preflight report claimed.
    assert report["final_decision"]["real_site_execute"] is False
    assert report["final_decision"]["executable"] is False


def test_blocked_when_more_than_one_item_active() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    queue = approved_queue_for(text)
    queue["items"][1]["status"] = "CURRENT"  # now both item 0 and item 1 are active
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["one_item_at_a_time"] is False


def test_blocked_when_queue_shows_auto_next_evidence() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    queue = approved_queue_for(text)
    assert queue["items"][0]["status"] == "CURRENT"
    queue["items"][1]["status"] = "DONE"  # later item finished before item 0 did
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)

    assert report["status"] == BLOCKED
    assert report["checklist"]["no_auto_next_evidence"] is False


def test_concise_output_has_no_raw_selectors_outerhtml_or_candidates() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)
    output = format_concise_readiness_checklist(report)

    assert "Status: PASS" in output
    assert "outerHTML" not in output
    assert "PengBet.Value" not in output
    assert "candidate_selectors" not in output
    assert "button[data-number" not in output
    assert len(output.splitlines()) < 40


def test_pretty_output_renders_checklist_and_final_decision() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    report = build_readiness_checklist(queue, clean_profile(), item_index=0)
    output = format_pretty_readiness_checklist(report)

    assert "Live Assisted Fill Readiness Checklist" in output
    assert "Checklist:" in output
    assert "Final Decision:" in output
    assert "executable: false" in output


def test_cli_real_site_fill_readiness_concise(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(approved_queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8"
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(clean_profile(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-readiness",
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
    assert "Live Assisted Fill Readiness Checklist (concise)" in output
    assert "Status: PASS" in output


def test_cli_real_site_fill_readiness_json_default(tmp_path, monkeypatch, capsys) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(approved_queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8"
    )
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(clean_profile(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--real-site-fill-readiness",
            "--queue",
            str(queue_path),
            "--profile",
            str(profile_path),
        ],
    )
    webfill_cli.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["status"] == "PASS"
    assert parsed["final_decision"]["executable"] is False


def test_cli_real_site_fill_readiness_requires_queue_and_profile(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--real-site-fill-readiness"])
    try:
        webfill_cli.main()
        assert False, "expected SystemExit"
    except SystemExit:
        pass
    error_output = capsys.readouterr().err
    assert "--real-site-fill-readiness requires --queue and --profile" in error_output


def test_input_queue_and_profile_are_not_mutated() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    profile = clean_profile()
    queue_before = copy.deepcopy(queue)
    profile_before = copy.deepcopy(profile)
    build_readiness_checklist(queue, profile, item_index=0)
    assert queue == queue_before
    assert profile == profile_before
