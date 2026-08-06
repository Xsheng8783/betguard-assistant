"""Customer handoff audit tests for the sample-034 OCR / ROI / review flow.

Mapping to the 30 required checks in the audit spec (section 18).
Dataset-dependent tests skip when BETGUARD_DATASET is not configured.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

DEBUG = Path(__file__).resolve().parents[1] / "sample-034-debug"
REVIEW = DEBUG / "review-tool"
sys.path.insert(0, str(DEBUG))
sys.path.insert(0, str(REVIEW))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("BETGUARD_DATASET", r"C:\BetguardOCRDataset")

from migration_backfill_multipliers import (  # noqa: E402
    extract_normal_multiplier,
    migrate_sample,
)
import prelabel_geo as pg  # noqa: E402
from test_combined_bbox import COLUMN_COMBO_PROMPT, call_column_combo_crop  # noqa: E402

DATASET = Path(os.environ["BETGUARD_DATASET"])
RAW = DATASET / "raw"


def _bands(sections):
    sec_ys = []
    for sec in sections:
        ys = []
        for r in sec.get("rows") or []:
            for t in r.get("tokens") or []:
                if len(t.get("bbox", [])) == 4:
                    ys.extend((t["bbox"][1], t["bbox"][3]))
        sec_ys.append((min(ys), max(ys)) if ys else (None, None))
    n = len(sec_ys)
    bands = []
    for i, (lo, hi) in enumerate(sec_ys):
        if lo is None:
            bands.append(None)
            continue
        y1 = int((sec_ys[i - 1][1] + lo) / 2) if i > 0 and sec_ys[i - 1][1] is not None else int(lo - 40)
        y2 = int((hi + sec_ys[i + 1][0]) / 2) + 8 if i < n - 1 and sec_ys[i + 1][0] is not None else int(hi + 40)
        bands.append([y1, y2])
    return bands


@pytest.fixture()
def sample034_fixture():
    p = DEBUG / "fixtures" / "sample-034-combined.json"
    if not p.exists():
        pytest.skip("fixture sample-034-combined.json missing")
    return json.loads(p.read_text(encoding="utf-8"))


# 1-6: normal-row multiplier extraction
def test_mult_2_x_1():
    assert extract_normal_multiplier("02 30 33 2 x 1") == "2X1"


def test_mult_3_slash_4_x_1():
    assert extract_normal_multiplier("02 30 33 3 / 4 x 1") == "3/4X1"


def test_mult_2_slash_3_times_01():
    assert extract_normal_multiplier("02 30 33 2 / 3 \u00d7 0.1") == "2/3X0.1"


def test_mult_34X1():
    assert extract_normal_multiplier("02 30 33 34X1") == "34X1"


def test_mult_sansi_X1():
    assert extract_normal_multiplier("02 30 33 \u4e09\u56dbX1") == "\u4e09\u56dbX1"


def test_mult_no_x_returns_none():
    assert extract_normal_multiplier("02 30 33 39") is None


# 7-9: play digits only single 2/3/4 (never from multi-char tokens)
@pytest.mark.parametrize("bad", ["39", "24", "31", "3?", "4?", " ", "34"])
def test_play_digit_rejects_multichar(bad):
    assert pg._safe_play_digit(bad) is None


@pytest.mark.parametrize("good", ["2", "3", "4"])
def test_play_digit_accepts_single(good):
    assert pg._safe_play_digit(good) == good


# 10: positional order 上4下3 vs 上3下4 are different
def test_positional_upper_lower_order():
    def mark(text, cy):
        return {"text": text, "bbox": [500, int(cy - 20), 560, int(cy + 20)]}

    a = pg._first_pass_play_mark([mark("4x1", 200), mark("3x1", 300)])
    b = pg._first_pass_play_mark([mark("3x1", 200), mark("4x1", 300)])
    assert a["upper_digits"] == ["4"] and a["lower_digits"] == ["3"]
    assert b["upper_digits"] == ["3"] and b["lower_digits"] == ["4"]
    assert a != b


# 11/12: R02/R03 crop boxes exclude adjacent rows' number centers
def test_r02_crop_excludes_neighbors(sample034_fixture):
    secs = sample034_fixture["sections"]
    bands = _bands(secs)

    def centers(si):
        out = []
        for r in secs[si].get("rows") or []:
            for t in r.get("tokens") or []:
                if len(t.get("bbox", [])) == 4 and t["text"].isdigit() and len(t["text"]) == 2:
                    out.append((t["bbox"][1] + t["bbox"][3]) / 2)
        return out

    band = bands[1]
    y1, y2 = band
    assert all(c < y1 or c > y2 for c in centers(0) + centers(2))


def test_r03_crop_excludes_neighbors(sample034_fixture):
    secs = sample034_fixture["sections"]
    bands = _bands(secs)

    def centers(si):
        out = []
        for r in secs[si].get("rows") or []:
            for t in r.get("tokens") or []:
                if len(t.get("bbox", [])) == 4 and t["text"].isdigit() and len(t["text"]) == 2:
                    out.append((t["bbox"][1] + t["bbox"][3]) / 2)
        return out

    band = bands[2]
    y1, y2 = band
    assert all(c < y1 or c > y2 for c in centers(1) + centers(3))


# 13-16: column-combo consensus (monkeypatched model)
GOOD = json.dumps({
    "columns": [["03"], ["11"], ["29"], ["34"], ["18", "28"], ["27", "37"]],
    "collision": "2/3", "multiplier": "0.1", "uncertain": False, "uncertain_reason": None,
})
BAD1 = json.dumps({"columns": [["03"], ["11"]], "collision": None, "multiplier": None, "uncertain": True})
BAD2 = json.dumps({"columns": [["99"], ["11"]], "collision": None, "multiplier": None, "uncertain": True})


def _toks():
    return [
        {"text": "03", "bbox": [85, 665, 135, 705]},
        {"text": "x", "bbox": [145, 665, 165, 705]},
        {"text": "11", "bbox": [175, 665, 225, 705]},
        {"text": "x", "bbox": [235, 665, 255, 705]},
        {"text": "29", "bbox": [265, 665, 315, 705]},
        {"text": "x", "bbox": [325, 665, 345, 705]},
        {"text": "34", "bbox": [355, 665, 405, 705]},
        {"text": "x", "bbox": [415, 665, 435, 705]},
        {"text": "18", "bbox": [445, 665, 495, 705]},
        {"text": "x", "bbox": [505, 665, 525, 705]},
        {"text": "27", "bbox": [535, 665, 585, 705]},
    ]


def _run_combo(monkeypatch, responses):
    calls = []

    def fake(img, bbox, *, save_path=None, box=None):
        calls.append(list(box) if box else None)
        return responses.pop(0)

    monkeypatch.setattr("test_combined_bbox.call_column_combo_crop", fake)
    return pg._read_column_combo(Path("x.jpg"), _toks(), crop_box=[45, 640, 720, 748]), calls


def test_combo_consensus_2of3(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, GOOD, BAD1])
    assert res["status"] == "consensus"
    assert res["columns"] == [["03"], ["11"], ["29"], ["34"], ["18", "28"], ["27", "37"]]


def test_combo_all_different_divergent(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, BAD1, BAD2])
    assert res["status"] == "divergent"
    assert res["uncertain"] is True


def test_combo_single_valid_insufficient(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, "not json", BAD2])
    assert res["status"] == "insufficient_evidence"
    assert res["uncertain"] is True


def test_combo_hallucinated_extra_cells_do_not_win(monkeypatch):
    hallucinated = json.dumps({
        "columns": [["03"], ["11"], ["29"], ["34"], ["18", "28", "99"], ["27", "37"]],
        "collision": "2/3", "multiplier": "0.1", "uncertain": False,
    })
    res, _ = _run_combo(monkeypatch, [GOOD, GOOD, hallucinated])
    assert res["status"] == "consensus"
    assert res["columns"][4] == ["18", "28"]  # NOT the 3-cell hallucination


# 17: dy shifts BOTH y1 and y2
def test_combo_dy_moves_both_y1_y2(monkeypatch):
    _, calls = _run_combo(monkeypatch, [GOOD, GOOD, GOOD])
    assert calls[0][1] == calls[1][1] - 8 and calls[0][3] == calls[1][3] - 8
    assert calls[1][1] == calls[2][1] - 8 and calls[1][3] == calls[2][3] - 8


# 18: prompt must not contain sample-034 GT answers
def test_column_combo_prompt_no_gt_leak():
    assert '["03"]' not in COLUMN_COMBO_PROMPT
    assert '["18", "28"]' not in COLUMN_COMBO_PROMPT
    assert '["27", "37"]' not in COLUMN_COMBO_PROMPT
    assert "sample-034" not in COLUMN_COMBO_PROMPT


# 19/29: model_raw_text immutable; adoption stays needs_review
def test_apply_roi_keeps_model_raw_text_and_needs_review():
    import server as S
    line = {
        "line_id": "R02-L1",
        "raw_text": "02 . 30 . 33 . 39 3x1",
        "model_raw_text": "02 . 30 . 33 . 39 3x1",
        "number_groups": [["02", "30", "33", "39"]],
        "layout_hint": "normal_row",
        "multiplier_text": "3x1",
        "uncertain": True,
        "uncertain_reason": "play_mark_unclear",
        "play_mark": {
            "roi_upper_digits": ["3"], "roi_lower_digits": ["4"],
            "roi_categories": ["3", "4"], "roi_multiplier": "1",
        },
    }
    res = S._apply_roi_to_line(line, line["play_mark"])
    assert res["ok"] is True
    assert line["multiplier_text"] == "\u4e09\u56dbX1"
    assert line["model_raw_text"] == "02 . 30 . 33 . 39 3x1"  # immutable
    assert line["uncertain"] is True  # needs_review stays
    assert line["review_action"] == "corrected"


# 20: forged client play_mark is ignored by the handler (server source check)
def test_apply_roi_ignores_client_play_mark():
    src = (REVIEW / "server.py").read_text(encoding="utf-8")
    assert 'body.get("play_mark")' not in src
    assert "line.get(\"play_mark\")" in src


# 21: manual edit re-runs the pipeline
def test_revalidate_line_runs_pipeline():
    import server as S
    line = {
        "line_id": "R01-L1",
        "raw_text": "02 . 30 . 33 2x1",
        "human_raw_text": "02 30 33 2X1",
        "multiplier_text": "2X1",
        "layout_hint": "normal_row",
        "number_groups": [["02", "30", "33"]],
    }
    S._revalidate_line(line)
    pr = line.get("pipeline_review") or {}
    assert pr.get("decision", {}).get("parse_status") == "success"


# 22-24: complete fail-closed
def _confirmed_line(lid, groups, reason=None):
    return {
        "line_id": lid, "review_action": "confirmed",
        "number_groups": groups, "layout_hint": "normal_row",
        "pipeline_review": {}, "uncertain_reason": reason,
    }


def test_complete_pending_conflict():
    import server as S
    draft = {"lines": [_confirmed_line("R01-L1", [["02", "30", "33"]])], "shared_multiplier_rules": []}
    draft["lines"][0]["review_action"] = "pending"
    codes = [i["code"] for i in S._complete_validation(draft)]
    assert "LINE_NOT_CONFIRMED" in codes


def test_complete_empty_column_conflict():
    import server as S
    line = _confirmed_line("R06-L1", [["03"], [], ["29"]], None)
    line["layout_hint"] = "column_bet"
    codes = [i["code"] for i in S._complete_validation({"lines": [line], "shared_multiplier_rules": []})]
    assert "EMPTY_COLUMN" in codes


def test_complete_unresolved_region_conflict():
    import server as S
    line = _confirmed_line("R02-L1", [["02", "30"]], "unresolved_region")
    codes = [i["code"] for i in S._complete_validation({"lines": [line], "shared_multiplier_rules": []})]
    assert "UNRESOLVED_REGION" in codes


# 25: revision mismatch -> 409 (RevisionConflict raised before any write)
def test_revision_mismatch_raises():
    import server as S
    with pytest.raises(S.RevisionConflict):
        S.save_draft("sample-009", {"sample_id": "sample-009", "lines": []}, expected_revision=999999)


# 26: v3 fallback must not silently replace number_groups
def test_v3_fallback_no_silent_replace(monkeypatch, tmp_path):
    pre = tmp_path / "prelabels"
    pre.mkdir()
    (pre / "sample-034.json").write_text(json.dumps({
        "raw_model_output": json.dumps({
            "sections": [{"rows": [
                {"numbers": [["02"], ["30"], ["33"]]},
            ]}],
        }),
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pg, "PRELABELS", pre)
    line = {
        "line_id": "R01-L1",
        "number_groups": [["02", "30"]],  # partial vs prelabel
        "raw_text": "02 30",
        "warnings": [],
    }
    draft = {"lines": [line]}
    pg._apply_v3_fallback("sample-034", draft)
    assert line["number_groups"] == [["02", "30"]]
    assert "cross_pass_divergent" in line["warnings"]
    assert line.get("fallback_candidate", {}).get("rule") == "partial_overlap_only_never_replace"


# 27/28: migration idempotency + conflict non-overwrite
def test_migration_idempotent_and_conflict(monkeypatch, tmp_path):
    import migration_backfill_multipliers as mig
    monkeypatch.setattr(mig, "DRAFT", tmp_path)
    draft = {
        "lines": [
            {"line_id": "R01-L1", "layout_hint": "normal_row", "raw_text": "02 30 33 2 x 1", "multiplier_text": None},
            {"line_id": "R02-L1", "layout_hint": "normal_row", "raw_text": "02 30 33 3 x 1", "multiplier_text": "99"},
        ]
    }
    (tmp_path / "sample-099.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    first = mig.migrate_sample("sample-099")
    assert first["modified"] == 1
    assert first["conflicts"] == 1
    second = mig.migrate_sample("sample-099")
    assert second["modified"] == 0
    out = json.loads((tmp_path / "sample-099.json").read_text(encoding="utf-8"))
    assert out["lines"][0]["multiplier_text"] == "2X1"
    assert out["lines"][1]["multiplier_text"] == "99"  # conflict not overwritten
    assert "multiplier_conflict" in out["lines"][1]["warnings"]


# 30: blocked never becomes executable / exportable on replay
def test_replay_blocked_never_executable():
    from betguard.vision.replay import replay_file, safety_violations
    fixtures = Path(__file__).parent / "fixtures" / "ab_formal"
    files = sorted(fixtures.glob("sample-*-run*.json"))[:6]
    assert files
    violations = 0
    for f in files:
        rec = replay_file(f)
        violations += len(safety_violations(rec, rec))
    assert violations == 0


# image quality gate (dataset present only)
def test_image_quality_sample034():
    img = RAW / "sample-034.jpg"
    if not img.exists():
        pytest.skip("sample-034 image missing")
    assert pg.check_image_quality(img) == []
