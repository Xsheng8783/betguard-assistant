from betguard.review import build_report
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.fill_plan import build_fill_plan


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
        "number_candidates": {
            number: [candidate(number)]
            for number in plan.get("numbers", [])
        },
        "amount_field_candidates": {
            star: [candidate(star, frame="amountFrame")]
            for star in plan.get("stars", [])
        },
        "danger_candidates": [
            candidate("送出注單"),
            candidate("確認"),
        ],
    }


def test_full_mapping_report_is_safe_but_never_executable() -> None:
    plan = normal_fill_plan()
    report = build_mapping_report(plan, selector_report_for(plan))

    assert report["mode"] == "assisted_fill_dry_run_mapping_report"
    assert report["status"] == "SAFE"
    assert report["final_decision"]["executable"] is False
    assert report["danger_check"]["danger_candidates_found"] is True
    assert report["danger_check"]["dangerous_buttons_detected"] == ["送出注單", "確認"]
    assert report["actions"][0]["selector_found"] is True
    assert report["actions"][0]["confidence"] == "high"


def test_missing_number_selector_blocks_report() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    selector_report["number_candidates"].pop("06")

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert {"type": "number", "label": "06"} in report["missing"]


def test_missing_amount_selector_blocks_report() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    first_star = plan["stars"][0]
    selector_report["amount_field_candidates"].pop(first_star)

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert {"type": "amount", "star": first_star} in report["missing"]


def test_empty_danger_candidates_blocks_report() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    selector_report["danger_candidates"] = []

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert {"type": "danger", "reason": "danger buttons not verified"} in report["missing"]
    assert "danger buttons not verified; cannot proceed beyond dry-run" in report["warnings"]


def test_fill_plan_errors_block_report() -> None:
    plan = build_fill_plan(build_report("13.38.13 二三100").to_dict())
    selector_report = selector_report_for(normal_fill_plan())

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert "review result is not ok" in report["errors"]


def test_executable_true_plan_blocks_report() -> None:
    plan = normal_fill_plan()
    plan["executable"] = True
    selector_report = selector_report_for(normal_fill_plan())

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert "fill plan executable must be false in v0" in report["errors"]


def test_pretty_report_contains_required_sections() -> None:
    plan = normal_fill_plan()
    report = build_mapping_report(plan, selector_report_for(plan))

    pretty = format_pretty_mapping_report(report)

    assert "Assisted Fill Dry-run Mapping" in pretty
    assert "Status: SAFE" in pretty
    assert "Market:" in pretty
    assert "Actions:" in pretty
    assert "Danger Check:" in pretty
    assert "Final Decision:" in pretty
    assert "- executable: false" in pretty


def test_report_can_accept_prebuilt_mapping_result() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    mapping = build_dry_run_mapping(plan, selector_report)

    report = build_mapping_report(plan, selector_report, mapping_result=mapping)

    assert report["status"] == "SAFE"
