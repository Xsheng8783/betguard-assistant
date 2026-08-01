"""Test PaddleOCR subprocess provider — uses fake OCR Python, no real PaddleOCR."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from betguard.vision.providers.paddleocr_subprocess import (
    _ocr_python,
    _worker_path,
    _run_worker,
    recognize_with_metadata,
)
from betguard.vision.image_intake import validate_and_store, save_metadata, delete_image


# ── Fake OCR Python script ───────────────────────────────────────────────────


def _make_fake_ocr_script(response: dict | str, exit_code: int = 0, delay: float = 0) -> Path:
    """Create a fake OCR Python script that writes the response to stdout."""
    tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
    if isinstance(response, str):
        content = response
    else:
        content = json.dumps(response, ensure_ascii=False)

    script = f'''
import json, sys, time
if {delay} > 0:
    time.sleep({delay})
if {exit_code} != 0:
    sys.stderr.write("fake error")
    sys.exit({exit_code})
sys.stdout.write(''' + repr(content) + ''' + "\\n")
'''
    tmp.write(script)
    tmp.close()
    return Path(tmp.name)


def _fake_worker_path() -> Path:
    """Create a fake worker.py that just echoes a valid response."""
    response = {
        "ok": True,
        "protocol_version": "betguard.vision.worker.v1",
        "request_id": "_placeholder_",
        "engine": {
            "name": "paddleocr",
            "paddle_version": "3.3.0",
            "paddleocr_version": "3.7.0",
            "device": "cpu",
            "detection_model": "PP-OCRv5_mobile_det",
            "recognition_model": "PP-OCRv5_mobile_rec",
        },
        "elapsed_ms": 100.0,
        "items": [
            {"text": "05", "score": 0.99, "polygon": [[10, 10], [50, 10], [50, 30], [10, 30]], "box": [10, 10, 50, 30]},
            {"text": "09", "score": 0.95, "polygon": [[60, 10], [100, 10], [100, 30], [60, 30]], "box": [60, 10, 100, 30]},
            {"text": "15", "score": 0.97, "polygon": [[10, 40], [50, 40], [50, 60], [10, 60]], "box": [10, 40, 50, 60]},
        ],
        "warnings": [],
    }

    tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
    script = f'''
import json, sys
raw = sys.stdin.buffer.read(1024 * 1024)
req = json.loads(raw.decode("utf-8"))
resp = {json.dumps(response)}
resp["request_id"] = req.get("request_id", "")
json.dump(resp, sys.stdout, ensure_ascii=False)
sys.stdout.write("\\n")
'''
    tmp.write(script)
    tmp.close()
    return Path(tmp.name)


def _make_png() -> bytes:
    import struct, zlib
    w, h = 10, 10
    sig = b'\x89PNG\r\n\x1a\n'
    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b'\x00' + b'\xff\x00\x00\xff' * w * h
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


# ── Tests ────────────────────────────────────────────────────────────────────


class TestEnvValidation:
    def test_no_env_configured(self, monkeypatch):
        monkeypatch.delenv("BETGUARD_OCR_PYTHON", raising=False)
        with pytest.raises(RuntimeError, match="OCR_PYTHON_NOT_CONFIGURED"):
            _ocr_python()

    def test_python_not_found(self, monkeypatch):
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", "/nonexistent/python.exe")
        with pytest.raises(RuntimeError, match="OCR_PYTHON_NOT_FOUND"):
            _ocr_python()


class TestSubprocessSuccess:
    def test_fake_worker_success(self, monkeypatch):
        """Run via fake OCR Python that echoes a valid response."""
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)

        # Create a self-contained fake worker that reads stdin and writes response
        import tempfile
        worker = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        worker.write("""
import json, sys
raw = sys.stdin.buffer.read(1048576)
req = json.loads(raw.decode("utf-8"))
resp = {
    "ok": True,
    "protocol_version": "betguard.vision.worker.v1",
    "request_id": req.get("request_id", ""),
    "engine": {"name": "paddleocr", "paddle_version": "3.3.0", "paddleocr_version": "3.7.0",
               "device": "cpu", "detection_model": "PP-OCRv5_mobile_det",
               "recognition_model": "PP-OCRv5_mobile_rec"},
    "elapsed_ms": 100.0,
    "items": [
        {"text": "05", "score": 0.99, "polygon": [[10,10],[50,10],[50,30],[10,30]], "box": [10,10,50,30]},
        {"text": "09", "score": 0.95, "polygon": [[60,10],[100,10],[100,30],[60,30]], "box": [60,10,100,30]},
        {"text": "15", "score": 0.97, "polygon": [[10,40],[50,40],[50,60],[10,60]], "box": [10,40,50,60]},
    ],
    "warnings": [],
}
sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\\n")
""")
        worker.close()
        worker_path = Path(worker.name)
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker_path,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png", "test.png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "completed", f"Got status={result.status.value}, error={result.provider_error}"
            assert result.provider.id == "paddleocr-local"
            assert len(result.lines) == 3
            assert result.lines[0].text == "05"
            assert result.lines[1].text == "09"
            assert result.raw_text == "05\n09\n15"
        finally:
            delete_image(meta.image_id)
            worker_path.unlink(missing_ok=True)

    def test_sorted_by_position(self, monkeypatch):
        """Items should be sorted by y, then x."""
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)

        import tempfile
        worker = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        worker.write("""
import json, sys
raw = sys.stdin.buffer.read(1048576)
req = json.loads(raw.decode("utf-8"))
resp = {
    "ok": True,
    "protocol_version": "betguard.vision.worker.v1",
    "request_id": req.get("request_id", ""),
    "engine": {"name": "paddleocr"},
    "elapsed_ms": 50.0,
    "items": [
        {"text": "BOTTOM", "score": 1.0, "polygon": [[10,200],[50,200],[50,220],[10,220]]},
        {"text": "TOP", "score": 1.0, "polygon": [[10,10],[50,10],[50,30],[10,30]]},
        {"text": "MID", "score": 1.0, "polygon": [[10,100],[50,100],[50,120],[10,120]]},
    ],
}
sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\\n")
""")
        worker.close()
        worker_path = Path(worker.name)
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker_path,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert len(result.lines) == 3
            assert result.lines[0].text == "TOP"
            assert result.lines[1].text == "MID"
            assert result.lines[2].text == "BOTTOM"
        finally:
            delete_image(meta.image_id)
            worker_path.unlink(missing_ok=True)


class TestSubprocessErrors:
    def test_non_json_output(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script("not json at all")
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "failed"
            assert result.provider_error is not None
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_non_zero_exit(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script("{}", exit_code=1)
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "failed"
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_protocol_mismatch(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script({
            "ok": True,
            "protocol_version": "wrong.version",
            "request_id": "r",
            "engine": {},
            "items": [],
        })
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "failed"
            assert result.provider_error is not None
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_request_id_mismatch(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script({
            "ok": True,
            "protocol_version": "betguard.vision.worker.v1",
            "request_id": "wrong-id",
            "engine": {},
            "items": [],
        })
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "failed"
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_timeout(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script("{}", delay=5)
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta, timeout=1)
            assert result.status.value == "failed"
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_error_not_ok(self, monkeypatch):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script({
            "ok": False,
            "protocol_version": "betguard.vision.worker.v1",
            "request_id": "r",
            "error": {"code": "OCR_INFERENCE_FAILED", "message": "fail"},
        })
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            assert result.status.value == "failed"
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)

    def test_error_no_path_leak(self, monkeypatch, tmp_path):
        fake_python = sys.executable
        monkeypatch.setenv("BETGUARD_OCR_PYTHON", fake_python)
        worker = _make_fake_ocr_script("not json")
        monkeypatch.setattr(
            "betguard.vision.providers.paddleocr_subprocess._worker_path",
            lambda: worker,
        )

        png = _make_png()
        meta = validate_and_store(png, "image/png")
        img_path = str(meta.storage_path)
        save_metadata(meta)
        try:
            result = recognize_with_metadata(meta)
            err_str = json.dumps(result.to_dict(), default=str)
            assert img_path not in err_str
            assert "Traceback" not in err_str
        finally:
            delete_image(meta.image_id)
            worker.unlink(missing_ok=True)


class TestNoPollution:
    """Main process must NOT import paddle, paddleocr, parser, validator, webfill."""

    def test_no_paddle_in_main(self):
        assert "paddle" not in sys.modules
        assert "paddleocr" not in sys.modules

    def test_subprocess_module_no_prohibited_imports(self):
        """Source file must not import prohibited modules."""
        import re
        import betguard.vision.providers.paddleocr_subprocess as p
        source = p.__file__
        if source:
            with open(source, encoding="utf-8") as f:
                content = f.read()
            # Check actual import statements, not docstring mentions
            for banned in ["betguard.parser", "betguard.validator", "betguard.webfill",
                           "betguard.normalizer"]:
                assert banned not in content, f"paddleocr_subprocess.py imports {banned}"
            for pat in [r"^import paddleocr\b", r"^from paddleocr\b",
                        r"^import cv2\b", r"^from cv2\b",
                        r"^import paddle\b", r"^from paddle\b"]:
                assert not re.search(pat, content, re.MULTILINE), \
                    f"paddleocr_subprocess.py matches {pat}"

    def test_worker_module_no_prohibited_imports(self):
        """Worker module imports only paddleocr."""
        worker_path = Path(__file__).parents[1] / "tools" / "vision" / "paddleocr_worker.py"
        if worker_path.is_file():
            content = worker_path.read_text(encoding="utf-8")
            for banned in ["betguard.parser", "betguard.validator", "betguard.webfill",
                           "betguard.normalizer"]:
                assert banned not in content, f"worker.py imports {banned}"

    def test_subprocess_uses_shell_false(self):
        """Verify the source code uses shell=False."""
        import betguard.vision.providers.paddleocr_subprocess as p
        source = p.__file__
        if source:
            with open(source, encoding="utf-8") as f:
                content = f.read()
            assert "subprocess.run" in content
            assert "shell=True" not in content
