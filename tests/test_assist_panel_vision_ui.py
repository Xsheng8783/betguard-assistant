"""Product contract for the simple image-to-text Assist Panel flow."""

from __future__ import annotations


def _html() -> str:
    from betguard.webui.assist_panel_vision_html import render_vision_ui_section

    return render_vision_ui_section()


def test_image_flow_is_four_plain_user_steps() -> None:
    html = _html()

    for label in ("上傳圖片", "AI 辨識", "修改文字", "解析／輔助填入"):
        assert label in html
    assert 'id="vision-drop-zone"' in html
    assert 'id="vision-run-btn"' in html
    assert 'id="vision-transcription-text"' in html
    assert 'id="vision-use-text-btn"' in html


def test_transcription_is_large_editable_text_not_a_review_schema() -> None:
    html = _html()

    assert '<textarea id="vision-transcription-text"' in html
    assert "min-height:210px" in html
    assert "直接修改、刪除或補上內容" in html
    assert "逐筆確認" not in html
    assert "AI_UNCERTAIN" not in html
    assert "number_groups" not in html
    assert "bbox" not in html.lower()
    assert "field conflict" not in html.lower()


def test_image_text_uses_the_existing_text_parser_entrypoint() -> None:
    html = _html()

    assert 'document.getElementById("batch-text").value = text' in html
    assert 'switchMode("text")' in html
    assert "createBatch();" in html
    assert "/assist-panel/create-batch" not in html
    assert "image-specific parser" in html


def test_gemma_transcription_has_no_qwen_or_model_voting_controls() -> None:
    html = _html()

    assert 'fetch("/api/vision/v1/transcriptions"' in html
    assert "Qwen" not in html
    assert "runtime-reader-router" not in html
    assert "second opinion" not in html.lower()
    assert "provider_id" not in html


def test_failed_ai_never_blocks_manual_text_entry() -> None:
    html = _html()

    assert "您仍可直接輸入文字" in html
    assert 'transcription.style.display = "block"' in html
    assert "quality Gate" not in html
    assert "visionQualityPassed" not in html


def test_upload_accepts_supported_images_drag_drop_and_paste() -> None:
    html = _html()

    assert 'accept="image/png,image/jpeg,image/webp"' in html
    assert 'addEventListener("drop"' in html
    assert 'addEventListener("paste"' in html
    assert "clipboardData" in html


def test_no_automatic_or_real_site_action_is_present() -> None:
    html = _html()

    assert "auto-submit=false" in html
    assert "auto-confirm=true" not in html
    assert "submit(" not in html.lower()
    assert "candidate-queue" not in html
    assert "assist-fill/start" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_original_text_input_remains_the_default_product_entry() -> None:
    from betguard.webui.assist_panel_html import ASSIST_PANEL_HTML

    assert 'id="batch-text"' in ASSIST_PANEL_HTML
    assert 'id="createBatchBtn"' in ASSIST_PANEL_HTML
    assert "function createBatch()" in ASSIST_PANEL_HTML
    assert 'fetch("/assist-panel/create-batch"' in ASSIST_PANEL_HTML
    assert 'style="display:none"' in _html().replace(" ", "")
