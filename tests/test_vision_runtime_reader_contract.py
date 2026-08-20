from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.gemma_shadow import (
    ADAPTER_VERSION as GEMMA_ADAPTER_VERSION,
    API_KEY_ENV as GEMMA_API_KEY_ENV,
    GemmaShadowCacheIdentity,
    GemmaShadowConfig,
    MODEL_NAME as GEMMA_MODEL,
    MODEL_VERSION as GEMMA_MODEL_VERSION,
    PROMPT_SHA256,
    PROVIDER_ID as GEMMA_PROVIDER_ID,
    RAW_READER_PROMPT,
    REQUEST_SCHEMA_VERSION as GEMMA_REQUEST_SCHEMA_VERSION,
    run_gemma_shadow,
)
from betguard.vision.ppocr_shadow import (
    ADAPTER_VERSION as PP_ADAPTER_VERSION,
    MODEL_NAME as PP_MODEL,
    MODEL_VERSION as PP_MODEL_VERSION,
    PROVIDER_ID as PP_PROVIDER_ID,
    PROVIDER_VERSION as PP_PROVIDER_VERSION,
    PPShadowCacheIdentity,
)
from betguard.vision.providers.qwen_dashscope import (
    QwenClientError,
    QwenDashScopeClient,
    QwenDashScopeConfig,
    QwenDashScopeProvider,
)
from betguard.vision.runtime_reader_router import (
    PROVIDER_ID as ROUTER_PROVIDER_ID,
    QWEN_RUNTIME_TIMEOUT_SECONDS,
    SCHEMA_VERSION,
    VALUE_AUTHORITY,
    ReaderRoutingResult,
    validate_reader_routing_result,
)


IMAGE_SHA = "c" * 64
REPO = Path(__file__).resolve().parents[1]


def _request(image: Path) -> RecognitionRequest:
    return RecognitionRequest(
        request_id="contract-request",
        image_id="contract-image",
        image_path=str(image),
        mime_type="image/png",
        metadata={
            "sha256": IMAGE_SHA,
            "width": 10,
            "height": 10,
            "size_bytes": image.stat().st_size,
        },
    )


def _minimal_result() -> dict:
    return ReaderRoutingResult(
        schema_version=SCHEMA_VERSION,
        image_sha256=IMAGE_SHA,
        routing_decision="MANUAL_REVIEW_ONLY",
        primary_machine_source=GEMMA_PROVIDER_ID,
        fallback_reason=None,
        gemma_evidence={"status": "unavailable"},
        pp_evidence={"status": "unavailable"},
        qwen_evidence=None,
        selected_prefill_source=None,
        review_seed={
            "schema_version": "betguard.vision.human-review-seed.v1",
            "status": "manual_entry_required",
            "selected_machine_source": None,
            "draft_items": [],
            "structured_human_answer_required": True,
            "human_confirmed": False,
            "human_confirmation_required": True,
            "value_authority": VALUE_AUTHORITY,
            "candidate_created": False,
            "auto_confirm": False,
            "auto_submit": False,
        },
        field_conflicts=[],
        cache_status={
            "gemma": {"checked": True, "cache_hit": False, "identity": None},
            "ppocr": {"checked": True, "cache_hit": False, "identity": None},
            "qwen": {"checked": False, "cache_hit": False, "identity": None},
        },
        latency={"total_routing_latency_ms": 0.0},
        model_call_counters={
            "gemma_attempts": 1,
            "gemma_external_calls": 0,
            "gemma_cache_hits": 0,
            "gemma_retries": 0,
            "pp_local_inference_calls": 0,
            "pp_cache_hits": 0,
            "qwen_attempts": 0,
            "qwen_external_calls": 0,
            "qwen_cache_hits": 0,
            "qwen_retries": 0,
            "codex_vision_runtime_calls": 0,
        },
    ).to_dict()


def test_reader_routing_result_v1_is_strict_and_human_authoritative() -> None:
    value = _minimal_result()
    assert value["schema_version"] == SCHEMA_VERSION
    assert value["human_confirmation_required"] is True
    assert value["value_authority"] == "human_confirmed_answer"
    assert value["auto_confirm"] is False
    assert value["auto_submit"] is False
    assert value["review_seed"]["human_confirmed"] is False
    assert value["review_seed"]["candidate_created"] is False

    for key, replacement in (
        ("value_authority", "machine"),
        ("human_confirmation_required", False),
        ("auto_confirm", True),
        ("auto_submit", True),
    ):
        malicious = {**value, key: replacement}
        with pytest.raises(ValueError):
            validate_reader_routing_result(malicious)

    with pytest.raises(ValueError):
        validate_reader_routing_result({**value, "candidate": {"bets": []}})


def test_cache_identities_bind_image_model_prompt_and_schema_without_secrets() -> None:
    gemma = GemmaShadowCacheIdentity(
        image_sha256=IMAGE_SHA,
        provider=GEMMA_PROVIDER_ID,
        model=GEMMA_MODEL,
        model_version=GEMMA_MODEL_VERSION,
        adapter_version=GEMMA_ADAPTER_VERSION,
        prompt_sha256=PROMPT_SHA256,
        request_schema_version=GEMMA_REQUEST_SCHEMA_VERSION,
    ).to_dict()
    assert gemma["image_sha256"] == IMAGE_SHA
    assert gemma["model"] == GEMMA_MODEL
    assert gemma["prompt_sha256"] == PROMPT_SHA256
    assert gemma["request_schema_version"] == GEMMA_REQUEST_SCHEMA_VERSION

    pp = PPShadowCacheIdentity(
        image_sha256=IMAGE_SHA,
        provider=PP_PROVIDER_ID,
        provider_version=PP_PROVIDER_VERSION,
        model=PP_MODEL,
        model_version=PP_MODEL_VERSION,
        adapter_version=PP_ADAPTER_VERSION,
    ).to_dict()
    assert pp["image_sha256"] == IMAGE_SHA
    assert pp["model"] == PP_MODEL
    assert pp["model_version"] == PP_MODEL_VERSION

    serialized = json.dumps({"gemma": gemma, "pp": pp}, sort_keys=True).lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_gemma_runtime_path_has_one_attempt_and_zero_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "image.bin"
    image.write_bytes(b"not-sent")
    calls: list[int] = []

    def timeout(_payload, _api_key, _config):
        calls.append(1)
        raise TimeoutError("mock timeout")

    monkeypatch.setenv(GEMMA_API_KEY_ENV, "test-only-secret")
    config = GemmaShadowConfig(
        enabled=True,
        endpoint="https://invalid.test/mock",
        timeout_seconds=1.0,
        cache_dir=tmp_path / "gemma-cache",
    )
    evidence = run_gemma_shadow(_request(image), config=config, transport=timeout)
    assert calls == [1]
    assert evidence["status"] == "timeout"
    assert evidence["external_call_count"] == 1
    assert evidence["retry_count"] == 0
    assert "test-only-secret" not in json.dumps(evidence)
    assert not list((tmp_path / "gemma-cache").glob("*.json"))


def test_qwen_runtime_provider_override_forces_zero_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), "white").save(image_path)
    calls: list[int] = []

    def retryable_failure(_payload, _api_key, _config):
        calls.append(1)
        raise QwenClientError("mock 500", http_status=500, retryable=True)

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-key")
    client = QwenDashScopeClient(
        config=QwenDashScopeConfig(max_retries=9),
        transport=retryable_failure,
        sleep=lambda _seconds: pytest.fail("router retry sleep must not run"),
    )
    result = QwenDashScopeProvider(client=client, retries=0).recognize(
        _request(image_path)
    )
    assert result.status.value == "failed"
    assert calls == [1]
    assert client.transport_call_count == 1


def test_qwen_runtime_router_caps_transport_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import betguard.vision.runtime_reader_router as router

    image_path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), "white").save(image_path)
    observed: dict[str, QwenDashScopeConfig] = {}

    monkeypatch.setattr(
        router.QwenDashScopeConfig,
        "from_env",
        classmethod(
            lambda _cls: QwenDashScopeConfig(
                timeout_seconds=240.0,
                max_retries=9,
            )
        ),
    )

    def stop_after_config(*, config: QwenDashScopeConfig):
        observed["config"] = config
        raise RuntimeError("test stop after config")

    monkeypatch.setattr(router, "QwenDashScopeClient", stop_after_config)
    with pytest.raises(RuntimeError, match="test stop after config"):
        router._run_qwen_once(_request(image_path))

    assert observed["config"].timeout_seconds == QWEN_RUNTIME_TIMEOUT_SECONDS == 60.0
    assert observed["config"].max_retries == 0


def test_service_exposes_runtime_router_without_changing_candidate_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import betguard.vision.service as service

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    meta = SimpleNamespace(
        storage_path=image,
        mime_type="image/png",
        sha256=IMAGE_SHA,
        width=10,
        height=10,
        byte_size=image.stat().st_size,
    )
    monkeypatch.setattr(service, "get_metadata", lambda _image_id: meta)
    monkeypatch.setattr(
        service,
        "route_runtime_readers",
        lambda request, *, second_opinion_requested: {
            **_minimal_result(),
            "image_sha256": request.metadata["sha256"],
            "second_opinion_observed": second_opinion_requested,
        },
    )
    response = service.run_job(
        "image-id",
        ROUTER_PROVIDER_ID,
        second_opinion_requested=True,
    )
    assert response["ok"] is True
    assert response["routing_result"]["second_opinion_observed"] is True
    assert "candidate" not in response
    assert "queue" not in response

    providers = service.list_providers()["providers"]
    router = next(item for item in providers if item["id"] == ROUTER_PROVIDER_ID)
    assert router["primary_machine_source"] == GEMMA_PROVIDER_ID
    assert router["conditional_fallback_source"] == "qwen-dashscope"
    assert router["runtime_retries"] == 0
    assert router["codex_vision_runtime_calls"] == 0
    assert router["value_authority"] == "human_confirmed_answer"


def test_router_import_boundary_excludes_authority_execution_and_codex_runtime() -> None:
    source_path = REPO / "src" / "betguard" / "vision" / "runtime_reader_router.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    lowered = "\n".join(imported).lower()
    for forbidden in (
        "candidate",
        "queue",
        "claim",
        "prepare",
        "mapping",
        "webfill",
        "codex",
    ):
        assert forbidden not in lowered
    assert "codex_vision_runtime_calls" in source
    assert "codex_vision_runtime_calls\": 0" in source


def test_bet_level_compiler_is_generic_and_prompt_does_not_invent_boundaries() -> None:
    router_source = (
        REPO / "src" / "betguard" / "vision" / "runtime_reader_router.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "sample-007",
        "sample_007",
        "84b5133a49da",
        "ground-truth",
        "human truth",
    ):
        assert forbidden not in router_source

    normalized_prompt = " ".join(RAW_READER_PROMPT.lower().split())
    assert "one physical betting record" in normalized_prompt
    assert "do not split those components" in normalized_prompt
    assert "never combine unrelated" in normalized_prompt
    assert "do not invent multiplication" in normalized_prompt
