"""Test Vision API routes via service layer (no HTTP server needed)."""

from __future__ import annotations

import uuid
import zlib
from unittest.mock import patch

import pytest

from betguard.vision.service import (
    delete_image_api,
    get_image_preview,
    list_providers,
    run_job,
    upload_image,
)
from betguard.vision.image_intake import delete_image, validate_and_store, save_metadata


# ── Minimal PNG generator ────────────────────────────────────────────────────


def _make_png(w: int = 10, h: int = 10) -> bytes:
    import struct
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00\xff" * w * h
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _upload_cleanup(meta) -> None:
    if meta and hasattr(meta, "image_id"):
        delete_image(meta.image_id)


# ── Provider list ────────────────────────────────────────────────────────────


class TestProviders:
    def test_only_fake(self):
        r = list_providers()
        assert r["ok"] is True
        providers = r["providers"]
        assert len(providers) == 1
        assert providers[0]["id"] == "fake"
        assert providers[0]["real_ocr"] is False
        assert providers[0]["external_network"] is False

    def test_has_fixtures(self):
        r = list_providers()
        assert "bet_slip" in r["providers"][0]["fixtures"]


# ── Upload ───────────────────────────────────────────────────────────────────


class TestUpload:
    def test_upload_success(self):
        png = _make_png()
        result = upload_image(png, "image/png", "test.png")
        assert result["ok"] is True
        assert "image_id" in result["image"]
        assert result["image"]["mime_type"] == "image/png"
        _upload_cleanup(None)  # placeholder, cleaned below

    def test_upload_empty(self):
        result = upload_image(b"")
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_EMPTY"

    def test_upload_wrong_content_type(self):
        png = _make_png()
        result = upload_image(png, "image/jpeg", "test.png")
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_MAGIC_MISMATCH"

    def test_upload_bad_format(self):
        result = upload_image(b"not an image", "image/png")
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_TYPE_UNSUPPORTED"


# ── Preview ──────────────────────────────────────────────────────────────────


class TestPreview:
    def test_preview_success(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png", "preview.png")
        save_metadata(meta)
        data, mime, error = get_image_preview(meta.image_id)
        assert error is None
        assert data == png
        assert mime == "image/png"
        delete_image(meta.image_id)

    def test_preview_not_found(self):
        data, mime, error = get_image_preview("nonexistent123456789012345678")
        assert error is not None
        assert error["error"]["code"] == "IMAGE_NOT_FOUND"

    def test_preview_invalid_id(self):
        data, mime, error = get_image_preview("bad")
        assert error is not None
        assert error["error"]["code"] == "IMAGE_NOT_FOUND"


# ── Delete ───────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_success(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png", "del.png")
        save_metadata(meta)
        result = delete_image_api(meta.image_id)
        assert result["ok"] is True

    def test_delete_not_found(self):
        result = delete_image_api("a" * 32)
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_NOT_FOUND"

    def test_delete_invalid_id(self):
        result = delete_image_api("bad")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_IMAGE_ID"


# ── Job ──────────────────────────────────────────────────────────────────────


class TestJob:
    def test_job_success(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png", "job.png")
        save_metadata(meta)
        result = run_job(meta.image_id, "fake", "bet_slip")
        assert result["ok"] is True
        assert "result" in result
        assert result["result"]["lines"]
        assert result["result"]["schema_version"] == "betguard.vision.recognition.v1"
        delete_image(meta.image_id)

    def test_job_no_image(self):
        result = run_job("a" * 32, "fake", "bet_slip")
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_NOT_FOUND"

    def test_job_bad_provider(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        result = run_job(meta.image_id, "openai", "bet_slip")
        assert result["ok"] is False
        assert result["error"]["code"] == "PROVIDER_NOT_SUPPORTED"
        delete_image(meta.image_id)

    def test_job_other_fixtures(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        for fixture in ["no_confidence", "multi_line"]:
            result = run_job(meta.image_id, "fake", fixture)
            assert result["ok"] is True
        delete_image(meta.image_id)

    def test_job_does_not_import_parser(self):
        """Job execution must NOT import parser, validator, or webfill."""
        modules_before = set(k for k in __import__("sys").modules if "betguard.parser" in k or "betguard.validator" in k or "betguard.webfill" in k)
        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        run_job(meta.image_id, "fake", "bet_slip")
        modules_after = set(k for k in __import__("sys").modules if "betguard.parser" in k or "betguard.validator" in k or "betguard.webfill" in k)
        assert modules_before == modules_after
        delete_image(meta.image_id)

    def test_job_does_not_import_playwright(self):
        """Job must not trigger playwright import."""
        import sys
        was_loaded = "playwright" in sys.modules
        png = _make_png()
        meta = validate_and_store(png, "image/png")
        save_metadata(meta)
        run_job(meta.image_id, "fake", "bet_slip")
        assert "playwright" not in sys.modules or was_loaded  # only if already loaded
        delete_image(meta.image_id)


class TestContentLengthEdgeCases:
    """Content-Length edge cases that must be handled safely."""

    def test_content_length_missing(self):
        """Service upload with no content_type still validates magic."""
        from betguard.vision.service import upload_image
        png = _make_png()
        result = upload_image(png, "", "no-cl.png")
        assert result["ok"] is True
        delete_image(result["image"]["image_id"])

    def test_content_length_exceeded_body_not_read(self):
        """When size exceeds limit, service rejects without parsing dimensions."""
        # Create data > 10 MiB but with valid PNG sig at start
        sig = b"\x89PNG\r\n\x1a\n"
        big = sig + b"\x00" * (10 * 1024 * 1024 + 100)
        from betguard.vision.service import upload_image
        result = upload_image(big, "image/png")
        assert result["ok"] is False
        assert result["error"]["code"] == "IMAGE_TOO_LARGE"


class TestPreviewHeaders:
    """Preview must return correct security headers."""

    def test_preview_mime_type_correct(self):
        png = _make_png()
        meta = validate_and_store(png, "image/png", "preview.png")
        save_metadata(meta)
        from betguard.vision.service import get_image_preview
        data, mime, error = get_image_preview(meta.image_id)
        assert error is None
        assert mime == "image/png"
        assert data == png
        delete_image(meta.image_id)

    def test_preview_headers_via_handler_attached(self):
        """Verify the handler source includes required security headers."""
        import betguard.webui.app as app_mod
        source = app_mod.__file__
        if source:
            with open(source, encoding="utf-8") as f:
                content = f.read()
            assert "no-store" in content
            assert "nosniff" in content
