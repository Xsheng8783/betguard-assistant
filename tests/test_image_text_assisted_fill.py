"""Image transcription remains a typing aid for the existing text flow."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from betguard.vision import image_text_acceptance, service
from betguard.webfill.batch_mock_queue import build_batch_mock_queue
from betguard.webui import app as webui_app


def _metadata(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_path=tmp_path / "input.png",
        mime_type="image/png",
        sha256="a" * 64,
        width=1200,
        height=1600,
        byte_size=42,
        is_expired=lambda: False,
    )


@contextmanager
def _running_app():
    handler = webui_app.build_workbench_handler(
        project_version="image-text-test", git_commit="test"
    )
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_gemma_records_become_plain_text_without_semantic_rewriting() -> None:
    evidence = {
        "status": "completed",
        "items": [
            {"raw_text": "05 × 08 09 23 × 10 20 29\n2,3 × 2"},
            {"raw_text": "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5 尾"},
        ],
    }

    assert service.gemma_evidence_to_betguard_text(evidence) == (
        "05 × 08 09 23 × 10 20 29\n2,3 × 2\n\n"
        "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5 尾"
    )


def test_explicit_transcription_uses_whole_image_gemma_only(
    tmp_path: Path, monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(service, "get_metadata", lambda _image_id: _metadata(tmp_path))

    def fake_run(request, *, config):
        captured["request"] = request
        captured["config"] = config
        return {
            "status": "completed",
            "items": [{"raw_text": "05.08.09 二三各 0.5"}],
            "cache_hit": False,
            "external_call_count": 1,
            "retry_count": 0,
        }

    monkeypatch.setattr(service, "run_gemma_shadow", fake_run)
    monkeypatch.setattr(
        image_text_acceptance,
        "record_machine_transcription",
        lambda image_id, **kwargs: captured.update(
            {"capture_image_id": image_id, "capture": kwargs}
        ),
    )
    result = service.transcribe_image_to_text("image-id")

    assert result["ok"] is True
    assert result["text"] == "05.08.09 二三各 0.5"
    assert result["reader"] == "gemma4-26b-shadow"
    assert result["machine_transcription_only"] is True
    assert result["user_editable"] is True
    assert result["value_authority"] == "existing_text_parser_after_explicit_user_action"
    assert result["external_call_count"] == 1
    assert result["retry_count"] == 0
    assert result["verified_sample_capture_available"] is True
    assert result["auto_apply"] is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False
    assert captured["request"].image_path.endswith("input.png")
    assert captured["config"].enabled is True
    assert captured["capture_image_id"] == "image-id"
    assert captured["capture"]["ai_original_text"] == "05.08.09 二三各 0.5"
    assert captured["capture"]["source_image_sha256"] == "a" * 64


def test_empty_prediction_stays_editable_and_never_auto_applies(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(service, "get_metadata", lambda _image_id: _metadata(tmp_path))
    monkeypatch.setattr(
        service,
        "run_gemma_shadow",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "items": [],
            "external_call_count": 1,
            "retry_count": 0,
        },
    )

    result = service.transcribe_image_to_text("image-id")

    assert result["ok"] is False
    assert result["error"]["code"] == "IMAGE_TRANSCRIPTION_EMPTY"
    assert result["external_call_count"] == 1
    assert result["retry_count"] == 0
    assert result["auto_apply"] is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False


def test_http_transcription_endpoint_returns_only_editable_text(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "transcribe_image_to_text",
        lambda image_id, *, reader="gemma", game="六合": {
            "ok": True,
            "text": "05.08.09 二三50",
            "image_id_seen": image_id,
            "reader_seen": reader,
            "machine_transcription_only": True,
            "user_editable": True,
            "auto_submit": False,
        },
    )

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/vision/v1/transcriptions",
                body=json.dumps({"image_id": "image-1"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert payload == {
        "ok": True,
        "text": "05.08.09 二三50",
        "image_id_seen": "image-1",
        "reader_seen": "gemma",
        "machine_transcription_only": True,
        "user_editable": True,
        "auto_submit": False,
    }


def test_http_transcription_endpoint_forwards_selected_luna_reader(monkeypatch) -> None:
    seen = {}

    def fake_transcription(image_id, *, reader="gemma", game="六合"):
        seen.update({"image_id": image_id, "reader": reader})
        return {
            "ok": True,
            "text": "05 × 08 09 33 × 10 20 39 2,3 × 1",
            "reader": "openai-gpt-5.6-luna-transcription",
            "machine_transcription_only": True,
            "user_editable": True,
            "auto_submit": False,
        }

    monkeypatch.setattr(service, "transcribe_image_to_text", fake_transcription)

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/vision/v1/transcriptions",
                body=json.dumps({"image_id": "image-1", "reader": "luna"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert seen == {"image_id": "image-1", "reader": "luna"}
    assert payload["reader"] == "openai-gpt-5.6-luna-transcription"
    assert payload["auto_submit"] is False


def test_http_parser_preflight_is_preview_only(monkeypatch) -> None:
    seen = {}

    def fake_preflight(text, *, game="六合"):
        seen["text"] = text
        return {
            "ok": True,
            "all_parseable": False,
            "unresolved_count": 1,
            "issues": [{"line_no": 3, "raw": "?尾", "reason": "無法完整解析"}],
            "preview_only": True,
            "text_mutated": False,
            "auto_submit": False,
        }

    monkeypatch.setattr(service, "preflight_image_text", fake_preflight)

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/vision/v1/transcriptions/preflight",
                body=json.dumps(
                    {
                        "text": "03 × 16 × ?尾",
                        "source": "IMAGE_TRANSCRIPTION_TEXT",
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert seen["text"] == "03 × 16 × ?尾"
    assert payload["preview_only"] is True
    assert payload["text_mutated"] is False
    assert payload["auto_submit"] is False
    assert payload["source"] == "IMAGE_TRANSCRIPTION_TEXT"


def test_image_unresolved_never_builds_queue_or_legacy_cards(
    tmp_path: Path, monkeypatch,
) -> None:
    from betguard.webfill import batch_mock_queue

    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(
        service,
        "preflight_image_text",
        lambda _text, *, game="六合": {
            "ok": True,
            "all_parseable": False,
            "unresolved_count": 21,
            "issues": [
                {
                    "line_no": 4,
                    "raw": "2,3×4×05",
                    "reason": "倍率格式無法完整解析",
                }
            ],
            "preview_only": True,
            "text_mutated": False,
            "auto_submit": False,
        },
    )

    def must_not_build_queue(*_args, **_kwargs):
        raise AssertionError("image unresolved must stop before queue construction")

    monkeypatch.setattr(batch_mock_queue, "build_batch_mock_queue", must_not_build_queue)

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/assist-panel/create-batch",
                body=json.dumps(
                    {
                        "text": "06.13.23.22 二三50\n2,3×4×05",
                        "source": "IMAGE_TRANSCRIPTION_TEXT",
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 409
    assert payload["error_code"] == "IMAGE_TEXT_UNRESOLVED"
    assert payload["parser_preflight"]["unresolved_count"] == 21
    assert payload["legacy_review_cards_created"] is False
    assert payload["partial_fill"] is False
    assert payload["auto_submit"] is False
    assert list(tmp_path.rglob("*.json")) == []


def test_valid_image_text_hands_off_to_existing_batch_without_review_cards(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    text = "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5"

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/assist-panel/create-batch",
                body=json.dumps(
                    {"text": text, "source": "IMAGE_TRANSCRIPTION_TEXT"}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["source"] == "IMAGE_TRANSCRIPTION_TEXT"
    assert len(payload["valid_candidates"]) == 1
    assert payload["invalid_fragments"] == []


def test_pasted_text_keeps_legacy_mixed_review_behavior(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    text = "06.13.23.22 二三50\n99.98.97 234.100"

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/assist-panel/create-batch",
                body=json.dumps({"text": text, "source": "TEXT_INPUT"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert payload["ok"] is True
    assert payload["source"] == "TEXT_INPUT"
    assert len(payload["valid_candidates"]) == 1
    assert len(payload["invalid_fragments"]) == 1


def test_http_verified_sample_uses_only_image_id_and_human_text(monkeypatch) -> None:
    captured = {}

    def fake_save(image_id, verified_text, *, game="六合"):
        captured["image_id"] = image_id
        captured["verified_text"] = verified_text
        return {
            "ok": True,
            "sample": {"revision_id": "revision-000001"},
            "dataset_status": {
                "unique_human_verified_images": 1,
                "target": 10,
                "acceptance_dataset_ready": False,
            },
        }

    monkeypatch.setattr(image_text_acceptance, "save_human_verified_sample", fake_save)

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/vision/v1/acceptance-dataset/samples",
                body=json.dumps(
                    {
                        "image_id": "image-1",
                        "human_verified_betguard_text": "06.13.23.22 二三50",
                        "ai_original_text": "browser must not control this",
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 201
    assert payload["ok"] is True
    assert captured == {
        "image_id": "image-1",
        "verified_text": "06.13.23.22 二三50",
    }


def test_http_acceptance_status_reports_unique_images(monkeypatch) -> None:
    monkeypatch.setattr(
        image_text_acceptance,
        "get_dataset_status",
        lambda: {
            "ok": True,
            "unique_human_verified_images": 4,
            "target": 10,
            "acceptance_dataset_ready": False,
        },
    )

    with _running_app() as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/api/vision/v1/acceptance-dataset/status")
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
        finally:
            connection.close()

    assert response.status == 200
    assert payload["unique_human_verified_images"] == 4


def test_manually_corrected_image_text_is_accepted_by_existing_parser() -> None:
    # This represents the user correcting an imperfect AI transcription before
    # pressing the same createBatch action used by pasted text.
    corrected_text = "06.13.23.22 二三50"
    queue = build_batch_mock_queue(corrected_text, game="六合")

    assert queue["preprocessing"]["summary"]["valid_count"] == 1
    assert queue["preprocessing"]["invalid_fragments"] == []
    parsed = queue["preprocessing"]["valid_candidates"][0]["result"]
    assert parsed["numbers"] == [6, 13, 23, 22]
    assert parsed["stars"] == [2, 3]
    assert parsed["money"] == 50
    assert queue["final_decision"]["auto_submit"] is False


def test_prompt_examples_are_already_supported_by_the_existing_parser() -> None:
    examples = (
        (
            "05 × 08 09 23 × 10 20 29\n2,3 × 2",
            [[5], [8, 9, 23], [10, 20, 29]],
            [2, 3],
            2,
        ),
        (
            "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5",
            [[36, 38], [7, 17], [8, 18], [6, 13]],
            [2, 3, 4],
            0.5,
        ),
    )

    for text, columns, stars, unit in examples:
        queue = build_batch_mock_queue(text, game="六合")
        assert queue["preprocessing"]["summary"]["valid_count"] == 1
        assert queue["preprocessing"]["invalid_fragments"] == []
        parsed = queue["preprocessing"]["valid_candidates"][0]["result"]
        assert parsed["type"] == "column"
        assert parsed["columns"] == columns
        assert parsed["stars"] == stars
        assert parsed["unit"] == unit


def test_prompt_requests_betguard_literals_without_value_authority() -> None:
    from betguard.vision.gemma_shadow import RAW_READER_PROMPT

    for literal in ("line breaks", "x / X / ×", "01-39", "0.5", "尾", "車", "半車", "各"):
        assert literal in RAW_READER_PROMPT
    assert "plain text" in RAW_READER_PROMPT
    assert "Do not use betting knowledge" in RAW_READER_PROMPT
