from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.gemma_shadow import (
    ADAPTER_VERSION as GEMMA_ADAPTER_VERSION,
    EVIDENCE_SCHEMA_VERSION as GEMMA_SCHEMA_VERSION,
    MODEL_NAME as GEMMA_MODEL,
    MODEL_VERSION as GEMMA_MODEL_VERSION,
    PROVIDER_ID as GEMMA_PROVIDER_ID,
    REQUEST_SCHEMA_VERSION as GEMMA_REQUEST_SCHEMA_VERSION,
)
from betguard.vision.runtime_reader_router import (
    FALLBACK_GEMMA_API_FAILURE,
    FALLBACK_GEMMA_FINISH_FAILURE,
    FALLBACK_GEMMA_NO_USABLE_ITEMS,
    FALLBACK_GEMMA_SCHEMA_INVALID,
    FALLBACK_GEMMA_TIMEOUT,
    FALLBACK_SECOND_OPINION,
    QWEN_EVIDENCE_SCHEMA_VERSION,
    ROUTING_GEMMA_PRIMARY,
    ROUTING_GEMMA_WITH_SECOND_OPINION,
    ROUTING_MANUAL_ONLY,
    ROUTING_QWEN_FALLBACK,
    RuntimeReaderRouter,
)


IMAGE_SHA = "a" * 64


def _request(tmp_path: Path) -> RecognitionRequest:
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture-only")
    return RecognitionRequest(
        request_id="runtime-route-test",
        image_id="image-id",
        image_path=str(image),
        mime_type="image/png",
        metadata={
            "sha256": IMAGE_SHA,
            "width": 100,
            "height": 200,
            "size_bytes": image.stat().st_size,
        },
    )


def _gemma_item() -> dict[str, Any]:
    return {
        "raw_text": "08x01\n04 2x5",
        "numbers": "08 01 04",
        "multiplier_text": "2x5",
        "layout_guess": "column",
        "continuation": "yes",
        "special_text": "none",
        "cancelled": "no",
        "uncertain": False,
        "uncertain_reason": "none",
    }


def _gemma(
    *,
    items: list[dict[str, Any]] | None = None,
    cache_hit: bool = False,
    external_calls: int | None = None,
) -> dict[str, Any]:
    external_calls = int(not cache_hit) if external_calls is None else external_calls
    return {
        "schema_version": GEMMA_SCHEMA_VERSION,
        "provider": {
            "id": GEMMA_PROVIDER_ID,
            "mode": "external_api_shadow",
            "model_name": GEMMA_MODEL,
            "model_version": GEMMA_MODEL_VERSION,
            "adapter_version": GEMMA_ADAPTER_VERSION,
        },
        "request_id": "runtime-route-test:gemma-shadow",
        "model": GEMMA_MODEL,
        "image_sha256": IMAGE_SHA,
        "prompt_sha256": "b" * 64,
        "request_schema_version": GEMMA_REQUEST_SCHEMA_VERSION,
        "status": "completed",
        "items": [_gemma_item()] if items is None else items,
        "raw_response_text": "{}",
        "provider_request_id": "mock-response",
        "finish_reason": "STOP",
        "usage": {},
        "cache_hit": cache_hit,
        "cache_identity": {
            "image_sha256": IMAGE_SHA,
            "provider": GEMMA_PROVIDER_ID,
            "model": GEMMA_MODEL,
            "model_version": GEMMA_MODEL_VERSION,
            "adapter_version": GEMMA_ADAPTER_VERSION,
            "prompt_sha256": "b" * 64,
            "request_schema_version": GEMMA_REQUEST_SCHEMA_VERSION,
        },
        "external_call_count": external_calls,
        "retry_count": 0,
        "latency_ms": 10.0,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "authority": "qwen-dashscope",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _gemma_failure(code: str, *, external_calls: int = 1) -> dict[str, Any]:
    return {
        "schema_version": GEMMA_SCHEMA_VERSION,
        "status": "timeout" if code == "GEMMA_SHADOW_TIMEOUT" else "failed",
        "provider": {"id": GEMMA_PROVIDER_ID},
        "error": {"code": code},
        "cache_hit": False,
        "external_call_count": external_calls,
        "retry_count": 0,
        "latency_ms": 5.0,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _pp(*, status: str = "completed", cache_hit: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "betguard.vision.ppocr-shadow-evidence.v1",
        "status": status,
        "provider": {"id": "ppocrv6-shadow"},
        "regions": [] if status == "completed" else None,
        "cache_hit": cache_hit,
        "cache_identity": {
            "image_sha256": IMAGE_SHA,
            "provider": "ppocrv6-shadow",
            "provider_version": "fixture",
            "model": "PP-OCRv6_medium",
            "model_version": "fixture",
            "adapter_version": "betguard.ppocr-shadow.v1",
        },
        "local_inference_calls": 0 if cache_hit or status != "completed" else 1,
        "latency_ms": 4.0,
        "evidence_only": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    if status != "completed":
        result["error"] = {"code": "PPOCR_SUBPROCESS_FAILED"}
    return result


def _qwen(*, status: str = "completed", cache_hit: bool = False) -> dict[str, Any]:
    return {
        "schema_version": QWEN_EVIDENCE_SCHEMA_VERSION,
        "status": status,
        "provider": "qwen-dashscope",
        "recognition_result": {
            "status": status,
            "raw_text": "08 01 04",
            "lines": [
                {"line_id": "S01-L01", "text": "08 01 04"},
            ] if status == "completed" else [],
        },
        "cache_hit": cache_hit,
        "cache_identity": {
            "image_sha256": IMAGE_SHA,
            "model": "qwen3-vl-plus",
            "prompt_version": "combined-bbox-v1",
            "prompt_sha256": "d" * 64,
            "request_schema_version": "dashscope-compatible-chat-completions-v1",
        },
        "external_call_count": 0 if cache_hit else 1,
        "retry_count": 0,
        "latency_ms": 7.0,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "value_authority": "human_confirmed_answer",
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _router(
    gemma: dict[str, Any],
    *,
    pp: dict[str, Any] | None = None,
    qwen: dict[str, Any] | None = None,
    qwen_calls: list[int] | None = None,
) -> RuntimeReaderRouter:
    def qwen_reader(_request: RecognitionRequest) -> dict[str, Any]:
        if qwen_calls is not None:
            qwen_calls.append(1)
        return copy.deepcopy(qwen if qwen is not None else _qwen())

    return RuntimeReaderRouter(
        gemma_reader=lambda _request: copy.deepcopy(gemma),
        pp_reader=lambda _request: copy.deepcopy(pp if pp is not None else _pp()),
        qwen_reader=qwen_reader,
    )


def test_gemma_success_is_primary_and_qwen_external_calls_zero(tmp_path: Path) -> None:
    calls: list[int] = []
    result = _router(_gemma(), qwen_calls=calls).route(_request(tmp_path)).to_dict()

    assert calls == []
    assert result["routing_decision"] == ROUTING_GEMMA_PRIMARY
    assert result["selected_prefill_source"] == GEMMA_PROVIDER_ID
    assert result["model_call_counters"]["gemma_external_calls"] == 1
    assert result["model_call_counters"]["qwen_attempts"] == 0
    assert result["model_call_counters"]["qwen_external_calls"] == 0
    assert result["review_seed"]["draft_items"][0]["number_groups_raw"] == "08 01 04"


def test_partial_gemma_read_keeps_valid_items_and_never_calls_qwen(
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    gemma = _gemma()
    gemma.update(
        raw_item_count=2,
        accepted_item_count=1,
        rejected_item_count=1,
        rejected_items=[
            {"item_index": 2, "reason_code": "GEMMA_ITEM_LAYOUT_INVALID"}
        ],
        rejected_reason_codes=["GEMMA_ITEM_LAYOUT_INVALID"],
        partial_machine_read=True,
        needs_review=True,
    )

    result = _router(gemma, qwen_calls=calls).route(_request(tmp_path)).to_dict()

    assert calls == []
    assert result["routing_decision"] == ROUTING_GEMMA_PRIMARY
    assert result["fallback_reason"] is None
    assert result["selected_prefill_source"] == GEMMA_PROVIDER_ID
    assert len(result["review_seed"]["draft_items"]) == 1
    assert result["review_seed"]["partial_machine_read"] is True
    assert result["review_seed"]["needs_review"] is True
    assert result["review_seed"]["machine_read_diagnostics"] == {
        "raw_item_count": 2,
        "accepted_item_count": 1,
        "rejected_item_count": 1,
        "rejected_reason_codes": ["GEMMA_ITEM_LAYOUT_INVALID"],
    }
    assert result["review_seed"]["human_confirmed"] is False
    assert result["model_call_counters"]["qwen_external_calls"] == 0


def test_gemma_cache_hit_does_not_count_external_call(tmp_path: Path) -> None:
    result = _router(_gemma(cache_hit=True)).route(_request(tmp_path)).to_dict()
    counters = result["model_call_counters"]
    assert counters["gemma_attempts"] == 1
    assert counters["gemma_external_calls"] == 0
    assert counters["gemma_cache_hits"] == 1
    assert result["cache_status"]["gemma"]["cache_hit"] is True


@pytest.mark.parametrize(
    ("gemma", "reason"),
    [
        (_gemma_failure("GEMMA_SHADOW_TIMEOUT"), FALLBACK_GEMMA_TIMEOUT),
        (_gemma_failure("GEMMA_SHADOW_HTTP_ERROR"), FALLBACK_GEMMA_API_FAILURE),
        (
            _gemma_failure("GEMMA_SHADOW_FINISH_FAILURE"),
            FALLBACK_GEMMA_FINISH_FAILURE,
        ),
    ],
)
def test_allowed_gemma_failures_call_qwen_exactly_once(
    tmp_path: Path, gemma: dict[str, Any], reason: str
) -> None:
    calls: list[int] = []
    result = _router(gemma, qwen_calls=calls).route(_request(tmp_path)).to_dict()
    assert calls == [1]
    assert result["fallback_reason"] == reason
    assert result["routing_decision"] == ROUTING_QWEN_FALLBACK
    assert result["model_call_counters"]["qwen_attempts"] == 1
    assert result["model_call_counters"]["qwen_external_calls"] == 1


def test_gemma_strict_schema_invalid_calls_qwen_exactly_once(tmp_path: Path) -> None:
    invalid = _gemma()
    invalid["items"][0]["layout_guess"] = "invented"
    calls: list[int] = []
    result = _router(invalid, qwen_calls=calls).route(_request(tmp_path)).to_dict()
    assert calls == [1]
    assert result["fallback_reason"] == FALLBACK_GEMMA_SCHEMA_INVALID
    assert result["gemma_evidence"]["error"]["code"] == "GEMMA_ROUTER_SCHEMA_INVALID"


def test_gemma_completed_with_zero_usable_items_calls_qwen_once(tmp_path: Path) -> None:
    calls: list[int] = []
    result = _router(_gemma(items=[]), qwen_calls=calls).route(
        _request(tmp_path)
    ).to_dict()
    assert calls == [1]
    assert result["fallback_reason"] == FALLBACK_GEMMA_NO_USABLE_ITEMS
    assert result["selected_prefill_source"] == "qwen-dashscope"


def test_explicit_second_opinion_never_overrides_gemma_prefill(tmp_path: Path) -> None:
    calls: list[int] = []
    result = _router(_gemma(), qwen_calls=calls).route(
        _request(tmp_path), second_opinion_requested=True
    ).to_dict()
    assert calls == [1]
    assert result["fallback_reason"] == FALLBACK_SECOND_OPINION
    assert result["routing_decision"] == ROUTING_GEMMA_WITH_SECOND_OPINION
    assert result["selected_prefill_source"] == GEMMA_PROVIDER_ID
    assert result["field_conflicts"] == [
        {
            "scope": "unmapped_page_evidence",
            "classification": "SECOND_OPINION_REQUIRES_HUMAN_COMPARISON",
            "automatic_field_mapping": False,
            "automatic_vote": False,
            "automatic_overwrite": False,
            "needs_review": True,
        }
    ]


def test_qwen_fallback_failure_still_produces_manual_review(tmp_path: Path) -> None:
    result = _router(
        _gemma_failure("GEMMA_SHADOW_TIMEOUT"),
        qwen=_qwen(status="failed"),
    ).route(_request(tmp_path)).to_dict()
    assert result["routing_decision"] == ROUTING_MANUAL_ONLY
    assert result["selected_prefill_source"] is None
    assert result["review_seed"]["status"] == "manual_entry_required"
    assert result["review_seed"]["human_confirmed"] is False
    assert result["review_seed"]["candidate_created"] is False


def test_pp_failure_does_not_block_gemma_or_manual_review(tmp_path: Path) -> None:
    gemma_result = _router(_gemma(), pp=_pp(status="failed")).route(
        _request(tmp_path)
    ).to_dict()
    assert gemma_result["routing_decision"] == ROUTING_GEMMA_PRIMARY

    manual = _router(
        _gemma_failure("GEMMA_SHADOW_NOT_CONFIGURED", external_calls=0),
        pp=_pp(status="failed"),
    ).route(_request(tmp_path)).to_dict()
    assert manual["routing_decision"] == ROUTING_MANUAL_ONLY
    assert manual["model_call_counters"]["qwen_attempts"] == 0


def test_model_counters_are_exact_and_retries_are_zero(tmp_path: Path) -> None:
    result = _router(
        _gemma_failure("GEMMA_SHADOW_TIMEOUT"),
        pp=_pp(cache_hit=True),
        qwen=_qwen(cache_hit=True),
    ).route(_request(tmp_path)).to_dict()
    assert result["model_call_counters"] == {
        "gemma_attempts": 1,
        "gemma_external_calls": 1,
        "gemma_cache_hits": 0,
        "gemma_retries": 0,
        "pp_local_inference_calls": 0,
        "pp_cache_hits": 1,
        "qwen_attempts": 1,
        "qwen_external_calls": 0,
        "qwen_cache_hits": 1,
        "qwen_retries": 0,
        "codex_vision_runtime_calls": 0,
    }
    assert result["cache_status"]["qwen"]["identity"] == {
        "image_sha256": IMAGE_SHA,
        "model": "qwen3-vl-plus",
        "prompt_version": "combined-bbox-v1",
        "prompt_sha256": "d" * 64,
        "request_schema_version": "dashscope-compatible-chat-completions-v1",
    }


def test_gemma_and_pp_begin_in_parallel(tmp_path: Path) -> None:
    starts: dict[str, float] = {}
    barrier = threading.Barrier(2)

    def gemma(_request: RecognitionRequest) -> dict[str, Any]:
        starts["gemma"] = time.perf_counter()
        barrier.wait(timeout=1)
        time.sleep(0.03)
        return _gemma()

    def pp(_request: RecognitionRequest) -> dict[str, Any]:
        starts["pp"] = time.perf_counter()
        barrier.wait(timeout=1)
        time.sleep(0.03)
        return _pp()

    result = RuntimeReaderRouter(
        gemma_reader=gemma,
        pp_reader=pp,
        qwen_reader=lambda _request: pytest.fail("Qwen must not run"),
    ).route(_request(tmp_path)).to_dict()
    assert abs(starts["gemma"] - starts["pp"]) < 0.05
    assert result["latency"]["gemma_pp_parallel"] is True
    assert result["latency"]["qwen_conditional_after_gemma"] is False


def test_machine_evidence_is_not_mutated_or_fused(tmp_path: Path) -> None:
    gemma = _gemma()
    pp = _pp()
    qwen = _qwen()
    before = copy.deepcopy((gemma, pp, qwen))
    _router(gemma, pp=pp, qwen=qwen).route(
        _request(tmp_path), second_opinion_requested=True
    )
    assert (gemma, pp, qwen) == before
