from betguard.review import build_report
from betguard.webfill.fill_mapping_report import build_mapping_report
from betguard.webfill.fill_plan import build_fill_plan
from betguard.webfill.selector_discovery import (
    CONTAINER_LABEL_LIMIT,
    READONLY_ENRICHMENT_KEYS,
    READONLY_HTML_LIMIT,
    _normalize_element,
    _selector_record,
    detect_amount_field_candidates,
)
from betguard.webfill.site_profile import (
    AMOUNT_CONTEXT_TEXT_LIMIT,
    build_site_profile,
)


def raw_input(**over) -> dict:
    base = {
        "tag": "input",
        "text": "",
        "id": "",
        "name": "",
        "className": "BDAll",
        "type": "text",
        "parentText": "每碰金額 二星",
        "outerHTML": "<input class='BDAll'>",
        "visible": True,
        "box": {"w": 80.4, "h": 20.6, "top": 100.2, "left": 50.9},
        "display": "inline-block",
        "visibilityCss": "visible",
        "hidden": False,
        "ariaHidden": "",
        "amountTableAnchor": "amountTbl",
        "amountTableInputIndex": 0,
        "containerLabel": "每碰金額 二星 三星 四星",
        "index": 5,
        "frame_name": "mainFrame",
    }
    base.update(over)
    return base


# --- retained enriched fields ---

def test_normalize_carries_enriched_fields() -> None:
    n = _normalize_element(raw_input())
    for key in READONLY_ENRICHMENT_KEYS:
        assert key in n
    assert n["visible"] is True
    assert n["box"] == {"w": 80, "h": 21, "top": 100, "left": 51}
    assert n["amountTableInputIndex"] == 0
    assert n["amountTableAnchor"] == "amountTbl"
    assert n["display"] == "inline-block"


def test_amount_candidate_retains_enriched_fields_for_input() -> None:
    candidates = detect_amount_field_candidates([raw_input(id="", amountTableInputIndex=0)])
    records = candidates["二星"]
    assert records
    record = records[0]
    assert record["id"] == ""
    assert record["visible"] is True
    assert record["amountTableInputIndex"] == 0
    assert record["amountTableAnchor"] == "amountTbl"
    assert "每碰金額" in record["containerLabel"]
    assert "parentText" in record and "candidate_selectors" in record


# --- fail-closed visibility ---

def test_visibility_fails_closed() -> None:
    assert _normalize_element(raw_input(visible=None))["visible"] is False
    assert _normalize_element(raw_input(visible="true"))["visible"] is False  # only bool True counts
    assert _normalize_element(raw_input(visible=1))["visible"] is False
    missing = raw_input()
    missing.pop("visible")
    assert _normalize_element(missing)["visible"] is False
    assert _normalize_element(raw_input(box="nope"))["box"] == {}
    assert _normalize_element(raw_input(amountTableInputIndex="x"))["amountTableInputIndex"] == -1


# --- bounded text/HTML ---

def test_html_and_context_fields_are_bounded() -> None:
    big = "x" * 2000
    n = _normalize_element(
        raw_input(outerHTML=big, grandparentHTML=big, parentHTML=big, containerLabel="每" * 500)
    )
    assert len(n["outerHTML"]) <= READONLY_HTML_LIMIT
    assert len(n["grandparentHTML"]) <= READONLY_HTML_LIMIT
    assert len(n["parentHTML"]) <= READONLY_HTML_LIMIT
    assert len(n["containerLabel"]) <= CONTAINER_LABEL_LIMIT


def test_profile_bounds_amount_context_text() -> None:
    record = _selector_record(raw_input(id="", grandparentText="Z" * 5000))
    selector_report = {
        "number_candidates": {},
        "amount_field_candidates": {"二星": [record]},
        "danger_candidates": [{"tag": "button", "text": "送出注單"}],
        "market_state": {},
    }
    profile = build_site_profile(selector_report, site_name="t", page_name="b03")
    persisted = profile["amount_field_candidates"]["二星"][0]
    assert "visible" in persisted and "amountTableAnchor" in persisted
    assert len(persisted["grandparentText"]) <= AMOUNT_CONTEXT_TEXT_LIMIT


# --- no-id visible amount input keeps table-scoped order ---

def test_no_id_visible_amount_input_keeps_table_order() -> None:
    # Each no-id visible amount input must carry its own table-scoped order and
    # anchor so a later mapper can distinguish the boxes. (Ordering the stars is
    # NOT done here — that is the not-yet-implemented mapper's job.)
    for expected_index, star in enumerate(("二星", "三星", "四星")):
        record = _selector_record(
            raw_input(id="", parentText="每碰金額 " + star, amountTableInputIndex=expected_index)
        )
        assert record["id"] == ""
        assert record["visible"] is True
        assert record["amountTableInputIndex"] == expected_index
        assert record["amountTableAnchor"] == "amountTbl"


# --- #GroupSet_Value stays an ordinary id record, not a safe mapping ---

def test_group_set_value_retained_as_id_not_marked_safe() -> None:
    candidates = detect_amount_field_candidates([raw_input(id="GroupSet_Value")])
    record = candidates["二星"][0]
    assert record["id"] == "GroupSet_Value"
    assert "#GroupSet_Value" in record["candidate_selectors"]
    # Enrichment adds captured data only; never a mapping/safety decision.
    assert not any(k in record for k in ("safe", "mapping", "executable", "manual_override"))


# --- hidden #ta_* is representable as visible=false ---

def test_hidden_ta_field_is_visible_false() -> None:
    candidates = detect_amount_field_candidates(
        [raw_input(id="ta_1_2", visible=False, parentText="每碰金額 三星")]
    )
    record = candidates["三星"][0]
    assert record["id"] == "ta_1_2"
    assert record["visible"] is False


# --- enrichment performs no amount mapping and keeps mapping BLOCKED ---

def test_enrichment_does_not_perform_amount_mapping_and_stays_blocked() -> None:
    plan = build_fill_plan(build_report("06-13-23-22/50").to_dict())
    amount = {
        star: [_selector_record(raw_input(id="GroupSet_Value", parentText="每碰金額 " + star))]
        for star in plan["stars"]
    }
    selector_report = {
        "market_state": {"can_probe_bet_page": True, "current_game_name": "天天樂"},
        "number_candidates": {
            n: [{"tag": "button", "text": n, "candidate_selectors": [f"text={n}"]}]
            for n in plan["numbers"]
        },
        "amount_field_candidates": amount,
        "danger_candidates": [{"tag": "button", "text": "送出注單", "candidate_selectors": ["text=送出注單"]}],
    }
    report = build_mapping_report(plan, selector_report)

    assert report["amount_field_status"] == "BLOCKED"
    assert report["amount_field_source"] == "automatic_mapping"
    assert "#GroupSet_Value" in report["shared_amount_selectors"]
    # Safety invariants: nothing executable, no override applied.
    assert report["final_decision"]["executable"] is False
    assert report["amount_field_overrides"]["applied"] == []


def test_build_site_profile_is_not_executable() -> None:
    record = _selector_record(raw_input(id="GroupSet_Value"))
    selector_report = {
        "number_candidates": {},
        "amount_field_candidates": {"二星": [record]},
        "danger_candidates": [{"tag": "button", "text": "送出注單"}],
        "market_state": {},
    }
    profile = build_site_profile(selector_report, site_name="t", page_name="b03")
    assert "executable" not in profile
    for key in ("safe_to_assisted_fill", "real_site_operation", "auto_submit", "danger_buttons_clicked"):
        assert key not in profile
