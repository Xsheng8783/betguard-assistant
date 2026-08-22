"""Human-verified image-to-text acceptance samples.

The image reader remains a transcription aid.  This module only records an
explicitly saved, parser-valid correction as future acceptance truth.  Machine
transcriptions are captured separately so edited browser text can never be
mistaken for the original prediction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from betguard.user_data import get_runs_dir
from betguard.vision.image_intake import get_metadata, read_image_data


DATASET_SCHEMA_VERSION = "betguard-image-text-acceptance-dataset-v1"
SAMPLE_SCHEMA_VERSION = "betguard-image-text-verified-sample-v1"
CAPTURE_SCHEMA_VERSION = "betguard-image-text-machine-capture-v1"
DATASET_TARGET = 10

_DATASET_DIR_NAME = "image-to-text-acceptance-dataset-v1"
_CAPTURE_DIR_NAME = "image-text-transcription-captures-v1"
_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STORAGE_LOCK = threading.RLock()
_MIGRATED_HUMAN_AUTHORITY_KINDS = {
    "canonical_human_truth_promotion",
    "explicit_human_final_confirmation",
    "frozen_human_ground_truth_v1",
    "manifest_reviewed_existing",
    "persisted_complete_human_review",
}


def default_dataset_root() -> Path:
    return Path(get_runs_dir()) / _DATASET_DIR_NAME


def default_capture_root() -> Path:
    return Path(get_runs_dir()) / _CAPTURE_DIR_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write(path, payload + b"\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _capture_path(image_id: str, capture_root: Path) -> Path:
    normalized = str(image_id or "").strip().lower()
    if not _IMAGE_ID_RE.fullmatch(normalized):
        raise ValueError("INVALID_IMAGE_ID")
    return capture_root / f"{normalized}.json"


def record_machine_transcription(
    image_id: str,
    *,
    source_image_sha256: str,
    ai_original_text: str,
    reader: str,
    model_cache_identity: dict[str, Any] | None = None,
    capture_root: Path | None = None,
) -> dict[str, Any]:
    """Capture original AI text before the editable value reaches the browser."""
    sha256 = str(source_image_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError("INVALID_IMAGE_SHA256")
    text = str(ai_original_text or "")
    if not text.strip():
        raise ValueError("AI_ORIGINAL_TEXT_EMPTY")
    root = Path(capture_root) if capture_root is not None else default_capture_root()
    captured = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "image_id": str(image_id).lower(),
        "source_image_sha256": sha256,
        "ai_original_text": text,
        "reader": str(reader or ""),
        "model_cache_identity": dict(model_cache_identity or {}),
        "prediction_timestamp": _utc_now(),
    }
    with _STORAGE_LOCK:
        _atomic_write_json(_capture_path(image_id, root), captured)
    return captured


def _verified_latest_records(dataset_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    samples_root = dataset_root / "samples"
    if not samples_root.exists():
        return records
    for latest_path in samples_root.glob("*/latest.json"):
        record = _read_json(latest_path)
        if not record or record.get("human_verified") is not True:
            continue
        sha256 = str(record.get("source_image_sha256") or "").lower()
        if not _SHA256_RE.fullmatch(sha256) or latest_path.parent.name != sha256:
            continue
        records[sha256] = record
    return records


def get_dataset_status(dataset_root: Path | None = None) -> dict[str, Any]:
    root = Path(dataset_root) if dataset_root is not None else default_dataset_root()
    with _STORAGE_LOCK:
        unique_count = len(_verified_latest_records(root))
    return {
        "ok": True,
        "schema_version": DATASET_SCHEMA_VERSION,
        "unique_human_verified_images": unique_count,
        "target": DATASET_TARGET,
        "acceptance_dataset_ready": unique_count >= DATASET_TARGET,
    }


def _parser_errors(queue: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    preprocessing = queue.get("preprocessing", {})
    for fragment in preprocessing.get("invalid_fragments", []):
        messages = [
            str(message)
            for message in list(fragment.get("errors", []))
            + list(fragment.get("warnings", []))
            if str(message).strip()
        ]
        errors.append(
            {
                "fragment": str(fragment.get("raw") or ""),
                "messages": messages or ["existing parser requires review"],
            }
        )
    for ignored in preprocessing.get("ignored_metadata_lines", []):
        errors.append(
            {
                "fragment": str(ignored.get("raw") or ""),
                "messages": ["existing parser ignored this line"],
            }
        )
    for message in queue.get("errors", []):
        errors.append({"fragment": "", "messages": [str(message)]})
    return errors


def validate_with_existing_parser(text: str) -> dict[str, Any]:
    """Require every non-empty fragment to parse through the existing path."""
    from betguard.webfill.batch_mock_queue import READY_FOR_QUEUE, build_batch_mock_queue

    normalized_text = str(text or "").strip()
    if not normalized_text:
        return {
            "ok": False,
            "error": {"code": "VERIFIED_TEXT_EMPTY", "message": "正確文字不可為空白。"},
            "parser_errors": [],
        }
    try:
        queue = build_batch_mock_queue(normalized_text, game="六合")
    except Exception:
        return {
            "ok": False,
            "error": {
                "code": "VERIFIED_TEXT_PARSER_FAILED",
                "message": "文字無法由現有 Betguard parser 完整解析。",
            },
            "parser_errors": [],
        }

    preprocessing = queue.get("preprocessing", {})
    summary = preprocessing.get("summary", {})
    candidates = list(preprocessing.get("valid_candidates", []))
    invalid = list(preprocessing.get("invalid_fragments", []))
    ignored = list(preprocessing.get("ignored_metadata_lines", []))
    candidate_count = int(summary.get("candidate_count") or 0)
    full_success = (
        candidate_count > 0
        and len(candidates) == candidate_count
        and not invalid
        and not ignored
        and preprocessing.get("status") == "READY"
        and queue.get("status") == READY_FOR_QUEUE
    )
    if not full_success:
        return {
            "ok": False,
            "error": {
                "code": "VERIFIED_TEXT_PARSER_FAILED",
                "message": "文字尚未完整解析，請依 parser 提示修改後再保存。",
            },
            "parser_errors": _parser_errors(queue),
        }

    normalized_bets = []
    for candidate in candidates:
        normalized_bets.append(
            {
                "index": candidate.get("index"),
                "raw": candidate.get("raw"),
                "original_line": candidate.get("original_line"),
                "original_lines": list(candidate.get("original_lines") or []),
                "preprocessing_notes": list(candidate.get("preprocessing_notes") or []),
                "summary": candidate.get("summary"),
                "result": candidate.get("result"),
            }
        )
    return {
        "ok": True,
        "parser_normalized_result": {
            "schema_version": "betguard-existing-parser-normalized-v1",
            "game": "六合",
            "candidate_count": candidate_count,
            "bets": normalized_bets,
        },
    }


def _extension_for_mime(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(str(mime_type), ".bin")


def _next_revision_number(revisions_root: Path) -> int:
    highest = 0
    for path in revisions_root.glob("revision-*.json"):
        match = re.match(r"revision-(\d{6})\.json$", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _write_index(dataset_root: Path) -> dict[str, Any]:
    records = _verified_latest_records(dataset_root)
    entries = []
    for sha256, record in sorted(records.items()):
        entries.append(
            {
                "source_image_sha256": sha256,
                "latest_revision_id": record.get("revision_id"),
                "latest_revision_number": record.get("revision_number"),
                "verified_at": record.get("verified_at"),
                "human_verified": True,
            }
        )
    index = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "unique_human_verified_images": len(entries),
        "target": DATASET_TARGET,
        "acceptance_dataset_ready": len(entries) >= DATASET_TARGET,
        "updated_at": _utc_now(),
        "samples": entries,
    }
    _atomic_write_json(dataset_root / "index.json", index)
    return index


def save_human_verified_sample(
    image_id: str,
    human_verified_betguard_text: str,
    *,
    dataset_root: Path | None = None,
    capture_root: Path | None = None,
) -> dict[str, Any]:
    """Save an explicit human correction after strict existing-parser validation."""
    validation = validate_with_existing_parser(human_verified_betguard_text)
    if not validation.get("ok"):
        return validation

    dataset = Path(dataset_root) if dataset_root is not None else default_dataset_root()
    captures = Path(capture_root) if capture_root is not None else default_capture_root()
    try:
        capture_path = _capture_path(image_id, captures)
    except ValueError:
        return {
            "ok": False,
            "error": {"code": "INVALID_IMAGE_ID", "message": "圖片識別碼無效。"},
        }
    capture = _read_json(capture_path)
    if not capture:
        return {
            "ok": False,
            "error": {
                "code": "AI_ORIGINAL_TEXT_NOT_CAPTURED",
                "message": "請先完成 AI 辨識，再保存正確範例。",
            },
        }

    metadata = get_metadata(image_id)
    image_data = read_image_data(image_id)
    if metadata is None or metadata.is_expired() or image_data is None:
        return {
            "ok": False,
            "error": {"code": "IMAGE_NOT_FOUND", "message": "圖片不存在或已過期。"},
        }
    actual_sha256 = hashlib.sha256(image_data).hexdigest()
    expected_sha256 = str(capture.get("source_image_sha256") or "").lower()
    if actual_sha256 != expected_sha256 or actual_sha256 != str(metadata.sha256).lower():
        return {
            "ok": False,
            "error": {"code": "IMAGE_IDENTITY_MISMATCH", "message": "圖片識別資料不一致。"},
        }

    with _STORAGE_LOCK:
        extension = _extension_for_mime(metadata.mime_type)
        stored_image = dataset / "images" / f"{actual_sha256}{extension}"
        if stored_image.exists():
            if hashlib.sha256(stored_image.read_bytes()).hexdigest() != actual_sha256:
                return {
                    "ok": False,
                    "error": {"code": "DATASET_IMAGE_CONFLICT", "message": "已保存圖片內容衝突。"},
                }
        else:
            _atomic_write(stored_image, image_data)

        sample_root = dataset / "samples" / actual_sha256
        revisions_root = sample_root / "revisions"
        revision_number = _next_revision_number(revisions_root)
        revision_id = f"revision-{revision_number:06d}"
        verified_at = _utc_now()
        sample = {
            "schema_version": SAMPLE_SCHEMA_VERSION,
            "sample_id": f"verified-image-{actual_sha256[:16]}",
            "revision_id": revision_id,
            "revision_number": revision_number,
            "source_image_sha256": actual_sha256,
            "source_image_reference": stored_image.relative_to(dataset).as_posix(),
            "source_image_mime_type": metadata.mime_type,
            "source_image_original_filename": str(
                getattr(metadata, "original_filename", "") or ""
            ),
            "ai_original_text": str(capture.get("ai_original_text") or ""),
            "ai_original_text_available": True,
            "human_verified_betguard_text": str(human_verified_betguard_text).strip(),
            "parser_normalized_result": validation["parser_normalized_result"],
            "verified_at": verified_at,
            "human_verified": True,
            "verification_source": "explicit_save_button",
        }
        _atomic_write_json(revisions_root / f"{revision_id}.json", sample)
        _atomic_write_json(sample_root / "latest.json", sample)
        index = _write_index(dataset)

    return {
        "ok": True,
        "sample": {
            "source_image_sha256": actual_sha256,
            "revision_id": revision_id,
            "revision_number": revision_number,
            "human_verified": True,
        },
        "dataset_status": {
            "unique_human_verified_images": index["unique_human_verified_images"],
            "target": index["target"],
            "acceptance_dataset_ready": index["acceptance_dataset_ready"],
        },
    }


def save_migrated_human_verified_sample(
    source_image_path: str | Path,
    human_verified_betguard_text: str,
    *,
    expected_image_sha256: str,
    source_sample_id: str,
    source_truth_provenance: dict[str, Any],
    source_semantic_result: dict[str, Any],
    semantic_round_trip: dict[str, Any],
    ai_original_text: str | None = None,
    dataset_root: Path | None = None,
) -> dict[str, Any]:
    """Save a parser-exact migration from an existing human truth source.

    This is deliberately separate from the UI save path.  A migration must
    carry a successful semantic round-trip proof and an independently checked
    image identity; model output alone can never enter this path.
    """
    if not isinstance(semantic_round_trip, dict) or semantic_round_trip.get("exact") is not True:
        return {
            "ok": False,
            "error": {
                "code": "MIGRATED_TRUTH_ROUND_TRIP_NOT_EXACT",
                "message": "既有 Human Truth 未通過語意 round-trip，不可匯入。",
            },
        }
    authority_kind = (
        str(source_truth_provenance.get("authority_kind") or "")
        if isinstance(source_truth_provenance, dict)
        else ""
    )
    if authority_kind not in _MIGRATED_HUMAN_AUTHORITY_KINDS:
        return {
            "ok": False,
            "error": {
                "code": "MIGRATED_TRUTH_PROVENANCE_REQUIRED",
                "message": "既有 Human Truth 缺少可稽核來源。",
            },
        }

    validation = validate_with_existing_parser(human_verified_betguard_text)
    if not validation.get("ok"):
        return validation
    if validation.get("parser_normalized_result") != semantic_round_trip.get(
        "parser_normalized_result"
    ):
        return {
            "ok": False,
            "error": {
                "code": "MIGRATED_TRUTH_ROUND_TRIP_STALE",
                "message": "parser 結果與 round-trip seal 不一致。",
            },
        }
    if semantic_round_trip.get("expected_semantic_sha256") != _canonical_json_sha256(
        source_semantic_result
    ) or semantic_round_trip.get("parser_normalized_sha256") != _canonical_json_sha256(
        validation["parser_normalized_result"]
    ):
        return {
            "ok": False,
            "error": {
                "code": "MIGRATED_TRUTH_ROUND_TRIP_STALE",
                "message": "Human Truth 或 parser seal 已失效。",
            },
        }

    expected_sha256 = str(expected_image_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected_sha256):
        return {
            "ok": False,
            "error": {"code": "INVALID_IMAGE_SHA256", "message": "來源圖片 SHA-256 無效。"},
        }
    image_path = Path(source_image_path)
    try:
        image_data = image_path.read_bytes()
    except OSError:
        return {
            "ok": False,
            "error": {"code": "IMAGE_NOT_FOUND", "message": "來源圖片不存在。"},
        }
    actual_sha256 = hashlib.sha256(image_data).hexdigest()
    if actual_sha256 != expected_sha256:
        return {
            "ok": False,
            "error": {"code": "IMAGE_IDENTITY_MISMATCH", "message": "來源圖片 SHA-256 不一致。"},
        }

    suffix = image_path.suffix.lower()
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    dataset = Path(dataset_root) if dataset_root is not None else default_dataset_root()
    ai_text = str(ai_original_text or "")

    with _STORAGE_LOCK:
        extension = _extension_for_mime(mime_type)
        stored_image = dataset / "images" / f"{actual_sha256}{extension}"
        if stored_image.exists():
            if hashlib.sha256(stored_image.read_bytes()).hexdigest() != actual_sha256:
                return {
                    "ok": False,
                    "error": {"code": "DATASET_IMAGE_CONFLICT", "message": "已保存圖片內容衝突。"},
                }
        else:
            _atomic_write(stored_image, image_data)

        sample_root = dataset / "samples" / actual_sha256
        revisions_root = sample_root / "revisions"
        revision_number = _next_revision_number(revisions_root)
        revision_id = f"revision-{revision_number:06d}"
        verified_at = _utc_now()
        sample = {
            "schema_version": SAMPLE_SCHEMA_VERSION,
            "sample_id": f"verified-image-{actual_sha256[:16]}",
            "source_sample_id": str(source_sample_id),
            "revision_id": revision_id,
            "revision_number": revision_number,
            "source_image_sha256": actual_sha256,
            "source_image_reference": stored_image.relative_to(dataset).as_posix(),
            "source_image_mime_type": mime_type,
            "source_image_original_filename": image_path.name,
            "ai_original_text": ai_text,
            "ai_original_text_available": bool(ai_text.strip()),
            "human_verified_betguard_text": str(human_verified_betguard_text).strip(),
            "parser_normalized_result": validation["parser_normalized_result"],
            "source_semantic_result": source_semantic_result,
            "semantic_round_trip": semantic_round_trip,
            "verified_at": verified_at,
            "human_verified": True,
            "verification_source": "migrated_existing_human_truth",
            "source_truth_provenance": source_truth_provenance,
        }
        _atomic_write_json(revisions_root / f"{revision_id}.json", sample)
        _atomic_write_json(sample_root / "latest.json", sample)
        index = _write_index(dataset)

    return {
        "ok": True,
        "sample": {
            "source_image_sha256": actual_sha256,
            "source_sample_id": str(source_sample_id),
            "revision_id": revision_id,
            "revision_number": revision_number,
            "human_verified": True,
        },
        "dataset_status": {
            "unique_human_verified_images": index["unique_human_verified_images"],
            "target": index["target"],
            "acceptance_dataset_ready": index["acceptance_dataset_ready"],
        },
    }
