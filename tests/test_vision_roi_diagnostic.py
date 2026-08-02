"""Test ROI diagnostic CLI — mock worker, report generation, safety gates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from betguard.vision.roi_annotations import ROI_SCHEMA_VERSION, RoiAnnotationSet, RoiGroup
from betguard.vision.roi_eval import evaluate_region, summarize
from scripts.vision_roi_diagnostic import (
    _check_paid_allowed,
    _html_escape,
    build_html_report,
    build_svg_overlay,
)


def _make_annotation(tmp_path) -> RoiAnnotationSet:
    img = tmp_path / "sample.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
    return RoiAnnotationSet(
        schema_version=ROI_SCHEMA_VERSION,
        image=str(img),
        groups=[
            RoiGroup(id="G01", bbox=[10, 20, 100, 50], ground_truth="01 20 x1"),
            RoiGroup(id="G02", bbox=[30, 80, 100, 50], ground_truth="18 26 x1"),
        ],
    )


class TestPaidGate:
    def test_paid_blocked_by_default(self):
        os.environ.pop("BETGUARD_ALLOW_PAID_VISION", None)
        with pytest.raises(SystemExit):
            _check_paid_allowed("openai-vision")

    def test_paid_allowed_with_env(self, monkeypatch):
        monkeypatch.setenv("BETGUARD_ALLOW_PAID_VISION", "1")
        _check_paid_allowed("openai-vision")  # must not raise

    def test_local_always_allowed(self):
        _check_paid_allowed("paddleocr-local")


class TestReport:
    def test_html_escapes_ocr_text(self, tmp_path):
        img = tmp_path / "s.png"
        img.write_bytes(b"x")
        ann = RoiAnnotationSet(schema_version=ROI_SCHEMA_VERSION, image=str(img), groups=[
            RoiGroup(id="G01", bbox=[1, 2, 3, 4], ground_truth="01 20"),
        ])
        ev = evaluate_region("01 20", "<script>alert(1)</script>", 1)
        ev.group_id = "G01"
        html = build_html_report(ann, [ev], {"G01": {"original": "<script>alert(1)</script>"}},
                                 summarize([ev]), {"name": "mock"})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_svg_overlay_numbered(self, tmp_path):
        img = tmp_path / "s.png"
        img.write_bytes(b"x")
        ann = RoiAnnotationSet(schema_version=ROI_SCHEMA_VERSION, image=str(img), groups=[
            RoiGroup(id="G01", bbox=[10, 20, 100, 50], ground_truth="01 20"),
            RoiGroup(id="G02", bbox=[200, 300, 80, 40], ground_truth="18 26"),
        ])
        svg = build_svg_overlay(str(img), ann)
        assert "<svg" in svg
        assert 'x="10" y="20"' in svg
        assert 'x="200" y="300"' in svg
        assert ">1</text>" in svg
        assert ">2</text>" in svg

    def test_report_shows_missed_regions(self, tmp_path):
        img = tmp_path / "s.png"
        img.write_bytes(b"x")
        ann = RoiAnnotationSet(schema_version=ROI_SCHEMA_VERSION, image=str(img), groups=[
            RoiGroup(id="G01", bbox=[1, 2, 3, 4], ground_truth="01 20"),
        ])
        ev = evaluate_region("01 20", "", 0)  # detection failure
        ev.group_id = "G01"
        html = build_html_report(ann, [ev], {"G01": {}}, summarize([ev]), {"name": "mock"})
        assert "region_detection_failure" in html
        assert "human_correction_required_count" not in html  # summary rendered as numbers


class TestEndToEndMockWorker:
    """Runs the CLI against a stdlib mock worker (no cv2/paddleocr)."""

    _MOCK_WORKER = textwrap.dedent("""
        import json, sys
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        regions = []
        for i, reg in enumerate(req["regions"]):
            gid = reg["id"]
            if gid == "G01":
                text, items = "01 20 x1", 3
            elif gid == "G02":
                text, items = "18 26 x1", 3
            else:
                text, items = "", 0
            regions.append({
                "id": gid, "bbox": reg["bbox"],
                "versions": {
                    v: {"file": f"crop_{gid}_{v}.png", "ocr_text": text,
                        "item_count": items,
                        "items": [{"text": t, "score": 0.9, "polygon": [], "box": []}
                                  for t in text.split()]}
                    for v in ("original", "contrast", "grid_suppressed")
                },
            })
        print(json.dumps({
            "ok": True, "protocol_version": req["protocol_version"],
            "request_id": req["request_id"], "engine": {"name": "mock"},
            "elapsed_ms": 1.0, "image_size": [200, 100], "regions": regions,
        }, ensure_ascii=False))
    """).strip()

    def _run_cli(self, tmp_path, annotations, extra_args=()):
        from scripts.vision_roi_diagnostic import main
        worker = tmp_path / "mock_worker.py"
        worker.write_text(self._MOCK_WORKER, encoding="utf-8")
        out = tmp_path / "out"
        args = [
            "--annotations", str(annotations),
            "--output-dir", str(out),
            "--ocr-python", sys.executable,
            "--worker", str(worker),
        ] + list(extra_args)
        # Patch sys.argv
        old = sys.argv
        sys.argv = ["vision_roi_diagnostic.py"] + args
        try:
            main()
        finally:
            sys.argv = old
        return out

    def test_mock_flow(self, tmp_path):
        ann = _make_annotation(tmp_path)
        import betguard.vision.roi_annotations as ra
        ann_path = tmp_path / "ann.json"
        ra.save_annotation_set(ann, str(ann_path))

        out = self._run_cli(tmp_path, ann_path)
        result = json.loads((out / "roi-diagnostic-result.json").read_text(encoding="utf-8"))
        assert result["summary"]["region_count"] == 2
        assert result["summary"]["number_exact_match_count"] == 2
        assert result["summary"]["unmatched_region_count"] == 0
        assert result["summary"]["mean_token_f1"] == 1.0
        assert (out / "roi-diagnostic-report.html").is_file()

    def test_mock_flow_with_failure(self, tmp_path):
        ann = _make_annotation(tmp_path)
        ann.groups.append(RoiGroup(id="G03", bbox=[5, 5, 20, 20], ground_truth="07 19"))
        import betguard.vision.roi_annotations as ra
        ann_path = tmp_path / "ann.json"
        ra.save_annotation_set(ann, str(ann_path))

        out = self._run_cli(tmp_path, ann_path)
        result = json.loads((out / "roi-diagnostic-result.json").read_text(encoding="utf-8"))
        assert result["summary"]["region_count"] == 3
        # G03 mock returns blank → detection failure, NOT excluded from denominator
        assert result["summary"]["unmatched_region_count"] == 1
        assert result["summary"]["failure_counts"]["region_detection_failure"] == 1

    def test_worker_not_called_without_ocr_python(self, tmp_path):
        """No BETGUARD_OCR_PYTHON → CLI exits with error, no API call."""
        ann = _make_annotation(tmp_path)
        import betguard.vision.roi_annotations as ra
        ann_path = tmp_path / "ann.json"
        ra.save_annotation_set(ann, str(ann_path))
        from scripts.vision_roi_diagnostic import main
        old = sys.argv
        sys.argv = ["x.py", "--annotations", str(ann_path), "--output-dir", str(tmp_path / "o2"),
                    "--ocr-python", ""]
        with pytest.raises(SystemExit):
            main()
        sys.argv = old


class TestNoPollution:
    def test_no_parser_import(self):
        import scripts.vision_roi_diagnostic as mod
        with open(mod.__file__, encoding="utf-8") as f:
            content = f.read()
        for banned in ["betguard.parser", "betguard.validator", "betguard.webfill"]:
            assert banned not in content

    def test_no_paid_import_in_cli(self):
        """CLI must not import the paid provider module at top level."""
        import scripts.vision_roi_diagnostic as mod
        with open(mod.__file__, encoding="utf-8") as f:
            content = f.read()
        assert "openai_paid" not in content
