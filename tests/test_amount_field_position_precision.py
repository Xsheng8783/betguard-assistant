from betguard.review import build_report
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.fill_plan import build_fill_plan, to_numbers_only_plan


B03_FRAME_URL = "https://w1.gts362.com/token/Front/B/B03"
PENGBET_HTML = (
    '<input class="BDAll fontb RightText" size="5" maxlength="9" '
    'data-bind="value: PengBet.Value, hasfocus: PengBet.InFocus, '
    "enable: PengBet.Enabled, event: { 'keyup': OnChkVal, blur: OnBlur }\">"
)


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def pengbet_candidate(*, top: float, left: float, visible: bool = True, hidden: bool = False) -> dict:
    return {
        "tag": "input",
        "id": "",
        "visible": visible,
        "hidden": hidden,
        "box": {"top": top, "left": left, "w": 55, "h": 19},
        "outerHTML": PENGBET_HTML,
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": ["input.BDAll"],
        "parentText": "",
        "grandparentText": "",
    }


def group_set_value_candidate(*, top: float = 404, left: float = 56, fake_pengbet_html: bool = False) -> dict:
    return {
        "tag": "input",
        "id": "GroupSet_Value",
        "visible": True,
        "hidden": False,
        "box": {"top": top, "left": left, "w": 48, "h": 21},
        "outerHTML": PENGBET_HTML if fake_pengbet_html else '<input id="GroupSet_Value" type="text" value="0">',
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": ["#GroupSet_Value"],
        "parentText": "",
        "grandparentText": "(分組序號範圍 1~999)",
    }


def hidden_ta_candidate(element_id: str, *, top: float = 245, left: float = 65) -> dict:
    return {
        "tag": "input",
        "id": element_id,
        "visible": False,
        "hidden": False,
        "box": {"top": top, "left": left, "w": 20, "h": 20},
        "outerHTML": f'<input id="{element_id}" class="BDAll fontRed3 fontb font15 CenterText input1">',
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": [f"#{element_id}"],
        "parentText": "",
        "grandparentText": "",
    }


def quick_input_candidate(*, top: float = 101, left: float = 323) -> dict:
    return {
        "tag": "input",
        "id": "",
        "visible": True,
        "hidden": False,
        "box": {"top": top, "left": left, "w": 32, "h": 23},
        "outerHTML": '<input class="btn1" type="text" data-bind="value: QkNums, event: { }">',
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": ['input[type="text"]'],
        "parentText": "號碼 送出",
        "grandparentText": "號碼 送出",
    }


def number_candidate(label: str) -> dict:
    return {
        "tag": "td",
        "text": label,
        "innerText": label,
        "frame_name": "mainFrame",
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": [f"text={label}"],
    }


def danger_candidate(label: str) -> dict:
    return {
        "tag": "button",
        "text": label,
        "frame_name": "mainFrame",
        "candidate_selectors": [f"text={label}"],
    }


def base_selector_report(plan: dict) -> dict:
    return {
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
        "number_candidates": {number: [number_candidate(number)] for number in plan.get("numbers", [])},
        "amount_field_candidates": {"二星": [], "三星": [], "四星": []},
        "danger_candidates": [danger_candidate("送出注單"), danger_candidate("確認")],
    }


def real_539_style_selector_report(plan: dict) -> dict:
    """Mirrors the actual discovery output: all 3 PengBet.Value inputs and the
    #GroupSet_Value decoy get filed (unevenly) under 二星/三星, nothing under 四星."""
    report = base_selector_report(plan)
    three_inputs = [
        pengbet_candidate(top=245, left=65),
        pengbet_candidate(top=245, left=138),
        pengbet_candidate(top=245, left=211),
    ]
    report["amount_field_candidates"]["二星"] = three_inputs + [group_set_value_candidate()]
    report["amount_field_candidates"]["三星"] = [group_set_value_candidate()]
    report["amount_field_candidates"]["四星"] = []
    return report


# --- Position-verified mapping succeeds ---


def test_three_pengbet_inputs_map_to_stars_by_left_order() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)

    report = build_mapping_report(plan, selector_report)

    actions_by_star = {
        a["plan_step"]["star"]: a
        for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount"
    }
    assert actions_by_star["二星"]["selector"] == 'input[data-bind*="PengBet.Value"] >> nth=0'
    assert actions_by_star["三星"]["selector"] == 'input[data-bind*="PengBet.Value"] >> nth=1'
    assert actions_by_star["四星"]["selector"] == 'input[data-bind*="PengBet.Value"] >> nth=2'
    for star, action in actions_by_star.items():
        assert action["selector_found"] is True
        assert action["unique_selector"] is True
        assert action["selector_unsafe"] is False
        assert action["confidence"] == "high"
    # Three distinct targets, not the same field reused.
    assert len({a["selector"] for a in actions_by_star.values()}) == 3


def test_full_amount_map_dry_run_is_safe_when_position_verified() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "SAFE"
    assert report["amount_field_status"] == "SAFE"
    assert report["ambiguous_amount_fields"] == []
    assert report["shared_amount_selectors"] == []
    assert report["final_decision"]["executable"] is False


# --- #GroupSet_Value and other decoys must never be used as amount fields ---


def test_group_set_value_is_rejected_as_amount_field() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)

    report = build_mapping_report(plan, selector_report)

    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert action["selector"] != "#GroupSet_Value"
            assert "GroupSet_Value" not in action["selector"]


def test_group_set_value_rejected_even_with_fake_pengbet_marker() -> None:
    """Adversarial: GroupSet_Value's outerHTML claims PengBet.Value too, but its
    id must still hard-exclude it -- the id check is not skippable."""
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)
    # Corrupt the decoy so it would otherwise pass the PengBet.Value filter.
    selector_report["amount_field_candidates"]["二星"][-1] = group_set_value_candidate(fake_pengbet_html=True)
    selector_report["amount_field_candidates"]["三星"][0] = group_set_value_candidate(fake_pengbet_html=True)

    report = build_mapping_report(plan, selector_report)

    # Still exactly 3 real PengBet inputs after excluding the decoy by id, so
    # position verification still succeeds and #GroupSet_Value never appears.
    assert report["status"] == "SAFE"
    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert action["selector"] != "#GroupSet_Value"


def test_hidden_ta_fields_are_rejected() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)
    selector_report["amount_field_candidates"]["二星"].append(hidden_ta_candidate("ta_0_0"))
    selector_report["amount_field_candidates"]["三星"].append(hidden_ta_candidate("ta_1_2"))

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "SAFE"
    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert "ta_0_0" not in action["selector"]
            assert "ta_1_2" not in action["selector"]


def test_quick_input_number_submit_field_is_rejected() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)
    selector_report["amount_field_candidates"]["二星"].append(quick_input_candidate())

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "SAFE"
    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert action["selector"].startswith('input[data-bind*="PengBet.Value"]')


# --- Structural verification must BLOCK on any ambiguity, never guess ---


def test_only_two_pengbet_inputs_blocks() -> None:
    plan = normal_fill_plan()
    selector_report = base_selector_report(plan)
    two_inputs = [pengbet_candidate(top=245, left=65), pengbet_candidate(top=245, left=138)]
    selector_report["amount_field_candidates"]["二星"] = two_inputs + [group_set_value_candidate()]
    selector_report["amount_field_candidates"]["三星"] = [group_set_value_candidate()]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert action["selector"] != 'input[data-bind*="PengBet.Value"] >> nth=0'


def test_four_pengbet_inputs_blocks() -> None:
    plan = normal_fill_plan()
    selector_report = base_selector_report(plan)
    four_inputs = [
        pengbet_candidate(top=245, left=65),
        pengbet_candidate(top=245, left=138),
        pengbet_candidate(top=245, left=211),
        pengbet_candidate(top=245, left=284),
    ]
    selector_report["amount_field_candidates"]["二星"] = four_inputs
    selector_report["amount_field_candidates"]["三星"] = [group_set_value_candidate()]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"


def test_pengbet_inputs_on_different_rows_blocks() -> None:
    plan = normal_fill_plan()
    selector_report = base_selector_report(plan)
    mismatched_row_inputs = [
        pengbet_candidate(top=245, left=65),
        pengbet_candidate(top=245, left=138),
        pengbet_candidate(top=999, left=211),  # different row -- not the same amount table
    ]
    selector_report["amount_field_candidates"]["二星"] = mismatched_row_inputs
    selector_report["amount_field_candidates"]["三星"] = [group_set_value_candidate()]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"


def test_hidden_pengbet_inputs_do_not_count_toward_the_three() -> None:
    plan = normal_fill_plan()
    selector_report = base_selector_report(plan)
    selector_report["amount_field_candidates"]["二星"] = [
        pengbet_candidate(top=245, left=65),
        pengbet_candidate(top=245, left=138, visible=False),  # hidden duplicate, must not count
        pengbet_candidate(top=245, left=211),
        group_set_value_candidate(),
    ]
    selector_report["amount_field_candidates"]["三星"] = [group_set_value_candidate()]

    report = build_mapping_report(plan, selector_report)

    # Only 2 visible PengBet inputs -- must stay BLOCKED, not guess a 3rd.
    assert report["status"] == "BLOCKED"


def test_shared_selector_is_not_forced_safe_when_verification_fails() -> None:
    plan = normal_fill_plan()
    selector_report = base_selector_report(plan)
    # No PengBet.Value structure at all -- falls back to the old ranking path,
    # which must still correctly BLOCK on the shared #GroupSet_Value selector.
    for star in plan["stars"]:
        selector_report["amount_field_candidates"][star] = [group_set_value_candidate()]

    report = build_mapping_report(plan, selector_report)

    assert report["status"] == "BLOCKED"
    assert report["shared_amount_selectors"] == ["#GroupSet_Value"]
    for action in report["actions"]:
        if action["plan_step"].get("type") == "set_amount":
            assert action["confidence"] != "high"


# --- Numbers-only mode is unaffected by amount precision changes ---


def test_numbers_only_mode_still_reports_safe_with_manual_amounts() -> None:
    plan = normal_fill_plan()
    numbers_only = to_numbers_only_plan(plan)
    # Even with a fully position-verifiable amount structure available, a
    # numbers-only plan must never map or attempt any amount action.
    selector_report = real_539_style_selector_report(plan)

    report = build_mapping_report(numbers_only, selector_report)

    assert report["status"] == "SAFE"
    assert report["amount_manual_required"] is True
    assert report["amount_field_status"] == "SKIPPED_BY_DESIGN"
    assert all(a["plan_step"].get("type") != "set_amount" for a in report["actions"])

    pretty = format_pretty_mapping_report(report)
    assert "amount_manual_required: true" in pretty
    assert "no amount fill action will be executed" in pretty


# --- Danger / forbidden-step behavior must be completely unchanged ---


def test_position_precision_does_not_change_danger_or_forbidden_steps() -> None:
    plan = normal_fill_plan()
    selector_report = real_539_style_selector_report(plan)

    report = build_mapping_report(plan, selector_report)

    assert report["danger_check"]["danger_candidates_found"] is True
    assert report["danger_check"]["dangerous_buttons_detected"] == ["送出注單", "確認"]
    assert report["forbidden_steps"] == ["submit", "confirm", "send_bet", "click_danger_button"]
    assert report["final_decision"]["executable"] is False
