"""Failure-only, local diagnostic preservation for Qwen schema errors."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from betguard.vision.contracts import RecognitionRequest, RecognitionStatus
from betguard.vision.providers.qwen_dashscope import (
    API_KEY_ENV,
    QwenDashScopeClient,
    QwenDashScopeConfig,
    QwenDashScopeProvider,
    QwenOutputTruncatedError,
    QwenSchemaError,
    validate_task_response,
)
from betguard.vision.qwen_cache import QwenResponseCache
from betguard.vision.qwen_diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    NESTED_JSON_SALVAGE_MISLEADING_ERROR,
    QWEN_OUTPUT_TRUNCATED,
    QwenFailureDiagnosticStore,
    default_qwen_diagnostics_dir,
)
from betguard.vision.qwen_prompts import (
    PROMPT,
    PROMPT_VERSION,
    TASK_TYPE_FOCUSED_CROP,
    TASK_TYPE_FULL_PAGE,
    prompt_sha256,
)


VALID_FULL_PAGE = json.dumps(
    {
        "sections": [
            {
                "rows": [
                    {
                        "tokens": [{"text": "05", "bbox": [10, 20, 40, 50]}],
                        "numbers": [["05"]],
                        "multiplier": None,
                        "layout_hint": "normal_row",
                    }
                ],
                "shared_multiplier": None,
            }
        ]
    }
)


def _response(
    content: str,
    *,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> dict:
    return {
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 20,
            "completion_tokens": 30,
            "total_tokens": 50,
            "prompt_tokens_details": {"image_tokens": 12, "text_tokens": 8},
        },
    }


def _client(tmp_path: Path, transport) -> QwenDashScopeClient:
    return QwenDashScopeClient(
        config=QwenDashScopeConfig(),
        cache=QwenResponseCache(tmp_path / "cache"),
        diagnostic_store=QwenFailureDiagnosticStore(tmp_path / "diagnostics"),
        transport=transport,
        sleep=lambda _: None,
    )


def _chat(client: QwenDashScopeClient, raw: bytes = b"diagnostic-image"):
    digest = hashlib.sha256(raw).hexdigest()
    return client.chat(
        base64.b64encode(raw).decode(),
        "image/png",
        PROMPT,
        max_tokens=8000,
        image_sha256=digest,
        crop_box=[1, 2, 3, 4],
        scale=3,
        image_variant="original_3x",
        prompt_version=PROMPT_VERSION,
        task_type=TASK_TYPE_FULL_PAGE,
        effective_crop_sha256=digest,
        request_id="req-diagnostic",
    )


def _diagnostic(tmp_path: Path) -> tuple[dict, str]:
    metadata_files = list((tmp_path / "diagnostics").glob("*.json"))
    response_files = list((tmp_path / "diagnostics").glob("*.response.txt"))
    assert len(metadata_files) == 1
    assert len(response_files) == 1
    return (
        json.loads(metadata_files[0].read_text(encoding="utf-8")),
        response_files[0].read_text(encoding="utf-8"),
    )


def test_valid_response_creates_success_cache_without_failure_diagnostic(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    client = _client(tmp_path, lambda payload, api_key, config: _response(VALID_FULL_PAGE))

    content, meta = _chat(client)

    assert content == VALID_FULL_PAGE
    assert meta["response_schema_valid"] is True
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert not (tmp_path / "diagnostics").exists()


def test_sections_presence_variants_are_preserved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    variants = [
        ("missing", "{}"),
        ("empty", '{"sections": []}'),
        ("null", '{"sections": null}'),
        ("wrong_type", '{"sections": "rows"}'),
    ]

    for index, (expected, content) in enumerate(variants):
        case_dir = tmp_path / str(index)
        client = _client(case_dir, lambda payload, api_key, config, value=content: _response(value))
        returned, meta = _chat(client, raw=f"image-{index}".encode())
        assert returned == content
        diagnostic, raw = _diagnostic(case_dir)
        assert diagnostic["sections_presence"] == expected
        assert diagnostic["strict_json_parse_ok"] is True
        assert diagnostic["strict_root_type"] == "object"
        assert diagnostic["salvage_attempted"] is False
        assert diagnostic["salvage_success"] is False
        assert raw == content
        assert meta["failure_diagnostic"]["diagnostic_id"] == diagnostic["diagnostic_id"]
        assert not list((case_dir / "cache").glob("*.json"))


def test_truncated_root_records_nested_salvage_and_length_usage(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    content = (
        '{"sections":[{"rows":[{"tokens":['
        '{"text":"05","bbox":[10,20,40,50]}'
    )
    usage = {
        "prompt_tokens": 667,
        "completion_tokens": 8000,
        "total_tokens": 8667,
        "prompt_tokens_details": {"image_tokens": 167, "text_tokens": 500},
        "completion_tokens_details": {"text_tokens": 8000},
    }
    client = _client(
        tmp_path,
        lambda payload, api_key, config: _response(
            content,
            finish_reason="length",
            usage=usage,
        ),
    )

    returned, meta = _chat(client)
    diagnostic, raw = _diagnostic(tmp_path)

    assert returned == content
    assert raw == content
    assert diagnostic["diagnostic_schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert diagnostic["strict_json_parse_ok"] is False
    assert diagnostic["strict_json_error"].startswith("JSONDecodeError:")
    assert diagnostic["strict_json_error_position"] == len(content)
    assert diagnostic["strict_json_error_at_end"] is True
    assert diagnostic["strict_root_type"] == "unknown"
    assert diagnostic["salvage_attempted"] is True
    assert diagnostic["salvage_success"] is True
    assert diagnostic["salvage_offset"] == content.index('{"text"')
    assert diagnostic["salvaged_root_keys"] == ["text", "bbox"]
    assert diagnostic["sections_presence"] == "missing"
    assert diagnostic["braces_balance"] > 0
    assert diagnostic["brackets_balance"] > 0
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["usage"]["prompt_tokens"] == 667
    assert diagnostic["usage"]["completion_tokens"] == 8000
    assert diagnostic["usage"]["total_tokens"] == 8667
    assert diagnostic["usage"]["image_tokens"] == 167
    assert diagnostic["attempt_count"] == 1
    assert diagnostic["retry_count"] == 0
    assert diagnostic["http_status"] == 200
    assert diagnostic["classification"] == QWEN_OUTPUT_TRUNCATED
    assert diagnostic["secondary_classifications"] == [
        NESTED_JSON_SALVAGE_MISLEADING_ERROR,
    ]
    assert meta["response_schema_valid"] is False
    assert meta["failure_diagnostic"]["classification"] == QWEN_OUTPUT_TRUNCATED
    assert meta["failure_diagnostic"]["finish_reason"] == "length"
    assert not list((tmp_path / "cache").glob("*.json"))


def test_full_page_truncated_nested_object_never_becomes_valid_root() -> None:
    content = (
        '{"sections":[{"rows":['
        '{"rows":[],"shared_multiplier":null}'
    )

    with pytest.raises(QwenOutputTruncatedError) as exc_info:
        validate_task_response(
            content,
            TASK_TYPE_FULL_PAGE,
            response_metadata={"finish_reason": "length"},
        )

    assert exc_info.value.classification == QWEN_OUTPUT_TRUNCATED
    assert exc_info.value.secondary_classifications == (
        NESTED_JSON_SALVAGE_MISLEADING_ERROR,
    )


def test_full_page_balanced_nested_salvage_is_rejected_as_non_root() -> None:
    wrapped = "model output: " + VALID_FULL_PAGE

    with pytest.raises(
        QwenSchemaError,
        match="complete top-level JSON object",
    ):
        validate_task_response(wrapped, TASK_TYPE_FULL_PAGE)


def test_other_task_type_keeps_existing_nested_extract_behavior() -> None:
    focused = (
        'model output: {"full_text":"2X1",'
        '"category_digits":"2","multiplier":"1"}'
    )

    parsed = validate_task_response(focused, TASK_TYPE_FOCUSED_CROP)

    assert parsed == {
        "full_text": "2X1",
        "category_digits": "2",
        "multiplier": "1",
    }


def test_complete_full_page_with_stop_remains_unchanged() -> None:
    parsed = validate_task_response(
        VALID_FULL_PAGE,
        TASK_TYPE_FULL_PAGE,
        response_metadata={"finish_reason": "stop"},
    )

    assert parsed == json.loads(VALID_FULL_PAGE)


def test_finish_reason_length_fails_closed_even_if_json_happens_to_close() -> None:
    with pytest.raises(QwenOutputTruncatedError):
        validate_task_response(
            VALID_FULL_PAGE,
            TASK_TYPE_FULL_PAGE,
            response_metadata={"finish_reason": "length"},
        )


def test_truncated_schema_failure_does_not_auto_retry_or_write_success_cache(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    calls = 0
    content = '{"sections":[{"rows":[{"tokens":['

    def transport(payload, api_key, config):
        nonlocal calls
        calls += 1
        return _response(
            content,
            finish_reason="length",
            usage={
                "completion_tokens": 8000,
                "total_tokens": 9382,
                "prompt_tokens_details": {"image_tokens": 882},
            },
        )

    client = _client(tmp_path, transport)
    returned, meta = _chat(client)

    assert returned == content
    assert calls == 1
    assert meta["retries"] == 0
    assert meta["response_schema_valid"] is False
    assert not list((tmp_path / "cache").glob("*.json"))


def test_empty_content_preserves_zero_length_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    client = _client(tmp_path, lambda payload, api_key, config: _response(""))

    with pytest.raises(QwenSchemaError, match="Qwen 回傳空 content"):
        _chat(client)
    diagnostic, raw = _diagnostic(tmp_path)

    assert raw == ""
    assert diagnostic["response_content_length"] == 0
    assert diagnostic["response_content_sha256"] == hashlib.sha256(b"").hexdigest()
    assert diagnostic["strict_json_parse_ok"] is False
    assert diagnostic["salvage_success"] is False
    assert diagnostic["sections_presence"] == "unknown"
    assert not list((tmp_path / "cache").glob("*.json"))


def test_failure_artifacts_have_complete_safe_identity_and_no_request_secrets(
    tmp_path, monkeypatch,
) -> None:
    api_key = "diagnostic-secret-api-key"
    encoded_image = base64.b64encode(b"diagnostic-image").decode()
    monkeypatch.setenv(API_KEY_ENV, api_key)
    content = '{"sections": []}'
    client = _client(tmp_path, lambda payload, key, config: _response(content))

    _chat(client)
    diagnostic, raw = _diagnostic(tmp_path)
    all_artifacts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "diagnostics").iterdir()
        if path.is_file()
    )

    digest = hashlib.sha256(b"diagnostic-image").hexdigest()
    assert diagnostic["request_id"] == "req-diagnostic"
    assert diagnostic["provider_id"] == "qwen-dashscope"
    assert diagnostic["model"] == "qwen3-vl-plus"
    assert diagnostic["prompt_version"] == PROMPT_VERSION
    assert diagnostic["prompt_sha256"] == prompt_sha256(PROMPT)
    assert diagnostic["task_type"] == TASK_TYPE_FULL_PAGE
    assert diagnostic["image_sha256"] == digest
    assert diagnostic["effective_crop_sha256"] == digest
    assert diagnostic["crop_box"] == [1, 2, 3, 4]
    assert diagnostic["image_variant"] == "original_3x"
    assert diagnostic["mime_type"] == "image/png"
    assert diagnostic["max_tokens"] == 8000
    assert diagnostic["endpoint_host"] == "dashscope-intl.aliyuncs.com"
    assert diagnostic["response_content_length"] == len(content)
    assert diagnostic["response_content_sha256"] == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()
    assert diagnostic["schema_error_type"] == "QwenSchemaError"
    assert "sections must be a non-empty list" in diagnostic["schema_error_message"]
    assert raw == content
    assert diagnostic["diagnostic_id"].startswith("req-diagnostic-")
    assert diagnostic["response_artifact_filename"].startswith("req-diagnostic-")
    assert api_key not in all_artifacts
    assert "Authorization" not in all_artifacts
    assert "Bearer" not in all_artifacts
    assert encoded_image not in all_artifacts
    assert not list((tmp_path / "cache").glob("*.json"))


def test_failed_provider_keeps_contract_safety_and_only_diagnostic_side_effect(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    image = tmp_path / "input.png"
    Image.new("RGB", (40, 30), "white").save(image, format="PNG")
    client = _client(
        tmp_path,
        lambda payload, api_key, config: _response('{"sections": []}'),
    )

    result = QwenDashScopeProvider(client=client).recognize(
        RecognitionRequest(
            request_id="req-provider-diagnostic",
            image_id="image-provider-diagnostic",
            image_path=str(image),
            mime_type="image/png",
        )
    )
    result_dict = result.to_dict()
    serialized = json.dumps(result_dict, ensure_ascii=False)

    assert result.status == RecognitionStatus.FAILED
    assert result.raw_text == ""
    assert result.lines == []
    assert result.preprocessing["response_schema_valid"] is False
    assert result.preprocessing["human_confirmation_required"] is True
    assert result.preprocessing["auto_confirm"] is False
    assert result.preprocessing["auto_submit"] is False
    diagnostic_ref = result.preprocessing["failure_diagnostic"]
    assert diagnostic_ref["request_id"] == "req-provider-diagnostic"
    for forbidden in (
        "accepted_by_human",
        "manual_candidate_id",
        "approved_fill_queue",
        "queue_entry",
        "draft_write",
        "webfill",
        "executable",
        "exportable",
    ):
        assert forbidden not in serialized
    assert not list((tmp_path / "cache").glob("*.json"))


def test_truncated_provider_fails_closed_without_partial_result_or_workflow_side_effects(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "diagnostic-unit-test-key")
    image = tmp_path / "input.png"
    Image.new("RGB", (40, 30), "white").save(image, format="PNG")
    content = (
        '{"sections":[{"rows":[{"tokens":['
        '{"text":"05","bbox":[10,20,40,50]}'
    )
    client = _client(
        tmp_path,
        lambda payload, api_key, config: _response(
            content,
            finish_reason="length",
            usage={"completion_tokens": 8000, "total_tokens": 9382},
        ),
    )

    result = QwenDashScopeProvider(client=client).recognize(
        RecognitionRequest(
            request_id="req-provider-truncated",
            image_id="image-provider-truncated",
            image_path=str(image),
            mime_type="image/png",
        )
    )
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.status == RecognitionStatus.FAILED
    assert result.provider_error is not None
    assert result.provider_error.code == QWEN_OUTPUT_TRUNCATED
    assert "sections must be a non-empty list" not in result.provider_error.message
    assert result.raw_text == ""
    assert result.lines == []
    assert result.preprocessing["response_schema_valid"] is False
    assert result.preprocessing["human_confirmation_required"] is True
    assert result.preprocessing["auto_confirm"] is False
    assert result.preprocessing["auto_submit"] is False
    diagnostic = result.preprocessing["failure_diagnostic"]
    assert diagnostic["classification"] == QWEN_OUTPUT_TRUNCATED
    assert diagnostic["secondary_classifications"] == [
        NESTED_JSON_SALVAGE_MISLEADING_ERROR,
    ]
    assert diagnostic["finish_reason"] == "length"
    for forbidden in (
        "qwen_response",
        "accepted_by_human",
        "manual_candidate_id",
        "approved_fill_queue",
        "queue_entry",
        "draft_write",
        "webfill",
        "executable",
        "exportable",
    ):
        assert forbidden not in serialized
    assert not list((tmp_path / "cache").glob("*.json"))


def test_default_diagnostics_path_is_user_data_not_repository() -> None:
    diagnostics_dir = default_qwen_diagnostics_dir()
    repo = Path(__file__).resolve().parents[1]
    assert diagnostics_dir.is_absolute()
    assert diagnostics_dir != repo
    assert repo not in diagnostics_dir.parents
