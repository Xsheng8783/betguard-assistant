"""PaddleOCR subprocess provider — runs OCR via external Python worker process.

Does NOT import paddleocr, paddle, cv2, or numpy.
Uses stdin/stdout JSON protocol: betguard.vision.worker.v1
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from betguard.vision.contracts import (
    SCHEMA_VERSION,
    BoundingBox,
    Confidence,
    ConfidenceLevel,
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
    Token,
)
from betguard.vision.image_intake import ImageMetadata
from betguard.vision.worker_protocol import (
    WORKER_PROTOCOL_VERSION,
    WorkerResponse,
    sort_ocr_items,
)


PROVIDER_ID = "paddleocr-local"
DEFAULT_TIMEOUT = 180
MAX_STDOUT = 5 * 1024 * 1024
MAX_STDERR = 1 * 1024 * 1024

# ── Error mapping ────────────────────────────────────────────────────────────


_ERROR_CODES = {
    "OCR_PYTHON_NOT_CONFIGURED",
    "OCR_PYTHON_NOT_FOUND",
    "OCR_WORKER_NOT_FOUND",
    "OCR_REQUEST_INVALID",
    "OCR_PROCESS_TIMEOUT",
    "OCR_PROCESS_FAILED",
    "OCR_OUTPUT_TOO_LARGE",
    "OCR_OUTPUT_INVALID",
    "OCR_PROTOCOL_MISMATCH",
    "OCR_REQUEST_ID_MISMATCH",
    "OCR_ENGINE_UNAVAILABLE",
    "OCR_MODEL_INIT_FAILED",
    "OCR_INFERENCE_FAILED",
}


def _ocr_python() -> str:
    """Get OCR worker Python path from env var."""
    path = os.environ.get("BETGUARD_OCR_PYTHON", "")
    if not path:
        raise RuntimeError("OCR_PYTHON_NOT_CONFIGURED")
    if not os.path.isfile(path):
        raise RuntimeError("OCR_PYTHON_NOT_FOUND")
    return path


def _worker_path() -> Path:
    """Get absolute path to paddleocr_worker.py."""
    # tools/vision/paddleocr_worker.py relative to project root
    candidates = [
        Path(__file__).resolve().parents[3] / "tools" / "vision" / "paddleocr_worker.py",
        Path("tools/vision/paddleocr_worker.py").resolve(),
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise RuntimeError("OCR_WORKER_NOT_FOUND")


def _run_worker(request_json: str, timeout: int = DEFAULT_TIMEOUT) -> WorkerResponse:
    """Execute worker subprocess and parse response."""
    python_exe = _ocr_python()
    worker = str(_worker_path())

    # Build env with UTF-8 enforcement for Windows
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:backslashreplace"

    try:
        proc = subprocess.run(
            [python_exe, worker],
            input=request_json.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("OCR_PROCESS_TIMEOUT")
    except FileNotFoundError:
        raise RuntimeError("OCR_PYTHON_NOT_FOUND")
    except Exception:
        raise RuntimeError("OCR_PROCESS_FAILED")

    stdout_bytes = proc.stdout or b""
    stderr_bytes = proc.stderr or b""

    # Size checks on raw bytes
    if len(stdout_bytes) > MAX_STDOUT:
        raise RuntimeError("OCR_OUTPUT_TOO_LARGE")
    if len(stderr_bytes) > MAX_STDERR:
        stderr_bytes = stderr_bytes[:MAX_STDERR]

    # Decode stderr with replace for diagnostics (never exposed)
    _stderr_text = stderr_bytes.decode("utf-8", errors="replace")[:500]

    # Check exit code
    if proc.returncode != 0:
        raise RuntimeError("OCR_PROCESS_FAILED")

    # Decode stdout strictly — must be valid UTF-8 JSON
    try:
        stdout = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("OCR_OUTPUT_INVALID")

    # Parse JSON
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError("OCR_OUTPUT_INVALID")

    if not isinstance(data, dict):
        raise RuntimeError("OCR_OUTPUT_INVALID")

    # Check ok
    if not data.get("ok", False):
        err = data.get("error", {})
        code = err.get("code", "OCR_PROCESS_FAILED")
        raise RuntimeError(code)

    # Validate protocol
    if data.get("protocol_version") != WORKER_PROTOCOL_VERSION:
        raise RuntimeError("OCR_PROTOCOL_MISMATCH")

    # Parse response
    try:
        response = WorkerResponse.from_dict(data)
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("OCR_OUTPUT_INVALID")

    return response


# ── Conversion to RecognitionResult ──────────────────────────────────────────


def _score_to_confidence(score: float) -> Confidence:
    """Map OCR score (0-1) to ConfidenceLevel."""
    if score >= 0.8:
        level = ConfidenceLevel.HIGH
    elif score >= 0.5:
        level = ConfidenceLevel.MEDIUM
    elif score >= 0.2:
        level = ConfidenceLevel.LOW
    else:
        level = ConfidenceLevel.UNKNOWN
    return Confidence(value=score, source="paddleocr", calibrated=False, level=level)


def _items_to_lines(items: list, image_meta: ImageMetadata | None = None) -> list[Line]:
    """Convert sorted WorkerOcrItems to Line list."""
    lines = []
    for i, item in enumerate(items):
        tokens = []
        if item.text:
            tokens.append(Token(
                token_id=f"tok-{i:04d}",
                text=item.text,
                start=0,
                end=len(item.text),
                confidence=_score_to_confidence(item.score),
            ))

        bbox = None
        if item.polygon:
            bbox = BoundingBox(
                coordinate_space="pixel",
                polygon=item.polygon,
            )

        confidence = _score_to_confidence(item.score)
        warnings: list[str] = []
        if item.score < 0.5:
            warnings.append(f"Low confidence: {item.text} (score={item.score:.3f})")

        lines.append(Line(
            line_id=f"line-{i:04d}",
            order=i + 1,
            text=item.text,
            confidence=confidence,
            bounding_box=bbox,
            tokens=tokens,
            warnings=warnings,
        ))
    return lines


def recognize_with_metadata(
    image_meta: ImageMetadata,
    request_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> RecognitionResult:
    """Run PaddleOCR on a stored image via subprocess worker.

    Args:
        image_meta: ImageMetadata from image_intake
        request_id: Request ID for tracing
        timeout: Subprocess timeout in seconds

    Returns:
        RecognitionResult (status=COMPLETED or FAILED)
    """
    from betguard.vision.errors import ProviderError

    if not request_id:
        request_id = f"req-{uuid.uuid4().hex[:8]}"

    request_payload = {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "request_id": request_id,
        "image_path": str(image_meta.storage_path),
        "options": {
            "device": "cpu",
            "text_detection_model_name": "PP-OCRv5_mobile_det",
            "text_recognition_model_name": "PP-OCRv5_mobile_rec",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "cpu_threads": 4,
        },
    }

    try:
        request_json = json.dumps(request_payload, ensure_ascii=False)
        response = _run_worker(request_json, timeout=timeout)
    except RuntimeError as e:
        code = str(e)
        if code not in _ERROR_CODES:
            code = "OCR_PROCESS_FAILED"
        return _build_failed_result(
            request_id=request_id,
            image_meta=image_meta,
            error_code=code,
            error_message=str(e),
        )

    # Verify request_id match
    if response.request_id != request_id:
        return _build_failed_result(
            request_id=request_id,
            image_meta=image_meta,
            error_code="OCR_REQUEST_ID_MISMATCH",
            error_message=f"Expected {request_id}, got {response.request_id}",
        )

    # Sort items
    sorted_items = sort_ocr_items(response.items)

    # Build raw_text
    raw_text = "\n".join(item.text for item in sorted_items)

    # Build lines
    lines = _items_to_lines(sorted_items, image_meta)

    return RecognitionResult(
        schema_version=SCHEMA_VERSION,
        recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(
            id=PROVIDER_ID,
            mode="real",
            model_name=f"{response.engine.detection_model}/{response.engine.recognition_model}",
            model_version=f"paddle={response.engine.paddle_version},paddleocr={response.engine.paddleocr_version}",
            adapter_version="1.0.0",
        ),
        source_image=SourceImage(
            image_id=image_meta.image_id,
            sha256=image_meta.sha256,
            mime_type=image_meta.mime_type,
            original_filename=image_meta.original_filename,
            width=image_meta.width,
            height=image_meta.height,
            byte_size=image_meta.byte_size,
        ),
        raw_text=raw_text,
        lines=lines,
        warnings=response.warnings,
        latency_ms=response.elapsed_ms,
    )


def _build_failed_result(
    request_id: str,
    image_meta: ImageMetadata | None = None,
    error_code: str = "OCR_PROCESS_FAILED",
    error_message: str = "",
) -> RecognitionResult:
    """Build a FAILED RecognitionResult."""
    from betguard.vision.errors import ErrorCode, ProviderError

    try:
        err_code = ErrorCode(error_code)
    except ValueError:
        err_code = error_code

    return RecognitionResult(
        schema_version=SCHEMA_VERSION,
        recognition_id=f"rec-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        status=RecognitionStatus.FAILED,
        provider=ProviderMetadata(id=PROVIDER_ID, mode="real"),
        source_image=SourceImage(
            image_id=image_meta.image_id if image_meta else "",
            sha256=image_meta.sha256 if image_meta else "",
            mime_type=image_meta.mime_type if image_meta else "",
            original_filename=image_meta.original_filename if image_meta else "",
            width=image_meta.width if image_meta else 0,
            height=image_meta.height if image_meta else 0,
            byte_size=image_meta.byte_size if image_meta else 0,
        ),
        provider_error=ProviderError(
            code=err_code,
            message=error_message or error_code,
            retryable=error_code in ("OCR_PROCESS_TIMEOUT",),
        ),
    )
