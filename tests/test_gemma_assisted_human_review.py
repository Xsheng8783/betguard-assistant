"""Gate 3A browser-local Gemma suggestion adoption regressions."""

from __future__ import annotations

import copy

import pytest

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from tests.test_vision_review_session_gate3a import (
    _confirm_structure,
    _job_result,
    _mount,
    _run_qwen,
    _structure_evidence,
    _wait_authority_ready,
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


def _structure(structure_id: str, groups: list[list[str]], multiplier: str) -> dict:
    value = copy.deepcopy(_structure_evidence()[0])
    value.update({
        "line_id": f"{structure_id}-L01",
        "structure_id": structure_id,
        "primary_line_id": f"{structure_id}-L01",
        "member_line_ids": [f"{structure_id}-L01"],
        "model_candidate": {
            "numbers": copy.deepcopy(groups),
            "multiplier": multiplier,
            "layout_hint": "column_bet",
        },
        "reconstructed_candidate": {
            "number_groups": copy.deepcopy(groups),
            "multiplier_rules": [multiplier],
            "layout": "column_bet",
            "collision": None,
        },
        "status": "divergent",
        "warnings": ["multi_model_literal_disagreement"],
    })
    return value


def _sample008_structures() -> list[dict]:
    return [
        _structure("S03", [["58"], ["01"]], "2X5"),
        _structure("S04", [["08"], ["16"]], "2X5"),
        _structure("S06", [["25"], ["26"], ["28"], ["07"]], "3X1"),
    ]


def _item(evidence_id: str, raw_text: str, numbers: str, multiplier: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "raw_text": raw_text,
        "numbers": numbers,
        "multiplier_text": multiplier,
        "layout_guess": "column",
        "continuation": "no",
        "special_text": "stacked writing",
        "cancelled": "no",
        "uncertain": False,
        "uncertain_reason": "none",
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
    }


def _sample008_gemma(*, duplicate_s03_id: bool = False) -> dict:
    items = [
        _item("GEMMA-0001", "(5.3.9) ①", "5.3.9 1", "none"),
        _item("GEMMA-0004", "08x01\n04 2x5", "08 01 04 2 5", "2x5"),
        _item("GEMMA-0005", "08x16\n26 2x1", "08 16 26 2 1", "2x1"),
        _item("GEMMA-0007", "25x26x28x07\n09 3x1", "25 26 28 07 09 3 1", "3x1"),
    ]
    if duplicate_s03_id:
        items.append(_item("GEMMA-0004", "09x01", "09 01", "2x5"))
    return {
        "status": "completed",
        "provider": {"id": "gemma4-26b-shadow", "model_name": "gemma-4-26b-a4b-it"},
        "items": items,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _mount_sample008(page, *, gemma: dict | None = None):
    return _mount(
        page,
        job_result=_job_result(),
        structure_evidence=_sample008_structures(),
        gemma_evidence=gemma or _sample008_gemma(),
    )


def _candidate_button(structure_id: str, evidence_id: str, component: str) -> str:
    return (
        f'.qwen-review-card[data-structure-id="{structure_id}"] '
        f'.gemma-unlinked-candidate[data-evidence-id="{evidence_id}"] '
        f'.adopt-gemma-candidate-{component}'
    )


def _click_candidate(page, structure_id: str, evidence_id: str, component: str) -> None:
    if page.locator(f'.qwen-review-card[data-structure-id="{structure_id}"]').count() == 0:
        page.click(f'.qwen-review-compact-item[data-structure-id="{structure_id}"]')
    page.locator(
        f'.qwen-review-card[data-structure-id="{structure_id}"] .qwen-card-advanced'
    ).evaluate("element => { element.open = true; }")
    page.click(_candidate_button(structure_id, evidence_id, component))


def test_unlinked_live_evidence_is_visible_but_never_positionally_attached(page) -> None:
    _mount_sample008(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert len(session["unlinked_gemma_items"]) == 4
    assert all(card["evidence_sources"]["gemma"] is None for card in session["structures"])
    assert page.locator(".qwen-review-compact-item").count() == 3
    assert page.locator(".gemma-card-source-unavailable").count() == 1
    assert page.locator(_candidate_button("S03", "GEMMA-0004", "numbers")).count() == 1
    assert "候選與原始證據收在進階證據" in page.text_content(
        '.qwen-review-card[data-structure-id="S03"]'
    )
    assert page.get_attribute(
        '.qwen-review-card[data-structure-id="S03"] .qwen-card-advanced', "open"
    ) is None


def test_sample008_s03_explicit_number_adoption_is_browser_local_and_pending(page) -> None:
    calls = _mount_sample008(page)
    _run_qwen(page)
    recognition_before = page.evaluate("qwenGetRecognitionResult()")
    urls_before = list(calls["urls"])
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    _wait_authority_ready(page)
    card = page.evaluate("qwenGetReviewSession().structures[0]")
    assert card["staged_structure"]["number_groups"] == [["08"], ["01", "04"]]
    assert card["staged_structure"]["multiplier_rules"] == ["2X5"]
    assert card["field_sources"]["numbers"]["source"] == "Gemma suggestion"
    assert card["field_sources"]["multiplier"]["source"] == "Qwen reconstruction"
    assert card["evidence_sources"]["gemma"]["association_method"] == "explicit_user_click"
    assert card["review_state"] == "pending"
    assert card["human_confirmed"] is False
    assert card["suggestion_adoptions"][0]["human_confirmed"] is False
    assert page.evaluate("qwenGetRecognitionResult()") == recognition_before
    new_urls = calls["urls"][len(urls_before):]
    assert len(new_urls) == 1
    assert "/api/vision/v1/review-sessions/" in new_urls[0]
    assert calls["review_replace"][-1]["bets"][0]["number_groups"] == [
        ["08"], ["01", "04"]
    ]
    assert calls["manual"] == []
    assert page.evaluate("qwenGetReviewSummary()") is None


def test_sample008_s04_number_and_multiplier_adoptions_are_independent(page) -> None:
    _mount_sample008(page)
    _run_qwen(page)
    _click_candidate(page, "S04", "GEMMA-0005", "numbers")
    after_numbers = page.evaluate("qwenGetReviewSession().structures[1].staged_structure")
    assert after_numbers["number_groups"] == [["08"], ["16", "26"]]
    assert after_numbers["multiplier_rules"] == ["2X5"]
    page.click('.qwen-review-card[data-structure-id="S04"] .adopt-gemma-multiplier')
    card = page.evaluate("qwenGetReviewSession().structures[1]")
    assert card["staged_structure"]["number_groups"] == [["08"], ["16", "26"]]
    assert card["staged_structure"]["multiplier_rules"] == ["2X1"]
    assert [entry["component"] for entry in card["suggestion_adoptions"]] == [
        "numbers", "multiplier"
    ]
    assert card["review_state"] == "pending"
    assert card["human_confirmed"] is False


def test_sample008_s06_explicit_adoption_adds_09_only_to_last_column(page) -> None:
    _mount_sample008(page)
    _run_qwen(page)
    _click_candidate(page, "S06", "GEMMA-0007", "numbers")
    staged = page.evaluate("qwenGetReviewSession().structures[2].staged_structure")
    assert staged["number_groups"] == [["25"], ["26"], ["28"], ["07", "09"]]
    assert staged["multiplier_rules"] == ["3X1"]


def test_gemma_adoption_stays_editable_and_unconfirmed_until_explicit_confirm(page) -> None:
    _mount_sample008(page)
    _run_qwen(page)
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    page.click('.qwen-review-card[data-structure-id="S03"] .qwen-edit-structure')
    editor = '.qwen-review-card[data-structure-id="S03"] .qwen-card-editable'
    assert "08 / 01 04" in page.input_value(editor)
    page.fill(editor, "08 / 01 04 09 二X5")
    assert page.input_value(editor) == "08 / 01 04 09 二X5"
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False
    page.click('.qwen-review-card[data-structure-id="S03"] .qwen-card-cancel')
    _confirm_structure(page, "S03")
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is True


def test_duplicate_evidence_id_fails_closed_without_adoption(page) -> None:
    _mount_sample008(page, gemma=_sample008_gemma(duplicate_s03_id=True))
    _run_qwen(page)
    before = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    after = page.evaluate("qwenGetReviewSession().structures[0]")
    assert after["staged_structure"] == before
    assert after["evidence_sources"]["gemma"] is None
    assert after["suggestion_adoptions"] == []


def test_one_unlinked_evidence_item_cannot_be_reused_by_another_card(page) -> None:
    _mount_sample008(page)
    _run_qwen(page)
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    before = page.evaluate("qwenGetReviewSession().structures[1].staged_structure")
    page.evaluate("qwenReviewAdoptGemmaCandidateNumbers(1, 1)")
    second = page.evaluate("qwenGetReviewSession().structures[1]")
    assert second["staged_structure"] == before
    assert second["evidence_sources"]["gemma"] is None


def test_539_number_parser_excludes_multiplier_digits_and_40_to_49(page) -> None:
    evidence = _sample008_gemma()
    evidence["items"].append(_item("GEMMA-0040", "40x01\n49 2x1", "40 01 49 2 1", "2x1"))
    _mount_sample008(page, gemma=evidence)
    _run_qwen(page)
    assert page.locator(_candidate_button("S03", "GEMMA-0040", "numbers")).count() == 0
    assert page.evaluate("qwenGetReviewSession().structures[0].staged_structure.number_groups") == [
        ["58"], ["01"]
    ]


def test_source_slots_are_future_capable_without_creating_side_effect_payloads(page) -> None:
    calls = _mount_sample008(page)
    _run_qwen(page)
    text = page.text_content('.qwen-review-card[data-structure-id="S03"] .review-evidence-source-slots')
    assert text == "sources=Qwen / Gemma / PP / Codex / Human Answer"
    _click_candidate(page, "S03", "GEMMA-0004", "numbers")
    serialized = str(page.evaluate("qwenGetReviewSession()"))
    for forbidden in ("manual_candidate_id", "accepted_by_human", "queue", "draft", "webfill"):
        assert forbidden not in serialized.lower()
    assert not any(any(word in url.lower() for word in ("queue", "draft", "webfill")) for url in calls["urls"])
    assert calls["manual"] == []
