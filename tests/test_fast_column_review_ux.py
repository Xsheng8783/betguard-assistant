"""Fast Human Answer correction UX; all machine values remain suggestions."""

from __future__ import annotations

import copy

import pytest

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from tests.test_assisted_human_review_productization import _single_structure
from tests.test_vision_review_session_gate3a import (
    _confirm_structure,
    _create_candidate,
    _mount,
    _run_qwen,
    _structure_evidence,
)


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(5000)
        yield browser_page
        context.close()
        browser.close()


def _wait_revision(page, before: int) -> None:
    page.wait_for_function(
        "before => qwenGetReviewSession().human_answer_revision === before + 1",
        arg=before,
    )


def _normal_structure(structure_id: str, numbers: list[str]) -> dict:
    item = copy.deepcopy(_structure_evidence()[0])
    item.update(
        structure_id=structure_id,
        line_id=f"{structure_id}-L01",
        primary_line_id=f"{structure_id}-L01",
        member_line_ids=[f"{structure_id}-L01"],
        model_candidate={
            "numbers": [numbers],
            "multiplier": "2X1",
            "layout_hint": "normal_row",
        },
        reconstructed_candidate={
            "number_groups": [numbers],
            "multiplier_rules": ["2X1"],
            "layout": "normal_row",
            "collision": None,
        },
        status="consistent",
        warnings=[],
    )
    return item


def test_set_normal_flattens_groups_without_changing_numbers_and_unconfirms(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    _confirm_structure(page, "S02")
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-quick-normal")
    _wait_revision(page, before)
    card = page.evaluate("qwenGetReviewSession().structures[1]")
    assert card["staged_structure"]["layout"] == "normal_row"
    assert card["staged_structure"]["number_groups"] == [
        ["24", "34", "08", "38", "16", "36", "03", "13"]
    ]
    assert card["human_confirmed"] is False
    assert card["review_state"] == "pending"


def test_two_rows_preview_then_apply_transposes_into_nested_columns(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-quick-two-row")
    assert page.evaluate("qwenGetReviewSession().human_answer_revision") == before
    assert page.evaluate("qwenGetReviewSession().structures[1].staged_structure.number_groups") == [
        ["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]
    ]
    preview = page.evaluate("qwenGetReviewSession().structures[1].quick_preview")
    assert preview["rows"] == [
        ["24", "08", "16", "03"], ["34", "38", "36", "13"]
    ]
    assert preview["groups"] == [
        ["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]
    ]
    page.click(".qwen-apply-column-preview")
    _wait_revision(page, before)
    card = page.evaluate("qwenGetReviewSession().structures[1]")
    assert card["staged_structure"]["layout"] == "column_bet"
    assert card["human_confirmed"] is False


def test_unequal_rows_show_warning_and_never_guess_missing_position(page) -> None:
    candidate = {
        "number_groups": [["36", "07", "08", "06", "38", "17", "18"]],
        "multiplier_rules": ["2X1"],
        "layout": "normal_row",
    }
    item = _single_structure("S20", candidate)[0]
    item["evidence"] = {"raw_text": "36 07 08 06\n38 17 18"}
    _mount(page, structure_evidence=[item])
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-quick-two-row")
    assert "兩排長度不一致" in page.text_content(".qwen-preview-warning")
    assert page.locator(".qwen-apply-column-preview").is_disabled()
    assert page.evaluate("qwenGetReviewSession().human_answer_revision") == before
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False


def test_explicit_merge_current_and_next_creates_one_column_and_one_revision(page) -> None:
    structures = [
        _normal_structure("S30", ["36", "07", "08", "06"]),
        _normal_structure("S31", ["38", "17", "18", "13"]),
    ]
    _mount(page, structure_evidence=structures)
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-quick-merge")
    assert page.evaluate("qwenGetReviewSession().human_answer_revision") == before
    page.click(".qwen-apply-merge-preview")
    _wait_revision(page, before)
    session = page.evaluate("qwenGetReviewSession()")
    assert len(session["structures"]) == 1
    assert session["structures"][0]["staged_structure"]["number_groups"] == [
        ["36", "38"], ["07", "17"], ["08", "18"], ["06", "13"]
    ]
    assert session["structures"][0]["human_confirmed"] is False


def test_split_requires_explicit_two_lines_and_creates_two_unconfirmed_bets(page) -> None:
    structure = _normal_structure("S40", ["06", "04", "36", "08", "15", "38"])
    structure["evidence"] = {"raw_text": "06 04 36\n08 15 38"}
    _mount(page, structure_evidence=[structure])
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-quick-split")
    page.fill(".qwen-split-rows", "06 04 36\n08 15 38")
    page.dispatch_event(".qwen-split-rows", "change")
    page.click(".qwen-apply-split")
    _wait_revision(page, before)
    cards = page.evaluate("qwenGetReviewSession().structures")
    assert [card["staged_structure"]["number_groups"] for card in cards] == [
        [["06", "04", "36"]], [["08", "15", "38"]]
    ]
    assert all(card["human_confirmed"] is False for card in cards)
    assert cards[0]["staged_structure"]["multiplier_rules"] == ["2X1"]
    assert cards[1]["staged_structure"]["multiplier_rules"] == []


def test_group_editor_moves_numbers_and_columns_without_raw_json(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-open-group-editor")
    page.evaluate("qwenGroupMoveNumber(1,0,1,1)")
    page.evaluate("qwenGroupMoveColumn(1,3,-1)")
    page.click(".qwen-apply-group-editor")
    _wait_revision(page, before)
    groups = page.evaluate("qwenGetReviewSession().structures[1].staged_structure.number_groups")
    assert groups == [["24"], ["08", "38", "34"], ["03", "13"], ["16", "36"]]
    assert page.locator(".qwen-card-editable").count() == 0


def test_multiplier_editor_preserves_decimal_as_rule_not_number(page) -> None:
    _mount(page)
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    original_numbers = page.evaluate("qwenGetReviewSession().structures[0].staged_structure.number_groups")
    page.click(".qwen-open-multiplier-editor")
    page.fill(".qwen-multiplier-category", "2/3")
    page.dispatch_event(".qwen-multiplier-category", "change")
    page.fill(".qwen-multiplier-value", "0.5")
    page.dispatch_event(".qwen-multiplier-value", "change")
    page.fill(".qwen-multiplier-scope", "bet")
    page.dispatch_event(".qwen-multiplier-scope", "change")
    page.click(".qwen-apply-multiplier-editor")
    _wait_revision(page, before)
    structure = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert structure["multiplier_rules"] == ["2/3X0.5"]
    assert structure["multiplier_scope"] == "bet"
    assert structure["number_groups"] == original_numbers


def test_multiplier_editor_preserves_explicit_rule_order(page) -> None:
    _mount(page)
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-open-multiplier-editor")
    page.evaluate("qwenMultiplierSet(0,0,'category','2'); qwenMultiplierSet(0,0,'value','2'); qwenMultiplierSet(0,0,'scope','bet')")
    page.evaluate("qwenMultiplierAdd(0)")
    page.evaluate("qwenMultiplierSet(0,1,'category','3'); qwenMultiplierSet(0,1,'value','5'); qwenMultiplierSet(0,1,'scope','bet')")
    page.click(".qwen-apply-multiplier-editor")
    _wait_revision(page, before)
    structure = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert structure["multiplier_rules"] == ["2X2", "3X5"]
    assert structure["multiplier_scope"] == "bet"


def test_special_play_editor_keeps_tail_literal_out_of_number_groups(page) -> None:
    _mount(page)
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    original_numbers = page.evaluate("qwenGetReviewSession().structures[0].staged_structure.number_groups")
    page.click(".qwen-open-special-editor")
    page.select_option(".qwen-special-kind", "tail")
    page.fill(".qwen-special-literal", "9尾")
    page.dispatch_event(".qwen-special-literal", "change")
    page.fill(".qwen-special-quick-scope", "bet")
    page.dispatch_event(".qwen-special-quick-scope", "change")
    page.click(".qwen-apply-special-editor")
    _wait_revision(page, before)
    structure = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert structure["tail"] == "9尾"
    assert structure["number_groups"] == original_numbers
    assert all("9" not in group for group in structure["number_groups"])
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False


def test_alt_shortcuts_only_open_preview_and_ctrl_enter_still_confirms(page) -> None:
    _mount(page)
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.keyboard.press("Alt+N")
    assert page.locator('.qwen-quick-preview[data-preview-kind="normal"]').count() == 1
    assert page.evaluate("qwenGetReviewSession().human_answer_revision") == before
    page.click(".qwen-cancel-quick-preview")
    page.keyboard.press("Control+Enter")
    page.wait_for_function(
        "before => qwenGetReviewSession().human_answer_revision === before + 1 && "
        "qwenGetReviewSession().structures[0].human_confirmed === true",
        arg=before,
    )


def test_edit_after_candidate_marks_old_candidate_stale(page) -> None:
    _mount(page)
    _run_qwen(page)
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    candidate = _create_candidate(page)
    assert candidate["candidate_id"].startswith("vc-")
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(".qwen-open-multiplier-editor")
    page.fill(".qwen-multiplier-value", "0.5")
    page.dispatch_event(".qwen-multiplier-value", "change")
    page.click(".qwen-apply-multiplier-editor")
    _wait_revision(page, before)
    session = page.evaluate("qwenGetReviewSession()")
    assert session["server_candidate"]["state"] == "STALE"
    assert session["structures"][1]["human_confirmed"] is False
    assert page.locator("#mvp-local-sandbox-fill").count() == 0


def test_main_review_hides_authority_ids_and_execution_controls(page) -> None:
    _mount(page)
    _run_qwen(page)
    main_text = page.text_content("#qwen-review-session")
    assert page.locator(".qwen-authority-advanced").get_attribute("open") is None
    assert page.locator("#qwen-authority-status").evaluate(
        "node => Array.from(node.childNodes).filter(item => item.nodeType === 3).map(item => item.textContent).join(' ')"
    ).strip() == "Human Answer 已安全保存"
    assert "fencing token" not in main_text.lower()
    assert "自動下注" not in main_text
    assert "送出" not in main_text
