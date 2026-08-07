"""Customer handoff audit tests for the sample-034 OCR / ROI / review flow.

Mapping to the 30 required checks in the audit spec (section 18).
Dataset-dependent tests skip when BETGUARD_DATASET is not configured.
"""
from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

DEBUG = Path(__file__).resolve().parents[1] / "sample-034-debug"
REVIEW = DEBUG / "review-tool"
sys.path.insert(0, str(DEBUG))
sys.path.insert(0, str(REVIEW))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("BETGUARD_DATASET", tempfile.gettempdir())

from migration_backfill_multipliers import (  # noqa: E402
    extract_normal_multiplier,
    migrate_sample,
)
import backfill_multiplier_candidates as bf  # noqa: E402
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

    def fake(img, bbox, *, save_path=None, box=None, variant="original_3x", meta=None, request_id=None):
        calls.append(list(box) if box else None)
        if meta is not None:
            meta["request_id"] = request_id or "rid"
        return responses.pop(0)

    monkeypatch.setattr("test_combined_bbox.call_column_combo_crop", fake)
    return pg._read_column_combo(Path("x.jpg"), _toks(), crop_box=[45, 640, 720, 748]), calls


def test_combo_consensus_2of3(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, GOOD, BAD1, GOOD, GOOD, BAD1])
    assert res["status"] == "consensus"
    assert res["columns"] == [["03"], ["11"], ["29"], ["34"], ["18", "28"], ["27", "37"]]
    assert res["roi_variant_divergent"] is False


def test_combo_all_different_divergent(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, BAD1, BAD2, GOOD, BAD1, BAD2])
    assert res["status"] == "divergent"
    assert res["uncertain"] is True


def test_combo_single_valid_insufficient(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, "not json", BAD2, GOOD, "not json", BAD2])
    assert res["status"] == "insufficient_evidence"
    assert res["uncertain"] is True


def test_combo_hallucinated_extra_cells_do_not_win(monkeypatch):
    hallucinated = json.dumps({
        "columns": [["03"], ["11"], ["29"], ["34"], ["18", "28", "99"], ["27", "37"]],
        "collision": "2/3", "multiplier": "0.1", "uncertain": False,
    })
    res, _ = _run_combo(monkeypatch, [GOOD, GOOD, hallucinated, GOOD, GOOD, hallucinated])
    assert res["status"] == "consensus"
    assert res["columns"][4] == ["18", "28"]  # NOT the 3-cell hallucination


# 17: dy shifts BOTH y1 and y2
def test_combo_dy_moves_both_y1_y2(monkeypatch):
    _, calls = _run_combo(monkeypatch, [GOOD, GOOD, GOOD, GOOD, GOOD, GOOD])
    assert calls[0][1] == calls[1][1] - 8 and calls[0][3] == calls[1][3] - 8
    assert calls[1][1] == calls[2][1] - 8 and calls[1][3] == calls[2][3] - 8


def test_combo_variant_divergent_needs_review(monkeypatch):
    res, _ = _run_combo(monkeypatch, [GOOD, GOOD, GOOD, BAD1, BAD1, BAD1])
    assert res["status"] == "consensus"
    assert res["roi_variant_divergent"] is True
    assert res["uncertain"] is True


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


# --- second-round static-review additions ---

def test_qwen_429_retries_400_does_not(monkeypatch):
    import io
    import urllib.error
    import test_combined_bbox as tcb

    # CI has no DASHSCOPE_API_KEY; the retry/backoff logic must be tested
    # without the real key guard (urlopen is fully mocked below).
    monkeypatch.setattr(tcb, "KEY", "sk-test-fake-key")

    calls = {"n": 0}

    def fake_urlopen(req, timeout=240):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"ok": 1}'}}]}).encode())

    monkeypatch.setattr(tcb.urllib.request, "urlopen", fake_urlopen)
    content, meta = tcb._qwen_chat("AAAA", "image/png", "p", max_tokens=10)
    assert content == '{"ok": 1}'
    assert calls["n"] == 3  # 2 retries + final success
    assert meta["retries"] == 2

    calls["n"] = 0

    def fake_400(req, timeout=240):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(tcb.urllib.request, "urlopen", fake_400)
    with pytest.raises(tcb.QwenClientError):
        tcb._qwen_chat("AAAA", "image/png", "p", max_tokens=10)
    assert calls["n"] == 1  # 4xx: no retry


def test_no_greedy_json_regex_in_pipeline():
    src = (DEBUG / "prelabel_geo.py").read_text(encoding="utf-8")
    brace = chr(123)
    assert ("re.search(r" + '"' + brace + ".*" + chr(125) + '"') not in src


def test_exif_rotation_applied_to_actual_bytes(tmp_path):
    from PIL import Image
    import test_combined_bbox as tcb

    img = Image.new("RGB", (400, 200), "white")
    img.save(tmp_path / "rot.jpg", exif=img.getexif())
    # inject orientation=6 (90° CW) via piexif-free approach: use Image.Exif
    ex = Image.Exif()
    ex[274] = 6
    img.save(tmp_path / "rot.jpg", exif=ex)
    norm, png_bytes = tcb.load_normalized(tmp_path / "rot.jpg")
    assert norm.size == (200, 400)  # rotated 90°
    from PIL import Image as I2
    loaded = I2.open(__import__("io").BytesIO(png_bytes))
    assert loaded.size == (200, 400)  # actual sent bytes are rotated too


def test_combo_no_consensus_geometry_fallback_needs_review(monkeypatch):
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    sec = {"rows": [{"tokens": [
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
        {"text": "2", "bbox": [605, 665, 625, 695]},
        {"text": "x", "bbox": [635, 665, 655, 705]},
        {"text": "0.1", "bbox": [665, 665, 715, 705]},
    ]}]}
    lines = pg.section_to_lines(sec, "R06", img_path=Path("x.jpg"), crop_box=[45, 640, 720, 748])
    assert lines[0]["uncertain"] is True
    assert lines[0]["uncertain_reason"] == "column_combo_insufficient_evidence"
    assert "column_combo_needs_review" in lines[0]["warnings"]


def test_complete_blocked_pipeline_review_conflict():
    import server as S
    line = _confirmed_line("R02-L1", [["02", "30", "33", "39"]])
    line["pipeline_review"] = {
        "decision": {"block_reasons": ["MISSING_MULTIPLIER"]},
        "checks": {"blocked": True},
        "parse_error": None,
    }
    codes = [i["code"] for i in S._complete_validation(
        {"lines": [line], "shared_multiplier_rules": [], "game": "539"})]
    assert "BLOCKED" in codes


def test_complete_parse_error_conflict():
    import server as S
    line = _confirmed_line("R02-L1", [["02", "30", "33", "39"]])
    line["pipeline_review"] = {
        "decision": {"block_reasons": []},
        "checks": {"blocked": False},
        "parse_error": "boom",
    }
    codes = [i["code"] for i in S._complete_validation(
        {"lines": [line], "shared_multiplier_rules": [], "game": "539"})]
    assert "PARSE_ERROR" in codes


def test_complete_stale_pipeline_review_conflict():
    import server as S
    line = _confirmed_line("R02-L1", [["02", "30", "33", "39"]])
    line["pipeline_review"] = {"decision": {"block_reasons": []}, "checks": {"blocked": False}, "parse_error": None}
    line["pipeline_review_revision"] = 1
    codes = [i["code"] for i in S._complete_validation(
        {"lines": [line], "shared_multiplier_rules": [], "game": "539"}, expected_revision=2)]
    assert "PIPELINE_REVIEW_STALE" in codes


def test_game_range_539_vs_hk():
    hk = json.dumps({"columns": [["40"], ["41"]], "collision": "2", "multiplier": "1", "uncertain": False})
    assert pg._parse_column_combo(hk, game="539") is None      # 40-49 rejected for 539
    assert pg._parse_column_combo(hk, game="hk") is not None   # accepted for 六合彩
    line539 = pg._column_combo_line("R01", {
        "columns": [["40"], ["41"]], "collision": "2", "multiplier": "1",
        "uncertain": False, "status": "consensus", "attempts": [],
    }, game="539")
    assert "column_combo_invalid_structure" in line539[0]["uncertain_reason"]


def test_structured_divergence_exact_columns():
    import server as S
    cs = {"semantics_numbers": [["03", "18"], ["11", "28"]]}
    sem = {"columns": [["03", "28"], ["11", "18"]], "multipliers": ["1"], "stars": [2]}
    assert S._structured_divergence(cs, sem) is True  # same numbers, different columns


# --- third-round fixes: ROI hash, crop evidence, no_attempts fail-closed ---

def test_roi_hash_is_crop_bytes_not_full_page(tmp_path, monkeypatch):
    """The image_sha256 sent with a ROI request must hash the EXACT crop bytes
    (buf.getvalue()), never the full-page normalized PNG bytes."""
    from PIL import Image
    import base64
    import test_combined_bbox as tcb

    p = tmp_path / "sample.jpg"
    Image.new("RGB", (800, 800), "white").save(p)
    _, png_bytes = tcb.load_normalized(p)
    full_hash = hashlib.sha256(png_bytes).hexdigest()
    captured = {}

    def fake_qwen(b64, mime, prompt, **kw):
        captured["b64"] = b64
        captured["sha"] = kw.get("image_sha256")
        captured["request_id"] = kw.get("request_id")
        return "{}", {"request_id": kw.get("request_id") or "rid"}

    monkeypatch.setattr(tcb, "_qwen_chat", fake_qwen)

    tcb.call_column_combo_crop(
        p, [0, 0, 100, 60], box=[10, 10, 120, 80],
        variant="original_3x", request_id="rid-combo",
    )
    crop_bytes = base64.b64decode(captured["b64"])
    assert captured["sha"] == hashlib.sha256(crop_bytes).hexdigest()
    assert captured["sha"] != full_hash
    assert crop_bytes != png_bytes
    assert captured["request_id"] == "rid-combo"

    tcb.call_play_mark_crop(
        p, [0, 0, 100, 60], box=[10, 10, 120, 80],
        variant="original_3x", request_id="rid-play",
    )
    crop_bytes = base64.b64decode(captured["b64"])
    assert captured["sha"] == hashlib.sha256(crop_bytes).hexdigest()
    assert captured["sha"] != full_hash
    assert crop_bytes != png_bytes
    assert captured["request_id"] == "rid-play"


def test_column_combo_unique_crop_evidence(tmp_path, monkeypatch):
    """All 6 ROI attempts (2 variants x 3 dy) must keep their own crop file and
    per-attempt evidence: crop_path / crop_box / image_sha256 / variant / dy /
    raw_response / request_id."""
    from PIL import Image
    import test_combined_bbox as tcb

    p = tmp_path / "sample.jpg"
    Image.new("RGB", (800, 800), "white").save(p)
    good = json.dumps({
        "columns": [["03"], ["11"]], "collision": "2", "multiplier": "1",
        "uncertain": False, "uncertain_reason": None,
    })

    def fake_qwen(b64, mime, prompt, **kw):
        rid = kw.get("request_id") or "rid"
        return good, {"request_id": rid, "image_sha256": kw.get("image_sha256")}

    monkeypatch.setattr(tcb, "_qwen_chat", fake_qwen)
    crops = tmp_path / "crops"
    toks = [
        {"text": "03", "bbox": [85, 665, 135, 705]},
        {"text": "x", "bbox": [145, 665, 165, 705]},
        {"text": "11", "bbox": [175, 665, 225, 705]},
        {"text": "x", "bbox": [235, 665, 255, 705]},
    ]
    res = pg._read_column_combo(
        p, toks,
        save_path=crops / "sample-034-R06-COL.png",
        crop_box=[45, 640, 720, 748],
    )
    files = sorted(crops.glob("*.png"))
    assert len(files) == 6
    assert len({f.name for f in files}) == 6  # unique names, nothing overwritten
    assert res["status"] == "consensus"
    assert len(res["attempts"]) == 6
    for a in res["attempts"]:
        assert a["crop_path"] and Path(a["crop_path"]).exists()
        assert a["variant"] in ("original_3x", "gray_enhanced_3x")
        assert a["dy"] in (-8, 0, 8)
        assert a["request_id"]
        assert a["raw_response"] == good
        assert a["image_sha256"] == hashlib.sha256(Path(a["crop_path"]).read_bytes()).hexdigest()
        assert a["crop_box"][1] == 640 + a["dy"]


def test_play_mark_unique_crop_evidence(tmp_path, monkeypatch):
    """Both play-mark ROI variants keep their own crop file + evidence."""
    from PIL import Image
    import test_combined_bbox as tcb

    p = tmp_path / "sample.jpg"
    Image.new("RGB", (800, 800), "white").save(p)
    good = json.dumps({
        "raw_text": "34x1", "main_numbers": [],
        "play_mark": {
            "upper_digits": ["3"], "lower_digits": ["4"], "other_visible_digits": [],
            "multiplier": "1", "layout": "vertical_stack", "raw_play_text": "34x1",
            "uncertain": False, "uncertain_candidates": [], "uncertain_reason": None,
        },
        "overall_uncertain": False, "overall_uncertain_reason": None,
    })

    def fake_qwen(b64, mime, prompt, **kw):
        rid = kw.get("request_id") or "rid"
        return good, {"request_id": rid, "image_sha256": kw.get("image_sha256")}

    monkeypatch.setattr(tcb, "_qwen_chat", fake_qwen)
    crops = tmp_path / "crops"
    toks = [{"text": "3x1", "bbox": [500, 660, 560, 700]}]
    roi = pg._read_play_mark(
        p, toks,
        save_path=crops / "sample-034-R06-L1.png",
        crop_box=[490, 650, 580, 710],
    )
    assert roi is not None
    files = sorted(crops.glob("*.png"))
    assert len(files) == 2
    assert len({f.name for f in files}) == 2
    assert len(roi["variant_results"]) == 2
    for v in roi["variant_results"]:
        assert v["crop_path"] and Path(v["crop_path"]).exists()
        assert v["variant"] in ("original_3x", "gray_enhanced_3x")
        assert v["request_id"]
        assert v["dy"] == 0
        assert v["raw_response"] == good
        assert v["image_sha256"] == hashlib.sha256(Path(v["crop_path"]).read_bytes()).hexdigest()


def test_combo_no_attempts_fail_closed(monkeypatch):
    """Both variants / all six returns invalid -> no_attempts must fail closed:
    the fallback line stays uncertain, never executable/exportable, and
    complete validation rejects it."""
    res, _ = _run_combo(monkeypatch, ["not json"] * 6)
    assert res["status"] == "no_attempts"
    assert res["uncertain"] is True
    assert len(res["attempts"]) == 6
    for a in res["attempts"]:
        assert a["parsed"] is None
        assert a["raw_response"] == "not json"
        assert a["variant"] in ("original_3x", "gray_enhanced_3x")
        assert a["dy"] in (-8, 0, 8)
        assert a["request_id"]

    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: res)
    sec = {"rows": [{"tokens": _toks() + [
        {"text": "2", "bbox": [605, 665, 625, 695]},
        {"text": "x", "bbox": [635, 665, 655, 705]},
        {"text": "0.1", "bbox": [665, 665, 715, 705]},
    ]}]}
    lines = pg.section_to_lines(sec, "R06", img_path=Path("x.jpg"), crop_box=[45, 640, 720, 748])
    assert lines and lines[0]["uncertain"] is True
    assert lines[0]["uncertain_reason"] == "column_combo_insufficient_evidence"
    assert "column_combo_needs_review" in lines[0]["warnings"]
    assert lines[0]["review_action"] == "pending"  # never auto-confirmed

    import server as S
    issues = [i["code"] for i in S._complete_validation(
        {"lines": lines, "shared_multiplier_rules": [], "game": "539"})]
    assert "LINE_NOT_CONFIRMED" in issues  # complete endpoint -> 409


# --- sample-010 right-side play marks / multipliers ---


@pytest.fixture()
def sample010_fixture():
    p = DEBUG / "fixtures" / "sample-010-combined.json"
    if not p.exists():
        pytest.skip("fixture sample-010-combined.json missing")
    return json.loads(p.read_text(encoding="utf-8"))


def _mark(text, cy):
    return {"text": text, "bbox": [500, cy - 20, 560, cy + 20]}


def test_sample010_extract_all_multipliers_not_last():
    """A. One row with two category multipliers keeps BOTH, never matches[-1]."""
    text = "11 , 15 , 24 . 37 2 x 5 3 x 2"
    assert pg.extract_multiplier_rules(text) == ["2X5", "3X2"]
    assert pg.extract_multiplier(text) == "2X5 3X2"


def test_sample010_column_separator_not_multiplier():
    """"24 x 22" / "24 x 37" are column separators, not multiplier rules."""
    assert pg.extract_multiplier_rules("15 . 24 x 22 2 x 1 35 28") == ["2X1"]
    assert pg.extract_multiplier_rules("24 x 37 2 x 4") == ["2X4"]


def test_sample010_stacked_34x1_still_kept():
    """A two-digit CATEGORY shorthand (34=三四) with a one-digit value stays."""
    assert pg.extract_multiplier_rules("02 30 33 34X1") == ["34X1"]


def test_sample010_normal_row_not_routed_to_column_combo(sample010_fixture, monkeypatch):
    """R01 is a normal row: its x's are play marks, so it must never call the
    column_combo ROI (which would waste a paid call and add false uncertainty)."""
    def boom(*a, **k):
        raise AssertionError("normal row must not call column_combo ROI")

    monkeypatch.setattr(pg, "_read_column_combo", boom)
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)
    lines = pg.section_to_lines(
        sample010_fixture["sections"][0], "R01", img_path=Path("x.jpg")
    )
    assert lines[0]["layout_hint"] == "normal_row"


def test_sample010_r01_keeps_both_rules(sample010_fixture, monkeypatch):
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)
    lines = pg.section_to_lines(
        sample010_fixture["sections"][0], "R01", img_path=Path("x.jpg")
    )
    l = lines[0]
    assert l["multiplier_text"] == "2X5 3X2"
    assert [r["rule_text"] for r in l["multiplier_rules"]] == ["2X5", "3X2"]
    assert l["model_raw_text"] == l["raw_text"]


def test_sample010_r02_columns_and_fail_closed(sample010_fixture, monkeypatch):
    """B. R02 columns are correct; the saved response has no '3' token, so the
    pipeline must NOT fabricate 2/3X1 — it stays needs_review with evidence."""
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    lines = pg.section_to_lines(
        sample010_fixture["sections"][1], "R02",
        img_path=Path("x.jpg"), crop_box=[0, 0, 2000, 2000],
    )
    l = lines[0]
    assert l["number_groups"] == [["15"], ["24"], ["22", "35", "28"]]
    assert l["multiplier_text"] == "2X1"
    assert l["uncertain"] is True
    assert l["uncertain_reason"] == "column_combo_insufficient_evidence"
    assert "column_combo_needs_review" in l["warnings"]


def test_sample010_r03_multiplier(sample010_fixture, monkeypatch):
    """C. R03 keeps 2X1 from the right-band tokens."""
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    lines = pg.section_to_lines(
        sample010_fixture["sections"][2], "R03",
        img_path=Path("x.jpg"), crop_box=[0, 0, 2000, 2000],
    )
    l = lines[0]
    assert l["number_groups"] == [["35"], ["24", "34"], ["18", "28"]]
    assert l["multiplier_text"] == "2X1"


def test_column_combo_crop_includes_right_play_zone(monkeypatch):
    """E. The column-combo ROI crop must extend to the RIGHT play mark (碰法/
    倍率), not stop at the last number column; the section band is preserved."""
    toks = [
        {"text": "15", "bbox": [50, 230, 90, 265]},
        {"text": "x", "bbox": [190, 235, 210, 260]},
        {"text": "22", "bbox": [220, 230, 260, 265]},
        {"text": "2", "bbox": [390, 255, 410, 280]},
        {"text": "x", "bbox": [420, 255, 440, 280]},
        {"text": "1", "bbox": [450, 255, 470, 280]},
        {"text": "35", "bbox": [220, 275, 260, 310]},
        {"text": "28", "bbox": [220, 300, 260, 335]},
    ]
    captured = {}
    captured["boxes"] = []

    def fake(img, bbox, *, save_path=None, box=None, variant="original_3x", meta=None, request_id=None):
        captured["bbox"] = list(bbox)
        captured["box"] = list(box) if box else None
        captured["boxes"].append(list(box) if box else None)
        if meta is not None:
            meta["request_id"] = request_id or "rid"
        return json.dumps({
            "columns": [["15"], ["22", "35", "28"]],
            "collision": "2", "multiplier": "1", "uncertain": False,
        })

    monkeypatch.setattr("test_combined_bbox.call_column_combo_crop", fake)
    pg._read_column_combo(Path("x.jpg"), toks, crop_box=[10, 200, 560, 350])
    assert captured["bbox"][2] >= 470          # reaches the play mark
    assert [10, 200, 560, 350] in captured["boxes"]  # dy=0 keeps full band
    assert all(b[2] >= 470 for b in captured["boxes"])


def test_column_combo_multiplier_text_2_3():
    """ROI path: collision 2/3 + multiplier 1 composes 2/3X1 (never 23X1)."""
    l = pg._column_combo_line("R02", {
        "columns": [["15"], ["24"], ["22", "35", "28"]],
        "collision": "2/3", "multiplier": "1",
        "uncertain": False, "status": "consensus", "attempts": [],
    }, game="539")[0]
    assert l["multiplier_text"] == "2/3X1"
    assert l["raw_text"] == "15 / 24 / 22 35 28 2/3X1"
    assert l["multiplier_rules"][0]["categories"] == ["2", "3"]


def test_column_combo_multiplier_text_2():
    """ROI path: collision 2 + multiplier 1 composes 2X1."""
    l = pg._column_combo_line("R03", {
        "columns": [["35"], ["24", "34"], ["18", "28"]],
        "collision": "2", "multiplier": "1",
        "uncertain": False, "status": "consensus", "attempts": [],
    }, game="539")[0]
    assert l["multiplier_text"] == "2X1"


def test_first_pass_merge_independent_lower_digit():
    """D. '3x1' + an independent '4' below -> upper 3 / lower 4 -> 3/4X1."""
    pm = pg._first_pass_play_mark([_mark("3x1", 200), _mark("4", 260)])
    assert pm["upper_digits"] == ["3"]
    assert pm["lower_digits"] == ["4"]
    assert pg._compose_mult_from_play_mark(pm, ["3X1"]) == "3/4X1"


def test_normal_lines_merge_independent_lower_digit():
    row = {
        "numbers": [["02"], ["17"], ["20"], ["33"]],
        "tokens": [
            {"text": "02", "bbox": [60, 575, 100, 610]},
            {"text": "17", "bbox": [135, 575, 175, 610]},
            {"text": "20", "bbox": [210, 575, 250, 610]},
            {"text": "33", "bbox": [285, 575, 325, 610]},
            {"text": "3x1", "bbox": [420, 585, 500, 610]},
            {"text": "4", "bbox": [420, 630, 500, 660]},
        ],
        "multiplier": None,
        "layout_hint": "normal_row",
    }
    l = pg._normal_lines([row], "R05")[0]
    assert l["multiplier_text"] == "3/4X1"
    assert l["multiplier_rules"][0]["categories"] == ["3", "4"]


def test_two_digit_numbers_not_play_digits():
    """E. Main numbers 24 / 34 must never be read as a stacked lower digit."""
    pm = pg._first_pass_play_mark([_mark("3x1", 200), _mark("24", 260)])
    assert pm["upper_digits"] == ["3"]
    assert pm["lower_digits"] == []
    assert pg._compose_mult_from_play_mark(pm, ["3X1"]) is None
    pm2 = pg._first_pass_play_mark([_mark("3x1", 200), _mark("34", 260)])
    assert pm2["lower_digits"] == []


def test_column_combo_missing_collision_or_multiplier_needs_review():
    """F. ROI without collision/multiplier stays needs_review (fail-closed)."""
    l = pg._column_combo_line("R02", {
        "columns": [["15"], ["24"], ["22", "35", "28"]],
        "collision": None, "multiplier": None,
        "uncertain": False, "status": "consensus", "attempts": [],
    }, game="539")[0]
    assert l["uncertain"] is True
    assert l["uncertain_reason"] == "column_combo_invalid_structure"
    assert "column_combo_needs_review" in l["warnings"]


def test_geometry_fallback_no_multiplier_fail_closed():
    """Columns read but no multiplier/collision -> never a certain result."""
    sec = {"rows": [{"tokens": [
        {"text": "15", "bbox": [50, 230, 90, 265]},
        {"text": "x", "bbox": [190, 235, 210, 260]},
        {"text": "22", "bbox": [220, 230, 260, 265]},
        {"text": "35", "bbox": [220, 275, 260, 310]},
        {"text": "28", "bbox": [220, 300, 260, 335]},
    ]}]}
    l = pg.section_to_lines(sec, "R06", img_path=None)[0]
    assert l["uncertain"] is True
    assert l["uncertain_reason"] == "missing_multiplier_or_collision"
    assert "column_combo_needs_review" in l["warnings"]


def test_v3_fallback_never_touches_model_raw_text(monkeypatch, tmp_path):
    """G. v3 prelabel evidence is recorded; model_raw_text and multiplier_text
    are never overwritten (no auto-fill of the stacked 4)."""
    pre = tmp_path / "prelabels"
    pre.mkdir()
    (pre / "sample-034.json").write_text(json.dumps({
        "raw_model_output": json.dumps({"sections": [{"rows": [
            {"numbers": [["02"], ["17"], ["20"], ["33"]], "multiplier": "3/4X1"}
        ]}]}),
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pg, "PRELABELS", pre)
    line = {
        "line_id": "R05-L1",
        "model_raw_text": "02 . 17 . 20 . 33 3 x 1",
        "raw_text": "02 . 17 . 20 . 33 3 x 1",
        "multiplier_text": "3X1",
        "number_groups": [["02", "17", "20", "33"]],
        "warnings": [],
        "uncertain": False,
    }
    pg._apply_v3_fallback("sample-034", {"lines": [line]})
    assert line["model_raw_text"] == "02 . 17 . 20 . 33 3 x 1"
    assert line["multiplier_text"] == "3X1"
    assert line["uncertain"] is True
    assert "cross_pass_multiplier_divergent" in line["warnings"]
    assert "possible_stacked_category_digit" in line["warnings"]
    assert line["fallback_candidate"]["multiplier_candidates"] == ["3/4X1"]


def test_sample010_needs_review_cannot_complete():
    """H. needs_review/pending rows never complete -> never exportable."""
    import server as S
    line = _confirmed_line("R02-L1", [["15"], ["24"], ["22", "35", "28"]], None)
    line["layout_hint"] = "column_bet"
    line["review_action"] = "pending"
    line["uncertain"] = True
    codes = [i["code"] for i in S._complete_validation(
        {"lines": [line], "shared_multiplier_rules": [], "game": "539"})]
    assert "LINE_NOT_CONFIRMED" in codes


def test_sample010_end_to_end_draft_v3_evidence(sample010_fixture, monkeypatch, tmp_path):
    """Regenerate the sample-010 draft from the saved combined response with
    the v3 prelabel evidence: R01 keeps 2X5+3X2, R02/R03 keep 2X1 (no
    fabricated 2/3), R05 keeps 3X1 with a 3/4X1 candidate, and model_raw_text
    is never touched."""
    pre = tmp_path / "prelabels"
    pre.mkdir()
    fixture_pre = DEBUG / "fixtures" / "sample-010-prelabel.json"
    if not fixture_pre.exists():
        pytest.skip("fixture sample-010-prelabel.json missing")
    (pre / "sample-010.json").write_text(
        fixture_pre.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(pg, "PRELABELS", pre)
    monkeypatch.setattr(pg, "DRAFT", tmp_path / "ground-truth-draft")
    pg.DRAFT.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)

    pg.process_parsed("sample-010", sample010_fixture, write=True, game="539")
    d = json.loads((pg.DRAFT / "sample-010.json").read_text(encoding="utf-8"))
    by_id = {l["line_id"]: l for l in d["lines"]}

    r01 = by_id["R01-L1"]
    assert r01["multiplier_text"] == "2X5 3X2"
    assert [r["rule_text"] for r in r01["multiplier_rules"]] == ["2X5", "3X2"]
    assert r01["model_raw_text"] == "11 , 15 , 24 . 37 2 x 5 3 x 2"

    r02 = by_id["R02-L1"]
    assert r02["number_groups"] == [["15"], ["24"], ["22", "35", "28"]]
    assert r02["multiplier_text"] == "2X1"
    assert r02["model_raw_text"] == "15 . 24 x 22 2 x 1 35 28"
    assert r02["uncertain"] is True
    assert r02["fallback_candidate"]["multiplier_candidates"] == ["3X1"]
    assert "possible_stacked_category_digit" in r02["warnings"]

    assert by_id["R03-L1"]["multiplier_text"] == "2X1"
    assert by_id["R04-L1"]["multiplier_text"] == "2X1"

    r05 = by_id["R05-L1"]
    assert r05["multiplier_text"] == "3X1"
    assert "3/4X1" in r05["fallback_candidate"]["multiplier_candidates"]
    assert "possible_stacked_category_digit" in r05["warnings"]
    assert r05["model_raw_text"] == "02 . 17 . 20 . 33 3 x 1"


# --- merge-only candidate backfill (never regenerates / never degrades) ---


@pytest.fixture()
def sample008_draft_fixture():
    p = DEBUG / "fixtures" / "sample-008-draft.json"
    if not p.exists():
        pytest.skip("fixture sample-008-draft.json missing")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture()
def sample008_prelabel_fixture():
    p = DEBUG / "fixtures" / "sample-008-prelabel.json"
    if not p.exists():
        pytest.skip("fixture sample-008-prelabel.json missing")
    return json.loads(p.read_text(encoding="utf-8"))


def test_backfill_sample008_r03_merge_only(monkeypatch, tmp_path, sample008_draft_fixture, sample008_prelabel_fixture):
    """Regression: sample-008 R03 must keep 08/04/2X5/column_bet; backfill only
    adds fallback_candidate/evidence; model_raw_text stays byte-identical."""
    draft_dir = tmp_path / "ground-truth-draft"
    pre_dir = tmp_path / "prelabels"
    draft_dir.mkdir()
    pre_dir.mkdir()
    (draft_dir / "sample-008.json").write_text(
        json.dumps(sample008_draft_fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    (pre_dir / "sample-008.json").write_text(
        json.dumps(sample008_prelabel_fixture, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bf, "DRAFT", draft_dir)
    monkeypatch.setattr(bf, "PRELABELS", pre_dir)

    before = sample008_draft_fixture
    bf.backfill_sample("sample-008", dry_run=False)
    after = json.loads((draft_dir / "sample-008.json").read_text(encoding="utf-8"))

    before_r03 = next(l for l in before["lines"] if l["line_id"] == "R03-L1")
    after_r03 = next(l for l in after["lines"] if l["line_id"] == "R03-L1")

    assert after_r03["number_groups"] == [["08"], ["01", "04"]]
    assert "68" not in [n for g in after_r03["number_groups"] for n in g]
    assert "04" in after_r03["number_groups"][1]
    assert after_r03["multiplier_text"] == "2X5"
    assert after_r03["layout_hint"] == "column_bet"
    assert after_r03.get("model_raw_text") == before_r03.get("model_raw_text")
    assert after_r03["raw_text"] == before_r03.get("raw_text")
    assert bf.diff_protected(before, after) == []
    fb = after_r03.get("fallback_candidate") or {}
    assert fb.get("evidence"), "evidence must be added"
    assert fb.get("evidence")[0]["rule"] == "merge_only_never_replace"


def test_backfill_divergent_adds_candidates_without_touching_protected():
    line = {
        "line_id": "R02-L1",
        "raw_text": "15 / 24 / 22 35 28 2X1",
        "model_raw_text": "15 . 24 x 22 2 x 1 35 28",
        "number_groups": [["15"], ["24"], ["22", "35", "28"]],
        "multiplier_text": "2X1",
        "multiplier_rules": [{"rule_text": "2X1", "categories": ["2"], "value": "1"}],
        "layout_hint": "column_bet",
        "region_id": "R02",
        "review_action": "pending",
        "uncertain": False,
        "warnings": [],
    }
    before = json.dumps(line, ensure_ascii=False, sort_keys=True)
    v3 = [(["15", "24", "22", "35", "28"], ["2X1", "3X1"])]
    stats = bf.backfill_line(line, v3)
    assert stats["candidates"] == ["3X1"]
    cand = line["fallback_candidate"]["multiplier_candidates"]
    assert cand[0]["rule_text"] == "3X1"
    assert cand[0]["candidate_mode"] == "unknown_requires_review"
    assert cand[0]["candidate_group_id"] is None
    assert cand[0]["confidence"] == "legacy/inferred"
    assert "cross_pass_multiplier_divergent" in line["warnings"]
    assert "possible_stacked_category_digit" in line["warnings"]
    assert line["uncertain"] is False  # protected: never auto-set
    assert line["review_action"] == "pending"
    assert line["multiplier_text"] == "2X1"
    assert line["number_groups"] == [["15"], ["24"], ["22", "35", "28"]]
    assert line["model_raw_text"] == "15 . 24 x 22 2 x 1 35 28"
    # only fallback_candidate / warnings changed
    del line["fallback_candidate"]
    line["warnings"] = []
    assert json.dumps(line, ensure_ascii=False, sort_keys=True) == before


def test_backfill_idempotent():
    line = {
        "line_id": "R05-L1",
        "number_groups": [["02"], ["17"], ["20"], ["33"]],
        "multiplier_text": "3X1",
        "layout_hint": "normal_row",
        "uncertain": True,
        "warnings": [],
    }
    v3 = [(["02", "17", "20", "33"], ["3/4X1"])]
    first = bf.backfill_line(line, v3)
    second = bf.backfill_line(line, v3)
    assert first["candidates"] == ["3/4X1"]
    cand = line["fallback_candidate"]["multiplier_candidates"]
    assert cand[0]["rule_text"] == "3/4X1"
    assert cand[0]["candidate_mode"] == "unknown_requires_review"
    assert len(cand) == 1  # idempotent: no duplicate candidate entry
    assert len(line["fallback_candidate"]["evidence"]) == 1
    assert line["warnings"].count("cross_pass_multiplier_divergent") == 1
    assert line["warnings"].count("possible_stacked_category_digit") == 1
    assert second["candidates"] == ["3/4X1"]  # idempotent: no duplicates


def test_diff_protected_reports_change():
    before = {"revision": 1, "lines": [
        {"line_id": "R03-L1", "number_groups": [["08"], ["01", "04"]], "multiplier_text": "2X5", "layout_hint": "column_bet"},
    ]}
    after_ok = {"revision": 1, "lines": [
        {"line_id": "R03-L1", "number_groups": [["08"], ["01", "04"]], "multiplier_text": "2X5", "layout_hint": "column_bet",
         "fallback_candidate": {"evidence": [{"rule": "merge_only_never_replace"}]}},
    ]}
    after_bad = {"revision": 1, "lines": [
        {"line_id": "R03-L1", "number_groups": [["68", "01"]], "multiplier_text": None, "layout_hint": "normal_row"},
    ]}
    assert bf.diff_protected(before, after_ok) == []
    bad = bf.diff_protected(before, after_bad)
    assert bad
    paths = {(v.get("line_id"), v.get("path")) for v in bad}
    assert ("R03-L1", "number_groups") in paths
    assert ("R03-L1", "multiplier_text") in paths
    assert ("R03-L1", "layout_hint") in paths


# --- sample-010 completion: digit-category multipliers must not block ---


def test_closed_set_digit_multiplier_forms():
    from betguard.vision.closed_set import parse_multiplier_text, validate_multiplier
    assert parse_multiplier_text("2X1") == [{"category": "二", "value_text": "1"}]
    assert parse_multiplier_text("2/3X1") == [
        {"category": "二", "value_text": "1"},
        {"category": "三", "value_text": "1"},
    ]
    assert parse_multiplier_text("3/4X1") == [
        {"category": "三", "value_text": "1"},
        {"category": "四", "value_text": "1"},
    ]
    assert parse_multiplier_text("2X5 3X2") == [
        {"category": "二", "value_text": "5"},
        {"category": "三", "value_text": "2"},
    ]
    assert parse_multiplier_text("2.2X1") == []  # duplicate category -> ambiguous
    for t in ("2X1", "2/3X1", "3/4X1", "2X5 3X2"):
        assert validate_multiplier(t) == []
    assert validate_multiplier("2.2X1") != []


def test_semantic_parser_compound_digit_rules():
    from betguard.semantic_parser import parse_ocr_text
    b = parse_ocr_text("11 15 24 37 二X5 三X2", game="539")
    assert b.type == "normal"
    assert b.numbers == [11, 15, 24, 37]
    assert b.stars == [2, 3]
    assert b.unit == 5


def test_revalidate_canonical_no_false_divergence():
    import server as S
    line = {
        "line_id": "R01-L1",
        "raw_text": "11 , 15 , 24 . 37 2 x 5 3 x 2",
        "human_raw_text": None,
        "number_groups": [["11", "15", "24", "37"]],
        "multiplier_text": "2X5 3X2",
        "multiplier_rules": [
            {"rule_text": "2X5", "categories": ["2"], "value": "5"},
            {"rule_text": "3X2", "categories": ["3"], "value": "2"},
        ],
        "layout_hint": "normal_row",
        "uncertain": False,
        "warnings": [],
    }
    S._revalidate_line(line, revision=1, game="539")
    assert "STRUCTURED_TEXT_DIVERGENT" not in line["warnings"]
    assert line["pipeline_review"]["semantic"]["numbers"] == [11, 15, 24, 37]

    bad = dict(line)
    bad.update({
        "line_id": "R99-L1",
        "number_groups": [["68"]],  # 68 is out of 539 range
        "multiplier_text": "X1",
        "raw_text": "68 X1",
    })
    S._revalidate_line(bad, revision=1, game="539")
    assert bad["pipeline_review"].get("parse_error")
    codes = [i["code"] for i in S._complete_validation(
        {"lines": [bad], "shared_multiplier_rules": [], "game": "539"})]
    assert "PARSE_ERROR" in codes


def test_sample010_like_complete_flow_passes():
    import server as S

    def line(lid, groups, raw, mult, layout):
        return {
            "line_id": lid,
            "review_action": "confirmed",
            "raw_text": raw,
            "human_raw_text": None,
            "number_groups": groups,
            "multiplier_text": mult,
            "multiplier_rules": [],
            "layout_hint": layout,
            "uncertain": False,
            "warnings": [],
        }

    lines = [
        line("R01-L1", [["11", "15", "24", "37"]], "11 , 15 , 24 . 37 2 x 5 3 x 2", "2X5 3X2", "normal_row"),
        line("R02-L1", [["15"], ["24"], ["22", "35", "28"]], "15 / 24 / 22 35 28 2/3X1", "2/3X1", "column_bet"),
        line("R05-L1", [["02", "17", "20", "33"]], "02 17 20 33 3/4X1", "3/4X1", "normal_row"),
        line("R09-L1", [["24", "37"]], "24 x 37 2 x 4", "2X4", "normal_row"),
    ]
    draft = {"lines": lines, "shared_multiplier_rules": [], "game": "539"}
    for l in lines:
        S._revalidate_line(l, revision=1, game="539")
    issues = S._complete_validation(draft, expected_revision=1)
    assert issues == []


# --- multiplier completeness policy / canonical merge / sample-011 repair ---


def test_multiplier_policy_classification():
    from betguard.vision.multiplier_policy import (
        COMPLETE,
        INVALID,
        PARTIAL,
        classify_multiplier_token,
    )
    assert classify_multiplier_token("2X1") == COMPLETE
    assert classify_multiplier_token("3X0.2") == COMPLETE
    assert classify_multiplier_token("2/3/4X0.1") == COMPLETE
    assert classify_multiplier_token("34X1") == COMPLETE
    assert classify_multiplier_token("4/3X1") == COMPLETE  # canonicalized on merge
    for t in ("2", "3", "2/3", "4/3", "X1", "X0.1", "2X"):
        assert classify_multiplier_token(t) == PARTIAL, t
    assert classify_multiplier_token("23 x 35") == INVALID  # column separator
    assert classify_multiplier_token("") == INVALID


def test_multiplier_policy_merge_canonical():
    from betguard.vision.multiplier_policy import merge_complete_rules
    # same value -> union categories, canonical 2/3/4 order
    assert merge_complete_rules(["2/3X0.1", "3/4X0.1"]) == ["2/3/4X0.1"]
    assert merge_complete_rules(["2X1", "3X1"]) == ["2/3X1"]
    # different values NEVER merge
    assert merge_complete_rules(["2X1", "3X0.2", "4X0.5"]) == ["2X1", "3X0.2", "4X0.5"]
    assert merge_complete_rules(["3X0.2", "4X0.5"]) == ["3X0.2", "4X0.5"]
    # reverse order canonicalized
    assert merge_complete_rules(["4/3X1"]) == ["3/4X1"]
    # two complete rules with different values stay two
    assert merge_complete_rules(["2X2", "3X5"]) == ["2X2", "3X5"]


def test_column_geometry_collision_canonical_order():
    from betguard.vision.column_geometry import parse_collision
    from betguard.vision.column_geometry import Token
    raw, zh = parse_collision([Token("4/3", [0, 0, 10, 10])]) or (None, None)
    assert raw == "3/4" and zh == "三四碰"


def test_column_separator_x_never_multiplier_r09():
    assert pg.extract_multiplier_rules("34 x 23 x 35 x 27 / 37 2 x 1") == ["2X1"]


def test_repair_line_evidence_driven():
    import repair_sample011_multipliers as R

    def line(lid, raw, groups, mult, layout):
        return {
            "line_id": lid,
            "raw_text": raw,
            "number_groups": groups,
            "multiplier_text": mult,
            "multiplier_rules": [],
            "layout_hint": layout,
            "review_action": "pending",
            "uncertain": False,
            "warnings": [],
            "human_raw_text": None,
            "model_raw_text": None,
        }

    # R03: monotonic upgrade keeps BOTH rules in order
    l = line("R03-L1", "32 . 34 . 35 2 x 2 3 x 5", [["32", "34", "35"]], "3 x 5", "normal_row")
    R.recover_multiplier(l, ["2X2", "3X5"], [])
    assert l["multiplier_text"] == "2X2 3X5"
    assert [r["rule_text"] for r in l["multiplier_rules"]] == ["2X2", "3X5"]
    res = R.apply_canonical_raw(l)
    assert res["changed"] is True
    assert l["raw_text"] == "32 34 35 2X2 3X5"
    assert l["correction_source"] == R.REPAIR_SOURCE

    # R04: partial fragment -> complete evidence (same-value union)
    l = line("R04-L1", "24 34 / 03 23 / 17 27 37 / 20 30 35 2/3", [["24", "34"], ["03", "23"], ["17", "27", "37"], ["20", "30", "35"]], "2/3", "column_bet")
    R.recover_multiplier(l, ["2/3/4X0.1"], [])
    assert l["multiplier_text"] == "2/3/4X0.1"

    # R07: three independent rules with DIFFERENT values stay three
    l = line("R07-L1", "21 35 / 23 / 34 / 37 4/3", [["21", "35"], ["23"], ["34"], ["37"]], "4/3", "column_bet")
    R.recover_multiplier(l, ["2X1", "3X0.2", "4X0.5"], [])
    assert l["multiplier_text"] == "2X1 3X0.2 4X0.5"
    assert len(l["multiplier_rules"]) == 3

    # R09: column structure restored ONLY with column evidence + v3 nested
    v3 = [(['34', '23', '35', '27', '37'], [['34'], ['23'], ['35'], ['27', '37']], ['2X1'], ['2×1'])]
    l = line("R09-L1", "34 x 23 x 35 x 27 / 37 2 x 1", [["34", "23", "35", "27", "37"]], "23 x 35", "normal_row")
    R.recover_multiplier(l, ["2X1"], v3, column_evidence=True)
    assert l["multiplier_text"] == "2X1"
    assert l["number_groups"] == [["34"], ["23"], ["35"], ["27", "37"]]
    assert l["layout_hint"] == "column_bet"

    # no evidence -> must NOT guess; stays needs_human and never gets a value
    l = line("R99-L1", "?? ?? 2", [["01", "02"]], "2", "column_bet")
    plan = R.recover_multiplier(l, [], [])
    assert plan["needs_human"] is True
    assert l["multiplier_text"] is None
    assert "incomplete_multiplier_evidence" in l["warnings"]


def test_repair_line_id_keyed_assertions():
    import repair_sample011_multipliers as R
    with pytest.raises(AssertionError):
        R._assert_unique_ids([{"line_id": "R03-L1"}, {"line_id": "R03-L1"}])

    r02 = {
        "line_id": "R02-L1",
        "number_groups": [["30", "35", "36", "38"]],
        "multiplier_text": "3/4 x 1",
        "layout_hint": "normal_row",
        "raw_text": "30 . 35 . 36 . 38 3 / 4 x 1",
        "human_raw_text": None,
        "model_raw_text": "30.35.36.38 ¾×1",
        "review_action": "confirmed",
        "uncertain": False,
        "warnings": [],
        "fallback_candidate": {},
    }
    before = json.dumps(r02, ensure_ascii=False, sort_keys=True)
    # Even if R09's evidence is passed by mistake, R02 stays untouched
    # (monotonic policy: existing complete 3/4X1 is not superseded by 2X1).
    plan = R.recover_multiplier(r02, ["2X1"], [], column_evidence=True)
    assert plan["changes"] == []
    assert json.dumps(r02, ensure_ascii=False, sort_keys=True) == before

    bad = dict(r02)
    bad["multiplier_text"] = "2"  # partial
    with pytest.raises(AssertionError):
        R._assert_preconditions(bad)


def test_multiplier_policy_spaced_complete():
    from betguard.vision.multiplier_policy import (
        is_complete_multiplier,
        partial_tokens,
        split_complete_rules,
    )
    assert is_complete_multiplier("3/4 x 1") is True
    assert is_complete_multiplier("2 x 1") is True
    assert is_complete_multiplier("2 x 2 3 x 5") is True
    assert split_complete_rules("2 x 2 3 x 5") == ["2X2", "3X5"]
    assert partial_tokens("3/4 x 1") == []
    assert partial_tokens("2 x 1") == []
    assert partial_tokens("2 x 2 3 x 5") == []
    assert partial_tokens("2/3") == ["2/3"]
    assert partial_tokens("23 x 35") == ["23X35"]


def test_partial_multiplier_never_executable():
    from betguard.vision.pipeline import process_row
    rec = process_row({
        "raw_text": "24 34 / 19 39 2/3",
        "numbers": [["24", "34"], ["19", "39"]],
        "multiplier": "2/3",
        "layout_hint": "column_bet",
    }, region_bound=True, game="539")
    assert rec["decision"]["executable"] is False
    assert rec["block_reason"] == "MISSING_MULTIPLIER"


def test_merge_same_value_flag():
    from betguard.vision.multiplier_policy import merge_complete_rules
    # default: same-value categories merge
    assert merge_complete_rules(["2X1", "3X1"]) == ["2/3X1"]
    # distinct physical slots: keep separate
    assert merge_complete_rules(["2X1", "3X1"], merge_same_value=False) == ["2X1", "3X1"]
    # different values NEVER merge in either mode
    assert merge_complete_rules(["2X2", "3X5"], merge_same_value=False) == ["2X2", "3X5"]


def test_column_geometry_triple_collision():
    from betguard.vision.column_geometry import Token, build_columns_from_bbox, parse_collision
    # separate stacked tokens 2 + 3 + 4
    raw, zh = parse_collision([
        Token("2", [0, 0, 10, 10]),
        Token("3", [0, 15, 10, 25]),
        Token("4", [0, 30, 10, 40]),
    ]) or (None, None)
    assert raw == "2/3/4" and zh == "二三四碰"
    # single 234 token
    raw, _ = parse_collision([Token("234", [0, 0, 10, 10])]) or (None, None)
    assert raw == "2/3/4"
    # 2/3/4 token
    raw, _ = parse_collision([Token("2/3/4", [0, 0, 10, 10])]) or (None, None)
    assert raw == "2/3/4"
    # only 2/3 evidence must NOT guess 4
    raw, _ = parse_collision([Token("2", [0, 0, 10, 10]), Token("3", [0, 15, 10, 25])]) or (None, None)
    assert raw == "2/3"
    # full geometry with 2/3/4 collision + x0.1
    res = build_columns_from_bbox([
        {"text": "24", "bbox": [0, 0, 40, 30]},
        {"text": "19", "bbox": [60, 0, 100, 30]},
        {"text": "2", "bbox": [150, 0, 170, 25]},
        {"text": "3", "bbox": [150, 25, 170, 50]},
        {"text": "4", "bbox": [150, 50, 170, 75]},
        {"text": "x", "bbox": [180, 10, 200, 40]},
        {"text": "0.1", "bbox": [210, 10, 260, 40]},
    ])
    assert res["collision_raw"] == "2/3/4"


def test_short_column_keeps_geometry_and_review(monkeypatch):
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)
    sec = {"rows": [{"tokens": [
        {"text": "34", "bbox": [110, 605, 165, 640]},
        {"text": "x", "bbox": [170, 610, 195, 640]},
        {"text": "15", "bbox": [200, 600, 255, 640]},
        {"text": "2", "bbox": [275, 610, 300, 640]},
        {"text": "x", "bbox": [305, 615, 330, 640]},
        {"text": "4", "bbox": [335, 605, 390, 640]},
    ]}]}
    lines = pg.section_to_lines(sec, "R06", img_path=Path("x.jpg"))
    l = lines[0]
    assert l["layout_hint"] == "column_bet", "two-number short column must not flatten to normal_row"
    assert l["number_groups"] == [["34"], ["15"]]
    assert l["multiplier_text"] == "2X4"
    assert l["uncertain"] is True
    assert l["uncertain_reason"] == "possible_column_bet"
    assert "possible_column_bet" in l["warnings"]
    assert "25" not in [n for g in l["number_groups"] for n in g], "never fabricate the missing stacked value"


def test_repair_sample012_rules():
    import repair_sample012_rules as R12

    def line(lid, groups, mult, layout, raw):
        return {
            "line_id": lid,
            "number_groups": groups,
            "multiplier_text": mult,
            "multiplier_rules": [],
            "layout_hint": layout,
            "raw_text": raw,
            "human_raw_text": None,
            "model_raw_text": "MODEL",
            "review_action": "confirmed",
            "uncertain": True,
            "warnings": [],
            "fallback_candidate": {},
        }

    r03 = line("R03-L1", [["02", "30", "33"]], "2X23X5", "normal_row", "02 30 33 2X2")
    r04 = line("R04-L1", [["02", "05", "17"]], "2X2 3X5", "normal_row", "02 05 17 2X2 3X5")
    r06 = line("R06-L1", [["34", "15"]], "2 x 4", "normal_row", "34 x 15 2 x 4")
    r08 = line("R08-L1", [["24", "34"], ["19", "39"], ["16", "36"], ["27", "37"]], "2/3X0.1", "column_bet", "24 34 / 19 39 / 16 36 / 27 37 2/3X0.1")
    draft = {"lines": [r03, r04, r06, r08]}
    R12.run_repair(draft, apply=True)
    assert r03["multiplier_text"] == "2X2 3X5"
    assert r03["raw_text"] == "02 30 33 2X2 3X5"
    assert r03["correction_source"] == R12.CORRECTION_SOURCE
    assert r03["fallback_candidate"]["evidence"][-1]["source"] == "human_verified_image_ground_truth"
    assert r03["model_raw_text"] == "MODEL"
    assert r03["review_action"] == "confirmed", "repair must not auto-confirm or change action"
    assert r04["multiplier_text"] == "2X2 3X5"
    assert r06["layout_hint"] == "column_bet"
    assert r06["number_groups"] == [["34"], ["15", "25"]]
    assert r06["multiplier_text"] == "2X4"
    assert r08["multiplier_text"] == "2/3/4X0.1"
    # idempotent
    rep = R12.run_repair(draft, apply=False)
    assert rep["changed_lines"] == 0
    # precondition fail-closed
    bad = line("R03-L1", [["99", "99", "99"]], "2X2", "normal_row", "x")
    with pytest.raises(AssertionError):
        R12.run_repair({"lines": [bad, r04, r06, r08]}, apply=True)


def test_car_canonical_keeps_play_text_and_non_exportable():
    import server as S
    line = {
        "line_id": "R01-L1",
        "play_type": "car_bet",
        "play_text": "15 34 各半車",
        "number_groups": [["15", "34"]],
        "multiplier_text": None,
        "layout_hint": "normal_row",
        "raw_text": "15 34 各半車",
    }
    assert S._canonical_text(line) == "15 34 各半車"
    from betguard.vision.pipeline import process_row
    rec = process_row({
        "raw_text": "15 34 各半車",
        "numbers": [["15", "34"]],
        "multiplier": None,
        "layout_hint": "normal_row",
    }, region_bound=True, game="539")
    assert rec["decision"]["executable"] is False
    assert rec["supported"] is False


def test_r03_play_zone_overlap_guard(monkeypatch):
    monkeypatch.setattr(pg, "_read_column_combo", lambda *a, **k: None)
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)
    sec = {"rows": [{"tokens": [
        {"text": "12", "bbox": [60, 510, 125, 555]},
        {"text": "x", "bbox": [135, 520, 150, 550]},
        {"text": "15", "bbox": [160, 510, 225, 555]},
        {"text": "x", "bbox": [235, 520, 250, 550]},
        {"text": "34", "bbox": [260, 510, 325, 555]},
        {"text": "x", "bbox": [335, 520, 350, 550]},
        {"text": "08", "bbox": [360, 505, 425, 555]},
        {"text": "2", "bbox": [460, 510, 495, 550]},
        {"text": "x", "bbox": [505, 520, 520, 550]},
        {"text": "3", "bbox": [530, 510, 590, 555]},
        {"text": "20", "bbox": [460, 555, 525, 595]},
        {"text": "3", "bbox": [560, 555, 595, 600]},
        {"text": "x", "bbox": [605, 565, 620, 595]},
        {"text": "1", "bbox": [630, 555, 690, 605]},
    ]}]}
    lines = pg.section_to_lines(sec, "R03", img_path=Path("x.jpg"))
    l = lines[0]
    assert l["layout_hint"] == "column_bet"
    assert l["multiplier_text"] == "2X3 3X1"
    assert l["uncertain"] is True
    assert "number_token_overlaps_play_zone" in l["warnings"]


def test_r04_complete_rules_not_bare_value(monkeypatch):
    assert pg.extract_multiplier_rules("12 . 15 . 36 . 37 2 x 3 3 x 1") == ["2X3", "3X1"]
    monkeypatch.setattr(pg, "_read_play_mark", lambda *a, **k: None)
    sec = {"rows": [{"tokens": [
        {"text": "12", "bbox": [60, 500, 110, 540]},
        {"text": ".", "bbox": [115, 505, 130, 535]},
        {"text": "15", "bbox": [135, 500, 185, 540]},
        {"text": ".", "bbox": [190, 505, 205, 535]},
        {"text": "36", "bbox": [210, 500, 260, 540]},
        {"text": ".", "bbox": [265, 505, 280, 535]},
        {"text": "37", "bbox": [285, 500, 335, 540]},
        {"text": "2", "bbox": [420, 495, 450, 540]},
        {"text": "x", "bbox": [455, 505, 475, 540]},
        {"text": "3", "bbox": [480, 495, 510, 540]},
        {"text": "3", "bbox": [420, 540, 450, 580]},
        {"text": "x", "bbox": [455, 545, 475, 580]},
        {"text": "1", "bbox": [480, 540, 510, 580]},
    ], "numbers": [["12"], ["15"], ["36"], ["37"]], "multiplier": "1"}]}
    lines = pg.section_to_lines(sec, "R04", img_path=Path("x.jpg"))
    l = lines[0]
    assert l["multiplier_text"] == "2X3 3X1"
    assert "incomplete_multiplier_evidence" not in l["warnings"], "bare model multiplier must not re-flag complete rules"
    assert l["uncertain"] is True and l["uncertain_reason"] == "play_mark_unclear"  # ROI mock artifact, not multiplier issue


def test_repair_sample013_rules():
    import repair_sample013_rules as R13

    def line(lid, groups, mult, layout, raw, model_raw):
        return {
            "line_id": lid,
            "number_groups": groups,
            "multiplier_text": mult,
            "multiplier_rules": [],
            "layout_hint": layout,
            "raw_text": raw,
            "human_raw_text": None,
            "model_raw_text": model_raw,
            "play_type": None,
            "play_text": None,
            "review_action": "pending",
            "uncertain": True,
            "warnings": [],
            "fallback_candidate": {},
        }

    r01 = line("R01-L1", [["15", "34"]], None, "normal_row", "15 各半車", "15 34 各半車")
    r03 = line("R03-L1", [["12"], ["15"], ["34"], ["08"], ["20"]], "2/3", "column_bet", "12 / 15 / 34 / 08 / 20 2/3", None)
    r04 = line("R04-L1", [["12", "15", "36", "37"]], "1", "normal_row", "12 . 15 . 36 . 37 2 x 3 3 x 1", None)
    draft = {"lines": [r01, r03, r04]}
    R13.run_repair(draft, apply=True)
    assert r01["play_type"] == "car_bet"
    assert r01["play_text"] == "15 34 各半車"
    assert r01["raw_text"] == "15 34 各半車"
    assert r03["number_groups"] == [["12"], ["15", "34"], ["08", "20"]]
    assert r03["multiplier_text"] == "2X3 3X1"
    assert len(r03["multiplier_rules"]) == 2
    assert r04["multiplier_text"] == "2X3 3X1"
    assert r04["raw_text"] == "12 15 36 37 2X3 3X1"
    assert r04["review_action"] == "pending"
    assert r01["model_raw_text"] == "15 34 各半車"
    assert r01["fallback_candidate"]["evidence"][-1]["source"] == "human_verified_image_ground_truth"
    assert R13.run_repair(draft, apply=False)["changed_lines"] == 0
    bad = line("R01-L1", [["99"]], None, "normal_row", "x", "x")
    with pytest.raises(AssertionError):
        R13.run_repair({"lines": [bad, r03, r04]}, apply=True)
