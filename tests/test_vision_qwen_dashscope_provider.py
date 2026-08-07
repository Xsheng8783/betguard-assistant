"""Phase-1 tests for the first-class DashScope Qwen integration."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from betguard.vision.contracts import RecognitionRequest, RecognitionStatus
from betguard.vision.providers.base import ImageRecognitionProvider
from betguard.vision.providers.qwen_dashscope import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_URL,
    PROVIDER_ID,
    REQUEST_SCHEMA_VERSION,
    QwenAPIKeyMissing,
    QwenClientError,
    QwenDashScopeClient,
    QwenDashScopeConfig,
    QwenDashScopeProvider,
    QwenSchemaError,
    QwenTimeoutError,
    validate_task_response,
)
from betguard.vision.qwen_cache import QwenResponseCache, default_qwen_cache_dir
from betguard.vision.qwen_prompts import (
    COLUMN_COMBO_PROMPT,
    FOCUSED_PROMPT,
    PLAY_MARK_PROMPT,
    PROMPT,
    PROMPT_ZH,
    PROMPT_VERSION,
    TASK_TYPE_FULL_PAGE,
    prompt_sha256,
)


PROMPT_SHA256_SNAPSHOTS = {
    "PROMPT": "672bbec2168f58078a84840904bd2e7e5dc30651c6f32ac71e1757f3abb9ff01",
    "FOCUSED_PROMPT": "e916b10687d6f630f63f2c87e84788a6f7935c13875b3a6e6b67b876c8f8c550",
    "PLAY_MARK_PROMPT": "c59e6fb8f472f31a0c22a053d7081c97038cbcb20372d6dadee0c1226ee2eb14",
    "COLUMN_COMBO_PROMPT": "c0014509f0a2ef2366e092c30b84970c2bcce8026fb66fd7989389b5b9e63b6b",
    "PROMPT_ZH": "9cb13b25718ee86a02a6dfcaa7c6b8b0d075100bdf416db6793f1b441e2990de",
}


VALID_FULL_PAGE = json.dumps(
    {
        "sections": [
            {
                "rows": [
                    {
                        "tokens": [
                            {"text": "05", "bbox": [10, 20, 40, 50]},
                            {"text": "x", "bbox": [45, 20, 60, 50]},
                        ],
                        "numbers": [["05"]],
                        "multiplier": None,
                        "layout_hint": "normal_row",
                    }
                ],
                "shared_multiplier": None,
            }
        ]
    },
    ensure_ascii=False,
)


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _png(path: Path, size: tuple[int, int] = (40, 30)) -> None:
    Image.new("RGB", size, "white").save(path, format="PNG")


def _client(tmp_path: Path, transport) -> QwenDashScopeClient:
    return QwenDashScopeClient(
        config=QwenDashScopeConfig(),
        cache=QwenResponseCache(tmp_path / "cache"),
        transport=transport,
        sleep=lambda _: None,
    )


def _chat(client: QwenDashScopeClient, raw: bytes = b"image"):
    encoded = base64.b64encode(raw).decode()
    digest = hashlib.sha256(raw).hexdigest()
    return client.chat(
        encoded,
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
    )


def test_prompt_sha256_snapshots_are_byte_exact() -> None:
    prompts = {
        "PROMPT": PROMPT,
        "FOCUSED_PROMPT": FOCUSED_PROMPT,
        "PLAY_MARK_PROMPT": PLAY_MARK_PROMPT,
        "COLUMN_COMBO_PROMPT": COLUMN_COMBO_PROMPT,
        "PROMPT_ZH": PROMPT_ZH,
    }
    assert {name: prompt_sha256(value) for name, value in prompts.items()} == PROMPT_SHA256_SNAPSHOTS


def test_typed_config_preserves_original_defaults(monkeypatch) -> None:
    for name in (
        "BETGUARD_QWEN_MODEL",
        "BETGUARD_QWEN_URL",
        "BETGUARD_QWEN_TIMEOUT_SECONDS",
        "BETGUARD_QWEN_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    config = QwenDashScopeConfig.from_env()
    assert config.model == DEFAULT_MODEL == "qwen3-vl-plus"
    assert config.url == DEFAULT_URL
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 240.0
    assert config.max_retries == 2


def test_api_key_is_read_for_each_request_not_stored_on_client(tmp_path, monkeypatch) -> None:
    seen_keys: list[str] = []

    def transport(payload, api_key, config):
        seen_keys.append(api_key)
        return _response(VALID_FULL_PAGE)

    client = _client(tmp_path, transport)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(QwenAPIKeyMissing):
        _chat(client)
    monkeypatch.setenv(API_KEY_ENV, "request-time-key")
    _chat(client)
    assert seen_keys == ["request-time-key"]
    assert "request-time-key" not in vars(client).values()


def test_request_payload_is_compatible_and_cache_identity_is_complete(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")
    payloads: list[dict] = []

    def transport(payload, api_key, config):
        payloads.append(payload)
        return _response(VALID_FULL_PAGE)

    client = _client(tmp_path, transport)
    content, first_meta = _chat(client)
    cached, second_meta = _chat(client)
    assert content == cached == VALID_FULL_PAGE
    assert len(payloads) == 1
    assert payloads[0] == {
        "model": "qwen3-vl-plus",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
            {"type": "text", "text": PROMPT},
        ]}],
        "max_tokens": 8000,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    assert first_meta["cache_hit"] is False
    assert second_meta["cache_hit"] is True

    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 1
    record = json.loads(cache_files[0].read_text(encoding="utf-8"))
    identity = record["identity"]
    assert set(identity) == {
        "image_sha256",
        "crop_box",
        "scale",
        "variant",
        "model",
        "prompt_version",
        "task_type",
        "prompt_sha256",
        "request_schema_version",
        "effective_crop_sha256",
        "mime_type",
        "max_tokens",
        "endpoint_url",
    }
    assert identity["prompt_sha256"] == prompt_sha256(PROMPT)
    assert identity["request_schema_version"] == REQUEST_SCHEMA_VERSION
    persisted = cache_files[0].read_text(encoding="utf-8")
    assert "unit-test-key" not in persisted
    assert "Bearer" not in persisted
    assert "aW1hZ2U=" not in persisted
    assert not list((tmp_path / "cache").glob("*.tmp"))


@pytest.mark.parametrize(
    "failure",
    [
        QwenTimeoutError("timeout"),
        QwenClientError("429", http_status=429, retryable=False),
        QwenClientError("500", http_status=500, retryable=False),
        QwenClientError("400", http_status=400, retryable=False),
        QwenSchemaError("outer invalid JSON"),
    ],
)
def test_transport_failures_are_never_cached(tmp_path, monkeypatch, failure) -> None:
    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")

    def transport(payload, api_key, config):
        raise failure

    client = _client(tmp_path, transport)
    with pytest.raises(type(failure)):
        _chat(client)
    assert not list((tmp_path / "cache").glob("*.json"))


@pytest.mark.parametrize("content", ["", "not json", '{"sections": "wrong"}'])
def test_empty_or_invalid_model_content_is_never_cached(tmp_path, monkeypatch, content) -> None:
    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")
    calls = 0

    def transport(payload, api_key, config):
        nonlocal calls
        calls += 1
        return _response(content)

    client = _client(tmp_path, transport)
    if not content:
        with pytest.raises(QwenSchemaError):
            _chat(client)
    else:
        returned, meta = _chat(client)
        assert returned == content
        assert meta["response_schema_valid"] is False
    assert calls == 1
    assert not list((tmp_path / "cache").glob("*.json"))


def test_default_cache_is_absolute_user_data_and_not_repository_relative() -> None:
    cache_dir = default_qwen_cache_dir()
    repo = Path(__file__).resolve().parents[1]
    assert cache_dir.is_absolute()
    assert cache_dir != repo
    assert repo not in cache_dir.parents


def test_provider_returns_existing_contract_and_safety_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")
    image = tmp_path / "input.png"
    _png(image)
    client = _client(tmp_path, lambda payload, api_key, config: _response(VALID_FULL_PAGE))
    provider = QwenDashScopeProvider(client=client)
    assert isinstance(provider, ImageRecognitionProvider)
    result = provider.recognize(
        RecognitionRequest(
            request_id="req-qwen-success",
            image_id="image-qwen-success",
            image_path=str(image),
            mime_type="image/png",
        )
    )
    assert result.status == RecognitionStatus.COMPLETED
    assert result.provider.id == PROVIDER_ID
    assert result.provider.model_name == DEFAULT_MODEL
    assert result.lines[0].tokens[0].text == "05"
    assert result.preprocessing["human_confirmation_required"] is True
    assert result.preprocessing["auto_confirm"] is False
    assert result.preprocessing["auto_submit"] is False


def test_invalid_response_schema_fails_closed_with_safety_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")
    image = tmp_path / "input.png"
    _png(image)
    client = _client(tmp_path, lambda payload, api_key, config: _response('{"sections": []}'))
    result = QwenDashScopeProvider(client=client).recognize(
        RecognitionRequest(
            request_id="req-qwen-invalid",
            image_id="image-qwen-invalid",
            image_path=str(image),
        )
    )
    assert result.status == RecognitionStatus.FAILED
    assert result.provider_error is not None
    assert result.preprocessing["response_schema_valid"] is False
    assert result.preprocessing["human_confirmation_required"] is True
    assert result.preprocessing["auto_confirm"] is False
    assert result.preprocessing["auto_submit"] is False
    assert not list((tmp_path / "cache").glob("*.json"))


def test_task_validation_is_structural_not_betting_semantic() -> None:
    parsed = validate_task_response(VALID_FULL_PAGE, TASK_TYPE_FULL_PAGE)
    assert parsed["sections"][0]["rows"][0]["numbers"] == [["05"]]


def test_debug_qwen_paths_have_no_second_http_client() -> None:
    repo = Path(__file__).resolve().parents[1]
    debug_source = (repo / "sample-034-debug" / "test_combined_bbox.py").read_text(encoding="utf-8")
    formal_source = (repo / "src" / "betguard" / "vision" / "providers" / "qwen_dashscope.py").read_text(encoding="utf-8")
    assert "urllib.request.Request" not in debug_source
    assert "get_default_client().chat(" in debug_source
    assert formal_source.count("urllib.request.Request(") == 1


def test_service_lists_qwen_safety_and_calls_it_only_when_explicit(
    tmp_path, monkeypatch,
) -> None:
    import betguard.vision.providers.qwen_dashscope as qwen_module
    from betguard.vision import service

    monkeypatch.setenv(API_KEY_ENV, "unit-test-key")
    providers = service.list_providers()["providers"]
    for item in providers:
        assert {
            "configured",
            "external_network",
            "human_confirmation_required",
            "auto_confirm",
            "auto_submit",
        } <= set(item)
    qwen_entry = next(item for item in providers if item["id"] == PROVIDER_ID)
    assert qwen_entry["configured"] is True
    assert qwen_entry["external_network"] is True
    assert qwen_entry["human_confirmation_required"] is True
    assert qwen_entry["auto_confirm"] is False
    assert qwen_entry["auto_submit"] is False

    calls = 0

    def transport(payload, api_key, config):
        nonlocal calls
        calls += 1
        return _response(VALID_FULL_PAGE)

    monkeypatch.setattr(qwen_module, "_default_client", _client(tmp_path, transport))
    image = tmp_path / "service-input.png"
    _png(image)
    uploaded = service.upload_image(image.read_bytes(), "image/png", image.name)
    image_id = uploaded["image"]["image_id"]
    try:
        result = service.run_job(image_id, PROVIDER_ID, game="539")
        assert result["ok"] is True
        assert result["result"]["provider"]["id"] == PROVIDER_ID
        assert result["structure_evidence"][0]["game"] == "539"
        assert calls == 1
    finally:
        service.delete_image_api(image_id)
