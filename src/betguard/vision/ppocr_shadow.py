"""Optional PP-OCRv6 evidence-only shadow adapter.

Paddle is deliberately invoked in an isolated interpreter.  This module uses
only the Python standard library and is safe to import in the Betguard runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from betguard.vision.contracts import RecognitionRequest


PROVIDER_ID = "ppocrv6-shadow"
MODEL_NAME = "PP-OCRv6_medium"
MODEL_VERSION = "PP-OCRv6_medium_det+PP-OCRv6_medium_rec"
PROVIDER_VERSION = "paddleocr-3.7.0+paddlepaddle-gpu-3.3.0"
ADAPTER_VERSION = "betguard.ppocr-shadow.v1"
WORKER_SCHEMA_VERSION = "betguard.vision.ppocr-worker.v1"
CACHE_SCHEMA_VERSION = "betguard.vision.ppocr-shadow-cache.v1"
EVIDENCE_SCHEMA_VERSION = "betguard.vision.ppocr-shadow-evidence.v1"
ENABLED_ENV = "BETGUARD_PPOCR_SHADOW_ENABLED"
PYTHON_ENV = "BETGUARD_PPOCR_SHADOW_PYTHON"
TIMEOUT_ENV = "BETGUARD_PPOCR_SHADOW_TIMEOUT_SECONDS"
DEVICE_ENV = "BETGUARD_PPOCR_SHADOW_DEVICE"
MODEL_CACHE_ENV = "BETGUARD_PPOCR_SHADOW_MODEL_CACHE"


@dataclass(frozen=True)
class PPShadowConfig:
    enabled: bool
    python_executable: Path
    worker_script: Path
    timeout_seconds: float
    device: str
    model_cache: Path
    cache_dir: Path
    model: str = MODEL_NAME
    provider_version: str = PROVIDER_VERSION
    adapter_version: str = ADAPTER_VERSION

    @property
    def configured(self) -> bool:
        return (
            self.python_executable.is_file()
            and self.worker_script.is_file()
            and self.detection_model_dir.is_dir()
            and self.recognition_model_dir.is_dir()
        )

    @property
    def detection_model_dir(self) -> Path:
        return self.model_cache / "official_models" / "PP-OCRv6_medium_det"

    @property
    def recognition_model_dir(self) -> Path:
        return self.model_cache / "official_models" / "PP-OCRv6_medium_rec"


@dataclass(frozen=True)
class PPShadowCacheIdentity:
    image_sha256: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    adapter_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "image_sha256": self.image_sha256,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "model": self.model,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
        }

    def key(self) -> str:
        material = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


class PPShadowCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or default_ppocr_shadow_cache_dir()

    def get(self, identity: PPShadowCacheIdentity) -> dict[str, Any] | None:
        path = self.cache_dir / f"{identity.key()}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            return None
        if record.get("identity") != identity.to_dict():
            return None
        evidence = record.get("evidence")
        expected_sha = record.get("evidence_sha256")
        if not isinstance(evidence, dict):
            return None
        encoded = _canonical_json(evidence)
        if hashlib.sha256(encoded).hexdigest() != expected_sha:
            return None
        try:
            return validate_worker_response(evidence)
        except ValueError:
            return None

    def put_validated(
        self,
        identity: PPShadowCacheIdentity,
        evidence: dict[str, Any],
    ) -> Path:
        validated = validate_worker_response(evidence)
        if validated.get("status") != "completed":
            raise ValueError("only completed PP-OCR evidence may be cached")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{identity.key()}.json"
        encoded_evidence = _canonical_json(validated)
        record = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity.to_dict(),
            "evidence": validated,
            "evidence_sha256": hashlib.sha256(encoded_evidence).hexdigest(),
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{identity.key()}.", suffix=".tmp", dir=self.cache_dir
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


def get_ppocr_shadow_config() -> PPShadowConfig:
    repo_root = Path(__file__).resolve().parents[3]
    python_value = os.environ.get(
        PYTHON_ENV, r"C:\BetguardOCRBench\venv\Scripts\python.exe"
    )
    timeout_value = os.environ.get(TIMEOUT_ENV, "30")
    try:
        timeout_seconds = float(timeout_value)
    except ValueError:
        timeout_seconds = 30.0
    timeout_seconds = min(max(timeout_seconds, 1.0), 300.0)
    return PPShadowConfig(
        enabled=os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"},
        python_executable=Path(python_value),
        worker_script=repo_root / "tools" / "vision" / "ppocr_shadow_worker.py",
        timeout_seconds=timeout_seconds,
        device=os.environ.get(DEVICE_ENV, "gpu:0").strip() or "gpu:0",
        model_cache=Path(os.environ.get(MODEL_CACHE_ENV, str(_default_model_cache()))),
        cache_dir=default_ppocr_shadow_cache_dir(),
    )


def run_ppocr_shadow(
    request: RecognitionRequest,
    *,
    config: PPShadowConfig | None = None,
    cache: PPShadowCache | None = None,
) -> dict[str, Any]:
    """Return optional evidence; failures are values and never exceptions."""
    config = config or get_ppocr_shadow_config()
    started = time.perf_counter()
    base = _base_evidence(request, config)
    if not config.enabled:
        return {**base, "status": "disabled", "error": {"code": "PPOCR_SHADOW_DISABLED"}, "latency_ms": 0.0}
    identity = PPShadowCacheIdentity(
        image_sha256=str(request.metadata.get("sha256") or ""),
        provider=PROVIDER_ID,
        provider_version=config.provider_version,
        model=config.model,
        model_version=MODEL_VERSION,
        adapter_version=config.adapter_version,
    )
    cache = cache or PPShadowCache(config.cache_dir)
    cached = cache.get(identity)
    if cached is not None:
        return {
            **base,
            "status": "completed",
            "regions": cached["regions"],
            "cache_hit": True,
            "cache_identity": identity.to_dict(),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    if not config.configured:
        return {
            **base,
            "status": "unavailable",
            "error": {"code": "PPOCR_SHADOW_NOT_CONFIGURED"},
            "cache_hit": False,
            "cache_identity": identity.to_dict(),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    try:
        with tempfile.TemporaryDirectory(prefix="betguard-ppocr-shadow-") as temp_dir:
            output_path = Path(temp_dir) / "result.json"
            command = [
                str(config.python_executable),
                str(config.worker_script),
                "--image",
                request.image_path,
                "--output",
                str(output_path),
                "--device",
                config.device,
                "--model-cache",
                str(config.model_cache),
                "--det-model-dir",
                str(config.detection_model_dir),
                "--rec-model-dir",
                str(config.recognition_model_dir),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                check=False,
                shell=False,
                cwd=str(config.worker_script.parents[2]),
                env=_sanitized_subprocess_env(config),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                return _failure(
                    base,
                    "PPOCR_SUBPROCESS_FAILED",
                    started,
                    detail=_safe_process_detail(completed.stderr),
                )
            try:
                raw = json.loads(output_path.read_text(encoding="utf-8"))
                validated = validate_worker_response(raw)
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return _failure(base, "PPOCR_INVALID_RESPONSE", started, detail=type(exc).__name__)
    except subprocess.TimeoutExpired:
        return _failure(base, "PPOCR_TIMEOUT", started)
    except (OSError, ValueError) as exc:
        return _failure(base, "PPOCR_INVOCATION_FAILED", started, detail=type(exc).__name__)

    try:
        cache.put_validated(identity, validated)
    except OSError:
        # Cache is optional; valid evidence remains available for this request.
        pass
    return {
        **base,
        "status": "completed",
        "regions": validated["regions"],
        "cache_hit": False,
        "cache_identity": identity.to_dict(),
        "worker_latency_ms": validated.get("latency_ms"),
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def validate_worker_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != WORKER_SCHEMA_VERSION:
        raise ValueError("invalid PP-OCR worker schema")
    if value.get("status") != "completed" or not isinstance(value.get("regions"), list):
        raise ValueError("PP-OCR worker result is not a completed region list")
    regions: list[dict[str, Any]] = []
    for index, item in enumerate(value["regions"]):
        if not isinstance(item, dict):
            raise ValueError("PP-OCR region must be an object")
        text = item.get("text")
        confidence = item.get("confidence")
        polygon = item.get("polygon")
        bbox = item.get("bbox")
        order = item.get("order")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("PP-OCR region text must be non-empty")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("PP-OCR confidence must be numeric")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("PP-OCR confidence is out of range")
        validated_bbox = _validated_bbox(bbox)
        validated_polygon = _validated_polygon(polygon)
        polygon_bbox = [
            min(point[0] for point in validated_polygon),
            min(point[1] for point in validated_polygon),
            max(point[0] for point in validated_polygon),
            max(point[1] for point in validated_polygon),
        ]
        if any(abs(left - right) > 0.001 for left, right in zip(validated_bbox, polygon_bbox)):
            raise ValueError("PP-OCR bbox must enclose the supplied polygon")
        if not isinstance(order, int) or isinstance(order, bool) or order != index + 1:
            raise ValueError("PP-OCR order must be contiguous and match region order")
        regions.append({
            "evidence_id": str(item.get("evidence_id") or f"PP-{index + 1:04d}"),
            "text": text,
            "confidence": confidence,
            "polygon": validated_polygon,
            "bbox": validated_bbox,
            "order": order,
            "provider": PROVIDER_ID,
            "model": MODEL_NAME,
        })
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "status": "completed",
        "regions": regions,
        "latency_ms": value.get("latency_ms"),
    }


def default_ppocr_shadow_cache_dir() -> Path:
    if os.name == "nt":
        base_value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(base_value) if base_value else Path.home() / "AppData" / "Local"
        return base / "Betguard Assistant" / "vision" / "ppocrv6-shadow-cache"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "betguard-assistant" / "vision" / "ppocrv6-shadow-cache"


def _default_model_cache() -> Path:
    candidates = [
        Path(r"C:\BetguardOCRBench\model-cache"),
        Path.home() / ".paddlex",
    ]
    for candidate in candidates:
        if (
            (candidate / "official_models" / "PP-OCRv6_medium_det").is_dir()
            and (candidate / "official_models" / "PP-OCRv6_medium_rec").is_dir()
        ):
            return candidate
    # Fail-closed ``configured`` stays false; the worker is never allowed to
    # download missing production-shadow models implicitly.
    return candidates[0]


def _base_evidence(request: RecognitionRequest, config: PPShadowConfig) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "provider": {
            "id": PROVIDER_ID,
            "mode": "local_subprocess_shadow",
            "model_name": config.model,
            "model_version": MODEL_VERSION,
            "provider_version": config.provider_version,
            "adapter_version": config.adapter_version,
        },
        "image_sha256": str(request.metadata.get("sha256") or ""),
        "evidence_only": True,
        "authority": "qwen-dashscope",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _failure(
    base: dict[str, Any],
    code: str,
    started: float,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    error = {"code": code}
    if detail:
        error["detail"] = detail
    return {
        **base,
        "status": "failed" if code != "PPOCR_TIMEOUT" else "timeout",
        "error": error,
        "cache_hit": False,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _validated_bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("PP-OCR bbox must have four values")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError("PP-OCR bbox values must be numeric")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError("PP-OCR bbox values must be finite")
    if result[0] < 0 or result[1] < 0 or result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("PP-OCR bbox must be positive and ordered")
    return result


def _validated_polygon(value: Any) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("PP-OCR polygon must have at least three points")
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("PP-OCR polygon points must have two values")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in point):
            raise ValueError("PP-OCR polygon values must be numeric")
        converted = [float(point[0]), float(point[1])]
        if not all(math.isfinite(item) and item >= 0.0 for item in converted):
            raise ValueError("PP-OCR polygon values must be finite and non-negative")
        result.append(converted)
    return result


def _sanitized_subprocess_env(config: PPShadowConfig) -> dict[str, str]:
    blocked = ("API_KEY", "AUTHORIZATION", "DASHSCOPE", "OPENAI")
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in blocked)
    }
    env["PADDLE_PDX_CACHE_HOME"] = str(config.model_cache)
    env["PYTHONUTF8"] = "1"
    return env


def _safe_process_detail(stderr: str | None) -> str | None:
    if not stderr:
        return None
    # A bounded diagnostic only; no command/environment/request body is exposed.
    return stderr.strip()[-500:]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
