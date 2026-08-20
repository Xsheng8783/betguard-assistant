"""Production machine-reader routing without value authority.

Gemma is the primary raw handwriting reader, PP-OCRv6 is local geometry/text
evidence, and Qwen is a conditional second reader.  This module deliberately
does not import Candidate, Queue, Claim, Prepare, Mapping, Webfill, or parser
code.  Its output is only a seed for explicit Human Review.
"""

from __future__ import annotations

import copy
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from betguard.vision.contracts import RecognitionRequest, RecognitionStatus
from betguard.vision.gemma_shadow import (
    PROVIDER_ID as GEMMA_PROVIDER_ID,
    get_gemma_shadow_config,
    run_gemma_shadow,
    validate_gemma_evidence,
)
from betguard.vision.ppocr_shadow import (
    PROVIDER_ID as PPOCR_PROVIDER_ID,
    get_ppocr_shadow_config,
    run_ppocr_shadow,
)
from betguard.vision.providers.qwen_dashscope import (
    PROVIDER_ID as QWEN_PROVIDER_ID,
    REQUEST_SCHEMA_VERSION as QWEN_REQUEST_SCHEMA_VERSION,
    QwenDashScopeClient,
    QwenDashScopeConfig,
    QwenDashScopeProvider,
)
from betguard.vision.qwen_prompts import PROMPT, PROMPT_VERSION, prompt_sha256


PROVIDER_ID = "runtime-reader-router"
SCHEMA_VERSION = "betguard.vision.reader-routing-result.v1"
REVIEW_SEED_SCHEMA_VERSION = "betguard.vision.human-review-seed.v1"
QWEN_EVIDENCE_SCHEMA_VERSION = "betguard.vision.qwen-machine-evidence.v1"
QWEN_RUNTIME_TIMEOUT_SECONDS = 60.0
VALUE_AUTHORITY = "human_confirmed_answer"

ROUTING_GEMMA_PRIMARY = "GEMMA_PRIMARY"
ROUTING_GEMMA_WITH_SECOND_OPINION = "GEMMA_PRIMARY_WITH_SECOND_OPINION"
ROUTING_QWEN_FALLBACK = "QWEN_FALLBACK"
ROUTING_MANUAL_ONLY = "MANUAL_REVIEW_ONLY"

FALLBACK_GEMMA_TIMEOUT = "GEMMA_TIMEOUT"
FALLBACK_GEMMA_API_FAILURE = "GEMMA_API_FAILURE"
FALLBACK_GEMMA_FINISH_FAILURE = "GEMMA_FINISH_FAILURE"
FALLBACK_GEMMA_SCHEMA_INVALID = "GEMMA_SCHEMA_INVALID"
FALLBACK_GEMMA_NO_USABLE_ITEMS = "GEMMA_NO_USABLE_ITEMS"
FALLBACK_SECOND_OPINION = "SECOND_OPINION_REQUESTED"

_ROUTING_DECISIONS = frozenset(
    {
        ROUTING_GEMMA_PRIMARY,
        ROUTING_GEMMA_WITH_SECOND_OPINION,
        ROUTING_QWEN_FALLBACK,
        ROUTING_MANUAL_ONLY,
    }
)
_FALLBACK_REASONS = frozenset(
    {
        FALLBACK_GEMMA_TIMEOUT,
        FALLBACK_GEMMA_API_FAILURE,
        FALLBACK_GEMMA_FINISH_FAILURE,
        FALLBACK_GEMMA_SCHEMA_INVALID,
        FALLBACK_GEMMA_NO_USABLE_ITEMS,
        FALLBACK_SECOND_OPINION,
    }
)
_COUNTER_KEYS = (
    "gemma_attempts",
    "gemma_external_calls",
    "gemma_cache_hits",
    "gemma_retries",
    "pp_local_inference_calls",
    "pp_cache_hits",
    "qwen_attempts",
    "qwen_external_calls",
    "qwen_cache_hits",
    "qwen_retries",
    "codex_vision_runtime_calls",
)
_NUMBER_LITERAL_RE = re.compile(r"(?<!\d)(?:0[1-9]|[12]\d|3[0-9])(?!\d)")
_INVALID_NUMBER_LITERAL_RE = re.compile(r"(?<!\d)(?:00|[4-9]\d)(?!\d)")
_MULTIPLIER_RULE_RE = re.compile(
    r"(?<![\d.])(?:[234](?:\s*/\s*[234])*)\s*[xX×]\s*"
    r"\d+(?:\.\d+)?(?![\d.])"
)
_COLUMN_OPERATOR_RE = re.compile(r"[xX×]")

GemmaReader = Callable[[RecognitionRequest], dict[str, Any]]
PPReader = Callable[[RecognitionRequest], dict[str, Any]]
QwenReader = Callable[[RecognitionRequest], dict[str, Any]]


@dataclass(frozen=True)
class ReaderRoutingResult:
    schema_version: str
    image_sha256: str
    routing_decision: str
    primary_machine_source: str
    fallback_reason: str | None
    gemma_evidence: dict[str, Any]
    pp_evidence: dict[str, Any]
    qwen_evidence: dict[str, Any] | None
    selected_prefill_source: str | None
    review_seed: dict[str, Any]
    field_conflicts: list[dict[str, Any]]
    cache_status: dict[str, Any]
    latency: dict[str, Any]
    model_call_counters: dict[str, int]
    human_confirmation_required: bool = True
    value_authority: str = VALUE_AUTHORITY
    auto_confirm: bool = False
    auto_submit: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "image_sha256": self.image_sha256,
            "routing_decision": self.routing_decision,
            "primary_machine_source": self.primary_machine_source,
            "fallback_reason": self.fallback_reason,
            "gemma_evidence": copy.deepcopy(self.gemma_evidence),
            "pp_evidence": copy.deepcopy(self.pp_evidence),
            "qwen_evidence": copy.deepcopy(self.qwen_evidence),
            "selected_prefill_source": self.selected_prefill_source,
            "review_seed": copy.deepcopy(self.review_seed),
            "field_conflicts": copy.deepcopy(self.field_conflicts),
            "cache_status": copy.deepcopy(self.cache_status),
            "latency": copy.deepcopy(self.latency),
            "model_call_counters": dict(self.model_call_counters),
            "human_confirmation_required": self.human_confirmation_required,
            "value_authority": self.value_authority,
            "auto_confirm": self.auto_confirm,
            "auto_submit": self.auto_submit,
        }
        return validate_reader_routing_result(value)


class RuntimeReaderRouter:
    """Run primary/evidence readers and conditionally invoke Qwen once."""

    def __init__(
        self,
        *,
        gemma_reader: GemmaReader | None = None,
        pp_reader: PPReader | None = None,
        qwen_reader: QwenReader | None = None,
    ) -> None:
        self._gemma_reader = gemma_reader or _run_primary_gemma
        self._pp_reader = pp_reader or _run_local_ppocr
        self._qwen_reader = qwen_reader or _run_qwen_once

    def route(
        self,
        request: RecognitionRequest,
        *,
        second_opinion_requested: bool = False,
    ) -> ReaderRoutingResult:
        started = time.perf_counter()
        image_sha256 = str(request.metadata.get("sha256") or "")
        if re.fullmatch(r"[0-9a-fA-F]{64}", image_sha256) is None:
            raise ValueError("RecognitionRequest metadata.sha256 must be a SHA-256 hex string")

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-reader") as pool:
            gemma_future = pool.submit(_call_gemma, self._gemma_reader, request)
            pp_future = pool.submit(_call_pp, self._pp_reader, request)
            gemma_evidence = gemma_future.result()
            pp_evidence = pp_future.result()

        gemma_usable = _gemma_usable_item_count(gemma_evidence)
        fallback_reason = _fallback_reason(
            gemma_evidence,
            usable_item_count=gemma_usable,
            second_opinion_requested=second_opinion_requested,
        )
        qwen_allowed = fallback_reason in _FALLBACK_REASONS
        qwen_evidence: dict[str, Any] | None = None
        if qwen_allowed:
            qwen_evidence = _call_qwen(self._qwen_reader, request)

        gemma_success = gemma_evidence.get("status") == "completed" and gemma_usable > 0
        qwen_success = bool(
            isinstance(qwen_evidence, dict)
            and qwen_evidence.get("status") == RecognitionStatus.COMPLETED.value
        )
        if gemma_success:
            selected_prefill_source = GEMMA_PROVIDER_ID
            routing_decision = (
                ROUTING_GEMMA_WITH_SECOND_OPINION
                if second_opinion_requested
                else ROUTING_GEMMA_PRIMARY
            )
        elif qwen_success:
            selected_prefill_source = QWEN_PROVIDER_ID
            routing_decision = ROUTING_QWEN_FALLBACK
        else:
            selected_prefill_source = None
            routing_decision = ROUTING_MANUAL_ONLY

        counters = _model_call_counters(
            gemma_evidence,
            pp_evidence,
            qwen_evidence,
            qwen_attempted=qwen_allowed,
        )
        cache_status = {
            "gemma": _cache_record(gemma_evidence),
            "ppocr": _cache_record(pp_evidence),
            "qwen": _cache_record(qwen_evidence),
        }
        review_seed = _review_seed(
            selected_prefill_source,
            gemma_evidence=gemma_evidence,
            qwen_evidence=qwen_evidence,
        )
        conflicts = _unmapped_second_opinion_conflicts(
            gemma_success=gemma_success,
            qwen_success=qwen_success,
        )
        latency = {
            "gemma_latency_ms": _nonnegative_float(gemma_evidence.get("latency_ms")),
            "pp_latency_ms": _nonnegative_float(pp_evidence.get("latency_ms")),
            "qwen_latency_ms": _nonnegative_float(
                qwen_evidence.get("latency_ms") if isinstance(qwen_evidence, dict) else 0.0
            ),
            "total_routing_latency_ms": round(
                (time.perf_counter() - started) * 1000.0, 3
            ),
            "gemma_pp_parallel": True,
            "qwen_conditional_after_gemma": qwen_allowed,
        }
        return ReaderRoutingResult(
            schema_version=SCHEMA_VERSION,
            image_sha256=image_sha256.lower(),
            routing_decision=routing_decision,
            primary_machine_source=GEMMA_PROVIDER_ID,
            fallback_reason=fallback_reason,
            gemma_evidence=gemma_evidence,
            pp_evidence=pp_evidence,
            qwen_evidence=qwen_evidence,
            selected_prefill_source=selected_prefill_source,
            review_seed=review_seed,
            field_conflicts=conflicts,
            cache_status=cache_status,
            latency=latency,
            model_call_counters=counters,
        )


def route_runtime_readers(
    request: RecognitionRequest,
    *,
    second_opinion_requested: bool = False,
    router: RuntimeReaderRouter | None = None,
) -> dict[str, Any]:
    return (router or RuntimeReaderRouter()).route(
        request,
        second_opinion_requested=second_opinion_requested,
    ).to_dict()


def validate_reader_routing_result(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "image_sha256",
        "routing_decision",
        "primary_machine_source",
        "fallback_reason",
        "gemma_evidence",
        "pp_evidence",
        "qwen_evidence",
        "selected_prefill_source",
        "review_seed",
        "field_conflicts",
        "cache_status",
        "latency",
        "model_call_counters",
        "human_confirmation_required",
        "value_authority",
        "auto_confirm",
        "auto_submit",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("ReaderRoutingResult keys are invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("ReaderRoutingResult schema is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", str(value.get("image_sha256") or "")) is None:
        raise ValueError("ReaderRoutingResult image SHA is invalid")
    if value.get("routing_decision") not in _ROUTING_DECISIONS:
        raise ValueError("ReaderRoutingResult routing decision is invalid")
    if value.get("primary_machine_source") != GEMMA_PROVIDER_ID:
        raise ValueError("Gemma must remain the primary machine source")
    if value.get("fallback_reason") not in {None, *_FALLBACK_REASONS}:
        raise ValueError("ReaderRoutingResult fallback reason is invalid")
    if value.get("selected_prefill_source") not in {
        None,
        GEMMA_PROVIDER_ID,
        QWEN_PROVIDER_ID,
    }:
        raise ValueError("ReaderRoutingResult selected prefill source is invalid")
    if not isinstance(value.get("gemma_evidence"), dict):
        raise ValueError("Gemma evidence must be an object")
    if not isinstance(value.get("pp_evidence"), dict):
        raise ValueError("PP evidence must be an object")
    if value.get("qwen_evidence") is not None and not isinstance(
        value.get("qwen_evidence"), dict
    ):
        raise ValueError("Qwen evidence must be null or an object")
    if not isinstance(value.get("field_conflicts"), list):
        raise ValueError("field_conflicts must be a list")
    if not isinstance(value.get("cache_status"), dict) or set(value["cache_status"]) != {
        "gemma",
        "ppocr",
        "qwen",
    }:
        raise ValueError("cache_status is invalid")
    if not isinstance(value.get("latency"), dict):
        raise ValueError("latency is invalid")
    counters = value.get("model_call_counters")
    if not isinstance(counters, dict) or tuple(counters) != _COUNTER_KEYS:
        raise ValueError("model_call_counters keys are invalid")
    if any(
        isinstance(counters[key], bool)
        or not isinstance(counters[key], int)
        or counters[key] < 0
        for key in _COUNTER_KEYS
    ):
        raise ValueError("model call counters must be non-negative integers")
    if counters["gemma_retries"] != 0 or counters["qwen_retries"] != 0:
        raise ValueError("runtime reader retries must be zero")
    if counters["qwen_external_calls"] > 1 or counters["qwen_attempts"] > 1:
        raise ValueError("Qwen may be attempted externally at most once")
    if counters["codex_vision_runtime_calls"] != 0:
        raise ValueError("Codex Vision production runtime calls must remain zero")
    if (
        value.get("human_confirmation_required") is not True
        or value.get("value_authority") != VALUE_AUTHORITY
        or value.get("auto_confirm") is not False
        or value.get("auto_submit") is not False
    ):
        raise ValueError("ReaderRoutingResult authority boundary is invalid")
    review_seed = value.get("review_seed")
    if not isinstance(review_seed, dict) or review_seed.get("human_confirmed") is not False:
        raise ValueError("review seed must remain unconfirmed")
    if review_seed.get("value_authority") != VALUE_AUTHORITY:
        raise ValueError("review seed value authority is invalid")
    return copy.deepcopy(dict(value))


def _run_primary_gemma(request: RecognitionRequest) -> dict[str, Any]:
    config = replace(get_gemma_shadow_config(), enabled=True)
    return run_gemma_shadow(request, config=config)


def _run_local_ppocr(request: RecognitionRequest) -> dict[str, Any]:
    config = replace(get_ppocr_shadow_config(), enabled=True)
    return run_ppocr_shadow(request, config=config)


def _run_qwen_once(request: RecognitionRequest) -> dict[str, Any]:
    configured = QwenDashScopeConfig.from_env()
    config = replace(
        configured,
        timeout_seconds=min(
            configured.timeout_seconds,
            QWEN_RUNTIME_TIMEOUT_SECONDS,
        ),
        max_retries=0,
    )
    client = QwenDashScopeClient(config=config)
    result = QwenDashScopeProvider(client=client, retries=0).recognize(request)
    result_dict = result.to_dict()
    request_metadata = (
        (result_dict.get("preprocessing") or {}).get("qwen_request") or {}
    )
    cache_hit = bool(
        request_metadata.get("cache_hit")
    )
    source_image = result_dict.get("source_image") or {}
    return {
        "schema_version": QWEN_EVIDENCE_SCHEMA_VERSION,
        "status": result.status.value,
        "provider": QWEN_PROVIDER_ID,
        "recognition_result": result_dict,
        "cache_hit": cache_hit,
        "cache_identity": {
            "image_sha256": str(
                request_metadata.get("image_sha256")
                or source_image.get("sha256")
                or request.metadata.get("sha256")
                or ""
            ),
            "model": str(request_metadata.get("model") or config.model),
            "prompt_version": str(
                request_metadata.get("prompt_version") or PROMPT_VERSION
            ),
            "prompt_sha256": str(
                request_metadata.get("prompt_sha256") or prompt_sha256(PROMPT)
            ),
            "request_schema_version": str(
                request_metadata.get("request_schema_version")
                or QWEN_REQUEST_SCHEMA_VERSION
            ),
        },
        "external_call_count": client.transport_call_count,
        "retry_count": 0,
        "latency_ms": round(float(result.latency_ms), 3),
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "value_authority": VALUE_AUTHORITY,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _call_gemma(
    reader: GemmaReader, request: RecognitionRequest
) -> dict[str, Any]:
    try:
        evidence = reader(request)
    except Exception as exc:
        return _machine_failure(
            GEMMA_PROVIDER_ID,
            "GEMMA_RUNTIME_INTERNAL_FAILURE",
            type(exc).__name__,
        )
    if not isinstance(evidence, dict):
        return _machine_failure(
            GEMMA_PROVIDER_ID,
            "GEMMA_ROUTER_SCHEMA_INVALID",
            "non_object",
        )
    evidence = copy.deepcopy(evidence)
    if evidence.get("status") == "completed":
        try:
            evidence = validate_gemma_evidence(evidence)
        except ValueError as exc:
            return {
                **_machine_failure(
                    GEMMA_PROVIDER_ID,
                    "GEMMA_ROUTER_SCHEMA_INVALID",
                    type(exc).__name__,
                ),
                "external_call_count": _bounded_call_count(
                    evidence.get("external_call_count")
                ),
                "cache_hit": bool(evidence.get("cache_hit")),
                "latency_ms": _nonnegative_float(evidence.get("latency_ms")),
            }
    return evidence


def _call_pp(reader: PPReader, request: RecognitionRequest) -> dict[str, Any]:
    try:
        evidence = reader(request)
    except Exception as exc:
        return _machine_failure(
            PPOCR_PROVIDER_ID,
            "PPOCR_RUNTIME_INTERNAL_FAILURE",
            type(exc).__name__,
        )
    if not isinstance(evidence, dict):
        return _machine_failure(
            PPOCR_PROVIDER_ID,
            "PPOCR_ROUTER_SCHEMA_INVALID",
            "non_object",
        )
    return copy.deepcopy(evidence)


def _call_qwen(reader: QwenReader, request: RecognitionRequest) -> dict[str, Any]:
    try:
        evidence = reader(request)
    except Exception as exc:
        return {
            **_machine_failure(
                QWEN_PROVIDER_ID,
                "QWEN_RUNTIME_INTERNAL_FAILURE",
                type(exc).__name__,
            ),
            "schema_version": QWEN_EVIDENCE_SCHEMA_VERSION,
        }
    if not isinstance(evidence, dict):
        return {
            **_machine_failure(
                QWEN_PROVIDER_ID,
                "QWEN_ROUTER_SCHEMA_INVALID",
                "non_object",
            ),
            "schema_version": QWEN_EVIDENCE_SCHEMA_VERSION,
        }
    result = copy.deepcopy(evidence)
    result.setdefault("schema_version", QWEN_EVIDENCE_SCHEMA_VERSION)
    result.setdefault("retry_count", 0)
    result.setdefault("external_call_count", 0)
    result.setdefault("cache_hit", False)
    result.setdefault("latency_ms", 0.0)
    result.setdefault("evidence_only", True)
    result.setdefault("machine_suggestion", True)
    result.setdefault("human_confirmed", False)
    result.setdefault("human_confirmation_required", True)
    result.setdefault("value_authority", VALUE_AUTHORITY)
    result.setdefault("auto_apply", False)
    result.setdefault("auto_confirm", False)
    result.setdefault("auto_submit", False)
    if _bounded_call_count(result.get("external_call_count")) > 1:
        return {
            **_machine_failure(
                QWEN_PROVIDER_ID,
                "QWEN_EXTERNAL_CALL_LIMIT_EXCEEDED",
                "external_call_count",
            ),
            "schema_version": QWEN_EVIDENCE_SCHEMA_VERSION,
        }
    return result


def _fallback_reason(
    gemma_evidence: Mapping[str, Any],
    *,
    usable_item_count: int,
    second_opinion_requested: bool,
) -> str | None:
    if gemma_evidence.get("status") == "completed":
        if usable_item_count == 0:
            return FALLBACK_GEMMA_NO_USABLE_ITEMS
        if second_opinion_requested:
            return FALLBACK_SECOND_OPINION
        return None
    code = str((gemma_evidence.get("error") or {}).get("code") or "")
    if code == "GEMMA_SHADOW_TIMEOUT":
        return FALLBACK_GEMMA_TIMEOUT
    if code in {
        "GEMMA_SHADOW_HTTP_ERROR",
        "GEMMA_SHADOW_API_FAILURE",
        "GEMMA_SHADOW_NETWORK_ERROR",
    }:
        return FALLBACK_GEMMA_API_FAILURE
    if code in {
        "GEMMA_SHADOW_FINISH_FAILURE",
        "GEMMA_SHADOW_TRUNCATED",
    }:
        return FALLBACK_GEMMA_FINISH_FAILURE
    if code in {
        "GEMMA_SHADOW_INVALID_RESPONSE",
        "GEMMA_ROUTER_SCHEMA_INVALID",
    } and _bounded_call_count(gemma_evidence.get("external_call_count")) == 1:
        return FALLBACK_GEMMA_SCHEMA_INVALID
    return None


def _gemma_usable_item_count(evidence: Mapping[str, Any]) -> int:
    if evidence.get("status") != "completed":
        return 0
    items = evidence.get("items")
    if not isinstance(items, list):
        return 0
    return sum(
        1
        for item in items
        if isinstance(item, dict) and str(item.get("raw_text") or "").strip()
    )


def _model_call_counters(
    gemma: Mapping[str, Any],
    ppocr: Mapping[str, Any],
    qwen: Mapping[str, Any] | None,
    *,
    qwen_attempted: bool,
) -> dict[str, int]:
    return {
        "gemma_attempts": 1,
        "gemma_external_calls": _bounded_call_count(
            gemma.get("external_call_count")
        ),
        "gemma_cache_hits": int(bool(gemma.get("cache_hit"))),
        "gemma_retries": 0,
        "pp_local_inference_calls": _bounded_call_count(
            ppocr.get("local_inference_calls")
        ),
        "pp_cache_hits": int(bool(ppocr.get("cache_hit"))),
        "qwen_attempts": int(qwen_attempted),
        "qwen_external_calls": _bounded_call_count(
            qwen.get("external_call_count") if isinstance(qwen, Mapping) else 0
        ),
        "qwen_cache_hits": int(
            bool(qwen.get("cache_hit")) if isinstance(qwen, Mapping) else False
        ),
        "qwen_retries": 0,
        "codex_vision_runtime_calls": 0,
    }


def _cache_record(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    if evidence is None:
        return {"checked": False, "cache_hit": False, "identity": None}
    return {
        "checked": True,
        "cache_hit": bool(evidence.get("cache_hit")),
        "identity": copy.deepcopy(evidence.get("cache_identity")),
    }


def _review_seed(
    selected_source: str | None,
    *,
    gemma_evidence: Mapping[str, Any],
    qwen_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    bet_drafts: list[dict[str, Any]] = []
    unresolved_machine_fragments: list[dict[str, Any]] = []
    machine_read_diagnostics = {
        "raw_item_count": 0,
        "accepted_item_count": 0,
        "rejected_item_count": 0,
        "rejected_reason_codes": [],
    }
    if selected_source == GEMMA_PROVIDER_ID:
        machine_read_diagnostics = {
            "raw_item_count": int(gemma_evidence.get("raw_item_count") or 0),
            "accepted_item_count": int(
                gemma_evidence.get("accepted_item_count") or 0
            ),
            "rejected_item_count": int(
                gemma_evidence.get("rejected_item_count") or 0
            ),
            "rejected_reason_codes": list(
                gemma_evidence.get("rejected_reason_codes") or []
            ),
        }
        for index, item in enumerate(gemma_evidence.get("items") or [], 1):
            if not isinstance(item, Mapping):
                continue
            promoted, reason = _promote_gemma_item(item, index)
            if promoted is not None:
                bet_drafts.append(promoted)
            else:
                line_drafts = _promote_gemma_line_candidates(item, index)
                bet_drafts.extend(line_drafts)
                fragment = _unresolved_gemma_fragment(item, index, reason)
                if line_drafts:
                    fragment.update(
                        reason_code="PARTIAL_RECORD_GROUP_REQUIRES_REVIEW",
                        source_reason_code=reason,
                        promoted_draft_ids=[
                            draft["draft_id"] for draft in line_drafts
                        ],
                    )
                unresolved_machine_fragments.append(fragment)
    elif selected_source == QWEN_PROVIDER_ID and isinstance(qwen_evidence, Mapping):
        recognition = qwen_evidence.get("recognition_result")
        if isinstance(recognition, Mapping):
            for index, line in enumerate(recognition.get("lines") or [], 1):
                if not isinstance(line, Mapping):
                    continue
                bet_drafts.append(
                    {
                        "draft_id": f"qwen-line-{index:04d}",
                        "source_evidence_id": line.get("line_id"),
                        "raw_text": str(line.get("text") or ""),
                        "number_groups_raw": "",
                        "multiplier_raw": "",
                        "layout_suggestion": "unclear",
                        "continuation_suggestion": "unclear",
                        "special_play_raw": "none",
                        "cancelled_suggestion": "unclear",
                        "uncertain": True,
                    }
                )
    machine_read_diagnostics.update(
        bet_draft_count=len(bet_drafts),
        unresolved_fragment_count=len(unresolved_machine_fragments),
    )
    partial_machine_read = bool(
        selected_source == GEMMA_PROVIDER_ID
        and (
            machine_read_diagnostics["rejected_item_count"] > 0
            or unresolved_machine_fragments
        )
    )
    machine_read_diagnostics.update(
        partial_machine_read=partial_machine_read,
        needs_review=bool(partial_machine_read or not bet_drafts),
    )
    return {
        "schema_version": REVIEW_SEED_SCHEMA_VERSION,
        "status": "machine_prefill_available" if bet_drafts else "manual_entry_required",
        "selected_machine_source": selected_source,
        "bet_drafts": bet_drafts,
        # Kept as a value-identical compatibility alias for older clients. New
        # clients must render only bet_drafts and treat raw evidence separately.
        "draft_items": copy.deepcopy(bet_drafts),
        "unresolved_machine_fragments": unresolved_machine_fragments,
        "needs_review": bool(partial_machine_read or not bet_drafts),
        "partial_machine_read": partial_machine_read,
        "machine_read_diagnostics": machine_read_diagnostics,
        "structured_human_answer_required": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "value_authority": VALUE_AUTHORITY,
        "candidate_created": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _promote_gemma_item(
    item: Mapping[str, Any], item_index: int
) -> tuple[dict[str, Any] | None, str]:
    raw_text = str(item.get("raw_text") or "")
    if not raw_text.strip():
        return None, "RAW_TEXT_EMPTY"
    if str(item.get("continuation") or "unclear") == "yes":
        return None, "CONTINUATION_FRAGMENT_REQUIRES_REVIEW"
    if str(item.get("continuation") or "unclear") != "no":
        return None, "CONTINUATION_STATE_UNCLEAR"
    cancelled = str(item.get("cancelled") or "unclear")
    if cancelled not in {"yes", "no"}:
        return None, "CANCELLED_STATE_UNCLEAR"
    layout = str(item.get("layout_guess") or "unclear")
    if layout == "unclear":
        return None, "LAYOUT_UNCLEAR"

    multiplier_rules, body = _literal_multiplier_rules_and_body(raw_text)
    multiplier_text = str(item.get("multiplier_text") or "").strip()
    if (
        multiplier_text
        and multiplier_text.lower() not in {"none", "x", "×"}
        and not multiplier_rules
    ):
        return None, "MULTIPLIER_OR_CATEGORY_FRAGMENT_REQUIRES_REVIEW"
    if _INVALID_NUMBER_LITERAL_RE.search(body):
        return None, "NUMBER_LITERAL_OUT_OF_RANGE"

    raw_numbers = _number_literals(body)
    number_field_literals = _number_literals(str(item.get("numbers") or ""))
    if not raw_numbers:
        if multiplier_rules:
            return None, "MULTIPLIER_OR_CATEGORY_FRAGMENT_REQUIRES_REVIEW"
        return None, "NO_VALID_NUMBER_EVIDENCE"
    number_order_matches = number_field_literals[: len(raw_numbers)] == raw_numbers

    has_column_operator = _COLUMN_OPERATOR_RE.search(body) is not None
    if layout == "column" and not has_column_operator:
        return None, "COLUMN_LITERAL_OPERATOR_MISSING"
    if has_column_operator:
        number_groups = _literal_column_groups(body)
        if number_groups is None:
            if (
                layout == "normal"
                and not number_order_matches
                and len(number_field_literals) >= 3
                and sorted(number_field_literals) == sorted(raw_numbers)
            ):
                number_groups = [number_field_literals]
                promoted_layout = "normal"
            else:
                return None, "COLUMN_STRUCTURE_AMBIGUOUS"
        else:
            flattened = [number for group in number_groups for number in group]
            if number_field_literals[: len(flattened)] != flattened:
                if (
                    layout == "normal"
                    and not number_order_matches
                    and len(number_field_literals) >= 3
                    and sorted(number_field_literals) == sorted(raw_numbers)
                ):
                    number_groups = [number_field_literals]
                    promoted_layout = "normal"
                else:
                    return None, "COLUMN_LITERAL_ORDER_CONFLICT"
            else:
                promoted_layout = "column"
    elif layout == "column":
        return None, "COLUMN_STRUCTURE_AMBIGUOUS"
    else:
        if not number_order_matches:
            return None, "NUMBER_LITERAL_ORDER_CONFLICT"
        body_lines = [line for line in body.splitlines() if line.strip()]
        if len(body_lines) > 1:
            return None, "MULTI_LINE_SCOPE_AMBIGUOUS"
        if not multiplier_rules and cancelled != "yes" and len(raw_numbers) < 3:
            return None, "INCOMPLETE_BET_SCOPE"
        number_groups = [raw_numbers]
        promoted_layout = "normal"

    return (
        {
            "draft_id": f"gemma-bet-{item_index:04d}",
            "source_evidence_id": item.get("evidence_id"),
            "raw_text": raw_text,
            "number_groups_raw": str(item.get("numbers") or ""),
            "number_groups_suggestion": number_groups,
            "multiplier_raw": multiplier_text,
            "multiplier_rules_suggestion": multiplier_rules,
            "layout_suggestion": promoted_layout,
            "continuation_suggestion": "no",
            "special_play_raw": str(item.get("special_text") or "none"),
            "cancelled_suggestion": cancelled,
            "uncertain": bool(item.get("uncertain", True)),
            "operator_conflict_downgraded_to_normal": bool(
                has_column_operator and promoted_layout == "normal"
            ),
            "promotion_contract": "betguard.gemma-bet-promotion.v1",
        },
        "PROMOTED",
    )


def _promote_gemma_line_candidates(
    item: Mapping[str, Any], item_index: int
) -> list[dict[str, Any]]:
    raw_text = str(item.get("raw_text") or "")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    if str(item.get("continuation") or "unclear") != "no":
        return []
    if str(item.get("cancelled") or "unclear") != "no":
        return []

    # A cross-field literal-order check is the guard against model-invented
    # multiplication operators. Line promotion is disabled if the independent
    # numbers reading does not preserve the raw line order exactly.
    if _number_literals(raw_text) != _number_literals(str(item.get("numbers") or "")):
        return []

    drafts: list[dict[str, Any]] = []
    offset = 0
    while offset < len(lines):
        line_index = offset + 1
        line = lines[offset]
        line_rules, line_body = _literal_multiplier_rules_and_body(line)
        line_numbers = _number_literals(line_body)
        source_line_end = line_index

        # A literal column anchor row may consume exactly one following line
        # when that line contains both continuation numbers and a complete
        # multiplier rule. This is a bounded, local record boundary—not a
        # nearest-neighbour merge. A preceding singleton makes the scope
        # ambiguous, so the pair remains unresolved.
        if (
            _COLUMN_OPERATOR_RE.search(line_body)
            and not line_rules
            and offset + 1 < len(lines)
        ):
            next_line = lines[offset + 1]
            next_rules, next_body = _literal_multiplier_rules_and_body(next_line)
            previous_is_singleton = False
            if offset > 0:
                previous_rules, previous_body = _literal_multiplier_rules_and_body(
                    lines[offset - 1]
                )
                previous_is_singleton = bool(
                    not previous_rules
                    and _COLUMN_OPERATOR_RE.search(previous_body) is None
                    and len(_number_literals(previous_body)) == 1
                )
            if (
                next_rules
                and _COLUMN_OPERATOR_RE.search(next_body) is None
                and _number_literals(next_body)
                and not previous_is_singleton
            ):
                line = f"{line}\n{next_line}"
                line_rules, line_body = _literal_multiplier_rules_and_body(line)
                line_numbers = _number_literals(line_body)
                source_line_end = line_index + 1

        if len(line_numbers) < 2 or (not line_rules and len(line_numbers) < 3):
            offset += source_line_end - line_index + 1
            continue
        line_item = {
            **item,
            "raw_text": line,
            "numbers": line,
            "multiplier_text": ",".join(line_rules) if line_rules else "none",
            "layout_guess": (
                "column" if _COLUMN_OPERATOR_RE.search(line_body) else "normal"
            ),
            "continuation": "no",
            "special_text": "none",
            "cancelled": "no",
        }
        promoted, _reason = _promote_gemma_item(line_item, item_index)
        if promoted is None:
            offset += source_line_end - line_index + 1
            continue
        promoted.update(
            draft_id=f"gemma-bet-{item_index:04d}-{line_index:02d}",
            source_line_number=line_index,
            source_line_end=source_line_end,
            source_item_split=True,
        )
        drafts.append(promoted)
        offset += source_line_end - line_index + 1
    return drafts


def _unresolved_gemma_fragment(
    item: Mapping[str, Any], item_index: int, reason: str
) -> dict[str, Any]:
    return {
        "fragment_id": f"gemma-fragment-{item_index:04d}",
        "source_evidence_id": item.get("evidence_id"),
        "reason_code": reason,
        "raw_text": str(item.get("raw_text") or ""),
        "numbers": str(item.get("numbers") or ""),
        "multiplier_text": str(item.get("multiplier_text") or ""),
        "layout_guess": str(item.get("layout_guess") or "unclear"),
        "continuation": str(item.get("continuation") or "unclear"),
        "special_text": str(item.get("special_text") or "none"),
        "cancelled": str(item.get("cancelled") or "unclear"),
        "uncertain": bool(item.get("uncertain", True)),
        "needs_review": True,
        "human_confirmed": False,
    }


def _literal_multiplier_rules_and_body(raw_text: str) -> tuple[list[str], str]:
    matches = list(_MULTIPLIER_RULE_RE.finditer(raw_text))
    rules: list[str] = []
    for match in matches:
        canonical = re.sub(r"\s+", "", match.group(0)).replace("×", "X").upper()
        if canonical not in rules:
            rules.append(canonical)
    characters = list(raw_text)
    for match in matches:
        for position in range(match.start(), match.end()):
            if characters[position] not in "\r\n":
                characters[position] = " "
    return rules, "".join(characters)


def _number_literals(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER_LITERAL_RE.finditer(text)]


def _literal_column_groups(body: str) -> list[list[str]] | None:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines or _COLUMN_OPERATOR_RE.search(lines[0]) is None:
        return None
    first_parts = _COLUMN_OPERATOR_RE.split(lines[0])
    groups = [_number_literals(part) for part in first_parts]
    if len(groups) < 2 or any(not group for group in groups):
        return None
    # V1 only promotes an unambiguous chain of single literal anchors plus a
    # final group that may receive same-record continuation numbers. A multi-
    # number left segment followed by x and one value is also a common
    # multiplier-shaped fragment, so it must remain unresolved.
    if any(len(group) != 1 for group in groups[:-1]):
        return None
    continuation_seen = False
    for line in lines[1:]:
        if _COLUMN_OPERATOR_RE.search(line) is None:
            continuation_numbers = _number_literals(line)
            if continuation_numbers:
                groups[-1].extend(continuation_numbers)
                continuation_seen = True
            continue
        if continuation_seen:
            return None
        row_parts = _COLUMN_OPERATOR_RE.split(line)
        row_groups = [_number_literals(part) for part in row_parts]
        if len(row_groups) != len(groups) or any(len(group) != 1 for group in row_groups):
            return None
        for group, row_group in zip(groups, row_groups):
            group.extend(row_group)
    return groups


def _unmapped_second_opinion_conflicts(
    *, gemma_success: bool, qwen_success: bool
) -> list[dict[str, Any]]:
    if not (gemma_success and qwen_success):
        return []
    return [
        {
            "scope": "unmapped_page_evidence",
            "classification": "SECOND_OPINION_REQUIRES_HUMAN_COMPARISON",
            "automatic_field_mapping": False,
            "automatic_vote": False,
            "automatic_overwrite": False,
            "needs_review": True,
        }
    ]


def _machine_failure(provider: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "provider": {"id": provider},
        "error": {"code": code, "detail": detail[:80]},
        "cache_hit": False,
        "external_call_count": 0,
        "retry_count": 0,
        "latency_ms": 0.0,
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "value_authority": VALUE_AUTHORITY,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _bounded_call_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, 1))


def _nonnegative_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(max(0.0, float(value)), 3)
