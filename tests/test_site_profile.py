from __future__ import annotations

import re
from pathlib import Path

from betguard.review import build_report
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_mapping_report import build_mapping_report
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.site_profile import (
    build_site_profile,
    load_site_profile,
    profile_as_selector_report,
    save_site_profile,
    validate_site_profile,
)


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


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def test_build_site_profile_preserves_candidates_and_metadata() -> None:
    selector_report = complete_selector_report()

    profile = build_site_profile(
        selector_report,
        site_name="gts362",
        page_name="二三四星",
        captured_at="2026-07-03T00:00:00",
    )

    assert profile["profile_version"] == 1
    assert profile["site_name"] == "gts362"
    assert profile["page_name"] == "二三四星"
    assert profile["captured_at"] == "2026-07-03T00:00:00"
    assert profile["number_candidates"] == selector_report["number_candidates"]
    assert profile["amount_field_candidates"] == selector_report["amount_field_candidates"]
    assert profile["danger_candidates"] == selector_report["danger_candidates"]
    assert profile["market_state"] == selector_report["market_state"]
    assert profile["metadata"]["number_count"] == 39
    assert profile["metadata"]["amount_field_count"] == 3
    assert profile["metadata"]["danger_candidate_count"] == 1


def test_validate_site_profile_accepts_complete_fixture() -> None:
    profile = build_site_profile(
        complete_selector_report(),
        site_name="gts362",
        page_name="二三四星",
    )

    result = validate_site_profile(profile)

    assert result["status"] == "OK"
    assert result["errors"] == []


def test_validate_site_profile_flags_missing_danger_candidates() -> None:
    selector_report = complete_selector_report()
    selector_report["danger_candidates"] = []
    profile = build_site_profile(selector_report, site_name="gts362", page_name="二三四星")

    result = validate_site_profile(profile)

    assert result["status"] == "BLOCKED"
    assert any("danger" in error for error in result["errors"])


def test_validate_site_profile_flags_fewer_than_39_number_candidates() -> None:
    selector_report = complete_selector_report()
    selector_report["number_candidates"].pop("01")
    profile = build_site_profile(selector_report, site_name="gts362", page_name="二三四星")

    result = validate_site_profile(profile)

    assert result["status"] == "BLOCKED"
    assert any("39" in error for error in result["errors"])


def test_validate_site_profile_flags_missing_amount_fields() -> None:
    selector_report = complete_selector_report()
    selector_report["amount_field_candidates"].pop("二星")
    profile = build_site_profile(selector_report, site_name="gts362", page_name="二三四星")

    result = validate_site_profile(profile)

    assert result["status"] == "BLOCKED"
    assert any("amount" in error for error in result["errors"])


def test_validate_site_profile_flags_missing_top_level_keys() -> None:
    profile = build_site_profile(complete_selector_report(), site_name="gts362", page_name="二三四星")
    del profile["market_state"]

    result = validate_site_profile(profile)

    assert result["status"] == "BLOCKED"
    assert any("market_state" in error for error in result["errors"])


def test_save_and_load_site_profile_round_trip(tmp_path: Path) -> None:
    profile = build_site_profile(
        complete_selector_report(),
        site_name="gts362",
        page_name="二三四星",
        captured_at="2026-07-03T00:00:00",
    )
    path = tmp_path / "profile.json"

    save_site_profile(profile, path)
    loaded = load_site_profile(path)

    assert loaded == profile


def test_profile_as_selector_report_works_with_existing_mapping_functions() -> None:
    profile = build_site_profile(complete_selector_report(), site_name="gts362", page_name="二三四星")
    selector_report = profile_as_selector_report(profile)

    mapping = build_dry_run_mapping(normal_fill_plan(), selector_report)
    report = build_mapping_report(normal_fill_plan(), selector_report)

    assert mapping["can_map_all_required_fields"] is True
    assert mapping["errors"] == []
    assert report["status"] == "SAFE"
    assert report["final_decision"]["executable"] is False


def test_incomplete_profile_missing_danger_candidates_stays_blocked_via_mapping_report() -> None:
    selector_report = complete_selector_report()
    selector_report["danger_candidates"] = []
    profile = build_site_profile(selector_report, site_name="gts362", page_name="二三四星")

    validation = validate_site_profile(profile)
    assert validation["status"] == "BLOCKED"

    mapped_selector_report = profile_as_selector_report(profile)
    report = build_mapping_report(normal_fill_plan(), mapped_selector_report)

    assert report["status"] == "BLOCKED"
    assert {"type": "danger", "reason": "danger buttons not verified"} in report["missing"]


def test_site_profile_module_has_no_browser_automation_code() -> None:
    source = Path("src/betguard/webfill/site_profile.py").read_text(encoding="utf-8")

    forbidden = re.compile(r"click|submit|playwright|selenium|browser", re.IGNORECASE)
    assert not forbidden.search(source)
