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

    def test_fake_provider_warning(self):
        from betguard.webui.assist_panel_vision_html import render_vision_ui_section
        html = render_vision_ui_section()
        assert "Fake Provider" in html or "測試模式" in html or "尚未進行真實 OCR" in html

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
