"""Persistent, auditable cache for validated DashScope Qwen responses."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = "betguard.vision.qwen-cache.v1"


@dataclass(frozen=True)
class QwenCacheIdentity:
    """Every input that can change a Qwen response or crop interpretation."""

    image_sha256: str
    crop_box: tuple[int, int, int, int] | None
    scale: int | None
    variant: str
    model: str
    prompt_version: str
    task_type: str
    prompt_sha256: str
    request_schema_version: str
    effective_crop_sha256: str
    mime_type: str
    max_tokens: int
    endpoint_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_sha256": self.image_sha256,
            "crop_box": list(self.crop_box) if self.crop_box is not None else None,
            "scale": self.scale,
            "variant": self.variant,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "task_type": self.task_type,
            "prompt_sha256": self.prompt_sha256,
            "request_schema_version": self.request_schema_version,
            "effective_crop_sha256": self.effective_crop_sha256,
            "mime_type": self.mime_type,
            "max_tokens": self.max_tokens,
            "endpoint_url": self.endpoint_url,
        }

    def key(self) -> str:
        material = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


class QwenResponseCache:
    """JSON-file cache that stores only caller-validated successful content."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or default_qwen_cache_dir()

    def get(self, identity: QwenCacheIdentity) -> str | None:
        path = self.cache_dir / f"{identity.key()}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            return None
        if data.get("identity") != identity.to_dict():
            return None
        content = data.get("response_content")
        if not isinstance(content, str) or not content.strip():
            return None
        expected_sha = data.get("response_content_sha256")
        actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if expected_sha != actual_sha:
            return None
        return content

    def put_validated(
        self,
        identity: QwenCacheIdentity,
        *,
        content: str,
        validated_response: dict[str, Any],
    ) -> Path:
        """Atomically store content after its task schema has been validated.

        ``validated_response`` is deliberately required and must be a JSON
        object.  It is not persisted; its presence prevents the transport
        layer from accidentally caching an unchecked response.
        """
        if not isinstance(validated_response, dict):
            raise TypeError("validated_response must be a JSON object")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("validated response content must not be empty")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{identity.key()}.json"
        record = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity.to_dict(),
            "response_content": content,
            "response_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{identity.key()}.",
            suffix=".tmp",
            dir=self.cache_dir,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return target


def default_qwen_cache_dir() -> Path:
    """Return an OS user-data cache path that is never repository-relative."""
    if os.name == "nt":
        base_value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(base_value) if base_value else Path.home() / "AppData" / "Local"
        return base / "Betguard Assistant" / "vision" / "qwen-dashscope-cache"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "betguard-assistant" / "vision" / "qwen-dashscope-cache"
