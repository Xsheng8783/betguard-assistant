from __future__ import annotations

import json
from pathlib import Path

import pytest

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.providers.openai_paid import (
    API_KEY_ENV,
    MODEL_ENV,
    OpenAIPaidVisionError,
    OpenAIPaidVisionProvider,
    PaidVisionConfig,
    build_prompt,
    build_responses_payload,
    build_output_schema,
    cache_key,
    get_config_from_env,
    validate_model_output,
)


def _request(tmp_path: Path) -> RecognitionRequest:
    image = tmp_path / "sample.png"
    image.write_bytes(b"not-a-real-png-but-provider-only-reads-bytes")
    return RecognitionRequest(
        request_id="req-test",
        image_id="img-test",
        image_path=str(image),
        mime_type="image/png",
        metadata={"sha256": "abc123", "width": 800, "height": 600, "size_bytes": 42},
    )


def _model_output(**extra):
    data = {
        "schema_version": "1.1",
        "lines": [
            {
                "line_id": "L01",
                "entry_id": "E01",
                "region": "left_numbers",
                "layout_hint": "normal_like",
                "number_groups": [["18", "26"]],
                "multiplier_text": "×1",
                "raw_text": "18 26 ×1",
                "alternatives": [],
                "uncertain": False,
                "uncertain_reason": None,
            }
        ],
    }
    data.update(extra)
    return data


def _response(data):
    return {"output_text": json.dumps(data, ensure_ascii=False)}


def test_strict_schema_disallows_extra_fields():
    schema = build_output_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["lines"]["items"]["additionalProperties"] is False
    assert "pattern" in schema["properties"]["lines"]["items"]["properties"]["raw_text"]
    properties = schema["properties"]["lines"]["items"]["properties"]
    assert properties["layout_hint"]["enum"] == [
        "car_like",
        "column_like",
        "normal_like",
        "unknown",
    ]
    assert "number_groups" in properties
    assert "multiplier_text" in properties


def test_prompt_is_restricted_to_bet_slip_alphabet_and_no_guessing():
    prompt = build_prompt()
    assert "digits 0-9" in prompt
    assert "Chinese 二/三/四" in prompt
    assert "crossed out" in prompt
    assert "REQUIRED to refuse" in prompt or "Never guess" in prompt
    assert "column_like" in prompt
    assert "never a final bet type" in prompt
    assert "exactly one output item" in prompt
    assert "Never split" in prompt
    assert "Never merge content across a visible cell border" in prompt
    assert "11 18 20 三×10" in prompt
    assert "one inner list per visible column" in prompt
    assert "× marks between number clusters" in prompt
    assert "05 18 ×1" in prompt
    assert "14 16 23 28 二三×1" in prompt
    assert "CLOSED-ALPHABET" in prompt
    assert "REQUIRED to refuse" in prompt or "Never guess" in prompt
    # No decimal multipliers, no 各, no old lowercase-x examples
    assert "0.5" not in prompt
    assert "0.3" not in prompt
    assert "0.2" not in prompt
    assert "各" not in prompt
    assert "x0.5" not in prompt
    assert "x1" not in prompt


def test_column_document_mode_adds_non_flattening_instruction():
    prompt = build_prompt("column")

    assert "Human-supplied document mode: column" in prompt
    assert "do not flatten a column entry" in prompt


def test_document_mode_changes_paid_cache_key():
    normal_key = cache_key("abc123", "test-model", document_mode="normal")
    column_key = cache_key("abc123", "test-model", document_mode="column")

    assert normal_key != column_key


def test_handwritten_images_default_to_high_detail(monkeypatch):
    monkeypatch.delenv("BETGUARD_VISION_IMAGE_DETAIL", raising=False)
    payload = build_responses_payload(
        model="test-model",
        image_bytes=b"image",
        mime_type="image/jpeg",
    )
    assert payload["input"][0]["content"][1]["detail"] == "high"


def test_payload_includes_human_document_mode():
    payload = build_responses_payload(
        model="test-model",
        image_bytes=b"image",
        mime_type="image/jpeg",
        document_mode="mixed",
    )

    assert "Human-supplied document mode: mixed" in payload["input"][0]["content"][0]["text"]


def test_paid_vision_defaults_to_one_long_request_without_automatic_retry(monkeypatch):
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    monkeypatch.delenv("BETGUARD_VISION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BETGUARD_VISION_RETRIES", raising=False)

    config = get_config_from_env()

    assert config is not None
    assert config.timeout_seconds == 120
    assert config.max_retries == 0


def test_validate_model_output_rejects_human_confirmed():
    data = _model_output()
    data["lines"][0]["human_confirmed"] = True
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_model_output(data)


def test_validate_model_output_rejects_validation_status():
    data = _model_output(validation_status="ok")
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_model_output(data)


def test_validate_model_output_rejects_printed_or_unexpected_chinese_text():
    data = _model_output()
    data["lines"][0]["raw_text"] = "御宿旅館"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_accepts_digits_and_supported_star_text():
    data = _model_output()
    data["lines"][0]["raw_text"] = "05 08 10 二三四×1"
    assert validate_model_output(data) == data


def test_validate_model_output_rejects_decimal_multiplier():
    data = _model_output()
    data["lines"][0]["raw_text"] = "05 08 10 二三四×0.5"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_english_letters():
    data = _model_output()
    data["lines"][0]["raw_text"] = "please read 05"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_english_in_alternatives():
    data = _model_output()
    data["lines"][0]["alternatives"] = ["read 05", "05 06"]
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_english_in_multiplier():
    data = _model_output()
    data["lines"][0]["multiplier_text"] = "x1"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_other_chinese():
    data = _model_output()
    data["lines"][0]["raw_text"] = "各二三×1"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_decimal_point():
    data = _model_output()
    data["lines"][0]["raw_text"] = "05.08 ×1"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_decimal_multiplier_text():
    data = _model_output()
    data["lines"][0]["multiplier_text"] = "×0.5"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_accepts_integer_multipliers():
    for m in ("×1", "×2", "×10"):
        data = _model_output()
        data["lines"][0]["multiplier_text"] = m
        data["lines"][0]["raw_text"] = f"05 08 {m}"
        assert validate_model_output(data) == data


def test_validate_model_output_accepts_question_mark_unknown():
    data = _model_output()
    data["lines"][0]["number_groups"] = [["1?"]]
    data["lines"][0]["raw_text"] = "1? ×1"
    data["lines"][0]["uncertain"] = True
    data["lines"][0]["uncertain_reason"] = "unreadable_digit"
    assert validate_model_output(data) == data


def test_validate_model_output_rejects_free_text_uncertain_reason():
    data = _model_output()
    data["lines"][0]["uncertain"] = True
    data["lines"][0]["uncertain_reason"] = "the digit looks like an 8 but could be 3"
    with pytest.raises(ValueError, match="closed-set enum"):
        validate_model_output(data)


def test_validate_model_output_accepts_enum_uncertain_reasons():
    for reason in ("unreadable_digit", "unclear_multiplier", "unclear_grouping",
                   "unclear_boundary", "unclear_reading_order", "unknown"):
        data = _model_output()
        data["lines"][0]["uncertain"] = True
        data["lines"][0]["uncertain_reason"] = reason
        assert validate_model_output(data) == data


def test_validate_model_output_rejects_multiple_rows_in_one_raw_text():
    data = _model_output()
    data["lines"][0]["raw_text"] = "05 08 x1\n09 20 x1"
    with pytest.raises(ValueError, match="unsupported OCR characters"):
        validate_model_output(data)


def test_validate_model_output_rejects_duplicate_line_ids():
    data = _model_output()
    data["lines"].append(dict(data["lines"][0]))
    with pytest.raises(ValueError, match="duplicate line_id"):
        validate_model_output(data)


def test_validate_model_output_rejects_split_rows_with_duplicate_entry_id():
    data = _model_output()
    second = dict(data["lines"][0])
    second["line_id"] = "L02"
    second["raw_text"] = "二三×1"
    data["lines"].append(second)

    with pytest.raises(ValueError, match="duplicate entry_id"):
        validate_model_output(data)


def test_validate_model_output_accepts_column_grouping():
    data = _model_output()
    data["lines"][0].update(
        {
            "layout_hint": "column_like",
            "number_groups": [["17", "20"], ["28"], ["34"]],
            "multiplier_text": "二三×1",
            "raw_text": "17 20 28 34 二三×1",
        }
    )

    assert validate_model_output(data) == data


def test_validate_model_output_rejects_invalid_layout_hint():
    data = _model_output()
    data["lines"][0]["layout_hint"] = "final_column"
    with pytest.raises(ValueError, match="layout_hint"):
        validate_model_output(data)


def test_validate_model_output_rejects_invalid_number_group_token():
    data = _model_output()
    data["lines"][0]["number_groups"] = [["18", "printed hotel"]]
    with pytest.raises(ValueError, match="number_groups"):
        validate_model_output(data)


def test_missing_api_key_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    provider = OpenAIPaidVisionProvider(client=lambda *_: pytest.fail("network called"))

    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "failed"
    assert result.provider_error is not None
    assert result.provider_error.code == "AUTHENTICATION_FAILED"


def test_missing_model_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.delenv(MODEL_ENV, raising=False)
    provider = OpenAIPaidVisionProvider(client=lambda *_: pytest.fail("network called"))

    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "failed"
    assert result.provider_error is not None
    assert result.provider_error.code == "BETGUARD_VISION_MODEL_NOT_CONFIGURED"


def test_valid_response_to_recognition_result(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    calls = []

    def fake_client(payload, api_key, timeout_seconds):
        calls.append(payload)
        assert api_key == "unit-test-token"
        assert "unit-test-token" not in json.dumps(payload)
        assert payload["text"]["format"]["strict"] is True
        assert payload["text"]["format"]["schema"]["additionalProperties"] is False
        assert payload["input"][0]["content"][1]["type"] == "input_image"
        return _response(_model_output())

    provider = OpenAIPaidVisionProvider(client=fake_client, cache_dir=tmp_path / "cache")
    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "completed"
    assert result.provider.id == "openai-vision-paid"
    assert result.provider.model_name == "test-vision-model"
    assert result.source_image.cloud_uploaded is True
    assert result.raw_text == "18 26 ×1"
    assert result.preprocessing["human_confirmation_required"] is True
    assert result.preprocessing["auto_submit"] is False
    assert result.preprocessing["openai_lines"][0]["raw_text"] == "18 26 ×1"
    assert result.preprocessing["openai_lines"][0]["needs_human_confirmation"] is False
    assert result.preprocessing["openai_lines"][0]["validation_issues"] == []
    assert result.preprocessing["openai_lines"][0]["layout_hint"] == "normal_like"
    assert len(calls) == 1


def test_uncertain_line_requires_reason(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    data = _model_output()
    data["lines"][0]["uncertain"] = True
    data["lines"][0]["uncertain_reason"] = None
    provider = OpenAIPaidVisionProvider(
        client=lambda *_: _response(data),
        cache_dir=tmp_path / "cache",
    )

    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "failed"
    assert result.provider_error is not None
    assert result.provider_error.code == "OPENAI_RESPONSE_SCHEMA_INVALID"


def test_cache_prevents_second_paid_call(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    call_count = {"n": 0}

    def fake_client(payload, api_key, timeout_seconds):
        call_count["n"] += 1
        return _response(_model_output())

    cache_dir = tmp_path / "cache"
    request = _request(tmp_path)
    provider = OpenAIPaidVisionProvider(client=fake_client, cache_dir=cache_dir)
    first = provider.recognize(request)
    second = provider.recognize(request)

    assert first.status.value == "completed"
    assert second.status.value == "completed"
    assert call_count["n"] == 1
    key = cache_key("abc123", "test-vision-model")
    cached = json.loads((cache_dir / f"{key}.json").read_text(encoding="utf-8"))
    assert "unit-test-token" not in json.dumps(cached)


def test_retry_then_success(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    calls = {"n": 0}

    def fake_client(payload, api_key, timeout_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OpenAIPaidVisionError("RATE_LIMITED", "temporary", retryable=True)
        return _response(_model_output())

    provider = OpenAIPaidVisionProvider(
        client=fake_client,
        cache_dir=tmp_path / "cache",
        config=PaidVisionConfig(model="test-vision-model", max_retries=1, timeout_seconds=1),
    )
    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "completed"
    assert calls["n"] == 2


def test_invalid_json_response_is_failed(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")
    provider = OpenAIPaidVisionProvider(
        client=lambda *_: {"output_text": "{not json"},
        cache_dir=tmp_path / "cache",
    )

    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "failed"
    assert result.provider_error is not None
    assert result.provider_error.code == "OPENAI_RESPONSE_NOT_JSON"


def test_api_error_becomes_failed_result(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "unit-test-token")
    monkeypatch.setenv(MODEL_ENV, "test-vision-model")

    def fake_client(payload, api_key, timeout_seconds):
        raise OpenAIPaidVisionError("PROVIDER_UNAVAILABLE", "service unavailable", retryable=False)

    provider = OpenAIPaidVisionProvider(client=fake_client, cache_dir=tmp_path / "cache")
    result = provider.recognize(_request(tmp_path))

    assert result.status.value == "failed"
    assert result.provider_error is not None
    assert result.provider_error.code == "PROVIDER_UNAVAILABLE"
