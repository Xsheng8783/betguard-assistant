"""Gate 3A browser-local Vision human review workflow tests."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui.assist_panel_vision_html import render_vision_ui_section
from betguard.webfill.manual_reparse import reparse_text


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(4000)
        yield browser_page
        context.close()
        browser.close()


def _png_bytes(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _line(line_id: str, text: str, box: list[int]) -> dict:
    x1, y1, x2, y2 = box
    bounding_box = {
        "coordinate_space": "pixel",
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }
    return {
        "line_id": line_id,
        "order": 1,
        "text": text,
        "bounding_box": bounding_box,
        "tokens": [
            {
                "token_id": f"{line_id}-T01",
                "text": text,
                "start": 0,
                "end": len(text),
                "bounding_box": bounding_box,
            }
        ],
        "warnings": [],
    }


def _job_result() -> dict:
    return {
        "schema_version": "vision-recognition-v1",
        "recognition_id": "gate3a-recognition",
        "request_id": "job-image-gate3a",
        "status": "completed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "source_image": {
            "image_id": "image-gate3a",
            "sha256": "image-sha-gate3a",
            "width": 100,
            "height": 100,
        },
        "raw_text": "05 06 10 28 3/4X1\n24 08 16 03 2/3X0.1\n34 38 36 13",
        "lines": [
            _line("S01-L01", "05 06 10 28 3/4X1", [5, 5, 75, 20]),
            _line("S02-L01", "24 08 16 03 2/3X0.1", [8, 35, 82, 50]),
            _line("S02-L02", "34 38 36 13", [8, 52, 65, 67]),
        ],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
            "prompt_version": "combined-bbox-v1",
            "prompt_sha256": "prompt-sha",
            "qwen_request": {
                "model": "qwen3-vl-plus",
                "image_sha256": "image-sha-gate3a",
                "cache_hit": True,
                "request_id": "qwen-request-gate3a",
            },
            "qwen_response": {
                "sections": [
                    {
                        "shared_multiplier": None,
                        "rows": [{
                            "tokens": [],
                            "numbers": [["05", "06", "10", "28"]],
                            "multiplier": "3/4X1",
                            "layout_hint": "normal_row",
                        }],
                    },
                    {
                        "shared_multiplier": None,
                        "rows": [
                            {
                                "tokens": [],
                                "numbers": [["24"], ["08"], ["16"], ["03"]],
                                "multiplier": "2/3X0.1",
                                "layout_hint": "column_bet",
                            },
                            {
                                "tokens": [],
                                "numbers": [["34"], ["38"], ["36"], ["13"]],
                                "multiplier": None,
                                "layout_hint": "column_bet",
                            },
                        ],
                    },
                ]
            },
        },
    }


def _structure_evidence() -> list[dict]:
    safety = {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    return [
        {
            "line_id": "S01-L01",
            "structure_id": "S01",
            "primary_line_id": "S01-L01",
            "member_line_ids": ["S01-L01"],
            "line_role": "primary",
            "game": "539",
            "model_candidate": {
                "numbers": [["05", "06", "10", "28"]],
                "multiplier": "3/4X1",
                "layout_hint": "normal_row",
            },
            "reconstructed_candidate": {
                "number_groups": [["05", "06", "10", "28"]],
                "multiplier_rules": ["3/4X1"],
                "layout": "normal_row",
                "collision": "3/4",
            },
            "status": "consistent",
            "warnings": ["model_collision_evidence_missing"],
            "evidence": {"bbox_debug": {}},
            **safety,
        },
        {
            "line_id": "S02-L01",
            "structure_id": "S02",
            "primary_line_id": "S02-L01",
            "member_line_ids": ["S02-L01", "S02-L02"],
            "line_role": "primary",
            "game": "539",
            "model_candidate": {
                "numbers": [["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                "multiplier": "2/3X0.1",
                "layout_hint": "column_bet",
            },
            "reconstructed_candidate": {
                "number_groups": [["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                "multiplier_rules": ["2/3/4X0.1"],
                "layout": "column_bet",
                "collision": "2/3/4",
            },
            "status": "divergent",
            "warnings": ["fragment_multiplier_ambiguous"],
            "evidence": {"bbox_debug": {}},
            **safety,
        },
        {
            "line_id": "S02-L02",
            "structure_id": "S02",
            "primary_line_id": "S02-L01",
            "member_line_ids": ["S02-L01", "S02-L02"],
            "line_role": "continuation",
            "game": "539",
            "status": "incomplete",
            "warnings": ["continuation_of:S02-L01"],
            "evidence": {},
            **safety,
        },
    ]


def _manual_result() -> dict:
    return {
        "ok": True,
        "numbers": [5, 9, 17, 28],
        "stars": [2, 3],
        "amounts": {"2": 100, "3": 100},
        "type": "normal",
        "columns": [],
        "summary": "05,09,17,28｜23星｜100元",
        "accepted_by_human": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _mount(
    page,
    *,
    failed: bool = False,
    job_result: dict | None = None,
    structure_evidence: list[dict] | None = None,
    gemma_evidence: dict | None = None,
):
    calls: dict[str, list] = {"jobs": [], "manual": [], "urls": [], "uploads": []}
    upload_count = 0
    first = _png_bytes()

    def handle(route):
        nonlocal upload_count
        request = route.request
        calls["urls"].append(request.url)
        if request.url == "http://gate3a.test/":
            route.fulfill(
                status=200,
                content_type="text/html",
                body='<!doctype html><html><head><meta charset="utf-8"></head><body>'
                + render_vision_ui_section()
                + "</body></html>",
            )
            return
        if request.url.endswith("/api/vision/v1/providers"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "providers": [
                        {"id": "qwen-dashscope", "configured": True},
                        {"id": "openai-vision-paid", "configured": False},
                    ],
                }),
            )
            return
        if request.url.endswith("/api/vision/v1/images") and request.method == "POST":
            upload_count += 1
            image_id = f"image-gate3a-{upload_count}"
            calls["uploads"].append(image_id)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "image": {
                        "image_id": image_id,
                        "original_filename": f"slip-{upload_count}.png",
                        "mime_type": "image/png",
                        "width": 100,
                        "height": 100,
                        "byte_size": len(first),
                        "sha256": f"upload-sha-{upload_count}",
                    },
                }),
            )
            return
        if "/api/vision/v1/images/image-gate3a-" in request.url and request.method == "GET":
            route.fulfill(status=200, content_type="image/png", body=first)
            return
        if request.url.endswith("/api/vision/v1/jobs"):
            calls["jobs"].append(request.post_data_json)
            if failed:
                body = {
                    "ok": True,
                    "result": {
                        "status": "failed",
                        "provider": {"id": "qwen-dashscope"},
                        "provider_error": {"message": "private stack trace"},
                        "lines": [],
                        "preprocessing": {},
                    },
                }
            else:
                body = {
                    "ok": True,
                    "result": job_result if job_result is not None else _job_result(),
                    "structure_evidence": (
                        structure_evidence
                        if structure_evidence is not None
                        else _structure_evidence()
                    ),
                    "gemma_shadow_evidence": gemma_evidence,
                }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return
        if request.url.endswith("/manual-reparse"):
            calls["manual"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_manual_result()),
            )
            return
        if request.method == "DELETE":
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return
        route.abort()

    page.route("**/*", handle)
    page.goto("http://gate3a.test/")
    page.evaluate("document.getElementById('vision-section').style.display = 'block'")
    page.set_input_files(
        "#vision-file-input",
        {"name": "slip.png", "mimeType": "image/png", "buffer": first},
    )
    page.wait_for_selector("#vision-qwen-run-btn:visible")
    return calls


def _run_qwen(page) -> None:
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-review-session .qwen-review-card")


def _sample011_s02_result() -> dict:
    line_id = "S02-L01"
    token_specs = [
        ("34", [54, 225, 95, 257]),
        (".", [95, 230, 108, 252]),
        ("35", [112, 225, 152, 257]),
        (".", [152, 230, 165, 252]),
        ("36", [170, 225, 210, 257]),
        (".", [210, 230, 223, 252]),
        ("38", [228, 225, 268, 257]),
        (" ", [268, 228, 282, 254]),
        ("3", [285, 215, 315, 257]),
        ("/", [315, 225, 332, 257]),
        ("4", [332, 225, 362, 257]),
        ("x", [365, 225, 385, 257]),
        ("1", [388, 225, 415, 257]),
    ]
    tokens = []
    for index, (text, box) in enumerate(token_specs, start=1):
        x1, y1, x2, y2 = box
        tokens.append({
            "token_id": f"{line_id}-T{index:02d}",
            "text": text,
            "start": 0,
            "end": len(text),
            "bounding_box": {
                "coordinate_space": "pixel",
                "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            },
        })
    result = _job_result()
    result["recognition_id"] = "sample-011-s02-cache"
    result["raw_text"] = "34 . 35 . 36 . 38   3 / 4 x 1"
    result["lines"] = [{
        "line_id": line_id,
        "order": 1,
        "text": result["raw_text"],
        "bounding_box": {
            "coordinate_space": "pixel",
            "polygon": [[54, 215], [415, 215], [415, 257], [54, 257]],
        },
        "tokens": tokens,
        "warnings": [],
    }]
    result["source_image"] = {
        "image_id": "sample-011",
        "sha256": "7f15be60d6876febcd5b455c361a868bad8ddab0c3b7a442d029f0140ad738a6",
        "width": 1000,
        "height": 1000,
    }
    result["preprocessing"]["qwen_request"]["image_sha256"] = result["source_image"]["sha256"]
    result["preprocessing"]["qwen_response"] = {
        "sections": [{
            "shared_multiplier": None,
            "rows": [{
                "tokens": [
                    {"text": text, "bbox": box}
                    for text, box in token_specs
                ],
                "numbers": [["34"], ["35"], ["36"], ["38"]],
                "multiplier": "3/4x1",
                "layout_hint": "row_bet",
            }],
        }],
    }
    return result


def _sample011_s02_structure(
    *,
    status: str = "incomplete",
    rules: list[str] | None = None,
    model_multiplier: str = "3/4x1",
) -> list[dict]:
    return [{
        "line_id": "S02-L01",
        "structure_id": "S02",
        "primary_line_id": "S02-L01",
        "member_line_ids": ["S02-L01"],
        "line_role": "primary",
        "game": "539",
        "model_candidate": {
            "numbers": [["34"], ["35"], ["36"], ["38"]],
            "multiplier": model_multiplier,
            "layout_hint": "row_bet",
            "shared_multiplier": None,
        },
        "reconstructed_candidate": {
            "number_groups": [["34", "35", "36", "38"]],
            "multiplier_rules": ["3/4X1"] if rules is None else rules,
            "layout": "normal_row",
            "collision": None,
            "shared_multiplier": None,
        },
        "status": status,
        "warnings": ["model_layout_unsupported"],
        "evidence": {},
        "needs_review": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }]


def test_new_qwen_result_creates_new_browser_review_session(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert session["schema_version"] == "vision-review-session-v1"
    assert session["source_image_id"] == "image-gate3a-1"
    assert session["game"] == "539"


def test_each_primary_structure_creates_exactly_one_bet_card(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.locator(".qwen-review-card").count() == 2
    assert page.locator('.qwen-review-card[data-structure-id="S01"]').count() == 1
    assert page.locator('.qwen-review-card[data-structure-id="S02"]').count() == 1


def test_continuation_line_never_creates_an_independent_card(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert [card["structure_id"] for card in session["structures"]] == ["S01", "S02"]
    assert session["structures"][1]["source_line_ids"] == ["S02-L01", "S02-L02"]
    assert page.locator('.qwen-review-card[data-structure-id="S02-L02"]').count() == 0


def test_pending_structure_changes_to_confirmed_only_in_session(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.get_attribute('.qwen-review-card[data-structure-id="S01"]', "data-review-state") == "pending"
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    assert page.get_attribute('.qwen-review-card[data-structure-id="S01"]', "data-review-state") == "confirmed"
    assert "已確認 1 / 總共 2" in page.text_content("#qwen-review-progress")


def test_edit_reparse_and_adopt_updates_only_staged_structure(page) -> None:
    _mount(page)
    _run_qwen(page)
    original = page.evaluate("qwenGetRecognitionResult()")
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector('.qwen-review-card[data-structure-id="S01"] .qwen-adopt-edit')
    before_adopt = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert before_adopt["number_groups"] == [["05", "06", "10", "28"]]
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-adopt-edit')
    staged = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert staged["number_groups"] == [["05", "09", "17", "28"]]
    assert staged["multiplier_rules"] == ["2/3X1"]
    assert page.evaluate("qwenGetRecognitionResult()") == original


def test_card_manual_reparse_is_explicit_and_register_candidate_false(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    assert calls["manual"] == []
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    assert calls["manual"] == []
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector(".qwen-adopt-edit")
    assert calls["manual"] == [{
        "text": "05 09 17 28 2/3X1",
        "game": "539",
        "register_candidate": False,
    }]


def test_whole_review_cannot_finish_until_every_card_is_confirmed(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.is_disabled("#qwen-complete-review")
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    assert page.is_disabled("#qwen-complete-review")


def test_all_confirmed_cards_create_candidate_preview(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-confirm-structure')
    assert page.is_enabled("#qwen-complete-review")
    page.click("#qwen-complete-review")
    summary = page.evaluate("qwenGetReviewSummary()")
    assert summary["schema_version"] == "vision-review-candidate-preview-v1"
    assert summary["game"] == "539"
    assert summary["source_image_id"] == "image-gate3a-1"
    assert summary["image_sha256"] == "upload-sha-1"
    assert len(summary["confirmed_structures"]) == 2
    assert "人工審核完成，尚未加入待選牌清單" in page.text_content("#qwen-review-complete-status")


def test_candidate_preview_never_writes_queue(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        page.click(f'.qwen-review-card[data-structure-id="{structure_id}"] .qwen-confirm-structure')
    before = list(calls["urls"])
    page.click("#qwen-complete-review")
    assert calls["urls"] == before
    assert not any("queue" in url for url in calls["urls"])


def test_candidate_preview_has_no_accepted_by_human_field(page) -> None:
    _mount(page)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        page.click(f'.qwen-review-card[data-structure-id="{structure_id}"] .qwen-confirm-structure')
    page.click("#qwen-complete-review")
    assert "accepted_by_human" not in json.dumps(page.evaluate("qwenGetReviewSummary()"))
    assert "manual_candidate_id" not in json.dumps(page.evaluate("qwenGetReviewSummary()"))


def test_adopted_manual_edit_is_retained_in_review_summary(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector(".qwen-adopt-edit")
    page.click(".qwen-adopt-edit")
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-confirm-structure')
    page.click("#qwen-complete-review")
    first = page.evaluate("qwenGetReviewSummary().confirmed_structures[0]")
    assert first["number_groups"] == [["05", "09", "17", "28"]]
    assert first["multiplier_rules"] == ["2/3X1"]
    assert first["manual_edits"][0]["canonical_text"] == "05 09 17 28 2/3X1"


def test_uploading_new_image_clears_old_session_state_and_preview(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    old_id = page.evaluate("qwenGetReviewSession().review_session_id")
    page.set_input_files(
        "#vision-file-input",
        {"name": "new.png", "mimeType": "image/png", "buffer": _png_bytes("gray")},
    )
    page.wait_for_function("document.getElementById('vision-filename').textContent === 'slip-2.png'")
    assert page.evaluate("qwenGetReviewSession()") is None
    _run_qwen(page)
    new_session = page.evaluate("qwenGetReviewSession()")
    assert new_session["review_session_id"] != old_id
    assert all(card["review_state"] == "pending" for card in new_session["structures"])
    assert all(card["manual_edits"] == [] for card in new_session["structures"])
    assert new_session["candidate_preview"] is None


def test_deleting_image_clears_review_session(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click("#vision-delete-btn")
    page.wait_for_function(
        "qwenGetReviewSession() === null && document.getElementById('vision-results').style.display === 'none'"
    )
    assert page.evaluate("qwenGetReviewSession()") is None
    assert not page.is_visible("#vision-results")
    assert not page.is_visible("#vision-structure-highlight")


def test_recognition_result_remains_immutable_through_review(page) -> None:
    _mount(page)
    expected = _job_result()
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-edit-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-card-cancel')
    assert page.evaluate("qwenGetRecognitionResult()") == expected


def test_qwen_failed_ux_is_safe_and_debug_details_are_collapsed(page) -> None:
    _mount(page, failed=True)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-failure-message")
    assert page.text_content("#qwen-failure-message") == (
        "AI 未能完整讀取這張圖片。可以重新辨識或改用手動輸入。"
    )
    assert page.get_attribute(".qwen-failure-details", "open") is None
    assert "private stack trace" not in page.text_content("#qwen-failure-message")
    assert page.evaluate("qwenGetReviewSession()") is None


def test_review_session_and_summary_keep_all_safety_flags(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert session["safety"] == {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    for structure_id in ("S01", "S02"):
        page.click(f'.qwen-review-card[data-structure-id="{structure_id}"] .qwen-confirm-structure')
    page.click("#qwen-complete-review")
    summary = page.evaluate("qwenGetReviewSummary()")
    assert summary["human_confirmation_required"] is True
    assert summary["auto_apply"] is False
    assert summary["auto_confirm"] is False
    assert summary["auto_submit"] is False


def test_review_workflow_never_calls_webfill_or_paid_provider(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-confirm-structure')
    page.click("#qwen-complete-review")
    assert calls["jobs"] == [{
        "image_id": "image-gate3a-1",
        "provider_id": "qwen-dashscope",
        "game": "539",
    }]
    assert not any("webfill" in url or "assist-fill" in url for url in calls["urls"])


def test_main_cards_use_human_status_and_keep_warnings_in_advanced_details(page) -> None:
    _mount(page)
    _run_qwen(page)
    cards_text = page.text_content("#qwen-review-cards")
    assert "AI 結構一致，仍請確認" in cards_text
    assert "AI 與規則結果不同，請檢查" in cards_text
    assert "fragment_multiplier_ambiguous" not in page.text_content(
        '.qwen-review-card[data-structure-id="S02"] .qwen-review-human-status'
    )
    advanced = page.text_content(
        '.qwen-review-card[data-structure-id="S02"] .qwen-card-advanced'
    )
    assert "fragment_multiplier_ambiguous" in advanced
    assert page.get_attribute("#qwen-advanced-evidence", "open") is None


def test_all_four_technical_statuses_have_required_human_labels() -> None:
    html = render_vision_ui_section()
    for label in (
        "AI 結構一致，仍請確認",
        "AI 與規則結果不同，請檢查",
        "資料需要人工檢查",
        "此玩法目前需要人工處理",
    ):
        assert label in html


def test_normal_and_column_cards_render_daily_review_shapes(page) -> None:
    _mount(page)
    _run_qwen(page)
    normal = page.locator('.qwen-review-card[data-structure-id="S01"]')
    column = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert "05 06 10 28" in normal.locator(".qwen-review-numbers").text_content()
    assert "3/4X1" in normal.locator(".qwen-review-play").text_content()
    assert column.locator(".qwen-review-column").all_text_contents() == [
        "24 34", "08 38", "16 36", "03 13",
    ]
    assert "2/3/4X0.1" in column.locator(".qwen-review-play").text_content()
    assert "2/3/4" in column.locator(".qwen-review-collision").text_content()
    normal.locator(".qwen-edit-structure").click()
    assert page.input_value('.qwen-card-editable[data-card-index="0"]') == (
        "05 06 10 28 三四X1"
    )
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-card-cancel')
    column.locator(".qwen-edit-structure").click()
    assert page.input_value('.qwen-card-editable[data-card-index="1"]') == (
        "24 34 / 08 38 / 16 36 / 03 13 二三四X0.1"
    )


def test_incomplete_card_keeps_complete_reconstructed_multiplier(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert "34 35 36 38" in card.locator(".qwen-review-numbers").text_content()
    assert "3/4X1" in card.locator(".qwen-review-play").text_content()
    assert "資料需要人工檢查" in card.locator(
        ".qwen-review-human-status"
    ).text_content()
    session_card = page.evaluate("qwenGetReviewSession().structures[0]")
    assert session_card["source_status"] == "incomplete"
    assert session_card["staged_structure"]["multiplier_rules"] == ["3/4X1"]
    assert session_card["review_state"] == "pending"


def test_incomplete_card_reports_multiplier_missing_only_when_rules_are_empty(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(rules=[]),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    play_text = card.locator(".qwen-review-play").text_content()
    assert "未辨識" in play_text
    assert "需要人工處理" in play_text
    assert "3/4X1" not in play_text


def test_divergent_card_stages_reconstruction_and_preserves_both_evidence(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(
            status="divergent",
            model_multiplier="3X1",
        ),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert "3/4X1" in card.locator(".qwen-review-play").text_content()
    assert "AI 與規則結果不同，請檢查" in card.locator(
        ".qwen-review-human-status"
    ).text_content()
    advanced = card.locator(".qwen-card-advanced").text_content()
    assert '\"multiplier\":\"3X1\"' in advanced
    assert '\"multiplier_rules\":[\"3/4X1\"]' in advanced
    assert page.evaluate("qwenGetReviewSession().structures[0].review_state") == "pending"


def test_sample011_s02_review_is_immutable_and_has_no_side_effect(page) -> None:
    result = _sample011_s02_result()
    calls = _mount(
        page,
        job_result=result,
        structure_evidence=_sample011_s02_structure(),
    )
    _run_qwen(page)

    assert page.evaluate("qwenGetRecognitionResult()") == result
    assert calls["manual"] == []
    assert not any(
        fragment in url
        for url in calls["urls"]
        for fragment in ("queue", "webfill", "assist-fill")
    )
    session = page.evaluate("qwenGetReviewSession()")
    assert session["safety"] == {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    serialized = json.dumps(session)
    assert "accepted_by_human" not in serialized
    assert "manual_candidate_id" not in serialized


def test_card_canonical_text_uses_existing_parser_contract() -> None:
    normal = reparse_text("05 06 10 28 三四X1", game="539")
    assert normal["ok"] is True
    assert normal["numbers"] == [5, 6, 10, 28]
    assert normal["stars"] == [3, 4]
    assert normal["type"] == "normal"

    column = reparse_text(
        "24 34 / 08 38 / 16 36 / 03 13 二三四X0.1",
        game="539",
    )
    assert column["ok"] is True
    assert column["columns"] == [[24, 34], [8, 38], [16, 36], [3, 13]]
    assert column["stars"] == [2, 3, 4]
    assert column["type"] == "column"


def test_clicking_card_highlights_only_reliable_structure_bbox(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S02"]')
    assert page.is_visible("#vision-structure-highlight")
    assert float(page.eval_on_selector("#vision-structure-highlight", "el => parseFloat(el.style.height)")) > 0
