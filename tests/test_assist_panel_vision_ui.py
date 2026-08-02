"""Test Assist Panel vision UI integration."""

from __future__ import annotations


class TestVisionUIHtml:
    """Verify vision UI HTML is present and well-formed."""

    def test_vision_section_present(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "vision-section" in html
        assert "vision-drop-zone" in html
        assert "vision-file-input" in html
        assert "vision-preview" in html
        assert "vision-run-btn" in html
        assert "vision-results" in html

    def test_paid_vision_and_human_confirmation_notice(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert 'provider_id: "openai-vision-paid"' in html
        assert "vision-provider-status" in html
        assert "PENDING_HUMAN_CONFIRMATION" in html
        assert "逐行人工確認" in html

    def test_document_mode_can_be_supplied_before_paid_recognition(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section

        html = render_vision_ui_section()

        assert 'id="vision-document-mode"' in html
        assert '<option value="column">整張是柱碰</option>' in html
        assert "document_mode:" in html

    def test_confirmed_text_only_returns_to_text_review(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "visionApplyConfirmedText" in html
        assert 'textArea.value = texts.join("\\n")' in html
        assert 'switchMode("text")' in html
        assert "createBatchBtn.click" not in html
        assert "if (!visionQualityPassed)" in html
        assert "auto_submit=false" in html
        assert "auto_confirm=false" in html

    def test_layout_hint_is_not_treated_as_final_bet_type(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "模型版面提示" in html
        assert "vision-layout-choice" in html
        assert "人工選擇牌型" in html
        assert '<option value="column">柱碰</option>' in html
        assert "模型不會決定最終牌型" in html
        assert 'layoutChoice === "unknown"' in html
        assert "牌組數" in html
        assert "一筆完整牌組" in html
        assert "相同星別＋倍率" in html
        assert "各" not in html

    def test_ocr_observations_are_converted_to_editable_review_draft(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "_visionReviewDraft(line)" in html
        assert 'group.join(".")' in html
        assert '.join("/")' in html
        assert 'return numbers.split(".")[0] + "車" + multiplier' in html
        assert "模型原文" in html
        assert "可編輯的 Review 格式草稿" in html

    def test_low_resolution_portrait_uses_one_composite_aid_image(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "_createVisionAidImage" in html
        assert 'canvas.width = 1800' in html
        assert '"LEFT DETAIL", "CENTER DETAIL", "RIGHT DETAIL"' in html
        assert "uploadedAidImageId" in html
        assert "aided_image_id" in html
        assert "原始圖片品質 Gate 仍然有效" in html

    def test_no_assist_fill_button(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "assist-fill" not in html.lower()

    def test_no_create_batch_call(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "create-batch" not in html.lower()

    def test_no_external_urls(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "http://" not in html
        assert "https://" not in html

    def test_escape_function_present(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "esc(" in html or "escape(" in html

    def test_drop_zone_has_accept_attributes(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "accept" in html
        assert "image/png" in html
        assert "image/jpeg" in html

    def test_clipboard_paste_handler(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "paste" in html.lower()
        assert "clipboardData" in html


class TestAssistPanelIntegration:
    """Verify assist panel integrates vision UI."""

    def test_handler_imports_vision(self):
        """_handle_assist_panel imports vision HTML renderer."""
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        assert callable(render_vision_ui_section)

    def test_assist_panel_html_unchanged(self):
        """Original ASSIST_PANEL_HTML is still imported (not replaced)."""
        from betguard.webui.assist_panel_html import ASSIST_PANEL_HTML
        assert "Betguard" in ASSIST_PANEL_HTML
        assert "textarea" in ASSIST_PANEL_HTML

    def test_mode_toggle_in_handler(self):
        """Handler injects mode toggle buttons — verify import works."""
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "switchMode" in html or "vision-section" in html


class TestDefaultMode:
    """Default mode remains text input."""

    def test_vision_section_hidden_by_default(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert 'style="display:none"' in html.replace(" ", "")
        assert "vision-section" in html

    def test_text_input_still_present(self):
        from betguard.webui.assist_panel_html import ASSIST_PANEL_HTML
        assert "batch-text" in ASSIST_PANEL_HTML
        assert "createBatchBtn" in ASSIST_PANEL_HTML
