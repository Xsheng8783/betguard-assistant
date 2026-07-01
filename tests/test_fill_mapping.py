from betguard.review import build_report
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_plan import build_fill_plan


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def selector_candidate(label: str) -> dict:
    return {
        "tag": "button",
        "text": label,
        "candidate_selectors": [f"text={label}"],
    }


def full_selector_report() -> dict:
    return {
        "market_state": {"can_probe_bet_page": True},
        "number_candidates": {
            "06": [selector_candidate("06")],
            "13": [selector_candidate("13")],
            "23": [selector_candidate("23")],
            "22": [selector_candidate("22")],
        },
        "amount_field_candidates": {
            "二星": [selector_candidate("二星")],
            "三星": [selector_candidate("三星")],
            "四星": [selector_candidate("四星")],
        },
        "danger_candidates": [selector_candidate("送出注單")],
    }


def test_full_normal_mapping_can_map_all_required_fields() -> None:
    mapping = build_dry_run_mapping(normal_fill_plan(), full_selector_report())

    assert mapping["mode"] == "assisted_fill_dry_run_mapping_v0"
    assert mapping["executable"] is False
    assert mapping["requires_human_review"] is True
    assert mapping["can_map_all_required_fields"] is True
    assert mapping["missing"] == []
    assert mapping["errors"] == []
    assert len(mapping["mapped_steps"]) == 7


def test_missing_number_selector_is_reported() -> None:
    report = full_selector_report()
    report["number_candidates"].pop("06")

    mapping = build_dry_run_mapping(normal_fill_plan(), report)

    assert mapping["can_map_all_required_fields"] is False
    assert {"type": "number", "label": "06"} in mapping["missing"]


def test_missing_amount_selector_is_reported() -> None:
    report = full_selector_report()
    report["amount_field_candidates"].pop("二星")

    mapping = build_dry_run_mapping(normal_fill_plan(), report)

    assert mapping["can_map_all_required_fields"] is False
    assert {"type": "amount", "star": "二星"} in mapping["missing"]


def test_market_state_false_stops_mapping() -> None:
    report = full_selector_report()
    report["market_state"] = {"can_probe_bet_page": False}

    mapping = build_dry_run_mapping(normal_fill_plan(), report)

    assert mapping["can_map_all_required_fields"] is False
    assert mapping["errors"] == ["bet page unavailable; cannot map selectors"]
    assert mapping["mapped_steps"] == []


def test_missing_danger_candidates_warns_but_does_not_block_mapping() -> None:
    report = full_selector_report()
    report["danger_candidates"] = []

    mapping = build_dry_run_mapping(normal_fill_plan(), report)

    assert mapping["can_map_all_required_fields"] is True
    assert "danger candidates not found; assisted fill must remain disabled" in mapping["warnings"]


def test_mapping_does_not_create_submit_steps() -> None:
    mapping = build_dry_run_mapping(normal_fill_plan(), full_selector_report())

    mapped_types = [item["plan_step"]["type"] for item in mapping["mapped_steps"]]
    assert "submit" not in mapped_types
    assert "confirm" not in mapped_types
    assert "send_bet" not in mapped_types
    assert "submit" in mapping["forbidden_steps"]
    assert "confirm" in mapping["forbidden_steps"]
    assert "send_bet" in mapping["forbidden_steps"]
    assert "click_danger_button" in mapping["forbidden_steps"]


def test_executable_true_fill_plan_is_rejected() -> None:
    plan = normal_fill_plan()
    plan["executable"] = True

    mapping = build_dry_run_mapping(plan, full_selector_report())

    assert mapping["can_map_all_required_fields"] is False
    assert mapping["errors"] == ["fill plan executable must be false in v0"]


def test_fill_plan_errors_stop_mapping() -> None:
    plan = build_fill_plan(build_report("13.38.13 二三100").to_dict())

    mapping = build_dry_run_mapping(plan, full_selector_report())

    assert mapping["can_map_all_required_fields"] is False
    assert "review result is not ok" in mapping["errors"]
