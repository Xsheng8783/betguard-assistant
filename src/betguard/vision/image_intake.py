"""Image intake: validation, metadata, and secure storage for OCR images.

Pure stdlib — no Pillow, OpenCV, or other image libraries.
Parses PNG/JPEG/WebP headers directly for dimensions.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from betguard.user_data import get_data_dir

# ── Limits ───────────────────────────────────────────────────────────────────

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_WIDTH = 12_000
MAX_HEIGHT = 12_000
MAX_PIXELS = 40_000_000
IMAGE_RETENTION_HOURS = 24

# ── Magic bytes ──────────────────────────────────────────────────────────────

# (magic_bytes, mime_type)
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP
]

ALLOWED_MIME_TYPES = frozenset({sig[1] for sig in _MAGIC_SIGNATURES})


def detect_mime_type(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes."""
    for magic, mime in _MAGIC_SIGNATURES:
        if data[: len(magic)] == magic:
            # WebP: check for WEBP at offset 8
            if mime == "image/webp":
                if len(data) >= 12 and data[8:12] == b"WEBP":
                    return mime
                return None  # RIFF but not WEBP
            return mime
    return None


# ── Dimension parsing (pure stdlib) ──────────────────────────────────────────


def _parse_png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse PNG IHDR chunk for width/height."""
    if len(data) < 33:
        raise ValueError("PNG too short for IHDR")
    # IHDR starts at offset 16 (8 sig + 4 length + 4 "IHDR")
    if data[12:16] != b"IHDR":
        raise ValueError("PNG missing IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _parse_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Parse JPEG SOF marker for width/height."""
    pos = 2
    while pos + 4 < len(data):
        if data[pos] != 0xFF:
            raise ValueError("Invalid JPEG marker")
        marker = data[pos + 1]
        if marker == 0xDA:  # SOS — no more SOF
            break
        if marker in (0xD8, 0xD9):  # SOI, EOI
            pos += 2
            continue
        if pos + 4 > len(data):
            break
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if marker >= 0xC0 and marker <= 0xC3:  # SOF0-SOF3
            if pos + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return width, height
        pos += 2 + length
    raise ValueError("JPEG dimensions not found")


def _parse_webp_dimensions(data: bytes) -> tuple[int, int]:
    """Parse WebP VP8/VP8L/VP8X chunk for width/height."""
    if len(data) < 30:
        raise ValueError("WebP too short")
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if len(data) < 30:
            raise ValueError("WebP VP8 too short")
        # Lossy: 10-byte frame header at offset 20
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return w, h
    elif chunk == b"VP8L":
        if len(data) < 25:
            raise ValueError("WebP VP8L too short")
        bits = struct.unpack("<I", data[21:25])[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return w, h
    elif chunk == b"VP8X":
        if len(data) < 30:
            raise ValueError("WebP VP8X too short")
        w = struct.unpack("<I", data[24:28])[0] & 0x00FFFFFF
        h = struct.unpack("<I", data[28:32])[0] & 0x00FFFFFF
        return w + 1, h + 1
    raise ValueError("Unsupported WebP chunk type")


def _parse_dimensions(data: bytes, mime_type: str) -> tuple[int, int]:
    """Parse image dimensions from raw bytes."""
    if mime_type == "image/png":
        return _parse_png_dimensions(data)
    elif mime_type == "image/jpeg":
        return _parse_jpeg_dimensions(data)
    elif mime_type == "image/webp":
        return _parse_webp_dimensions(data)
    raise ValueError(f"Unsupported MIME type: {mime_type}")


# ── ImageMetadata ────────────────────────────────────────────────────────────


@dataclass
class ImageMetadata:
    """Secure metadata for a stored image."""

    image_id: str
    original_filename: str = ""
    stored_filename: str = ""
    sha256: str = ""
    mime_type: str = ""
    width: int = 0
    height: int = 0
    byte_size: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=IMAGE_RETENTION_HOURS))
    retention: str = "temporary"
    cloud_uploaded: bool = False

    @property
    def storage_path(self) -> Path:
        """Filesystem path — never exposed via API."""
        return _image_dir(self.image_id) / self.stored_filename

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """API-safe dict: no storage_path, no base64."""
        return {
            "image_id": self.image_id,
            "original_filename": self.original_filename,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "retention": self.retention,
            "cloud_uploaded": self.cloud_uploaded,
        }


# ── Storage paths ────────────────────────────────────────────────────────────


def _vision_temp_root() -> Path:
    return Path(get_data_dir()) / "temp" / "vision"


def _image_dir(image_id: str) -> Path:
    """Resolve storage dir for an image_id, preventing path traversal."""
    # Sanitize: only allow UUID-format image_ids
    try:
        uuid.UUID(image_id)
    except ValueError:
        raise ValueError(f"Invalid image_id format: {image_id}")
    root = _vision_temp_root().resolve()
    subdir = root / image_id
    resolved = subdir.resolve()
    if not str(resolved).startswith(str(root)):
        raise ValueError("Path traversal detected")
    return resolved


def _cleanup_expired() -> int:
    """Remove expired images. Returns count of deleted dirs."""
    root = _vision_temp_root()
    if not root.exists():
        return 0

    deleted = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=IMAGE_RETENTION_HOURS)

    for entry in root.iterdir():
        if entry.is_dir():
            try:
                uuid.UUID(entry.name)  # only UUID-named dirs
                mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    _rmtree(entry)
                    deleted += 1
            except (ValueError, OSError):
                continue
    return deleted


def _rmtree(path: Path) -> None:
    """Remove a directory tree safely."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _rmtree(child)
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


# ── Public API ───────────────────────────────────────────────────────────────


def validate_and_store(
    data: bytes,
    content_type: str = "",
    original_filename: str = "",
) -> ImageMetadata:
    """Validate image data and store to temp directory. Returns metadata.

    Raises ValueError with stable error codes on failure.
    """
    # 1. Empty check
    if not data:
        raise ValueError("IMAGE_EMPTY")

    # 2. Size check
    if len(data) > MAX_BYTES:
        raise ValueError("IMAGE_TOO_LARGE")

    # 3. Magic bytes check
    detected_mime = detect_mime_type(data)
    if detected_mime is None:
        raise ValueError("IMAGE_TYPE_UNSUPPORTED")

    # 4. Content-Type vs magic mismatch
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise ValueError("IMAGE_TYPE_UNSUPPORTED")
    if content_type and content_type != detected_mime:
        raise ValueError("IMAGE_MAGIC_MISMATCH")

    # 5. Parse dimensions
    try:
        width, height = _parse_dimensions(data, detected_mime)
    except (ValueError, struct.error):
        raise ValueError("IMAGE_DIMENSIONS_INVALID")

    # 6. Dimension checks
    if width <= 0 or height <= 0:
        raise ValueError("IMAGE_DIMENSIONS_INVALID")
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        raise ValueError("IMAGE_DIMENSIONS_INVALID")
    if width * height > MAX_PIXELS:
        raise ValueError("IMAGE_PIXEL_LIMIT_EXCEEDED")

    # 7. Cleanup expired
    _cleanup_expired()

    # 8. Compute SHA-256
    sha256 = hashlib.sha256(data).hexdigest()

    # 9. Store
    image_id = uuid.uuid4().hex
    ext = _mime_to_ext(detected_mime)
    stored_filename = f"{image_id}{ext}"

    img_dir = _image_dir(image_id)
    img_dir.mkdir(parents=True, exist_ok=True)

    target = img_dir / stored_filename

    # Atomic write: temp file + rename
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(img_dir), prefix=".tmp_")
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp_path, str(target))
    except OSError:
        raise ValueError("IMAGE_STORAGE_FAILED")

    return ImageMetadata(
        image_id=image_id,
        original_filename=_sanitize_filename(original_filename),
        stored_filename=stored_filename,
        sha256=sha256,
        mime_type=detected_mime,
        width=width,
        height=height,
        byte_size=len(data),
    )


def get_metadata(image_id: str) -> ImageMetadata | None:
    """Retrieve metadata for a stored image (rebuilds from filesystem)."""
    try:
        img_dir = _image_dir(image_id)
    except ValueError:
        return None

    if not img_dir.exists():
        return None

    files = list(img_dir.iterdir())
    if not files:
        return None

    stored = files[0]
    stat = stored.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    if datetime.now(timezone.utc) > mtime + timedelta(hours=IMAGE_RETENTION_HOURS):
        return None  # expired

    # Read metadata if exists, otherwise rebuild minimal
    meta_path = img_dir / "meta.json"
    if meta_path.exists():
        import json
        try:
            d = json.loads(meta_path.read_text(encoding="utf-8"))
            return ImageMetadata(
                image_id=d.get("image_id", image_id),
                original_filename=d.get("original_filename", ""),
                stored_filename=stored.name,
                sha256=d.get("sha256", hashlib.sha256(stored.read_bytes()).hexdigest()),
                mime_type=d.get("mime_type", ""),
                width=d.get("width", 0),
                height=d.get("height", 0),
                byte_size=d.get("byte_size", stat.st_size),
                created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else mtime,
                expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else mtime + timedelta(hours=IMAGE_RETENTION_HOURS),
            )
        except Exception:
            pass

    # Fallback: rebuild minimal metadata from file (no meta.json)
    if stored.name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        data = stored.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        try:
            mime = detect_mime_type(data) or ""
            w, h = _parse_dimensions(data, mime) if mime else (0, 0)
        except Exception:
            mime, w, h = "", 0, 0
        return ImageMetadata(
            image_id=image_id,
            original_filename="",
            stored_filename=stored.name,
            sha256=sha256,
            mime_type=mime,
            width=w,
            height=h,
            byte_size=stat.st_size,
            created_at=mtime,
            expires_at=mtime + timedelta(hours=IMAGE_RETENTION_HOURS),
        )

    return None


def read_image_data(image_id: str) -> bytes | None:
    """Read stored image bytes."""
    try:
        meta = get_metadata(image_id)
    except ValueError:
        return None
    if meta is None:
        return None
    path = meta.storage_path
    if not path.exists():
        return None
    return path.read_bytes()


def delete_image(image_id: str) -> bool:
    """Delete stored image and metadata. Returns True if found+deleted."""
    try:
        img_dir = _image_dir(image_id)
    except ValueError:
        return False
    if not img_dir.exists():
        return False
    _rmtree(img_dir)
    return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """Strip path separators and dangerous characters."""
    if not name:
        return ""
    # Remove path separators
    name = name.replace("\\", "_").replace("/", "_")
    # Keep only safe chars
    safe = []
    for ch in name:
        if ch.isalnum() or ch in "._- ()":
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe)[:255]


def _mime_to_ext(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".bin")


# ── Metadata persistence ─────────────────────────────────────────────────────


def save_metadata(meta: ImageMetadata) -> None:
    """Persist metadata to disk alongside image."""
    img_dir = _image_dir(meta.image_id)
    img_dir.mkdir(parents=True, exist_ok=True)
    meta_path = img_dir / "meta.json"
    d = meta.to_dict()
    import json
    meta_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
