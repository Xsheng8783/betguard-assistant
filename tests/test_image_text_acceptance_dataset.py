"""Human-verified image text is strict, deduplicated acceptance truth."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from betguard.vision import image_text_acceptance as acceptance


IMAGE_ID = "a" * 32
IMAGE_DATA = b"stable-test-image-pixels"
IMAGE_SHA = hashlib.sha256(IMAGE_DATA).hexdigest()


def _metadata() -> SimpleNamespace:
    return SimpleNamespace(
        sha256=IMAGE_SHA,
        mime_type="image/png",
        original_filename="source.png",
        is_expired=lambda: False,
    )


def _capture(capture_root: Path, *, original_text: str = "06.13.23.28 二三50") -> None:
    acceptance.record_machine_transcription(
        IMAGE_ID,
        source_image_sha256=IMAGE_SHA,
        ai_original_text=original_text,
        reader="gemma4-26b-shadow",
        model_cache_identity={"model": "gemma-test", "cache_hit": False},
        capture_root=capture_root,
    )


def _mock_image(monkeypatch) -> None:
    monkeypatch.setattr(acceptance, "get_metadata", lambda _image_id: _metadata())
    monkeypatch.setattr(acceptance, "read_image_data", lambda _image_id: IMAGE_DATA)


def test_ai_text_can_be_corrected_parsed_and_saved_as_verified_truth(
    tmp_path: Path, monkeypatch,
) -> None:
    dataset_root = tmp_path / "dataset"
    capture_root = tmp_path / "captures"
    ai_original = "06.13.23.28 二三50"
    human_corrected = "06.13.23.22 二三50"
    _capture(capture_root, original_text=ai_original)
    _mock_image(monkeypatch)

    result = acceptance.save_human_verified_sample(
        IMAGE_ID,
        human_corrected,
        dataset_root=dataset_root,
        capture_root=capture_root,
    )

    assert result["ok"] is True
    assert result["dataset_status"] == {
        "unique_human_verified_images": 1,
        "target": 10,
        "acceptance_dataset_ready": False,
    }
    revision = json.loads(
        (dataset_root / "samples" / IMAGE_SHA / "revisions" / "revision-000001.json")
        .read_text(encoding="utf-8")
    )
    assert revision["source_image_sha256"] == IMAGE_SHA
    assert revision["source_image_reference"] == f"images/{IMAGE_SHA}.png"
    assert revision["ai_original_text"] == ai_original
    assert revision["human_verified_betguard_text"] == human_corrected
    assert revision["human_verified"] is True
    assert revision["parser_normalized_result"]["candidate_count"] == 1
    parsed = revision["parser_normalized_result"]["bets"][0]["result"]
    assert parsed["numbers"] == [6, 13, 23, 22]
    assert (dataset_root / revision["source_image_reference"]).read_bytes() == IMAGE_DATA


def test_parser_failure_is_never_promoted_to_human_truth(
    tmp_path: Path, monkeypatch,
) -> None:
    dataset_root = tmp_path / "dataset"
    capture_root = tmp_path / "captures"
    _capture(capture_root)
    _mock_image(monkeypatch)

    result = acceptance.save_human_verified_sample(
        IMAGE_ID,
        "這不是可解析投注",
        dataset_root=dataset_root,
        capture_root=capture_root,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VERIFIED_TEXT_PARSER_FAILED"
    assert result["parser_errors"]
    assert not (dataset_root / "samples").exists()


def test_same_image_sha_adds_revision_without_increasing_unique_count(
    tmp_path: Path, monkeypatch,
) -> None:
    dataset_root = tmp_path / "dataset"
    capture_root = tmp_path / "captures"
    _capture(capture_root)
    _mock_image(monkeypatch)

    first = acceptance.save_human_verified_sample(
        IMAGE_ID,
        "06.13.23.22 二三50",
        dataset_root=dataset_root,
        capture_root=capture_root,
    )
    second = acceptance.save_human_verified_sample(
        IMAGE_ID,
        "06.13.23.22 二三100",
        dataset_root=dataset_root,
        capture_root=capture_root,
    )

    assert first["sample"]["revision_number"] == 1
    assert second["sample"]["revision_number"] == 2
    assert second["dataset_status"]["unique_human_verified_images"] == 1
    revision_paths = sorted(
        (dataset_root / "samples" / IMAGE_SHA / "revisions").glob("revision-*.json")
    )
    assert [path.name for path in revision_paths] == [
        "revision-000001.json",
        "revision-000002.json",
    ]
    latest = json.loads(
        (dataset_root / "samples" / IMAGE_SHA / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["revision_number"] == 2
    assert latest["human_verified_betguard_text"] == "06.13.23.22 二三100"
    assert acceptance.get_dataset_status(dataset_root)["unique_human_verified_images"] == 1


def test_status_counts_only_explicit_human_verified_latest_records(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    unverified_sha = "b" * 64
    latest = dataset_root / "samples" / unverified_sha / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps(
            {"source_image_sha256": unverified_sha, "human_verified": False},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = acceptance.get_dataset_status(dataset_root)

    assert status["unique_human_verified_images"] == 0
    assert status["acceptance_dataset_ready"] is False


def test_ten_unique_verified_images_mark_dataset_ready_without_running_benchmark(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "dataset"
    for index in range(10):
        sha256 = f"{index + 1:064x}"
        latest = dataset_root / "samples" / sha256 / "latest.json"
        latest.parent.mkdir(parents=True)
        latest.write_text(
            json.dumps(
                {"source_image_sha256": sha256, "human_verified": True},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    status = acceptance.get_dataset_status(dataset_root)

    assert status["unique_human_verified_images"] == 10
    assert status["acceptance_dataset_ready"] is True
    assert "benchmark" not in status


def test_blank_text_is_rejected_before_any_parser_or_storage_work() -> None:
    result = acceptance.validate_with_existing_parser("  \n  ")

    assert result["ok"] is False
    assert result["error"]["code"] == "VERIFIED_TEXT_EMPTY"
