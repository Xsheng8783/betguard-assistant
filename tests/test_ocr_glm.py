"""GLM-OCR 後處理過濾測試（不需載入模型，快速）。"""
from __future__ import annotations

from betguard.ocr_glm import clean_glm_text


class TestCleanGlmText:
    def test_removes_pen_stroke_symbols(self):
        """筆跡符號（> < ≡ □ ∑ ≥）應全部清除。"""
        raw = "01 > 0 × 1 14 < 16 ≡ x 0.5\n□ × 3"
        assert clean_glm_text(raw) == "01 0 × 1 14 16 x 0.5\n× 3"

    def test_noise_replaced_with_space_not_concatenation(self):
        """噪音字元位置變空格，兩側數字不得黏連（5层>3 → 5 3 不是 53）。"""
        raw = "01×16×5层>3×1"
        assert clean_glm_text(raw) == "01×16×5 3×1"

    def test_keeps_decimal_point(self):
        """小數點必須保留（×0.5 是倍數）。"""
        raw = "38×17 4×12×0.5"
        assert clean_glm_text(raw) == "38×17 4×12×0.5"

    def test_empty_lines_dropped(self):
        """空白行被丟棄。"""
        raw = "11 20 × 1\n\n\n18 26 × 1"
        assert clean_glm_text(raw) == "11 20 × 1\n18 26 × 1"

    def test_all_noise_returns_empty(self):
        """全部都是噪音時回傳空字串。"""
        assert clean_glm_text(">>>===≡≡□□□") == ""

    def test_whitespace_collapsed(self):
        """連續空白壓縮成單一空格。"""
        raw = "01    20   ×   1"
        assert clean_glm_text(raw) == "01 20 × 1"
