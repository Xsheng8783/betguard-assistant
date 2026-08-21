from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from betguard.vision.cell_first import (  # noqa: E402
    CELL_BOUNDARY_UNCERTAIN,
    DECIMAL_LITERAL,
    MULTIPLIER_FRAGMENT,
    NUMBER_01_39,
    OPERATOR_X,
    SPECIAL_LITERAL,
    UNCERTAIN,
    classify_literal,
    make_token,
    reconstruct_visible_rows,
)


def test_literal_classification_stops_before_betting_semantics() -> None:
    assert classify_literal("03", 0.98) == NUMBER_01_39
    assert classify_literal("×", 0.98) == OPERATOR_X
    assert classify_literal("0.5", 0.98) == DECIMAL_LITERAL
    assert classify_literal("2/3 x 0.5", 0.98) == MULTIPLIER_FRAGMENT
    assert classify_literal("半車", 0.98) == SPECIAL_LITERAL
    assert classify_literal("07", 0.20) == UNCERTAIN


def test_rows_follow_geometry_and_protect_split_decimal() -> None:
    specs = [
        ("T1", "36", 70, 20, 94, 40),
        ("T2", "×", 100, 20, 112, 40),
        ("T3", "07", 120, 20, 144, 40),
        ("T4", "0", 80, 70, 91, 90),
        ("T5", ".", 93, 70, 99, 90),
        ("T6", "5", 102, 70, 113, 90),
        ("T7", "尾", 130, 70, 151, 90),
    ]
    tokens = [
        make_token(
            token_id=token_id,
            cell_id="CELL-0001",
            text_raw=text,
            confidence=0.99,
            bbox=[x1, y1, x2, y2],
        )
        for token_id, text, x1, y1, x2, y2 in reversed(specs)
    ]

    result = reconstruct_visible_rows("CELL-0001", tokens)

    assert [[item["text"] for item in row] for row in result["visible_rows"]] == [
        ["36", "×", "07"],
        ["0", ".", "5", "尾"],
    ]
    assert [span["literal"] for span in result["decimal_spans"]] == ["0.5"]
    decimal_members = {
        token["text_raw"]: token["classification"]
        for token in result["tokens"]
        if token.get("decimal_group_id")
    }
    assert decimal_members == {"0": DECIMAL_LITERAL, ".": DECIMAL_LITERAL, "5": DECIMAL_LITERAL}
    assert [
        token["text_raw"]
        for token in result["tokens"]
        if token["classification"] == NUMBER_01_39
    ] == ["36", "07"]
    assert result["machine_evidence_only"] is True
    assert result["human_confirmed"] is False
    assert result["candidate_created"] is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False


def _ocr_python() -> Path:
    configured = os.environ.get("BETGUARD_PPOCR_SHADOW_PYTHON")
    return Path(configured) if configured else Path(r"C:\BetguardOCRBench\venv\Scripts\python.exe")


@pytest.mark.skipif(not _ocr_python().exists(), reason="isolated OpenCV/PaddleOCR environment unavailable")
def test_red_grid_segmentation_and_original_image_bbox_mapping() -> None:
    script = r'''
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd() / "tools" / "vision"))
from cell_first_pipeline import run_cell_first


class FakeOcr:
    def predict(self, _path):
        texts = ["36", "×", "07", "0", ".", "5", "尾"]
        scores = [0.99] * len(texts)
        boxes = [
            [[15, 15], [39, 15], [39, 35], [15, 35]],
            [[48, 15], [60, 15], [60, 35], [48, 35]],
            [[69, 15], [93, 15], [93, 35], [69, 35]],
            [[20, 65], [31, 65], [31, 85], [20, 85]],
            [[34, 65], [40, 65], [40, 85], [34, 85]],
            [[43, 65], [54, 65], [54, 85], [43, 85]],
            [[70, 65], [91, 65], [91, 85], [70, 85]],
        ]
        return [{"res": {"rec_texts": texts, "rec_scores": scores, "rec_polys": boxes}}]


class MustNotRunOcr:
    def predict(self, _path):
        raise AssertionError("fail-closed segmentation must not invoke OCR")


with tempfile.TemporaryDirectory() as temp_dir:
    image = np.full((400, 600, 3), 255, dtype=np.uint8)
    for x in (40, 300, 560):
        cv2.line(image, (x, 50), (x, 350), (0, 0, 220), 4)
    for y in (50, 200, 350):
        cv2.line(image, (40, y), (560, y), (0, 0, 220), 4)
    image_path = Path(temp_dir) / "grid.png"
    cv2.imwrite(str(image_path), image)
    result = run_cell_first(image_path, ocr_engine=FakeOcr())
    blank_path = Path(temp_dir) / "blank.png"
    cv2.imwrite(str(blank_path), np.full((200, 300, 3), 255, dtype=np.uint8))
    blank_result = run_cell_first(blank_path, ocr_engine=MustNotRunOcr())
    print(json.dumps({"grid": result, "blank": blank_result}))
'''
    completed = subprocess.run(
        [str(_ocr_python()), "-c", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(completed.stdout)
    result = payload["grid"]

    assert result["status"] == "COMPLETED"
    assert len(result["cell_detection"]["accepted_cell_ids"]) == 4
    assert result["local_inference_calls"] == 4
    assert len(result["tokens"]) == 28
    assert all(0 <= token["bbox"][0] < token["bbox"][2] <= 600 for token in result["tokens"])
    assert all(0 <= token["bbox"][1] < token["bbox"][3] <= 400 for token in result["tokens"])
    assert all(len(cell_rows["visible_rows"]) == 2 for cell_rows in result["rows"])
    assert all(cell_rows["decimal_spans"][0]["literal"] == "0.5" for cell_rows in result["rows"])
    assert all(result[key] is expected for key, expected in {
        "machine_evidence_only": True,
        "human_confirmed": False,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }.items())
    assert payload["blank"]["status"] == CELL_BOUNDARY_UNCERTAIN
    assert payload["blank"]["local_inference_calls"] == 0
    assert payload["blank"]["tokens"] == []
