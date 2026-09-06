from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from betguard.vision import image_text_acceptance, service
from betguard.vision.openai_luna_transcription import (
    API_KEY_ENV,
    MODEL_NAME,
    PROMPT,
    PROVIDER_ID,
    RESPONSE_SCHEMA_VERSION,
    LunaTranscriptionConfig,
    LunaTranscriptionError,
    build_responses_payload,
    default_cache_dir,
    normalize_parser_safe_literals,
    transcribe_with_luna,
)


def _config(tmp_path: Path) -> LunaTranscriptionConfig:
    return LunaTranscriptionConfig(
        timeout_seconds=30,
        max_output_tokens=2_000,
        image_detail="auto",
        reasoning_effort="low",
        cache_dir=tmp_path / "cache",
    )


def _wire_response(
    records: list[dict] | None = None,
    *,
    uncertain_count: int = 0,
) -> dict:
    records = records or [
        {
            "betguard_lines": ["05 × 08 09 33 × 10 20 39 2,3 × 1"],
            "uncertain": False,
        }
    ]
    return {
        "status": "completed",
        "output_text": json.dumps(
            {
                "schema_version": RESPONSE_SCHEMA_VERSION,
                "records": records,
                "cancelled_count": 1,
                "uncertain_count": uncertain_count,
            },
            ensure_ascii=False,
        ),
        "usage": {
            "input_tokens": 4_000,
            "output_tokens": 500,
            "total_tokens": 4_500,
            "output_tokens_details": {"reasoning_tokens": 100},
        },
    }


def test_default_cache_dir_is_a_path() -> None:
    assert isinstance(default_cache_dir(), Path)
    assert default_cache_dir().name == "openai-luna-transcription-cache"


def test_parser_safe_car_literal_reformat_changes_no_digits() -> None:
    text, count = normalize_parser_safe_literals(
        "01 08 09 21 3,4×1\n\n33×1車\n\n07 33 × 12 38 2×6"
    )

    assert text == "01 08 09 21 3,4×1\n\n33車1支\n\n07 33 × 12 38 2×6"
    assert count == 1


def test_parser_safe_car_literal_does_not_guess_partial_or_multi_number_text() -> None:
    text = "33×?車\n11 33×1車\n33×1車 extra"

    assert normalize_parser_safe_literals(text) == (text, 0)


def test_payload_is_one_bounded_luna_image_request() -> None:
    payload = build_responses_payload(
        image_bytes=b"image",
        mime_type="image/png",
        config=LunaTranscriptionConfig(),
    )

    assert payload["model"] == "gpt-5.6-luna"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 4_000
    assert payload["instructions"] == PROMPT
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["input"][0]["content"][0]["type"] == "input_image"
    assert payload["input"][0]["content"][0]["detail"] == "original"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    record_schema = payload["text"]["format"]["schema"]["properties"]["records"]
    assert record_schema["items"]["properties"]["betguard_lines"]["items"]["pattern"]


def test_missing_key_fails_before_network(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    called = False

    def transport(*_args):
        nonlocal called
        called = True
        return _wire_response()

    with pytest.raises(LunaTranscriptionError) as raised:
        transcribe_with_luna(
            image_path=image,
            mime_type="image/png",
            image_sha256="a" * 64,
            config=_config(tmp_path),
            transport=transport,
        )

    assert raised.value.code == "OPENAI_API_KEY_MISSING"
    assert called is False


def test_success_is_cached_by_image_and_never_stores_key(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.setenv(API_KEY_ENV, "unit-test-secret")
    calls = []

    def transport(payload, api_key, config):
        calls.append(payload)
        assert api_key == "unit-test-secret"
        assert config.timeout_seconds == 30
        assert "unit-test-secret" not in json.dumps(payload)
        return _wire_response()

    first = transcribe_with_luna(
        image_path=image,
        mime_type="image/png",
        image_sha256="b" * 64,
        config=_config(tmp_path),
        transport=transport,
    )
    second = transcribe_with_luna(
        image_path=image,
        mime_type="image/png",
        image_sha256="b" * 64,
        config=_config(tmp_path),
        transport=transport,
    )

    assert len(calls) == 1
    assert first["reader"] == PROVIDER_ID
    assert first["model"] == MODEL_NAME
    assert first["external_call_count"] == 1
    assert first["retry_count"] == 0
    assert first["usage"]["total_tokens"] == 4_500
    assert first["record_count"] == 1
    assert second["cache_hit"] is True
    assert second["external_call_count"] == 0
    assert second["retry_count"] == 0
    cache_text = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "cache").glob("*.json")
    )
    assert "unit-test-secret" not in cache_text


def test_records_keep_physical_boundaries_and_repeat_numbers_for_distinct_rules(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.setenv(API_KEY_ENV, "unit-test-secret")
    response = _wire_response(
        [
            {
                "betguard_lines": ["05 14 27 33 3,4 ×1"],
                "uncertain": False,
            },
            {
                "betguard_lines": ["06 18 29 2×1", "06 18 29 3×5"],
                "uncertain": False,
            },
        ]
    )

    result = transcribe_with_luna(
        image_path=image,
        mime_type="image/png",
        image_sha256="e" * 64,
        config=_config(tmp_path),
        transport=lambda *_args: response,
    )

    assert result["text"] == (
        "05 14 27 33 3,4 ×1\n\n06 18 29 2×1\n06 18 29 3×5"
    )
    assert result["record_count"] == 2


def test_record_line_with_embedded_newline_is_rejected(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.setenv(API_KEY_ENV, "unit-test-secret")
    response = _wire_response(
        [{"betguard_lines": ["05 14 27 33 3,4 ×1\n4×1"], "uncertain": False}]
    )

    with pytest.raises(LunaTranscriptionError) as raised:
        transcribe_with_luna(
            image_path=image,
            mime_type="image/png",
            image_sha256="f" * 64,
            config=_config(tmp_path),
            transport=lambda *_args: response,
        )

    assert raised.value.code == "OPENAI_RESPONSE_TEXT_INVALID"


def test_uncertain_count_must_match_uncertain_records(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.setenv(API_KEY_ENV, "unit-test-secret")
    response = _wire_response(
        [{"betguard_lines": ["05 14 ? 33 3,4 ×1"], "uncertain": True}],
        uncertain_count=0,
    )

    with pytest.raises(LunaTranscriptionError) as raised:
        transcribe_with_luna(
            image_path=image,
            mime_type="image/png",
            image_sha256="1" * 64,
            config=_config(tmp_path),
            transport=lambda *_args: response,
        )

    assert raised.value.code == "OPENAI_RESPONSE_SCHEMA_INVALID"


def test_invalid_response_is_not_salvaged_or_cached(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    monkeypatch.setenv(API_KEY_ENV, "unit-test-secret")

    with pytest.raises(LunaTranscriptionError) as raised:
        transcribe_with_luna(
            image_path=image,
            mime_type="image/png",
            image_sha256="c" * 64,
            config=_config(tmp_path),
            transport=lambda *_args: {"status": "completed", "output_text": "not json"},
        )

    assert raised.value.code == "OPENAI_RESPONSE_NOT_JSON"
    assert not list((tmp_path / "cache").glob("*.json"))


def test_luna_service_result_stays_editable_and_records_original_prediction(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    metadata = SimpleNamespace(
        storage_path=image,
        mime_type="image/png",
        sha256="d" * 64,
        width=720,
        height=1280,
        byte_size=5,
        is_expired=lambda: False,
    )
    captured = {}
    monkeypatch.setattr(service, "get_metadata", lambda _image_id: metadata)
    monkeypatch.setattr(service, "get_luna_transcription_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        service,
        "transcribe_with_luna",
        lambda **_kwargs: {
            "status": "completed",
            "text": "05 × 08 09 33 × 10 20 39 2,3 × 1\n\n33×1車",
            "cancelled_count": 1,
            "uncertain_count": 0,
            "reader": PROVIDER_ID,
            "model": MODEL_NAME,
            "adapter_version": "adapter",
            "prompt_sha256": "prompt",
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "image_detail": "auto",
            "cache_identity": "cache",
            "cache_hit": False,
            "external_call_count": 1,
            "retry_count": 0,
            "latency_ms": 42,
            "usage": {"total_tokens": 123},
        },
    )
    monkeypatch.setattr(
        image_text_acceptance,
        "record_machine_transcription",
        lambda image_id, **kwargs: captured.update({"image_id": image_id, **kwargs}),
    )

    result = service.transcribe_image_to_text("image-id", reader="luna")

    assert result["ok"] is True
    assert result["reader"] == PROVIDER_ID
    assert result["text"] == "05 × 08 09 33 × 10 20 39 2,3 × 1\n\n33車1支"
    assert result["parser_preflight"]["all_parseable"] is True
    assert result["external_call_count"] == 1
    assert result["retry_count"] == 0
    assert result["auto_apply"] is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False
    assert captured["image_id"] == "image-id"
    assert captured["ai_original_text"].endswith("33×1車")
    assert captured["adapter_text"] == result["text"]
    assert captured["native_ocr_text"] is None  # legacy cache had no native capture
    assert captured["reader"] == PROVIDER_ID
    assert {notice["code"] for notice in result["transcription_notices"]} == {
        "CAR_LITERAL_REFORMATTED",
        "CANCELLED_RECORD_OMITTED",
    }
