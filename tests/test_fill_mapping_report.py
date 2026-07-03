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


# --- Selector Quality Guard / Precise Mapping v1 ---

B03_FRAME_URL = "https://w1.gts362.com/token/Front/B/B03"
INDEX_FRAME_URL = "https://w1.gts362.com/token/Front/Shared/Index"


def broad_html_candidate(target: str) -> dict:
    return {
        "tag": "html",
        "text": f"+++ var $Global = {{...huge page text containing {target} and much more...}}",
        "frame_name": "mainFrame",
        "frame_url": INDEX_FRAME_URL,
        "candidate_selectors": ["text=+++ var $Global = {...}", "html:has-text(\"06\")"],
    }


def precise_number_candidate(target: str) -> dict:
    return {
        "tag": "td",
        "text": target,
        "innerText": target,
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": [f"text={target}", f"td[id=\"num{target}\"]"],
    }


def broad_only_selector_report(plan: dict) -> dict:
    report = selector_report_for(plan)
    report["number_candidates"] = {
        number: [broad_html_candidate(number)] for number in plan.get("numbers", [])
    }
    return report


def test_broad_html_candidate_is_rejected_precise_b03_accepted() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # For 06: a broad html candidate first, then the precise B03 element.
    selector_report["number_candidates"]["06"] = [
        broad_html_candidate("06"),
        precise_number_candidate("06"),
    ]

    report = build_mapping_report(plan, selector_report)

    action = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    assert action["selector_found"] is True
    assert action["selector"] == "text=06"
    assert action["frame"] == "mainFrame"
    assert action["rejected_candidate_count"] == 1
    assert "$Global" not in action["selector"]
    assert "html:has-text" not in action["selector"]


def test_broad_only_number_candidates_force_blocked() -> None:
    plan = normal_fill_plan()
    report = build_mapping_report(plan, broad_only_selector_report(plan))

    assert report["status"] == "BLOCKED"
    action = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    assert action["selector_found"] is False
    assert action["confidence"] == "blocked"
    assert {"type": "number", "label": "06"} in report["missing"]


def test_amount_prefers_input_over_broad_container() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    first_star = plan["stars"][0]
    selector_report["amount_field_candidates"][first_star] = [
        {
            "tag": "div",
            "text": first_star,
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": ["div.amount-wrap"],
        },
        {
            "tag": "input",
            "name": f"{first_star}_amount",
            "text": first_star,
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": [f"input[name=\"{first_star}_amount\"]"],
        },
    ]

    report = build_mapping_report(plan, selector_report)

    action = next(
        a for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount" and a["plan_step"].get("star") == first_star
    )
    assert action["selector_found"] is True
    assert action["selector"] == f"input[name=\"{first_star}_amount\"]"
    assert action["rejected_candidate_count"] == 1


def test_amount_broad_only_container_blocks_report() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    first_star = plan["stars"][0]
    selector_report["amount_field_candidates"][first_star] = [
        {
            "tag": "table",
            "text": first_star,
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": ["table.bet-grid"],
        }
    ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert {"type": "amount", "star": first_star} in report["missing"]


# --- Selector Uniqueness / Precise Text Guard v2 ---


def class_only_number_candidate(target: str) -> dict:
    """A generic td.selectline2 cell shared by every number (no unique proof)."""
    return {
        "tag": "td",
        "text": target,
        "innerText": target,
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": ["td.selectline2"],
    }


def test_class_only_selector_shared_by_two_numbers_is_not_safe() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # 06 and 13 both collapse to the same generic td.selectline2 selector.
    selector_report["number_candidates"]["06"] = [class_only_number_candidate("06")]
    selector_report["number_candidates"]["13"] = [class_only_number_candidate("13")]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    action_06 = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    action_13 = next(a for a in report["actions"] if a["plan_step"].get("label") == "13")
    # Both must be flagged; they must NOT both be reported as safe/high confidence.
    assert action_06["selector_unsafe"] is True
    assert action_13["selector_unsafe"] is True
    assert action_06["confidence"] == "low"
    assert action_13["confidence"] == "low"
    assert {"type": "number", "label": "06", "reason": "selector shared by multiple numbers"} in report["missing"]
    assert {"type": "number", "label": "13", "reason": "selector shared by multiple numbers"} in report["missing"]


def test_precise_exact_text_selector_for_number_is_accepted() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    selector_report["number_candidates"]["06"] = [precise_number_candidate("06")]

    report = build_mapping_report(plan, selector_report)

    action_06 = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    assert action_06["selector"] == "text=06"
    assert action_06["selector_found"] is True
    assert action_06["unique_selector"] is True
    assert action_06["selector_unsafe"] is False
    assert action_06["confidence"] == "high"


def test_generic_class_only_number_selector_is_blocked() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # Only 06 is generic; the rest keep their precise text= selectors.
    selector_report["number_candidates"]["06"] = [class_only_number_candidate("06")]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    action_06 = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    assert action_06["selector"] == "td.selectline2"
    assert action_06["unique_selector"] is False
    assert action_06["selector_unsafe"] is True
    assert action_06["confidence"] == "low"
    assert {"type": "number", "label": "06", "reason": "selector not label-specific"} in report["missing"]


def test_real_number_cell_prefers_text_selector_over_class() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # Mirrors build_candidate_selectors output: class first, exact text after.
    selector_report["number_candidates"]["06"] = [
        {
            "tag": "td",
            "text": "06",
            "innerText": "06",
            "frame_name": "mainFrame",
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": ["td.selectline2", "text=06", 'td:has-text("06")'],
        }
    ]

    report = build_mapping_report(plan, selector_report)

    action_06 = next(a for a in report["actions"] if a["plan_step"].get("label") == "06")
    assert action_06["selector"] == "text=06"
    assert action_06["unique_selector"] is True
    assert action_06["selector_unsafe"] is False


def test_amount_shared_generic_input_class_is_rejected() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # Every star field collapses to the same generic input.BDAll selector.
    for star in plan.get("stars", []):
        selector_report["amount_field_candidates"][star] = [
            {
                "tag": "input",
                "text": star,
                "frame_url": B03_FRAME_URL,
                "candidate_selectors": ["input.BDAll"],
            }
        ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    first_star = plan["stars"][0]
    action = next(
        a for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount" and a["plan_step"].get("star") == first_star
    )
    assert action["unique_selector"] is False
    assert action["selector_unsafe"] is True
    assert action["confidence"] == "low"
    assert any(
        item.get("type") == "amount" and item.get("star") == first_star and item.get("reason")
        for item in report["missing"]
    )


# --- Amount Field Precise Mapping v1 ---


def unique_amount_candidate(star: str, selector: str) -> dict:
    return {
        "tag": "input",
        "text": star,
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": [selector],
    }


def _amount_action(report: dict, star: str) -> dict:
    return next(
        a
        for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount" and a["plan_step"].get("star") == star
    )


def test_amount_unique_id_name_selectors_are_safe() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # 二星 / 三星 / 四星 each get a distinct id or name selector.
    star_selectors: dict[str, str] = {}
    for index, star in enumerate(plan.get("stars", [])):
        selector = f"#Amount_{index}" if index % 2 == 0 else f'input[name="amt_{index}"]'
        star_selectors[star] = selector
        selector_report["amount_field_candidates"][star] = [unique_amount_candidate(star, selector)]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "SAFE"
    for star, selector in star_selectors.items():
        action = _amount_action(report, star)
        assert action["selector"] == selector
        assert action["unique_selector"] is True
        assert action["selector_unsafe"] is False
        assert action["confidence"] == "high"
    # Number selector guard must still hold alongside the amount fields.
    for number in plan.get("numbers", []):
        action = next(a for a in report["actions"] if a["plan_step"].get("label") == number)
        assert action["unique_selector"] is True
        assert action["confidence"] == "high"


def test_amount_shared_generic_input_class_stays_blocked() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # Every star collapses to the same generic input.BDAll selector.
    for star in plan.get("stars", []):
        selector_report["amount_field_candidates"][star] = [
            {
                "tag": "input",
                "text": star,
                "frame_url": B03_FRAME_URL,
                "candidate_selectors": ["input.BDAll"],
            }
        ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    for star in plan.get("stars", []):
        action = _amount_action(report, star)
        assert action["selector"] == "input.BDAll"
        assert action["unique_selector"] is False
        assert action["selector_unsafe"] is True
        assert action["confidence"] == "low"


def test_amount_star_specific_selector_preferred_over_generic_class() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    first_star = plan["stars"][0]
    # Discovery lists the shared generic class first and the star-specific
    # field second; the specific one must win ranking and be trusted.
    selector_report["amount_field_candidates"][first_star] = [
        {
            "tag": "input",
            "text": first_star,
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": ["input.BDAll"],
        },
        {
            "tag": "input",
            "text": first_star,
            "frame_url": B03_FRAME_URL,
            "candidate_selectors": [f"#Amount_{first_star}"],
        },
    ]

    report = build_mapping_report(plan, selector_report)

    action = _amount_action(report, first_star)
    assert action["selector"] == f"#Amount_{first_star}"
    assert action["unique_selector"] is True
    assert action["selector_unsafe"] is False
    assert action["confidence"] == "high"


def test_actions_summary_is_compact_without_candidate_dump() -> None:
    plan = normal_fill_plan()
    report = build_mapping_report(plan, selector_report_for(plan))

    assert report["actions_summary"]
    for entry in report["actions_summary"]:
        assert set(entry) == {
            "type",
            "label",
            "selector_found",
            "selector",
            "frame",
            "confidence",
            "rejected_candidate_count",
        }
        assert "selector_candidates" not in entry
