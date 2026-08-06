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
