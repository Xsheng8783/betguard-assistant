"""Productized assisted human-review UX and safety regressions."""

from __future__ import annotations

import copy
import json

import pytest

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from tests.test_gemma_assisted_human_review import (
    _click_candidate,
    _mount_sample008,
    _sample008_gemma,
)
from tests.test_vision_review_session_gate3a import (
    _confirm_structure,
    _create_candidate,
    _job_result,
    _mount,
    _run_qwen,
    _sample011_s02_result,
    _sample011_s02_structure,
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


def _single_structure(structure_id: str, candidate: dict, *, status: str = "consistent") -> list[dict]:
    item = copy.deepcopy(_structure_evidence()[0])
    item.update({
        "line_id": f"{structure_id}-L01",
        "structure_id": structure_id,
        "primary_line_id": f"{structure_id}-L01",
        "member_line_ids": [f"{structure_id}-L01"],
        "model_candidate": {
            "numbers": copy.deepcopy(candidate.get("number_groups", [])),
            "multiplier": copy.deepcopy(candidate.get("multiplier_rules", [])),
            "layout_hint": candidate.get("layout", "unknown"),
        },
        "reconstructed_candidate": copy.deepcopy(candidate),
        "status": status,
        "warnings": [] if status == "consistent" else ["human_review_required"],
    })
    return [item]


def test_normal_card_is_product_focused_and_advanced_evidence_is_folded(page) -> None:
    _mount(page)
    _run_qwen(page)
    card = page.locator('.qwen-review-card[data-structure-id="S01"]')
    assert "原圖位置" in card.locator(".review-image-context").text_content()
    assert "AI 建議" in card.locator(".review-ai-suggestions").text_content()
    assert "Human Answer" in card.locator(".human-answer-fields").text_content()
    assert card.locator(".qwen-card-advanced").get_attribute("open") is None
    assert page.get_attribute("#qwen-advanced-evidence", "open") is None
    assert page.get_attribute(".qwen-research-controls", "open") is None
    assert "model_candidate=" not in card.locator(".review-ai-suggestions").text_content()


def test_only_active_card_is_full_with_compact_list_and_position(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.locator(".qwen-review-card").count() == 1
    assert page.locator(".qwen-review-compact-item").count() == 2
    assert page.text_content("#qwen-review-position") == "第 1 / 2 筆"
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    assert page.locator('.qwen-review-card[data-structure-id="S02"]').count() == 1
    assert page.locator('.qwen-review-card[data-structure-id="S01"]').count() == 0
    assert page.text_content("#qwen-review-position") == "第 2 / 2 筆"


def test_field_level_conflict_does_not_color_the_whole_card(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert card.locator('.review-field[data-field="numbers"]').get_attribute("data-conflict") == "false"
    assert card.locator('.review-field[data-field="multiplier"]').get_attribute("data-conflict") == "true"
    assert "#dc2626" not in (card.get_attribute("style") or "")
    assert card.locator(".review-field-conflict").count() == 1


def test_sample008_number_multiplier_and_layout_adoption_are_separate_and_pending(page) -> None:
    evidence = _sample008_gemma()
    evidence["items"][1]["layout_guess"] = "normal"
    _mount_sample008(page, gemma=evidence)
    _run_qwen(page)
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    after_numbers = page.evaluate("qwenGetReviewSession().structures[0]")
    assert after_numbers["staged_structure"]["number_groups"] == [["08", "01", "04"]]
    assert after_numbers["staged_structure"]["multiplier_rules"] == ["2X5"]
    assert after_numbers["staged_structure"]["layout"] == "column_bet"
    page.click('.qwen-review-card[data-structure-id="S03"] .adopt-gemma-layout')
    after_layout = page.evaluate("qwenGetReviewSession().structures[0]")
    assert after_layout["staged_structure"]["layout"] == "normal_row"
    assert after_layout["staged_structure"]["multiplier_rules"] == ["2X5"]
    assert [item["field"] for item in after_layout["field_corrections"]] == ["numbers", "layout"]
    assert after_layout["review_state"] == "pending"
    assert after_layout["human_confirmed"] is False


def test_navigation_ctrl_enter_counters_and_auto_next(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.evaluate("qwenGetReviewSession().active_structure_id") == "S01"
    _confirm_structure(page, "S01")
    assert page.evaluate("qwenGetReviewSession().active_structure_id") == "S02"
    assert "unresolved=1" in page.text_content("#qwen-review-counts")
    page.keyboard.press("Control+Enter")
    page.wait_for_function("qwenGetReviewSession().structures[1].human_confirmed === true")
    assert page.locator("#qwen-complete-review").count() == 1
    page.click("#qwen-review-previous")
    assert page.evaluate("qwenGetReviewSession().active_structure_id") == "S01"
    page.click("#qwen-review-next")
    assert page.evaluate("qwenGetReviewSession().active_structure_id") == "S02"


def test_candidate_boundary_creates_only_server_candidate_with_zero_fill_side_effect(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    assert page.locator("#qwen-complete-review").count() == 0
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    urls_before = list(calls["urls"])
    candidate = _create_candidate(page)
    assert calls["urls"][len(urls_before):] == [
        "http://gate3a.test/api/vision/v1/candidates"
    ]
    signal = page.evaluate("qwenGetReviewSession().boundary_signal")
    assert signal == {
        "status": "candidate_created",
        "candidate_id": candidate["candidate_id"],
        "candidate_revision": candidate["revision"],
        "replayed": False,
        "queue_written": False,
        "external_fill_called": False,
    }
    assert candidate["safety"]["candidate_only"] is True
    assert candidate["safety"]["approved_for_fill"] is False
    assert not any(
        "queue" in url or "webfill" in url or "assist-fill" in url
        for url in calls["urls"]
    )


def test_sample007_qwen_failure_can_finish_by_explicit_manual_entry(page) -> None:
    calls = _mount(page, failed=True, gemma_evidence=_sample008_gemma())
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-add-manual-structure")
    assert page.evaluate("qwenGetReviewSession().structures") == []
    assert page.get_attribute("#qwen-advanced-evidence", "open") is None
    assert page.locator("#gemma-shadow-evidence").count() == 1
    page.click("#qwen-add-manual-structure")
    page.wait_for_selector('.qwen-card-editable[data-card-index="0"]')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector(".qwen-adopt-edit")
    page.click(".qwen-adopt-edit")
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False
    _confirm_structure(page, "MANUAL-01")
    assert page.locator("#qwen-complete-review").count() == 1
    assert calls["manual"] == [{
        "text": "05 09 17 28 2/3X1",
        "game": "539",
        "register_candidate": False,
    }]
    assert not any(
        word in url.lower()
        for url in calls["urls"]
        for word in ("queue", "webfill", "assist-fill")
    )


def test_sample010_special_scope_fields_survive_manual_edit_and_summary(page) -> None:
    candidate = {
        "number_groups": [["32", "34", "35"]],
        "multiplier_rules": ["2X2", "3X5"],
        "layout": "normal_row",
        "collision": None,
        "continuation": {"member_line_ids": ["S10-L01", "S10-L02"]},
        "tail": "尾",
        "car": "車",
        "half_car": "半車",
        "each": "各",
        "special_text": "各半車",
        "scope": "current_group",
    }
    _mount(page, structure_evidence=_single_structure("S10", candidate))
    _run_qwen(page)
    assert "各半車" in page.text_content(".review-special-fields")
    page.click(".qwen-edit-structure")
    page.fill(".qwen-card-editable", "05 09 17 28 2/3X1")
    page.click(".qwen-card-reparse")
    page.wait_for_selector(".qwen-adopt-edit")
    page.click(".qwen-adopt-edit")
    staged = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    for key in ("continuation", "tail", "car", "half_car", "each", "special_text", "scope"):
        assert staged[key] == candidate[key]
    _confirm_structure(page, "S10")
    human = _create_candidate(page)["active_bets"][0]
    special = json.loads(human["special_play"]["raw_text"])
    assert special["special_text"] == "各半車"
    assert human["continuation"]["present"] is True


def test_special_scope_controls_are_browser_local_pending_and_preserved(page) -> None:
    candidate = {
        "number_groups": [["32", "34", "35"]],
        "multiplier_rules": ["2X2", "3X5"],
        "layout": "normal_row",
        "continuation": {"member_line_ids": ["S10-L01", "S10-L02"]},
        "tail": "尾一",
        "car": "一車",
        "half_car": "半車一",
        "each": "各一",
        "special_text": "舊玩法",
        "scope": "current_group",
    }
    _mount(page, structure_evidence=_single_structure("S10", candidate))
    _run_qwen(page)
    _confirm_structure(page, "S10")
    page.click(".qwen-edit-structure")
    page.uncheck(".qwen-special-continuation")
    page.fill(".qwen-special-tail", "尾二")
    page.fill(".qwen-special-car", "二車")
    page.fill(".qwen-special-half-car", "半車二")
    page.fill(".qwen-special-each", "各二")
    page.fill(".qwen-special-text", "人工特殊玩法")
    page.fill(".qwen-special-scope", "next_group")
    page.click(".qwen-adopt-special-fields")
    card = page.evaluate("qwenGetReviewSession().structures[0]")
    assert card["staged_structure"]["continuation"] is False
    assert card["staged_structure"]["tail"] == "尾二"
    assert card["staged_structure"]["car"] == "二車"
    assert card["staged_structure"]["half_car"] == "半車二"
    assert card["staged_structure"]["each"] == "各二"
    assert card["staged_structure"]["special_text"] == "人工特殊玩法"
    assert card["staged_structure"]["scope"] == "next_group"
    assert card["review_state"] == "pending"
    assert card["human_confirmed"] is False
    assert {item["field"] for item in card["field_corrections"]} >= {
        "continuation", "tail", "car", "half_car", "each", "special_text", "scope"
    }
    _confirm_structure(page, "S10")
    human = _create_candidate(page)["active_bets"][0]
    special = json.loads(human["special_play"]["raw_text"])
    assert human["special_play"]["scope"] == "next_group"
    assert special["special_text"] == "人工特殊玩法"


def test_sample011_columns_are_never_flattened(page) -> None:
    _mount(page, job_result=_sample011_s02_result(), structure_evidence=_sample011_s02_structure())
    _run_qwen(page)
    staged = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert staged["number_groups"] == [["34", "35", "36", "38"]]
    assert staged["multiplier_rules"] == ["3/4X1"]
    assert page.locator(".qwen-review-column").count() == 0


def test_sample014_cancelled_is_not_active_and_scope_does_not_move(page) -> None:
    evidence = copy.deepcopy(_structure_evidence()[:2])
    evidence[0]["reconstructed_candidate"]["scope"] = "unresolved_region"
    evidence[0]["reconstructed_candidate"]["special_text"] = "各半車"
    original_second = copy.deepcopy(evidence[1]["reconstructed_candidate"])
    _mount(page, structure_evidence=evidence)
    _run_qwen(page)
    page.check('.qwen-review-card[data-structure-id="S01"] .qwen-card-cancelled')
    session = page.evaluate("qwenGetReviewSession()")
    assert session["structures"][0]["staged_structure"]["scope"] == "unresolved_region"
    assert session["structures"][1]["staged_structure"] == original_second
    assert "總共 1 active" in page.text_content("#qwen-review-progress")
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    summary = _create_candidate(page)
    assert len(summary["active_bets"]) == 1
    assert len(summary["cancelled_audit"]) == 1
    assert summary["cancelled_audit"][0]["cancelled"] is True


def test_machine_evidence_remains_deep_equal_across_product_review(page) -> None:
    result = _job_result()
    _mount(page, job_result=result)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-edit-structure')
    assert page.evaluate("qwenGetRecognitionResult()") == result
    serialized = json.dumps(page.evaluate("qwenGetReviewSession()"), ensure_ascii=False).lower()
    for forbidden in ("manual_candidate_id", "accepted_by_human", "queue_entry", "webfill_call"):
        assert forbidden not in serialized
