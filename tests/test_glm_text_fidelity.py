"""No inference: exercise only the independent candidate's text adapter."""
import pytest
from betguard.ocr_glm import clean_glm_text

@pytest.mark.parametrize("raw", [
    "03 × 16 × 7尾 二三×1", "11 33 各0.5車", "03 × 16 × ?尾 二三×1",
    "11 半車", "01 X 02/03 2,3×0.1", "  01 02 2×5\n\n未知> (crossed out)\n03 04 2×0.5  ",
])
def test_clean_is_lossless(raw):
    assert clean_glm_text(raw) == raw
