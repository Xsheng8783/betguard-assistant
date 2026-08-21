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
    DECIMAL_LITERAL,
    NUMBER_01_39,
    SPECIAL_LITERAL,
)
from betguard.vision.token_first import (  # noqa: E402
    TOKEN_GROUP_CONFLICT,
    group_regions_with_separators,
    literal_tokens_from_regions,
)


def _region(region_id: str, text: str, bbox: list[float]) -> dict[str, object]:
    return {
        "region_id": region_id,
        "text": text,
        "confidence": 0.98,
        "bbox": bbox,
    }


def test_literal_tokens_keep_parent_evidence_and_protect_decimal_special() -> None:
    regions = [_region("R1", "36 × 07 0 . 5 半車", [10, 20, 230, 50])]

    tokens = literal_tokens_from_regions(regions)
    grouping = group_regions_with_separators(
        regions, tokens, [], image_size=(300, 200)
    )

    assert [token["text_raw"] for token in tokens] == [
        "36", "×", "07", "0", ".", "5", "半車"
    ]
    assert all(token["cell_id"] is None for token in tokens)
    assert all(token["source_region_id"] == "R1" for token in tokens)
    assert grouping["retention_rate"] == 1.0
    assert grouping["ungrouped_token_ids"] == []
    assigned = grouping["groups"][0]["assigned_tokens"]
    assert [
        token["text_raw"]
        for token in assigned
        if token["classification"] == NUMBER_01_39
    ] == ["36", "07"]
    assert [
        token["text_raw"]
        for token in assigned
        if token["classification"] == SPECIAL_LITERAL
    ] == ["半車"]
    assert [span["literal"] for span in grouping["rows"][0]["decimal_spans"]] == ["0.5"]
    assert all(
        token["classification"] == DECIMAL_LITERAL
        for token in assigned
        if token["text_raw"] in {"0", ".", "5"}
    )


def test_red_separator_blocks_same_row_group_merge_without_token_loss() -> None:
    regions = [
        _region("LEFT", "36 × 07", [20, 40, 120, 70]),
        _region("RIGHT", "38 × 17", [180, 40, 280, 70]),
    ]
    tokens = literal_tokens_from_regions(regions)
    separator = {
        "separator_id": "SEP-1",
        "orientation": "vertical",
        "start": [150, 0],
        "end": [150, 120],
        "confidence": 0.99,
    }

    grouped = group_regions_with_separators(
        regions, tokens, [separator], image_size=(300, 200)
    )
    unseparated = group_regions_with_separators(
        regions, tokens, [], image_size=(300, 200)
    )

    assert len(grouped["groups"]) == 2
    assert len(unseparated["groups"]) == 1
    assert grouped["retention_rate"] == 1.0
    assert grouped["cross_separator_merge_count"] == 0
    assert grouped["adjacency"]["blocked_edges"][0]["crossing_separator_ids"] == ["SEP-1"]


def test_ambiguous_adjacency_records_conflict_without_duplication() -> None:
    regions = [
        _region("R1", "03", [0, 10, 100, 30]),
        _region("R2", "16", [70, 80, 170, 100]),
    ]
    tokens = literal_tokens_from_regions(regions)

    result = group_regions_with_separators(
        regions, tokens, [], image_size=(300, 400)
    )

    assert len(result["groups"]) == 2
    assert result["token_group_conflicts"][0]["code"] == TOKEN_GROUP_CONFLICT
    assert result["retention_rate"] == 1.0
    assert len(result["retained_token_ids"]) == len(set(result["retained_token_ids"]))


def _ocr_python() -> Path:
    configured = os.environ.get("BETGUARD_PPOCR_SHADOW_PYTHON")
    return Path(configured) if configured else Path(r"C:\BetguardOCRBench\venv\Scripts\python.exe")


@pytest.mark.skipif(not _ocr_python().exists(), reason="isolated OpenCV/PaddleOCR environment unavailable")
def test_full_image_ocr_runs_before_separator_grouping() -> None:
    script = r'''
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd() / "tools" / "vision"))
from token_first_pipeline import run_token_first


class FakeOcr:
    def __init__(self):
        self.calls = 0

    def predict(self, _path):
        self.calls += 1
        return [{"res": {
            "rec_texts": ["36 x 07", "38 x 17 0 . 5 尾"],
            "rec_scores": [0.99, 0.98],
            "rec_polys": [
                [[40, 80], [250, 80], [250, 115], [40, 115]],
                [[360, 80], [660, 80], [660, 115], [360, 115]],
            ],
        }}]


with tempfile.TemporaryDirectory() as temp_dir:
    image = np.full((300, 720, 3), 255, dtype=np.uint8)
    cv2.line(image, (305, 15), (305, 270), (0, 0, 220), 5)
    image_path = Path(temp_dir) / "broken-grid.png"
    cv2.imwrite(str(image_path), image)
    engine = FakeOcr()
    result = run_token_first(image_path, ocr_engine=engine)
    print(json.dumps({"calls": engine.calls, "result": result}))
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
    result = payload["result"]

    assert payload["calls"] == 1
    assert result["global_ocr_token_count"] > 0
    assert result["token_retention_rate"] == 1.0
    assert result["group_hypothesis_count"] == 2
    assert result["grouping"]["cross_separator_merge_count"] == 0
    assert result["local_inference_calls"] == 1
    assert all(token["cell_id"] is None for token in result["global_tokens"])
    assert all(result[key] is expected for key, expected in {
        "machine_evidence_only": True,
        "human_confirmed": False,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }.items())
