"""GLM-OCR 原文保真測試（不載入模型）。舊刪除契約已撤銷。"""
from __future__ import annotations

from betguard.ocr_glm import clean_glm_text


class TestCleanGlmText:
    def test_removes_pen_stroke_symbols(self):
        """舊名稱保留以比較 test ID；不能假定未知符號是可丟棄噪音。"""
        raw = "01 > 0 × 1 14 < 16 ≡ x 0.5\n□ × 3"
        assert clean_glm_text(raw) == raw

    def test_noise_replaced_with_space_not_concatenation(self):
        """未知位置必須保留，既不換空格也不黏連。"""
        raw = "01×16×5层>3×1"
        assert clean_glm_text(raw) == raw

    def test_keeps_decimal_point(self):
        """小數點必須保留（×0.5 是倍數）。"""
        raw = "38×17 4×12×0.5"
        assert clean_glm_text(raw) == "38×17 4×12×0.5"

    def test_empty_lines_dropped(self):
        """空白行可能表達投注邊界，須保留。"""
        raw = "11 20 × 1\n\n\n18 26 × 1"
        assert clean_glm_text(raw) == raw

    def test_all_noise_returns_empty(self):
        """只有未知字元也須保留。"""
        assert clean_glm_text(">>>===≡≡□□□") == ">>>===≡≡□□□"

    def test_whitespace_collapsed(self):
        """連續空白保持原樣。"""
        raw = "01    20   ×   1"
        assert clean_glm_text(raw) == raw
