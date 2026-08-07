"""Gate 1B safety tests for Qwen review-only UI evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui.assist_panel_vision_html import render_vision_ui_section


EXPECTED_LOCKED_HASHES = {
    ("raw", "sample-010.jpg"): "fa453fbfb2a3da16bbc04831843db7c9c07524704e66d3475a1add6b7893b526",
    ("raw", "sample-011.jpg"): "5f60cb2ebf751421fa0f0c510e14ee7fbc13e77412b089c6918b90765dfe4687",
    ("raw", "sample-012.jpg"): "b5e4428e6a8fcbc95a417e096c4568c3033857349acf66f30393e18112564284",
    ("raw", "sample-013.jpg"): "ef594a5b228379288de22c4e18e9d90dde751cc13fcc4b71082e279c45c1d388",
    ("ground-truth-draft", "sample-010.json"): "a701c78b63831cc577d5861a5e4cb9c043e3c850535206fcad0286155439cfaa",
    ("ground-truth-draft", "sample-011.json"): "3b1be2685ec6b9e0785b1f79c95147784ae23fc88b0ab93ccf75b8b19392536a",
    ("ground-truth-draft", "sample-012.json"): "ade32fa42863f8585637390eb186159cb90ce898515dd8354c95d3b4b38c0eff",
    ("ground-truth-draft", "sample-013.json"): "5f3a2acc045df5acefe070b87f8cf494893d575837e9bd130658be7502fbe100",
    ("prelabels", "sample-010.json"): "0f3d0c45077008a58eebd35e7027dd3065f763f8c63cc077b0954e5f2cd257f1",
    ("prelabels", "sample-011.json"): "5f98b584c68105218a673c965c20fdd4214940e66c42dff3c0b7c6e74facca25",
    ("prelabels", "sample-012.json"): "851528cb8cd9a213521b25ab3e48f97f77c583b003e6db310111a83acb07d640",
    ("prelabels", "sample-013.json"): "05984f1072e0343577b7ee95d446b75111fd2fe09fe0514a52f015b6ae249b12",
}


def _html() -> str:
    return render_vision_ui_section()


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(3000)
        yield browser_page
        context.close()
        browser.close()


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _completed_qwen_result(line_text: str = "99") -> dict:
    return {
        "request_id": "job-image-qwen",
        "status": "completed",
        "provider": {
            "id": "qwen-dashscope",
            "model_name": "qwen3-vl-plus",
            "model_version": "",
        },
        "source_image": {"sha256": "image-sha"},
        "raw_text": line_text,
        "lines": [
            {
                "line_id": "S01-L01",
                "text": line_text,
                "tokens": [
                    {
                        "text": line_text,
                        "bounding_box": {
                            "coordinate_space": "pixel",
                            "polygon": [[1, 2], [3, 2], [3, 4], [1, 4]],
                        },
                    }
                ],
                "warnings": [],
            }
        ],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
            "prompt_version": "combined-bbox-v1",
            "prompt_sha256": "prompt-sha",
            "qwen_request": {
                "model": "qwen3-vl-plus",
                "image_sha256": "image-sha",
                "cache_hit": False,
                "request_id": "qwen-request-id",
            },
            "qwen_response": {
                "sections": [
                    {
                        "shared_multiplier": None,
                        "rows": [
                            {
                                "tokens": [{"text": line_text, "bbox": [1, 2, 3, 4]}],
                                "numbers": [[line_text]],
                                "multiplier": None,
                                "layout_hint": "normal_row",
                            }
                        ],
                    }
                ]
            },
        },
    }


def _mount_qwen_ui(page, *, job_result: dict, manual_result: dict | None = None):
    calls: dict[str, list] = {"jobs": [], "manual": [], "urls": [], "errors": []}
    png = _png_bytes()

    def handle(route):
        request = route.request
        calls["urls"].append(request.url)
        if request.url == "http://gate1b.test/":
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
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "image": {
                        "image_id": "image-qwen",
                        "original_filename": "slip.png",
                        "mime_type": "image/png",
                        "width": 40,
                        "height": 40,
                        "byte_size": len(png),
                        "sha256": "image-sha",
                    },
                }),
            )
            return
        if "/api/vision/v1/images/image-qwen" in request.url and request.method == "GET":
            route.fulfill(status=200, content_type="image/png", body=png)
            return
        if request.url.endswith("/api/vision/v1/jobs"):
            calls["jobs"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "result": job_result}),
            )
            return
        if request.url.endswith("/manual-reparse"):
            calls["manual"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(manual_result or {
                    "ok": False,
                    "reason": "needs_review",
                    "auto_confirm": False,
                    "auto_submit": False,
                }),
            )
            return
        route.abort()

    page.route("**/*", handle)
    page.on("pageerror", lambda error: calls["errors"].append(str(error)))
    page.goto("http://gate1b.test/")
    try:
        page.wait_for_function(
            "document.getElementById('vision-provider-status').textContent.indexOf('Qwen 看圖已設定') >= 0",
            timeout=3000,
        )
    except Exception:
        pytest.fail(f"Qwen provider status did not initialize: {calls}")
    page.evaluate("document.getElementById('vision-section').style.display = 'block'")
    page.set_input_files(
        "#vision-file-input",
        {"name": "slip.png", "mimeType": "image/png", "buffer": png},
    )
    page.wait_for_selector("#vision-qwen-run-btn:visible")
    return calls


def test_qwen_is_called_only_by_explicit_button() -> None:
    html = _html()
    assert 'id="vision-qwen-run-btn"' in html
    assert '>Qwen 看圖</button>' in html
    assert html.count("visionRunQwenJob()") == 1
    assert 'window.visionRunQwenJob = function()' in html
    assert "只會在按下按鈕後呼叫" in html


def test_qwen_job_posts_explicit_provider_id() -> None:
    qwen_job = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderQwenFailure",
    )
    assert 'fetch("/api/vision/v1/jobs"' in qwen_job
    assert 'provider_id: "qwen-dashscope"' in qwen_job
    assert "openai-vision-paid" not in qwen_job
    assert "aided_image_id" not in qwen_job


def test_completed_means_waiting_for_human_not_confirmed() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "AI 辨識完成，待人工核對" in renderer
    assert "僅代表 provider job 完成，不代表人工確認" in renderer
    assert "needs_review" in renderer
    for forbidden in ("正確", "已確認", "可填入"):
        assert forbidden not in renderer


def test_qwen_evidence_keeps_required_provenance_and_structure() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    for field in (
        "provider=",
        "model=",
        "prompt_version=",
        "prompt_sha256=",
        "image_sha256=",
        "cache_hit=",
        "request_id=",
        "RecognitionResult.raw_text",
        "Line.text",
        "token bbox",
        "numbers=",
        "multiplier=",
        "layout_hint=",
        "shared_multiplier=",
    ):
        assert field in renderer


def test_each_line_has_copy_stage_and_manual_edit_controls() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "qwen-copy-line" in renderer and "複製" in renderer
    assert "qwen-stage-line" in renderer and "帶入修正欄" in renderer
    assert "qwen-line-edit" in renderer and "手動修改" in renderer
    assert "qwen-review-editable" in renderer


def test_stage_line_changes_only_frontend_temporary_field() -> None:
    stage = _between(
        _html(),
        "window.qwenStageLine = function(index)",
        "window.qwenManualReparse",
    )
    assert "target.value = inputs[index].value" in stage
    assert "fetch(" not in stage
    for forbidden in (
        "panelState",
        "validCandidates",
        "accepted_by_human",
        "ground-truth-draft",
        "approved_fill_queue",
        "model_raw_text =",
    ):
        assert forbidden not in stage


def test_manual_reparse_requires_explicit_human_button() -> None:
    html = _html()
    assert "人工確認並重新解析" in html
    qwen_job_and_render = _between(
        html,
        "window.visionRunQwenJob = function()",
        "window.qwenManualReparse = function()",
    )
    assert 'fetch("/manual-reparse"' not in qwen_job_and_render
    manual = _between(
        html,
        "window.qwenManualReparse = function()",
        "function _renderPendingConfirmation",
    )
    assert 'fetch("/manual-reparse"' in manual
    assert 'body: JSON.stringify({ text: text, game: "auto" })' in manual
    assert "parser／validator" in manual


def test_invalid_qwen_response_displays_failure_and_review_state() -> None:
    html = _html()
    failure = _between(
        html,
        "function _renderQwenFailure(message)",
        "function _flattenQwenRows",
    )
    renderer = _between(
        html,
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "Qwen 辨識失敗" in failure
    assert "needs_review" in failure
    assert 'result.status !== "completed"' in renderer
    assert "!sections.length || !lines.length" in renderer
    assert "invalid Qwen response schema" in renderer


def test_structurally_valid_but_wrong_qwen_output_never_becomes_candidate() -> None:
    qwen_flow = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderPendingConfirmation",
    )
    assert "needs_review" in qwen_flow
    for forbidden in (
        "panelState",
        "validCandidates",
        "accepted_by_human",
        "create-batch",
        "approved_fill_queue",
        "ground-truth-draft",
    ):
        assert forbidden not in qwen_flow


def test_qwen_flow_has_no_auto_actions_paid_fallback_or_webfill() -> None:
    qwen_flow = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderPendingConfirmation",
    )
    assert "auto_confirm=false" in qwen_flow
    assert "auto_submit=false" in qwen_flow
    assert "openai-vision-paid" not in qwen_flow
    assert "assist-fill" not in qwen_flow.lower()
    assert "webfill" not in qwen_flow.lower()


def test_qwen_prompt_and_provider_code_are_not_modified_by_gate1b() -> None:
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "src/betguard/vision/qwen_prompts.py").exists()
    assert (repo / "src/betguard/vision/providers/qwen_dashscope.py").exists()
    ui_source = (
        repo / "src/betguard/webui/assist_panel_vision_html.py"
    ).read_text(encoding="utf-8")
    assert "qwen_prompts" not in ui_source
    assert "QwenDashScopeProvider" not in ui_source


def test_locked_sample_hashes_are_unchanged() -> None:
    configured = os.environ.get("BETGUARD_DATASET", "").strip()
    configured_dataset = Path(configured) if configured else None
    configured_has_locked_samples = bool(
        configured_dataset
        and all(
            (configured_dataset / folder / name).exists()
            for folder, name in EXPECTED_LOCKED_HASHES
        )
    )
    dataset = (
        configured_dataset
        if configured_has_locked_samples
        else Path(r"C:\BetguardOCRDataset")
    )
    if not all((dataset / folder / name).exists() for folder, name in EXPECTED_LOCKED_HASHES):
        pytest.skip("locked OCR dataset is not available")
    actual = {
        (folder, name): hashlib.sha256((dataset / folder / name).read_bytes()).hexdigest()
        for folder, name in EXPECTED_LOCKED_HASHES
    }
    assert actual == EXPECTED_LOCKED_HASHES


def test_browser_qwen_evidence_requires_explicit_actions(page) -> None:
    calls = _mount_qwen_ui(page, job_result=_completed_qwen_result("99"))
    assert calls["jobs"] == []
    assert calls["manual"] == []
    assert page.get_attribute("#vision-preview-img", "src") == "/api/vision/v1/images/image-qwen"

    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")
    assert page.text_content("#qwen-evidence-status") == "AI 辨識完成，待人工核對"
    assert page.text_content("#qwen-raw-text") == "99"
    assert "needs_review" in page.text_content("#vision-results-body")
    assert calls["jobs"] == [
        {"image_id": "image-qwen", "provider_id": "qwen-dashscope"}
    ]
    assert calls["manual"] == []

    page.fill(".qwen-line-edit", "05 09 2X1")
    page.click(".qwen-stage-line")
    assert page.input_value("#qwen-review-editable") == "05 09 2X1"
    assert calls["manual"] == []
    assert not any("webfill" in url or "assist-fill" in url for url in calls["urls"])

    page.click("#qwen-manual-reparse-btn")
    page.wait_for_function("document.getElementById('qwen-manual-reparse-btn').disabled === false")
    assert calls["manual"] == [{"text": "05 09 2X1", "game": "auto"}]
    assert "needs_review" in page.text_content("#qwen-manual-reparse-result")


def test_browser_invalid_qwen_result_displays_failure(page) -> None:
    failed = {
        "request_id": "job-failed",
        "status": "failed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "provider_error": {"message": "invalid Qwen response schema"},
        "lines": [],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
    }
    calls = _mount_qwen_ui(page, job_result=failed)
    assert calls["jobs"] == []
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")
    assert page.text_content("#qwen-evidence-status") == "Qwen 辨識失敗"
    assert "invalid Qwen response schema" in page.text_content("#vision-results-body")
    assert page.locator("#qwen-manual-reparse-btn").count() == 0
    assert calls["manual"] == []
