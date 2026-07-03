import copy

from betguard.review import build_report
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.manual_amount_mapping import (
    apply_manual_amount_overrides,
    load_manual_amount_overrides,
)

B03_FRAME_URL = "https://w1.gts362.com/token/Front/B/B03"


def make_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def base_selector_report(plan: dict) -> dict:
    return {
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "天天樂",
            "selected_route": "二三四星",
        },
        "number_candidates": {
            number: [
                {
                    "tag": "button",
                    "text": number,
                    "frame_name": "mainFrame",
                    "candidate_selectors": [f"text={number}"],
                }
            ]
            for number in plan["numbers"]
        },
        "amount_field_candidates": {},
        "danger_candidates": [
            {"tag": "button", "text": "送出注單", "candidate_selectors": ["text=送出注單"]},
        ],
    }


def amount_candidate(star: str, selector: str, *, index: int, parent: str, tag: str = "input") -> dict:
    ident = selector.lstrip("#")
    return {
        "tag": tag,
        "text": star,
        "frame_url": B03_FRAME_URL,
        "candidate_selectors": [selector],
        "amount_diagnostic": {
            "matched_label": star,
            "source_index": index,
            "id": ident,
            "name": "",
            "className": "BDAll",
            "parentText": parent,
            "grandparentText": "539 下注資訊",
            "outerHTML": f"<input id='{ident}' class='BDAll'>",
            "diagnostic_source": "automatic_mapping",
        },
    }


def group_set(star: str, *, index: int = 4) -> dict:
    # The shared "set all" field that automatic mapping collapses onto.
    return amount_candidate(star, "#GroupSet_Value", index=index, parent=f"設定 {star} 每組金額")


def distinct(star: str, selector: str, *, index: int) -> dict:
    return amount_candidate(star, selector, index=index, parent=f"每碰金額 {star} 本金")


def amount_action(report: dict, star: str) -> dict:
    return next(
        a
        for a in report["actions"]
        if a["plan_step"].get("type") == "set_amount" and a["plan_step"].get("star") == star
    )


def override(star: str, selector: str, *, parent_needle: str = "每碰金額", **extra) -> dict:
    rule = {
        "game": "tiantianle",
        "star": star,
        "selector_candidate": selector,
        "require": {"parentText_contains": parent_needle},
    }
    rule.update(extra)
    return rule


# 1. Matching-context override maps 二星/三星 to distinct selectors, source manual_override.
def test_matching_context_override_maps_distinct_selectors() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two), distinct(two, "#ta_0_0", index=5)]
    sr["amount_field_candidates"][three] = [group_set(three), distinct(three, "#ta_1_2", index=8)]
    # Fourth star already resolves uniquely without an override.
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [override(two, "#ta_0_0"), override(three, "#ta_1_2")]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["status"] == "SAFE"
    assert report["amount_field_status"] == "MANUAL_VERIFIED"
    assert report["amount_field_source"] == "manual_override"
    assert set(report["amount_field_overrides"]["applied"]) == {two, three}

    a2 = amount_action(report, two)
    a3 = amount_action(report, three)
    assert a2["selector"] == "#ta_0_0"
    assert a3["selector"] == "#ta_1_2"
    assert a2["selector"] != a3["selector"]
    assert a2["source"] == "manual_override"
    assert a2["confidence"] == "manual_verified"
    assert a2["manual_override"]["require"] == {"parentText_contains": "每碰金額"}


# 2. Non-matching context rejects override and remains BLOCKED.
def test_non_matching_context_rejects_override_and_blocks() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    for star in (two, three):
        sr["amount_field_candidates"][star] = [group_set(star), distinct(star, "#ta_x", index=5)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [
        override(two, "#ta_x", parent_needle="不存在的文字"),
        override(three, "#ta_x", parent_needle="不存在的文字"),
    ]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_source"] == "automatic_mapping"
    assert report["amount_field_overrides"]["applied"] == []
    assert any(item["star"] == two for item in report["amount_field_overrides"]["rejected"])


# 3. Override collapsing two stars to one selector remains BLOCKED.
def test_override_collapsing_two_stars_to_one_selector_blocks() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two), distinct(two, "#ta_same", index=5)]
    sr["amount_field_candidates"][three] = [group_set(three), distinct(three, "#ta_same", index=6)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [override(two, "#ta_same"), override(three, "#ta_same")]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_status"] == "BLOCKED"
    a2 = amount_action(report, two)
    a3 = amount_action(report, three)
    assert a2["selector"] == "#ta_same"
    assert a3["selector"] == "#ta_same"
    assert a2["selector_unsafe"] is True
    assert a3["selector_unsafe"] is True


# 4. Override cannot turn #GroupSet_Value into SAFE.
def test_override_cannot_force_group_set_value_safe() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two)]
    sr["amount_field_candidates"][three] = [group_set(three)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [
        override(two, "#GroupSet_Value"),
        override(three, "#GroupSet_Value"),
    ]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_overrides"]["applied"] == []
    assert "#GroupSet_Value" in report["shared_amount_selectors"]
    assert any(
        "shared across stars" in item["reason"]
        for item in report["amount_field_overrides"]["rejected"]
    )


# 5. Missing override file preserves current behavior.
def test_missing_override_file_preserves_behavior() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two)]
    sr["amount_field_candidates"][three] = [group_set(three)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    # No overrides passed -> default file is absent -> unchanged BLOCKED behavior.
    report = build_mapping_report(plan, sr)
    assert report["status"] == "BLOCKED"
    assert report["amount_field_source"] == "automatic_mapping"
    assert report["amount_field_overrides"]["applied"] == []

    assert load_manual_amount_overrides(path="does/not/exist.json") == []


# 6. Override targeting a non-input/danger/button candidate is refused.
def test_override_targeting_button_candidate_is_refused() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    button_candidate = amount_candidate(two, "#SendBtn", index=9, parent="每碰金額 二星", tag="button")
    sr["amount_field_candidates"][two] = [group_set(two), button_candidate]
    sr["amount_field_candidates"][three] = [group_set(three)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [override(two, "#SendBtn")]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["status"] == "BLOCKED"
    assert report["amount_field_overrides"]["applied"] == []
    assert any(
        "not an amount input/select" in item["reason"]
        for item in report["amount_field_overrides"]["rejected"]
    )


# 7. selector_report / site_profile inputs are not mutated.
def test_override_does_not_mutate_selector_report() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two), distinct(two, "#ta_0_0", index=5)]
    sr["amount_field_candidates"][three] = [group_set(three), distinct(three, "#ta_1_2", index=8)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]
    before = copy.deepcopy(sr)

    overrides = [override(two, "#ta_0_0"), override(three, "#ta_1_2")]
    build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert sr == before


# 8. Number selector behavior unchanged.
def test_number_selectors_unchanged_with_override() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two), distinct(two, "#ta_0_0", index=5)]
    sr["amount_field_candidates"][three] = [group_set(three), distinct(three, "#ta_1_2", index=8)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [override(two, "#ta_0_0"), override(three, "#ta_1_2")]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    for number in plan["numbers"]:
        action = next(a for a in report["actions"] if a["plan_step"].get("label") == number)
        assert action["selector"] == f"text={number}"
        assert action["confidence"] == "high"
        assert action["unique_selector"] is True
        assert action.get("source") is None


# 9. Safety flags / non-executability remain unchanged.
def test_override_keeps_report_non_executable_and_safe() -> None:
    plan = make_plan()
    sr = base_selector_report(plan)
    two, three, four = plan["stars"]
    sr["amount_field_candidates"][two] = [group_set(two), distinct(two, "#ta_0_0", index=5)]
    sr["amount_field_candidates"][three] = [group_set(three), distinct(three, "#ta_1_2", index=8)]
    sr["amount_field_candidates"][four] = [distinct(four, "#ta_2_5", index=11)]

    overrides = [override(two, "#ta_0_0"), override(three, "#ta_1_2")]
    report = build_mapping_report(plan, sr, manual_amount_overrides=overrides)

    assert report["final_decision"]["executable"] is False
    # No override or action ever introduces a click/fill/submit/confirm instruction.
    for action in report["actions"]:
        assert not any(
            key in action for key in ("click", "fill", "submit", "confirm", "auto_next")
        )
    pretty = format_pretty_mapping_report(report)
    assert "Amount Field Mapping:" in pretty
    assert "source: manual_override" in pretty


def test_apply_overrides_directly_returns_provenance() -> None:
    # Direct-call smoke test: verified override applies, unrelated star untouched.
    actions = [
        {
            "plan_step": {"type": "set_amount", "star": "二星", "amount": 50},
            "selector_found": True,
            "selector": "#GroupSet_Value",
            "unique_selector": True,
            "selector_unsafe": False,
            "selector_candidates": [
                {
                    "tag": "input",
                    "candidate_selectors": ["#ta_0_0"],
                    "amount_diagnostic": {"parentText": "每碰金額 二星 本金", "source_index": 5},
                }
            ],
        }
    ]
    result = apply_manual_amount_overrides(
        actions, overrides=[override("二星", "#ta_0_0")]
    )
    assert result["applied"] == ["二星"]
    assert result["source"] == "manual_override"
    assert actions[0]["selector"] == "#ta_0_0"
    assert actions[0]["source"] == "manual_override"
