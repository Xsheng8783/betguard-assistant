"""Test image intake: magic bytes, dimension parsing, validation, storage."""

from __future__ import annotations

import hashlib
import struct
import uuid
import zlib
from datetime import timedelta, timezone

import pytest

from betguard.vision.image_intake import (
    ImageMetadata,
    _rmtree,
    delete_image,
    detect_mime_type,
    get_metadata,
    read_image_data,
    save_metadata,
    validate_and_store,
)


# ── Minimal image generators (pure stdlib) ───────────────────────────────────


def make_minimal_png(width: int = 10, height: int = 10) -> bytes:
    """Generate a minimal valid PNG."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(type_: bytes, data: bytes) -> bytes:
        c = type_ + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00\xff" * width * height
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_minimal_jpeg() -> bytes:
    """Generate a minimal valid JPEG (1x1)."""
    tables = bytes.fromhex(
        "ffd8ffe000104a464946000101000100010000ffdb004300"
        "080606070605080707070909080a0c140d0c0b0b0c19"
    )[:2]
    # Minimal JPEG: SOI + APP0 + DQT + SOF0 + DHT + SOS + EOI
    parts = [b"\xff\xd8"]  # SOI
    # DQT
    dqt = b"\xff\xdb\x00\x43\x00" + b"\x08" * 64
    parts.append(dqt)
    # SOF0 (1x1)
    sof = struct.pack(">HHHBHH", 0xFFC0, 8 + 3, 8, 1, 1, 0x0101)
    parts.append(sof)
    # DHT
    dht = b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
    parts.append(dht)
    # SOS
    sos = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x7f\xea" + b"\xff\xd9"
    parts.append(sos)
    return b"".join(parts)


def make_minimal_webp() -> bytes:
    """Generate a minimal valid lossless WebP (1x1)."""
    # RIFF header
    riff = b"RIFF"
    # VP8L header (lossless)
    vp8l_header = b"\x2f\x00\x00\x00"  # 1x1 image encoded
    # Actual minimal VP8L bitstream for a 1x1 image
    vp8l_data = bytes([0x2f]) + bytes([0x00] * 4)
    size = struct.pack("<I", 4 + 4 + len(vp8l_data))
    return riff + size + b"WEBPVP8L" + vp8l_data


# ── Magic bytes tests ────────────────────────────────────────────────────────


class TestMagicBytes:
    def test_png(self):
        assert detect_mime_type(make_minimal_png()) == "image/png"

    def test_jpeg(self):
        assert detect_mime_type(make_minimal_jpeg()) == "image/jpeg"

    def test_webp(self):
        assert detect_mime_type(make_minimal_webp()) == "image/webp"

    def test_empty(self):
        assert detect_mime_type(b"") is None

    def test_garbage(self):
        assert detect_mime_type(b"not an image\x00\x00\x00\x00") is None

    def test_svg_rejected(self):
        assert detect_mime_type(b"<svg></svg>") is None


# ── Validation tests ─────────────────────────────────────────────────────────


class TestImageValidation:
    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="IMAGE_EMPTY"):
            validate_and_store(b"")

    def test_too_large_rejected(self):
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 100)
        with pytest.raises(ValueError, match="IMAGE_TOO_LARGE"):
            validate_and_store(big)

    def test_content_type_mismatch(self):
        png = make_minimal_png()
        with pytest.raises(ValueError, match="IMAGE_MAGIC_MISMATCH"):
            validate_and_store(png, "image/jpeg")

    def test_unsupported_content_type(self):
        png = make_minimal_png()
        with pytest.raises(ValueError, match="IMAGE_TYPE_UNSUPPORTED"):
            validate_and_store(png, "image/bmp")

    def test_invalid_png_header(self):
        bad = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        with pytest.raises(ValueError, match="IMAGE_DIMENSIONS_INVALID"):
            validate_and_store(bad)

    def test_valid_png_succeeds(self):
        png = make_minimal_png(100, 50)
        meta = validate_and_store(png, "image/png", "test.png")
        assert meta.width == 100
        assert meta.height == 50
        assert meta.mime_type == "image/png"
        assert meta.byte_size == len(png)
        delete_image(meta.image_id)

    def test_valid_jpeg_succeeds(self):
        jpg = make_minimal_jpeg()
        meta = validate_and_store(jpg, "image/jpeg", "test.jpg")
        assert meta.mime_type == "image/jpeg"
        delete_image(meta.image_id)

    def test_dimensions_negative(self):
        png = make_minimal_png()
        # Corrupt IHDR width bytes
        bad = bytearray(png)
        bad[16:20] = struct.pack(">i", -1)
        with pytest.raises(ValueError, match="IMAGE_DIMENSIONS_INVALID"):
            validate_and_store(bytes(bad))


class TestMetadata:
    def test_sha256_correct(self):
        png = make_minimal_png(10, 10)
        expected = hashlib.sha256(png).hexdigest()
        meta = validate_and_store(png, "image/png")
        assert meta.sha256 == expected
        delete_image(meta.image_id)

    def test_uuid_format(self):
        png = make_minimal_png()
        meta = validate_and_store(png, "image/png")
        try:
            uuid.UUID(meta.image_id)
        except ValueError:
            pytest.fail("image_id is not valid UUID")
        delete_image(meta.image_id)

    def test_stored_filename_not_original(self):
        png = make_minimal_png()
        meta = validate_and_store(png, "image/png", "sensitive_name.png")
        assert "sensitive_name" not in meta.stored_filename
        delete_image(meta.image_id)

    def test_api_dict_no_storage_path(self):
        png = make_minimal_png()
        meta = validate_and_store(png, "image/png")
        d = meta.to_dict()
        assert "storage_path" not in d
        assert "base64" not in str(d).lower()
        delete_image(meta.image_id)

    def test_save_and_load_metadata(self):
        png = make_minimal_png(20, 30)
        meta = validate_and_store(png, "image/png", "roundtrip.png")
        save_metadata(meta)
        loaded = get_metadata(meta.image_id)
        assert loaded is not None
        assert loaded.image_id == meta.image_id
        assert loaded.width == 20
        assert loaded.height == 30
        delete_image(meta.image_id)

    def test_read_image_data(self):
        png = make_minimal_png()
        meta = validate_and_store(png, "image/png")
        data = read_image_data(meta.image_id)
        assert data == png
        delete_image(meta.image_id)


class TestDeleteAndCleanup:
    def test_delete_success(self):
        png = make_minimal_png()
        meta = validate_and_store(png, "image/png")
        assert delete_image(meta.image_id) is True
        assert get_metadata(meta.image_id) is None

    def test_delete_nonexistent(self):
        assert delete_image("nonexistent123456789012345678") is False

    def test_delete_invalid_id(self):
        assert delete_image("../etc/passwd") is False


class TestPathTraversal:
    def test_invalid_uuid_rejected(self):
        from betguard.vision.image_intake import _image_dir
        with pytest.raises(ValueError, match="Invalid image_id"):
            _image_dir("../evil")

    def test_uuid_path_is_safe(self):
        from betguard.vision.image_intake import _image_dir
        img_id = uuid.uuid4().hex
        d = _image_dir(img_id)
        assert ".." not in str(d)


class TestTruncatedHeaders:
    """Truncated image headers must be rejected cleanly."""

    def test_truncated_png_rejected(self):
        bad = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4  # no IHDR
        with pytest.raises(ValueError):
            validate_and_store(bad)

    def test_truncated_jpeg_rejected(self):
        bad = b"\xff\xd8\xff" + b"\x00" * 4  # SOI only, no SOF
        with pytest.raises(ValueError):
            validate_and_store(bad, "image/jpeg")

    def test_truncated_webp_rejected(self):
        bad = b"RIFF\x00\x00\x00\x00WEBP"  # no VP8 chunk
        with pytest.raises(ValueError):
            validate_and_store(bad)

    def test_jpeg_no_sof_dimensions_rejected(self):
        # JPEG with SOI but malformed — no valid SOF segment
        bad = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xd9"
        with pytest.raises(ValueError):
            validate_and_store(bad, "image/jpeg")


class TestErrorNoLeak:
    """API errors must not leak paths or tracebacks."""

    def test_upload_error_no_storage_path(self):
        from betguard.vision.service import upload_image
        result = upload_image(b"not an image", "image/png")
        assert result["ok"] is False
        # Error dict must not contain storage_path or absolute paths
        err_str = str(result)
        assert "storage_path" not in err_str
        assert "C:\\" not in err_str
        assert "Traceback" not in err_str

    def test_job_error_no_path(self):
        from betguard.vision.service import run_job
        result = run_job("a" * 32, "fake", "bet_slip")
        assert result["ok"] is False
        err_str = str(result)
        assert "storage_path" not in err_str
        assert "Traceback" not in err_str

    def test_error_has_stable_format(self):
        from betguard.vision.service import upload_image
        result = upload_image(b"", "image/png")
        assert result["ok"] is False
        assert "error" in result
        assert "code" in result["error"]
        assert "message" in result["error"]
        assert "retryable" in result["error"]
