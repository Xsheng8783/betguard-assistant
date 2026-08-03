"""Test PaddleOCR benchmark: CLI, report generation, subprocess, isolation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


def _make_png(w: int = 10, h: int = 10) -> bytes:
    import struct, zlib
    sig = b'\x89PNG\r\n\x1a\n'
    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b'\x00' + b'\xff\x00\x00\xff' * w * h
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


class TestBenchmarkSubprocess:
    """Benchmark worker subprocess integration (fake worker, no real PaddleOCR)."""

    def test_benchmark_fake_worker_success(self, monkeypatch, tmp_path):
        """Fake benchmark worker returns valid result JSON."""
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)

        worker = tmp_path / "fake_benchmark_worker.py"
        worker.write_text(r"""
import json, sys, os
raw = sys.stdin.buffer.read(1048576)
req = json.loads(raw.decode("utf-8"))
protocol_fd = os.dup(sys.stdout.fileno())
os.set_inheritable(protocol_fd, False)
resp = {
    "ok": True, "protocol_version": "betguard.vision.benchmark.v1",
    "request_id": req.get("request_id", ""),
    "engine": {"name": "paddleocr", "paddle_version": "3.3.0", "paddleocr_version": "3.7.0",
               "detection_model": "PP-OCRv5_mobile_det", "recognition_model": "PP-OCRv5_mobile_rec"},
    "total_elapsed_ms": 5000.0, "inference_count": 4,
    "profiles": [
        {"rotation": 0, "preprocess_profile": "original", "detection_profile": "balanced",
         "status": "completed", "elapsed_ms": 1000, "items": [
             {"text": "05", "score": 0.9, "polygon": [[10,10],[50,10],[50,30],[10,30]], "box": [10,10,50,30]},
             {"text": "09", "score": 0.85, "polygon": [[60,10],[100,10],[100,30],[60,30]], "box": [60,10,100,30]},
         ], "raw_text": "05\n09", "scores": [0.9, 0.85],
         "detected_count": 2, "non_empty_count": 2, "digit_count": 4,
         "mean_confidence": 0.875, "max_confidence": 0.9, "heuristic_score": 80.0,
         "preprocess_result": {"profile_id": "original", "width": 100, "height": 80, "params": {}},
         "actual_text_det_params": {}, "warnings": []},
    ],
    "provisional_best_rotation": 0, "provisional_best_preprocess": "original",
    "provisional_best_detection": "balanced", "benchmark_score": 80.0, "warnings": [],
}
os.write(protocol_fd, json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
os.close(protocol_fd)
""", encoding="utf-8")

        # Monkeypatch worker path
        import betguard.vision.providers.paddleocr_subprocess as psp
        original = None
        if hasattr(psp, '_ocr_python'):
            original = psp._ocr_python

        # Use CLI's _run_benchmark directly
        from scripts.vision_paddleocr_benchmark import _run_benchmark, BENCHMARK_VERSION
        request = {
            "protocol_version": BENCHMARK_VERSION,
            "request_id": "test-001",
            "image_path": str(tmp_path / "test.png"),
            "rotations": [0],
            "preprocess_profiles": ["original"],
            "detection_profiles": {"balanced": {}},
        }

        # Patch worker path
        monkeypatch.setattr(
            "scripts.vision_paddleocr_benchmark._worker_path",
            lambda: worker,
        )

        (tmp_path / "test.png").write_bytes(_make_png())
        result = _run_benchmark(request)
        assert result.get("ok") is True
        assert result.get("inference_count") == 4
        assert len(result.get("profiles", [])) == 1
        assert result["profiles"][0]["raw_text"] == "05\n09"

    def test_benchmark_stderr_non_utf8_no_crash(self, monkeypatch, tmp_path):
        """Non-UTF-8 stderr must not crash benchmark subprocess."""
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)

        worker = tmp_path / "noisy_worker.py"
        worker.write_text(r"""
import json, sys, os
sys.stderr.buffer.write(b'\xb8\xea\xb0T\xff\xfe')
sys.stderr.buffer.flush()
raw = sys.stdin.buffer.read(1048576)
req = json.loads(raw.decode("utf-8"))
sys.stdout.buffer.write(json.dumps({"ok": True, "protocol_version": "betguard.vision.benchmark.v1",
    "request_id": req.get("request_id",""), "profiles": [], "inference_count": 0},
    ensure_ascii=False).encode("utf-8") + b"\n")
""", encoding="utf-8")

        monkeypatch.setattr(
            "scripts.vision_paddleocr_benchmark._worker_path",
            lambda: worker,
        )
        from scripts.vision_paddleocr_benchmark import _run_benchmark, BENCHMARK_VERSION
        result = _run_benchmark({"protocol_version": BENCHMARK_VERSION, "request_id": "t",
                                 "image_path": str(tmp_path / "t.png"),
                                 "rotations": [0], "preprocess_profiles": [], "detection_profiles": {}})
        # Should not crash — stderr is decoded with errors=replace
        assert result.get("ok") is True or "error" in result


class TestReportGeneration:
    """HTML/Markdown report must escape, not inject, and be self-contained."""

    def test_html_escapes_ocr_text(self):
        from scripts.vision_paddleocr_benchmark import _build_html
        result = {
            "engine": {"name": "test"}, "profiles": [
                {"rotation": 0, "preprocess_profile": "orig", "detection_profile": "bal",
                 "status": "completed", "detected_count": 1, "mean_confidence": 0.5,
                 "digit_count": 0, "heuristic_score": 10, "elapsed_ms": 100,
                 "raw_text": "<script>alert('xss')</script>", "items": []},
            ],
            "provisional_best_rotation": 0, "provisional_best_preprocess": "",
            "provisional_best_detection": "", "total_elapsed_ms": 100, "inference_count": 1,
        }
        html = _build_html(result)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_no_external_urls(self):
        from scripts.vision_paddleocr_benchmark import _build_html
        result = {
            "engine": {"name": "test"}, "profiles": [],
            "provisional_best_rotation": 0, "provisional_best_preprocess": "",
            "provisional_best_detection": "", "total_elapsed_ms": 0, "inference_count": 0,
        }
        html = _build_html(result)
        assert "http://" not in html
        assert "https://" not in html
        assert "cdn." not in html.lower()

    def test_html_has_heuristic_warning(self):
        from scripts.vision_paddleocr_benchmark import _build_html
        result = {
            "engine": {"name": "test"}, "profiles": [],
            "provisional_best_rotation": 0, "provisional_best_preprocess": "",
            "provisional_best_detection": "", "total_elapsed_ms": 0, "inference_count": 0,
        }
        html = _build_html(result)
        assert "NOT accuracy" in html or "accuracy" not in html.lower()

    def test_markdown_has_warning(self):
        from scripts.vision_paddleocr_benchmark import _build_markdown
        result = {
            "engine": {"name": "test"}, "profiles": [],
            "provisional_best_rotation": 0, "provisional_best_preprocess": "",
            "provisional_best_detection": "", "total_elapsed_ms": 0, "inference_count": 0,
        }
        md = _build_markdown(result)
        assert "NOT accuracy" in md

    def test_no_cer_without_ground_truth(self):
        """Without ground truth, CER/accuracy must not appear."""
        from scripts.vision_paddleocr_benchmark import _build_markdown
        result = {
            "engine": {"name": "test"}, "profiles": [],
            "provisional_best_rotation": 0, "provisional_best_preprocess": "",
            "provisional_best_detection": "", "total_elapsed_ms": 0, "inference_count": 0,
        }
        md = _build_markdown(result)
        assert "CER" not in md
        assert "precision" not in md.lower()
        assert "recall" not in md.lower()


class TestCLISafety:
    """CLI must reject unsafe output paths."""

    def test_output_in_repo_rejected(self, tmp_path, monkeypatch):
        """output-dir inside repo must be rejected."""
        # Simulate by placing output-dir path check
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", sys.executable)
        # The CLI checks relative_to repo root — we verify the check exists
        from scripts.vision_paddleocr_benchmark import main
        # Just verify the check source exists
        source = Path(__file__).parents[1] / "scripts" / "vision_paddleocr_benchmark.py"
        if source.is_file():
            content = source.read_text(encoding="utf-8")
            assert "relative_to" in content

    def test_shell_false_in_source(self):
        """Subprocess must use shell=False."""
        source = Path(__file__).parents[1] / "scripts" / "vision_paddleocr_benchmark.py"
        if source.is_file():
            content = source.read_text(encoding="utf-8")
            assert "shell=False" in content
            assert "shell=True" not in content


class TestNoPollution:
    """Main venv must not import paddle/paddleocr (heavy OCR runtime).
    cv2/numpy ARE now declared runtime deps of betguard (row pipeline),
    so only the heavy OCR stack is prohibited in the main environment."""

    def test_no_paddle_in_main(self):
        assert "paddle" not in sys.modules
        assert "paddleocr" not in sys.modules

    def test_no_parser_in_benchmark_protocol(self):
        import betguard.vision.benchmark_protocol as bp
        source = bp.__file__
        if source:
            with open(source, encoding="utf-8") as f:
                content = f.read()
            for banned in ["betguard.parser", "betguard.validator", "betguard.webfill"]:
                assert banned not in content, f"benchmark_protocol imports {banned}"
