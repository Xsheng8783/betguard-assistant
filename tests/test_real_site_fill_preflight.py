from __future__ import annotations

import copy

from betguard.review import build_report
from betguard.webfill.real_site_fill_preflight import (
    BLOCKED,
    READY_FOR_HUMAN_REVIEW,
    build_real_site_fill_preflight_report,
    contains_forbidden_steps,
    format_concise_real_site_fill_preflight,
    format_pretty_real_site_fill_preflight,
)


def review_result(text: str = "06-13-23-22/50") -> dict:
    return build_report(text).to_dict()


def approved_entry(*, index: int = 0, accepted: bool = True, status: str | None = None) -> dict:
    entry = {
        "index": index,
        "original_fragment": "06-13-23-22/50",
        "bet_type": "normal",
        "review_result": review_result(),
        "accepted_by_human": accepted,
    }
    if status is not None:
        entry["status"] = status
    return entry


def approved_queue(entries: list[dict]) -> dict:
    return {"approved_fill_queue": entries}


def pengbet_candidate(left: float) -> dict:
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
    amount_field_candidates = {
        "二星": [pengbet_candidate(65)],
        "三星": [pengbet_candidate(138)],
        "四星": [pengbet_candidate(211)],
    }
    danger_candidates = [
        {
            "text": "送出注單",
            "tag": "button",
            "frame_name": "mainFrame",
            "candidate_selectors": ["text=送出注單"],
        }
    ]
    return {
        "profile_version": 1,
        "site_name": "gts362",
        "page_name": "539",
        "captured_at": "2026-07-05",
        "number_candidates": number_candidates,
        "amount_field_candidates": amount_field_candidates,
        "danger_candidates": danger_candidates,
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
    }


def test_blocked_when_approved_fill_queue_missing() -> None:
    report = build_real_site_fill_preflight_report({}, clean_profile(), item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["source_is_approved_fill_queue"] is False
    assert any("approved_fill_queue" in error for error in report["errors"])


def test_blocked_when_item_index_invalid() -> None:
    queue = approved_queue([approved_entry(index=0)])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=7)
    assert report["status"] == BLOCKED
    assert any("not found" in error for error in report["errors"])


def test_blocked_when_accepted_by_human_false() -> None:
    queue = approved_queue([approved_entry(accepted=False)])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["human_accepted"] is False


def test_blocked_when_accepted_by_human_missing() -> None:
    entry = approved_entry()
    del entry["accepted_by_human"]
    queue = approved_queue([entry])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["human_accepted"] is False


def test_blocked_for_needs_review_invalid_watchlist_states() -> None:
    for state in ("NEEDS_REVIEW", "INVALID", "WATCHLIST"):
        queue = approved_queue([approved_entry(status=state)])
        report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
        assert report["status"] == BLOCKED, state
        assert report["gates"]["review_state_excluded"] is False, state


def test_blocked_when_profile_validation_fails() -> None:
    broken_profile = clean_profile()
    broken_profile["danger_candidates"] = []
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, broken_profile, item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["profile_validation_ok"] is False
    assert report["profile_validation_status"] == "BLOCKED"


def test_blocked_when_map_dry_run_is_blocked_missing_amount_field() -> None:
    profile = clean_profile()
    del profile["amount_field_candidates"]["四星"]
    profile["amount_field_candidates"]["四星"] = []
    # validate_site_profile requires all three stars present with candidates,
    # so this profile itself becomes BLOCKED -- use a variant that passes
    # profile validation but still fails the underlying map-dry-run: give
    # 四星 a non-PengBet candidate so it can never resolve uniquely.
    profile["amount_field_candidates"]["四星"] = [
        {
            "tag": "input",
            "className": "BDAll",
            "frame_name": "mainFrame",
            "candidate_selectors": ["input.BDAll"],
        }
    ]
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, profile, item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["map_dry_run_status_safe"] is False


def test_blocked_when_groupset_value_would_be_accepted_as_safe_amount_selector() -> None:
    """Regression: the legacy ranking path (not position-verified) has no
    id-based GroupSet_Value exclusion, so a lone GroupSet_Value candidate can
    resolve as a unique, high-confidence amount selector if it is the only
    candidate offered for a star. The preflight must re-check and BLOCK this
    even though build_mapping_report alone might call it SAFE.
    """
    profile = clean_profile()
    # Replace the position-verified triple with a single ambiguous candidate
    # per star so the position-verified path cannot apply, forcing the
    # legacy first-candidate ranking to run instead.
    profile["amount_field_candidates"] = {
        "二星": [
            {
                "tag": "input",
                "id": "GroupSet_Value",
                "frame_name": "mainFrame",
                "candidate_selectors": ["#GroupSet_Value"],
            }
        ],
        "三星": [pengbet_candidate(138)],
        "四星": [pengbet_candidate(211)],
    }
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, profile, item_index=0)
    assert report["status"] == BLOCKED
    assert report["gates"]["groupset_value_excluded"] is False
    assert any("GroupSet_Value" in error for error in report["errors"])


def test_ready_for_human_review_on_clean_verified_data() -> None:
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert report["status"] == READY_FOR_HUMAN_REVIEW
    assert all(report["gates"].values())
    assert len(report["amounts"]) == 3
    assert all(amount["position_verified"] for amount in report["amounts"])
    assert len(report["numbers"]) == 4


def test_final_decision_always_safe_regardless_of_status() -> None:
    ready_report = build_real_site_fill_preflight_report(
        approved_queue([approved_entry()]), clean_profile(), item_index=0
    )
    blocked_report = build_real_site_fill_preflight_report({}, clean_profile(), item_index=0)
    for report in (ready_report, blocked_report):
        decision = report["final_decision"]
        assert decision["executable"] is False
        assert decision["real_site_execute"] is False
        assert decision["auto_submit"] is False
        assert decision["human_required_each_item"] is True


def test_danger_buttons_clicked_is_always_empty() -> None:
    ready_report = build_real_site_fill_preflight_report(
        approved_queue([approved_entry()]), clean_profile(), item_index=0
    )
    blocked_report = build_real_site_fill_preflight_report({}, clean_profile(), item_index=0)
    assert ready_report["danger_check"]["danger_buttons_clicked"] == []
    assert blocked_report["danger_check"]["danger_buttons_clicked"] == []


def test_input_queue_and_profile_are_not_mutated() -> None:
    queue = approved_queue([approved_entry()])
    profile = clean_profile()
    queue_before = copy.deepcopy(queue)
    profile_before = copy.deepcopy(profile)
    build_real_site_fill_preflight_report(queue, profile, item_index=0)
    assert queue == queue_before
    assert profile == profile_before


def test_forbidden_steps_are_detected() -> None:
    plan_with_submit = {"planned_steps": [{"type": "select_number", "label": "06"}, {"type": "submit"}]}
    assert contains_forbidden_steps(plan_with_submit) == ["submit"]

    plan_with_confirm = {"planned_steps": [{"type": "confirm"}]}
    assert contains_forbidden_steps(plan_with_confirm) == ["confirm"]

    plan_with_send_bet = {"planned_steps": [{"type": "send_bet"}]}
    assert contains_forbidden_steps(plan_with_send_bet) == ["send_bet"]

    plan_with_danger_click = {"planned_steps": [{"type": "click_danger_button"}]}
    assert contains_forbidden_steps(plan_with_danger_click) == ["click_danger_button"]

    clean_plan = {"planned_steps": [{"type": "select_number", "label": "06"}, {"type": "set_amount", "star": "二星", "amount": 50}]}
    assert contains_forbidden_steps(clean_plan) == []


def test_concise_output_has_no_raw_page_text_or_candidate_dumps() -> None:
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    output = format_concise_real_site_fill_preflight(report)
    assert "Status: READY_FOR_HUMAN_REVIEW" in output
    assert "outerHTML" not in output
    assert "PengBet.Value" not in output
    assert "candidate_selectors" not in output
    assert len(output.splitlines()) < 40


def test_pretty_output_renders_gates_and_final_decision() -> None:
    queue = approved_queue([approved_entry()])
    report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    output = format_pretty_real_site_fill_preflight(report)
    assert "Real-site Assisted Fill Preflight" in output
    assert "Gates:" in output
    assert "Final Decision:" in output
    assert "executable: false" in output
