from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from betguard.vision import service
from betguard.vision.contracts import (
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    SourceImage,
)
from betguard.vision.gemma_shadow import (
    ADAPTER_VERSION,
    CACHE_SCHEMA_VERSION,
    ENABLED_ENV,
    EVIDENCE_SCHEMA_VERSION,
    MODEL_NAME,
    MODEL_VERSION,
    PROVIDER_ID,
    REQUEST_SCHEMA_VERSION,
    GemmaShadowCache,
    GemmaShadowCacheIdentity,
    GemmaShadowConfig,
    compare_multi_model_evidence,
    default_gemma_shadow_cache_dir,
    get_gemma_shadow_config,
    run_gemma_shadow,
    validate_gemma_evidence,
)
from betguard.vision.ppocr_shadow import default_ppocr_shadow_cache_dir
from betguard.vision.qwen_cache import default_qwen_cache_dir


REPO = Path(__file__).resolve().parents[1]
DEMO_SHA256 = "afaef1da30dc0f569f63baad235ef5ad804a54d8f7a0e69f8beeaf315f785c56"


def _request(image: Path, sha: str = "image-sha") -> RecognitionRequest:
    return RecognitionRequest(
        request_id="job-image-id",
        image_id="image-id",
        image_path=str(image),
        mime_type="image/png",
        metadata={"sha256": sha, "width": 720, "height": 1280},
    )


def _config(tmp_path: Path, enabled: bool = True) -> GemmaShadowConfig:
    return GemmaShadowConfig(
        enabled=enabled,
        endpoint="https://example.invalid/generateContent",
        timeout_seconds=2.0,
        cache_dir=tmp_path / "gemma-cache",
    )


def _wire_item() -> dict:
    return {
        "raw_text": "30.35.36.38 3/4X1",
        "numbers": "30, 35, 36, 38",
        "multiplier_text": "3/4X1",
        "layout_guess": "normal",
        "continuation": "no",
        "special_text": "none",
        "cancelled": "no",
        "uncertain": False,
        "uncertain_reason": "none",
    }


def _envelope(item: dict | None = None) -> dict:
    raw = json.dumps(
        {"version": "gemma-raw-reader-v2", "items": [item or _wire_item()]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "candidates": [{
            "content": {"parts": [{"text": raw}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
        "modelVersion": MODEL_NAME,
        "responseId": "provider-response-123",
    }


def _completed_evidence() -> dict:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": "completed",
        "provider": {
            "id": PROVIDER_ID,
            "mode": "external_api_shadow",
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "adapter_version": ADAPTER_VERSION,
        },
        "request_id": "job-image-id:gemma-shadow",
        "model": MODEL_NAME,
        "provider_request_id": "provider-response-123",
        "image_sha256": "image-sha",
        "prompt_sha256": "prompt-sha",
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "items": [_wire_item()],
        "raw_response_text": json.dumps({"items": [_wire_item()]}, ensure_ascii=False),
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "authority": "qwen-dashscope",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "latency_ms": 10.0,
    }


def _result() -> RecognitionResult:
    return RecognitionResult(
        recognition_id="qwen-recognition",
        request_id="job-image-id",
        provider=ProviderMetadata(id="qwen-dashscope", model_name="qwen3-vl-plus"),
        source_image=SourceImage(
            image_id="image-id", sha256="image-sha", width=720, height=1280
        ),
        preprocessing={"qwen_response": {"sections": []}},
        raw_text="34",
        lines=[Line(line_id="S01-L01", order=1, text="34")],
        latency_ms=100.0,
    )


def _meta(image: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_path=image,
        mime_type="image/png",
        sha256="image-sha",
        width=720,
        height=1280,
        byte_size=image.stat().st_size,
    )


def test_default_off_and_key_is_read_at_request_time(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert get_gemma_shadow_config().enabled is False
    assert run_gemma_shadow(_request(image), config=_config(tmp_path, False))["status"] == "disabled"
    monkeypatch.setenv(ENABLED_ENV, "1")
    config = get_gemma_shadow_config()
    assert config.configured is False
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    assert config.configured is True
    assert "api_key" not in config.__dict__


def test_success_preserves_exact_raw_response_and_contract(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    envelope = _envelope()
    exact_raw = envelope["candidates"][0]["content"]["parts"][0]["text"]
    captured: dict = {}

    def transport(payload, api_key, config):
        captured.update(payload)
        assert api_key == "runtime-secret"
        return envelope

    evidence = run_gemma_shadow(
        _request(image), config=_config(tmp_path), transport=transport
    )
    assert evidence["status"] == "completed"
    assert evidence["raw_response_text"] == exact_raw
    assert evidence["provider_request_id"] == "provider-response-123"
    assert evidence["items"][0] == {"evidence_id": "GEMMA-0001", **_wire_item()}
    assert evidence["evidence_only"] is True
    assert evidence["machine_suggestion"] is True
    assert evidence["human_confirmed"] is False
    serialized = json.dumps(evidence).lower()
    assert "runtime-secret" not in serialized
    assert "inline_data" in captured["contents"][0]["parts"][1]
    response_schema = captured["generationConfig"]["responseJsonSchema"]
    assert response_schema["type"] == "object"
    assert response_schema["properties"]["version"]["enum"] == [
        "gemma-raw-reader-v2"
    ]
    assert response_schema["required"] == ["version", "items"]


def test_page_contract_stays_strict_but_invalid_items_are_rejected_individually(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    invalid = {**_wire_item(), "special_text": ["not", "a", "string"]}
    raw = json.dumps(
        {
            "version": "gemma-raw-reader-v2",
            "items": [_wire_item(), invalid, {**_wire_item(), "raw_text": "08 04 26 09"}],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    envelope = _envelope()
    envelope["candidates"][0]["content"]["parts"][0]["text"] = raw

    evidence = run_gemma_shadow(
        _request(image),
        config=_config(tmp_path),
        transport=lambda *_args: envelope,
    )

    assert evidence["status"] == "completed"
    assert evidence["raw_response_text"] == raw
    assert evidence["raw_item_count"] == 3
    assert evidence["accepted_item_count"] == 2
    assert evidence["rejected_item_count"] == 1
    assert [item["evidence_id"] for item in evidence["items"]] == [
        "GEMMA-0001",
        "GEMMA-0003",
    ]
    assert evidence["rejected_items"] == [
        {
            "item_index": 2,
            "reason_code": "GEMMA_ITEM_SPECIAL_TEXT_INVALID",
        }
    ]
    assert evidence["rejected_reason_codes"] == [
        "GEMMA_ITEM_SPECIAL_TEXT_INVALID"
    ]
    assert evidence["partial_machine_read"] is True
    assert evidence["needs_review"] is True
    assert evidence["human_confirmed"] is False


def test_bare_json_array_still_fails_the_versioned_page_contract(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    envelope = _envelope()
    envelope["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(
        [_wire_item()], separators=(",", ":")
    )

    evidence = run_gemma_shadow(
        _request(image),
        config=_config(tmp_path),
        transport=lambda *_args: envelope,
    )

    assert evidence["status"] == "failed"
    assert evidence["error"]["code"] == "GEMMA_SHADOW_INVALID_RESPONSE"
    assert evidence["external_call_count"] == 1
    assert evidence["retry_count"] == 0


def test_cache_is_atomic_validated_separate_and_does_not_store_secrets(tmp_path) -> None:
    identity = GemmaShadowCacheIdentity(
        image_sha256="image-sha",
        provider=PROVIDER_ID,
        model=MODEL_NAME,
        model_version=MODEL_VERSION,
        adapter_version=ADAPTER_VERSION,
        prompt_sha256="prompt-sha",
        request_schema_version=REQUEST_SCHEMA_VERSION,
    )
    cache = GemmaShadowCache(tmp_path / "gemma")
    target = cache.put_validated(identity, _completed_evidence())
    assert cache.get(identity)["items"][0]["raw_text"].startswith("30")
    assert not list((tmp_path / "gemma").glob("*.tmp"))
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["cache_schema_version"] == CACHE_SCHEMA_VERSION
    assert "api_key" not in json.dumps(record).lower()
    assert default_gemma_shadow_cache_dir() != default_qwen_cache_dir()
    assert default_gemma_shadow_cache_dir() != default_ppocr_shadow_cache_dir()


def test_completed_run_uses_independent_cache_and_never_calls_transport_twice(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    calls = 0

    def transport(*args):
        nonlocal calls
        calls += 1
        return _envelope()

    config = _config(tmp_path)
    first = run_gemma_shadow(_request(image), config=config, transport=transport)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    second = run_gemma_shadow(_request(image), config=config, transport=transport)
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert first["external_call_count"] == 1
    assert second["external_call_count"] == 0
    assert second["raw_response_text"] == first["raw_response_text"]
    assert calls == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(machine_suggestion=False),
        lambda value: value.update(human_confirmed=True),
        lambda value: value["items"][0].update(raw_text=""),
        lambda value: value["items"][0].update(layout_guess="confirmed"),
    ],
)
def test_invalid_or_unsafe_evidence_fails_closed(mutate) -> None:
    value = _completed_evidence()
    mutate(value)
    with pytest.raises(ValueError):
        validate_gemma_evidence(value)


def test_truncation_invalid_json_and_timeout_are_not_cached(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("GEMINI_API_KEY", "runtime-secret")
    cache = GemmaShadowCache(tmp_path / "cache")
    truncated = _envelope()
    truncated["candidates"][0]["finishReason"] = "MAX_TOKENS"
    failed = run_gemma_shadow(
        _request(image), config=_config(tmp_path), cache=cache,
        transport=lambda *args: truncated,
    )
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "GEMMA_SHADOW_FINISH_FAILURE"
    assert failed["external_call_count"] == 1
    assert failed["retry_count"] == 0
    assert not list((tmp_path / "cache").glob("*.json"))

    def timeout(*args):
        raise TimeoutError

    timed = run_gemma_shadow(
        _request(image), config=_config(tmp_path), cache=cache, transport=timeout
    )
    assert timed["status"] == "timeout"
    assert timed["error"]["code"] == "GEMMA_SHADOW_TIMEOUT"


def test_missing_credential_is_optional_and_does_not_call_transport(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = run_gemma_shadow(
        _request(image), config=_config(tmp_path),
        transport=lambda *args: pytest.fail("missing key must block transport"),
    )
    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "GEMMA_SHADOW_NOT_CONFIGURED"


def test_qwen_pp_and_gemma_are_submitted_in_parallel_without_mutating_primary(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    qwen_result = _result()
    before = copy.deepcopy(qwen_result.to_dict())
    starts: dict[str, float] = {}

    class StubProvider:
        def recognize(self, request):
            starts["qwen"] = time.perf_counter()
            time.sleep(0.15)
            return qwen_result

    def pp(request, *, config):
        starts["pp"] = time.perf_counter()
        time.sleep(0.15)
        return {"schema_version": "pp", "status": "failed", "latency_ms": 150.0}

    def gemma(request, *, config):
        starts["gemma"] = time.perf_counter()
        time.sleep(0.15)
        return _completed_evidence()

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta(image))
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: [{"status": "incomplete"}])
    monkeypatch.setattr(service, "get_ppocr_shadow_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(service, "get_gemma_shadow_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(service, "run_ppocr_shadow", pp)
    monkeypatch.setattr(service, "run_gemma_shadow", gemma)
    original_compare = compare_multi_model_evidence
    def timed_compare(*args):
        time.sleep(0.03)
        return original_compare(*args)
    monkeypatch.setattr(service, "compare_multi_model_evidence", timed_compare)

    response = service.run_job("image-id", "qwen-dashscope", game="539")
    assert response["ok"] is True
    assert response["result"] == before
    assert qwen_result.to_dict() == before
    assert response["gemma_shadow_evidence"]["authority"] == "qwen-dashscope"
    assert response["shadow_evidence"]["status"] == "failed"
    assert max(starts.values()) - min(starts.values()) < 0.08
    assert response["vision_latency"]["parallel_execution"] is True
    assert response["vision_latency"]["total_vision_latency_ms"] < 400
    assert response["vision_latency"]["comparison_latency_ms"] >= 25


def test_gemma_failure_or_internal_exception_never_fails_qwen(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    qwen_result = _result()

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta(image))
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: [])
    monkeypatch.setattr(service, "get_ppocr_shadow_config", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(service, "get_gemma_shadow_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(service, "run_gemma_shadow", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")))
    response = service.run_job("image-id", "qwen-dashscope", game="539")
    assert response["ok"] is True
    assert response["result"] == qwen_result.to_dict()
    assert response["gemma_shadow_evidence"]["error"]["code"] == "GEMMA_SHADOW_INTERNAL_FAILURE"


def test_gemma_is_nonselectable_default_off_evidence_provider(monkeypatch) -> None:
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    providers = service.list_providers()["providers"]
    provider = next(value for value in providers if value["id"] == PROVIDER_ID)
    assert provider["enabled"] is False
    assert provider["selectable"] is False
    assert provider["evidence_only"] is True
    assert provider["routing_provider"] == "runtime-reader-router"
    assert provider["primary_machine_source"] is True


def test_ui_uses_gemma_only_as_editable_plain_text_transcription() -> None:
    source = (REPO / "src/betguard/webui/assist_panel_vision_html.py").read_text(encoding="utf-8")
    service_source = (REPO / "src/betguard/vision/service.py").read_text(encoding="utf-8")

    assert 'id="vision-transcription-text"' in source
    assert 'fetch("/api/vision/v1/transcriptions"' in source
    assert 'document.getElementById("batch-text").value = text' in source
    assert "createBatch();" in source
    assert "run_gemma_shadow(request, config=config)" in service_source
    assert "machine_transcription_only" in service_source
    assert "existing_text_parser_after_explicit_user_action" in service_source
    assert "Qwen" not in source
    assert "card.accepted_by_human" not in source
    assert "card.manual_candidate_id" not in source


def test_payload_has_no_candidate_queue_draft_or_webfill_side_effects() -> None:
    serialized = json.dumps(_completed_evidence(), ensure_ascii=False).lower()
    for forbidden in (
        "manual_candidate_id", "accepted_by_human", "approved_fill_queue",
        "queue_entry", "draft_write", "webfill_call", "executable", "exportable",
    ):
        assert forbidden not in serialized


def test_qwen34_gemma30_is_page_evidence_disagreement_without_primary_mutation() -> None:
    qwen = _result().to_dict()
    qwen["preprocessing"]["qwen_response"] = {
        "sections": [{"rows": [{"numbers": [["34"]]}]}]
    }
    before = copy.deepcopy(qwen)
    gemma = _completed_evidence()
    gemma["items"][0]["numbers"] = "30"
    comparison = compare_multi_model_evidence(qwen, None, gemma)
    assert comparison["classification"] == "MULTI_MODEL_DISAGREEMENT"
    assert comparison["claims"] == {
        "qwen-dashscope": ["34"],
        PROVIDER_ID: ["30"],
    }
    assert comparison["authority"] == "qwen-dashscope"
    assert comparison["mapping_policy"] == "no_row_index_text_or_physical_bet_mapping"
    assert qwen == before


def test_all_literal_multisets_equal_is_evidence_only_agreement() -> None:
    qwen = _result().to_dict()
    qwen["preprocessing"]["qwen_response"] = {
        "sections": [{"rows": [{"numbers": [["34"]]}]}]
    }
    gemma = _completed_evidence()
    gemma["items"][0]["numbers"] = "34"
    pp = {"status": "completed", "regions": [{"text": "34"}]}
    comparison = compare_multi_model_evidence(qwen, pp, gemma)
    assert comparison["classification"] == "MULTI_MODEL_AGREEMENT"
    assert comparison["agreement_only"] is True
    assert comparison["needs_review"] is True
    assert comparison["evidence_only"] is True
    assert comparison["auto_confirm"] is False


def test_enabling_gemma_does_not_change_reconstruction_or_add_side_effects(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    qwen_result = _result()
    qwen_result.preprocessing["qwen_response"] = {
        "sections": [{"rows": [{"numbers": [["34"]]}]}]
    }
    reconstruction = [{
        "structure_id": "S01",
        "status": "incomplete",
        "reconstructed_candidate": {"number_groups": [["34"]], "multiplier_rules": []},
    }]

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    enabled = False
    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta(image))
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: copy.deepcopy(reconstruction))
    monkeypatch.setattr(service, "get_ppocr_shadow_config", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(service, "get_gemma_shadow_config", lambda: SimpleNamespace(enabled=enabled))
    monkeypatch.setattr(service, "run_gemma_shadow", lambda *args, **kwargs: _completed_evidence())

    disabled = service.run_job("image-id", "qwen-dashscope", game="539")
    enabled = True
    with_gemma = service.run_job("image-id", "qwen-dashscope", game="539")
    assert disabled["result"] == with_gemma["result"]
    assert disabled["structure_evidence"] == with_gemma["structure_evidence"]
    assert with_gemma["gemma_shadow_evidence"]["evidence_only"] is True
    serialized = json.dumps(with_gemma, ensure_ascii=False).lower()
    for forbidden in (
        "manual_candidate_id", "accepted_by_human", "approved_fill_queue",
        "queue_entry", "draft_write", "webfill_call", "executable", "exportable",
    ):
        assert forbidden not in serialized


def test_demo_scan_is_untouched() -> None:
    path = REPO / "demo_scan.py"
    if not path.exists():
        return  # clean MVP worktree intentionally excludes this local file
    assert hashlib.sha256(path.read_bytes()).hexdigest() == DEMO_SHA256
