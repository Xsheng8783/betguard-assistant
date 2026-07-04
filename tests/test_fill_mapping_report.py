from betguard.review import build_report
from betguard.webfill.fill_mapping import (
    AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT,
    _b03_amount_diagnostic_candidate,
    build_dry_run_mapping,
)
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.fill_plan import build_fill_plan, to_numbers_only_plan


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


# --- Amount Field Diagnostic Report v1 ---


def shared_group_set_candidate(star: str, *, index: int = 0) -> dict:
    """Amount candidate resolving to the shared #GroupSet_Value id (nested diag)."""
    return {
        "tag": "input",
        "text": star,
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": ["#GroupSet_Value"],
        "amount_diagnostic": {
            "matched_label": star,
            "source_index": index,
            "id": "GroupSet_Value",
            "name": "GroupSet_Value",
            "className": "BDAll RightText",
            "parentText": f"每碰金額 {star} 本金",
            "grandparentText": "539 下注資訊",
            "outerHTML": "<input id='GroupSet_Value' class='BDAll'>",
            "diagnostic_source": "automatic_mapping",
        },
    }


def _amount_diagnostics(report: dict) -> list[dict]:
    return report.get("amount_field_diagnostics") or []


def test_shared_group_set_value_creates_diagnostics_and_blocks() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    stars = plan["stars"]
    selector_report["amount_field_candidates"][stars[0]] = [shared_group_set_candidate(stars[0], index=25)]
    selector_report["amount_field_candidates"][stars[1]] = [shared_group_set_candidate(stars[1], index=21)]
    # Third star keeps a distinct unique id so it stays out of the ambiguity list.
    selector_report["amount_field_candidates"][stars[2]] = [
        unique_amount_candidate(stars[2], "#GroupSet_Value_4")
    ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_status"] == "BLOCKED"
    assert set(report["ambiguous_amount_fields"]) == {stars[0], stars[1]}
    assert report["shared_amount_selectors"] == ["#GroupSet_Value"]

    diag_stars = {entry["star"] for entry in _amount_diagnostics(report)}
    assert diag_stars == {stars[0], stars[1]}
    entry0 = next(e for e in _amount_diagnostics(report) if e["star"] == stars[0])
    assert entry0["selector"] == "#GroupSet_Value"
    assert entry0["shared"] is True
    assert entry0["shared_with"] == [stars[1]]
    assert entry0["confidence"] == "low"
    assert entry0["source_index"] == 25
    assert "GroupSet_Value" in entry0["blocked_reason"]

    # Number selectors must be unaffected by the amount diagnostics.
    for number in plan["numbers"]:
        action = next(a for a in report["actions"] if a["plan_step"].get("label") == number)
        assert action["confidence"] == "high"
        assert action["unique_selector"] is True


def test_shared_input_bdall_creates_diagnostics_and_blocks() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    for star in plan["stars"]:
        selector_report["amount_field_candidates"][star] = [
            {
                "tag": "input",
                "text": star,
                "frame_url": B03_FRAME_URL,
                "candidate_selectors": ["input.BDAll"],
                "amount_diagnostic": {
                    "matched_label": star,
                    "source_index": 3,
                    "className": "BDAll",
                    "parentText": f"每碰金額 {star}",
                    "diagnostic_source": "automatic_mapping",
                },
            }
        ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_status"] == "BLOCKED"
    assert set(report["ambiguous_amount_fields"]) == set(plan["stars"])
    assert report["shared_amount_selectors"] == ["input.BDAll"]
    for entry in _amount_diagnostics(report):
        assert entry["selector"] == "input.BDAll"
        assert entry["shared"] is True


def test_amount_diagnostics_include_parent_context_excerpts() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    for star in plan["stars"][:2]:
        selector_report["amount_field_candidates"][star] = [shared_group_set_candidate(star)]

    report = build_mapping_report(plan, selector_report)

    entry = _amount_diagnostics(report)[0]
    assert entry["parentText"]
    assert entry["grandparentText"]
    assert entry["diagnostic_source"] == "automatic_mapping"


def test_amount_diagnostic_candidate_bounds_outer_html_and_keeps_index() -> None:
    element = {
        "tag": "input",
        "text": "二星",
        "outerHTML": "<input " + "x" * 5000 + ">",
        "id": "GroupSet_Value",
        "name": "amt",
        "className": "BDAll",
        "parentText": "每碰金額 二星",
    }

    candidate = _b03_amount_diagnostic_candidate(element, star="二星", index=25)
    diag = candidate["amount_diagnostic"]

    assert diag["source_index"] == 25
    assert diag["matched_label"] == "二星"
    assert diag["id"] == "GroupSet_Value"
    assert diag["diagnostic_source"] == "automatic_mapping"
    assert 0 < len(diag["outerHTML"]) <= AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT
    assert len(diag["outerHTML"]) < 5000
    # The shared _b03_selector_candidate contract stays intact.
    assert candidate["candidate_selectors"]


def test_amount_diagnostics_do_not_require_structured_data_attributes() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    # Candidates carry no data-* structured fields at all.
    for star in plan["stars"][:2]:
        selector_report["amount_field_candidates"][star] = [shared_group_set_candidate(star)]

    report = build_mapping_report(plan, selector_report)

    assert _amount_diagnostics(report)
    for entry in _amount_diagnostics(report):
        assert not any(str(key).startswith("data-") for key in entry)


def test_unique_amount_selectors_produce_no_ambiguity_diagnostics() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    for index, star in enumerate(plan["stars"]):
        selector_report["amount_field_candidates"][star] = [
            unique_amount_candidate(star, f"#Amount_{index}")
        ]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "SAFE"
    assert report["amount_field_status"] == "SAFE"
    assert report["ambiguous_amount_fields"] == []
    assert report["shared_amount_selectors"] == []
    assert _amount_diagnostics(report) == []
    for number in plan["numbers"]:
        action = next(a for a in report["actions"] if a["plan_step"].get("label") == number)
        assert action["confidence"] == "high"
        assert action["unique_selector"] is True


def test_amount_diagnostics_agree_with_selector_guard_decision() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    for star in plan["stars"][:2]:
        selector_report["amount_field_candidates"][star] = [shared_group_set_candidate(star)]

    report = build_mapping_report(plan, selector_report)

    guard_unsafe = {
        a["plan_step"]["star"]
        for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount" and a.get("selector_unsafe")
    }
    diag_unsafe = {entry["star"] for entry in _amount_diagnostics(report)}
    # Diagnostics must reflect exactly the guard's unsafe decision.
    assert guard_unsafe == diag_unsafe
    assert guard_unsafe == set(report["ambiguous_amount_fields"])


def test_pretty_report_contains_amount_diagnostics_section() -> None:
    plan = normal_fill_plan()
    selector_report = selector_report_for(plan)
    for star in plan["stars"][:2]:
        selector_report["amount_field_candidates"][star] = [shared_group_set_candidate(star)]

    report = build_mapping_report(plan, selector_report)
    pretty = format_pretty_mapping_report(report)

    assert "Amount Field Diagnostics:" in pretty
    assert "Shared selector: #GroupSet_Value" in pretty
    assert "Result: amount fields are not safe for assisted fill" in pretty
    assert "attributes: id=GroupSet_Value" in pretty


# --- Numbers-only Assisted Plan v1 ---


def shared_group_set_selector_report(plan: dict) -> dict:
    """The real 539 scenario: numbers resolve cleanly, amounts share #GroupSet_Value."""
    report = selector_report_for(plan)
    for star in plan.get("stars", []):
        report["amount_field_candidates"][star] = [shared_group_set_candidate(star)]
    return report


def test_numbers_only_plan_maps_numbers_but_not_amounts() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    assert all(a["plan_step"].get("type") != "set_amount" for a in report["actions"])
    assert any(a["plan_step"].get("type") == "select_number" for a in report["actions"])
    for number in plan["numbers"]:
        action = next(a for a in report["actions"] if a["plan_step"].get("label") == number)
        assert action["selector_found"] is True
        assert action["confidence"] == "high"


def test_numbers_only_plan_is_safe_overall_despite_shared_amount_selector() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    # Numbers resolve cleanly and no amount action was ever attempted, so the
    # overall report is SAFE even though #GroupSet_Value would BLOCK a full plan.
    assert report["status"] == "SAFE"


def test_numbers_only_plan_reports_amount_manual_required() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    assert report["amount_manual_required"] is True
    assert report["amount_field_status"] == "SKIPPED_BY_DESIGN"
    assert len(report["amount_steps_removed"]) == 3
    removed_stars = {step["star"] for step in report["amount_steps_removed"]}
    assert removed_stars == set(plan["stars"])


def test_numbers_only_plan_never_forces_group_set_value_safe() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    # #GroupSet_Value is never evaluated at all for a true numbers-only plan
    # (no amount action exists to evaluate it against) -- it must not appear
    # anywhere as a "safe" or "resolved" selector.
    assert report["ambiguous_amount_fields"] == []
    assert report["shared_amount_selectors"] == []
    assert all(a.get("selector") != "#GroupSet_Value" for a in report["actions"])


def test_amount_manual_required_flag_does_not_hide_a_real_shared_amount_action() -> None:
    """A plan that falsely claims amount_manual_required but still carries a
    real set_amount step must not have that step hidden or force-passed."""
    plan = normal_fill_plan()
    selector_report = shared_group_set_selector_report(plan)
    plan["amount_manual_required"] = True  # falsely claimed; planned_steps unchanged

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_status"] == "BLOCKED"
    assert report["shared_amount_selectors"] == ["#GroupSet_Value"]


def test_pretty_numbers_only_report_shows_manual_amount_section() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)
    pretty = format_pretty_mapping_report(report)

    assert "Amount Fields (manual entry required):" in pretty
    assert "amount_manual_required: true" in pretty
    assert "amount fields skipped by design" in pretty
    assert "no amount fill action will be executed" in pretty
    for star in plan["stars"]:
        assert f"{star}: 50 (enter manually)" in pretty


def test_pretty_full_plan_does_not_show_manual_amount_section() -> None:
    plan = normal_fill_plan()
    report = build_mapping_report(plan, selector_report_for(plan))

    pretty = format_pretty_mapping_report(report)

    assert "Amount Fields (manual entry required):" not in pretty
    assert report["amount_manual_required"] is False


def test_numbers_only_plan_does_not_change_danger_or_forbidden_steps() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    selector_report = shared_group_set_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    assert report["danger_check"]["danger_candidates_found"] is True
    assert report["danger_check"]["dangerous_buttons_detected"] == ["送出注單", "確認"]
    assert report["forbidden_steps"] == ["submit", "confirm", "send_bet", "click_danger_button"]
    assert report["final_decision"]["executable"] is False
