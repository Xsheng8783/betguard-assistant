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
    BoundingBox,
    Confidence,
    Line,
    ProviderMetadata,
    RecognitionResult,
    RecognitionRequest,
    SourceImage,
    Token,
)
from betguard.vision.evidence_comparison import compare_ppocr_to_qwen
from betguard.vision.ppocr_shadow import (
    ADAPTER_VERSION,
    CACHE_SCHEMA_VERSION,
    MODEL_NAME,
    MODEL_VERSION,
    PROVIDER_ID,
    PROVIDER_VERSION,
    WORKER_SCHEMA_VERSION,
    PPShadowCache,
    PPShadowCacheIdentity,
    PPShadowConfig,
    _sanitized_subprocess_env,
    default_ppocr_shadow_cache_dir,
    get_ppocr_shadow_config,
    run_ppocr_shadow,
)
from betguard.vision.qwen_cache import default_qwen_cache_dir


REPO = Path(__file__).resolve().parents[1]
DEMO_SHA256 = "afaef1da30dc0f569f63baad235ef5ad804a54d8f7a0e69f8beeaf315f785c56"


def _box(x1: float, y1: float, x2: float, y2: float) -> BoundingBox:
    return BoundingBox(
        coordinate_space="pixel",
        polygon=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    )


def _result(*specs: tuple[str, float, float, float, float]) -> RecognitionResult:
    if not specs:
        specs = (("34", 10, 10, 30, 30),)
    line_text = " ".join(spec[0] for spec in specs)
    tokens: list[Token] = []
    cursor = 0
    for index, (text, x1, y1, x2, y2) in enumerate(specs, 1):
        start = line_text.index(text, cursor)
        end = start + len(text)
        cursor = end
        tokens.append(Token(
            token_id=f"S01-L01-T{index:02d}",
            text=text,
            start=start,
            end=end,
            confidence=Confidence(value=None, source="qwen", calibrated=False),
            bounding_box=_box(x1, y1, x2, y2),
        ))
    return RecognitionResult(
        recognition_id="rec-shadow-test",
        request_id="job-image-id",
        provider=ProviderMetadata(
            id="qwen-dashscope", model_name="qwen3-vl-plus"
        ),
        source_image=SourceImage(
            image_id="image-id", sha256="image-sha", width=1000, height=600
        ),
        preprocessing={"qwen_response": {"sections": []}},
        raw_text=line_text,
        lines=[Line(
            line_id="S01-L01",
            order=1,
            text=line_text,
            tokens=tokens,
            bounding_box=_box(5, 5, max(spec[3] for spec in specs) + 5, 35),
        )],
        latency_ms=100.0,
    )


def _production_qwen3vl_result(
    *specs: tuple[str, float, float, float, float],
    line_id: str = "S01-L01",
) -> RecognitionResult:
    """Build audited full-page Qwen3-VL normalized-1000 evidence."""
    result = _result(*specs)
    result.source_image.width = 720
    result.source_image.height = 1280
    result.source_image.sha256 = (
        "7f15be60d6876febcd5b455c361a868bad8ddab0c3b7a442d029f0140ad738a6"
    )
    result.preprocessing.update({
        "task_type": "full_page_combined",
        "prompt_version": "combined-bbox-v1",
    })
    result.lines[0].line_id = line_id
    for index, token in enumerate(result.lines[0].tokens, 1):
        token.token_id = f"{line_id}-T{index:02d}"
    return result


def _sample011_s02_result() -> RecognitionResult:
    result = _production_qwen3vl_result(
        ("34", 54, 225, 95, 257),
        (".", 95, 230, 108, 252),
        ("35", 112, 225, 152, 257),
        (".", 152, 230, 165, 252),
        ("36", 170, 225, 210, 257),
        (".", 210, 230, 223, 252),
        ("38", 228, 225, 268, 257),
        (" ", 268, 228, 282, 254),
        ("3", 285, 215, 315, 257),
        ("/", 315, 225, 332, 257),
        ("4", 332, 225, 362, 257),
        ("x", 365, 225, 385, 257),
        ("1", 388, 225, 415, 257),
        line_id="S02-L01",
    )
    result.preprocessing["qwen_response"] = {
        "sections": [{}, {
            "rows": [{
                "numbers": [["34"], ["35"], ["36"], ["38"]],
                "multiplier": "3/4x1",
                "layout_hint": "row_bet",
            }],
            "shared_multiplier": None,
        }],
    }
    return result


def _pp(
    text: str,
    bbox: list[float],
    *,
    confidence: float = 0.96,
    order: int = 1,
) -> dict:
    x1, y1, x2, y2 = bbox
    return {
        "evidence_id": f"PP-{order:04d}",
        "text": text,
        "confidence": confidence,
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "bbox": bbox,
        "order": order,
        "provider": PROVIDER_ID,
        "model": MODEL_NAME,
    }


def _worker_result(*regions: dict) -> dict:
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "status": "completed",
        "regions": list(regions or (_pp("30", [10, 10, 30, 30]),)),
        "latency_ms": 123.0,
    }


def _shadow_result(*regions: dict, status: str = "completed") -> dict:
    return {
        "schema_version": "betguard.vision.ppocr-shadow-evidence.v1",
        "status": status,
        "provider": {
            "id": PROVIDER_ID,
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "provider_version": PROVIDER_VERSION,
            "adapter_version": ADAPTER_VERSION,
        },
        "regions": list(regions),
        "cache_hit": False,
        "latency_ms": 150.0,
        "evidence_only": True,
        "authority": "qwen-dashscope",
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _meta() -> SimpleNamespace:
    return SimpleNamespace(
        storage_path=Path("unused.png"),
        mime_type="image/png",
        sha256="image-sha",
        width=1000,
        height=600,
        byte_size=100,
    )


def _enable_shadow(monkeypatch, shadow_callable) -> None:
    monkeypatch.setattr(
        service,
        "get_ppocr_shadow_config",
        lambda: SimpleNamespace(enabled=True, configured=True),
    )
    monkeypatch.setattr(service, "run_ppocr_shadow", shadow_callable)


def test_geometry_match_agree_and_disagree_never_selects_pp_authority() -> None:
    qwen = _result(("34", 10, 10, 30, 30)).to_dict()
    agree = compare_ppocr_to_qwen(qwen, [_pp("34", [10, 10, 30, 30])], image_width=1000, image_height=600)
    disagree = compare_ppocr_to_qwen(qwen, [_pp("30", [10, 10, 30, 30])], image_width=1000, image_height=600)
    assert agree["records"][0]["classification"] == "AGREE"
    assert disagree["records"][0]["classification"] == "DISAGREE"
    assert disagree["records"][0]["qwen"]["text"] == "34"
    assert disagree["records"][0]["ppocr"]["text"] == "30"
    assert disagree["authority"] == "qwen-dashscope"
    assert disagree["auto_apply"] is False
    assert disagree["auto_confirm"] is False
    assert disagree["auto_submit"] is False


def test_geometry_matching_uses_position_not_index_or_text() -> None:
    qwen = _result(("30", 10, 10, 30, 30), ("34", 100, 10, 120, 30)).to_dict()
    pp = [_pp("34", [100, 10, 120, 30], order=2), _pp("30", [10, 10, 30, 30], order=1)]
    comparison = compare_ppocr_to_qwen(qwen, pp, image_width=1000, image_height=600)
    matched = [record for record in comparison["records"] if record["qwen"] and record["ppocr"]]
    assert [(record["qwen"]["text"], record["ppocr"]["text"]) for record in matched] == [("34", "34"), ("30", "30")]
    far_same_text = compare_ppocr_to_qwen(
        qwen, [_pp("30", [500, 500, 530, 530])], image_width=1000, image_height=600
    )
    assert far_same_text["summary"] == {"PP_ONLY": 1, "QWEN_ONLY": 2}
    assert comparison["index_fallback"] is False
    assert comparison["string_fallback"] is False


def test_geometry_ambiguous_is_unresolved() -> None:
    qwen = _result(("30", 10, 10, 30, 30), ("34", 12, 10, 32, 30)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen, [_pp("30", [11, 10, 31, 30])], image_width=1000, image_height=600
    )
    record = comparison["records"][0]
    assert record["classification"] == "UNMATCHED_GEOMETRY"
    assert record["resolved"] is False
    assert record["warning"] == "pp_fused_region_has_no_token_bbox"
    assert record["local_token_geometry"]["candidate_count"] == 2


def test_sample011_s02_actual_cache_geometry_is_row_only_and_not_false_disagree() -> None:
    qwen_result = _sample011_s02_result()
    before = copy.deepcopy(qwen_result.to_dict())
    pp_fused = _pp(
        "30.35.36.38×1",
        [30, 269, 334, 347],
        confidence=0.8861289620399475,
        order=8,
    )
    pp_fused.update({
        "evidence_id": "PP-0008",
        "polygon": [[30, 274], [332, 269], [334, 342], [32, 347]],
    })
    pp_standalone = _pp(
        "30",
        [204, 559, 250, 598],
        confidence=0.9998488426208496,
        order=19,
    )
    pp_standalone.update({
        "evidence_id": "PP-0019",
        "polygon": [[204, 563], [247, 559], [250, 594], [207, 598]],
    })

    comparison = compare_ppocr_to_qwen(
        qwen_result.to_dict(),
        [pp_fused, pp_standalone],
        image_width=720,
        image_height=1280,
    )

    transform = comparison["coordinate_transform"]
    assert transform["reliable"] is True
    assert transform["source_contract"] == "qwen3vl_normalized_1000"
    assert transform["recognition_contract_label"] == "pixel"
    assert transform["scale_x"] == pytest.approx(0.72)
    assert transform["scale_y"] == pytest.approx(1.28)
    fused_record = next(
        record
        for record in comparison["records"]
        if (record.get("ppocr") or {}).get("evidence_id") == "PP-0008"
    )
    standalone_record = next(
        record
        for record in comparison["records"]
        if (record.get("ppocr") or {}).get("evidence_id") == "PP-0019"
    )
    assert fused_record["classification"] == "UNMATCHED_GEOMETRY"
    assert fused_record["warning"] == "pp_fused_region_has_no_token_bbox"
    assert fused_record["row_association"]["line_id"] == "S02-L01"
    assert fused_record["row_association"]["metrics"] == {
        "horizontal_overlap": 259.92,
        "vertical_overlap": 53.76,
        "horizontal_overlap_ratio": 1.0,
        "vertical_overlap_ratio": 1.0,
        "center_dx_px": 13.16,
        "center_dy_px": 5.92,
        "normalized_center_dx": 0.043289,
        "normalized_center_dy": 0.075897,
        "iou": 0.589292,
    }
    assert fused_record["local_token_geometry"]["candidate_count"] == 13
    assert standalone_record["classification"] == "PP_ONLY"
    assert standalone_record["warning"] == "no_qwen_row_geometry_match"
    assert not any(record["classification"] == "DISAGREE" for record in comparison["records"])
    assert comparison["fused_bbox_split"] is False
    assert qwen_result.to_dict() == before
    assert qwen_result.preprocessing["qwen_response"]["sections"][1]["rows"][0]["numbers"] == [
        ["34"], ["35"], ["36"], ["38"]
    ]


def test_qwen3vl_normalized1000_unique_same_text_can_agree() -> None:
    qwen = _production_qwen3vl_result(("34", 100, 100, 150, 150)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("34", [72, 128, 108, 192])],
        image_width=720,
        image_height=1280,
    )
    assert comparison["summary"] == {"AGREE": 1}
    assert comparison["records"][0]["qwen"]["bbox"] == [72.0, 128.0, 108.0, 192.0]


def test_qwen3vl_normalized1000_unique_different_text_is_evidence_only_disagree() -> None:
    qwen = _production_qwen3vl_result(("34", 100, 100, 150, 150)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("30", [72, 128, 108, 192])],
        image_width=720,
        image_height=1280,
    )
    assert comparison["summary"] == {"DISAGREE": 1}
    assert comparison["records"][0]["authority"] == "qwen-dashscope"
    assert comparison["auto_apply"] is False


def test_same_text_at_wrong_position_never_uses_string_fallback() -> None:
    qwen = _production_qwen3vl_result(("30", 100, 100, 150, 150)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("30", [400, 800, 440, 840])],
        image_width=720,
        image_height=1280,
    )
    assert comparison["summary"] == {"PP_ONLY": 1, "QWEN_ONLY": 1}
    assert comparison["string_fallback"] is False


def test_fused_pp_region_is_never_split_into_qwen_subtoken_boxes() -> None:
    qwen = _production_qwen3vl_result(
        ("30", 100, 100, 150, 150),
        (".", 155, 105, 170, 145),
        ("35", 175, 100, 225, 150),
    ).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("30.35", [72, 128, 162, 192])],
        image_width=720,
        image_height=1280,
    )
    record = comparison["records"][0]
    assert record["classification"] == "UNMATCHED_GEOMETRY"
    assert record["warning"] == "pp_fused_region_has_no_token_bbox"
    assert record["qwen"] is None
    assert comparison["fused_bbox_split"] is False


def test_ambiguous_row_band_is_unmatched_before_local_token_matching() -> None:
    qwen_result = _result(("30", 10, 10, 30, 30))
    second = copy.deepcopy(qwen_result.lines[0])
    second.line_id = "S02-L01"
    second.order = 2
    second.tokens[0].token_id = "S02-L01-T01"
    qwen_result.lines.append(second)
    comparison = compare_ppocr_to_qwen(
        qwen_result.to_dict(),
        [_pp("30", [10, 10, 30, 30])],
        image_width=1000,
        image_height=600,
    )
    record = comparison["records"][0]
    assert record["classification"] == "UNMATCHED_GEOMETRY"
    assert record["warning"] == "row_geometry_ambiguous"
    assert record["row_association"]["candidate_line_ids"] == ["S01-L01", "S02-L01"]


def test_multiple_pp_claims_to_one_token_are_all_unmatched() -> None:
    qwen = _result(("30", 10, 10, 30, 30)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("30", [10, 10, 30, 30], order=1), _pp("30", [10, 10, 30, 30], order=2)],
        image_width=1000,
        image_height=600,
    )
    pp_records = [record for record in comparison["records"] if record["ppocr"]]
    assert len(pp_records) == 2
    assert all(record["classification"] == "UNMATCHED_GEOMETRY" for record in pp_records)
    assert all(record["warning"] == "multiple_pp_regions_claim_qwen_token" for record in pp_records)


def test_unrecognized_production_coordinate_contract_fails_closed() -> None:
    qwen_result = _production_qwen3vl_result(("30", 100, 100, 150, 150))
    qwen_result.preprocessing["prompt_version"] = "unknown-contract"
    # Fail closed rather than silently accepting the misleading pixel label.
    comparison = compare_ppocr_to_qwen(
        qwen_result.to_dict(),
        [_pp("30", [72, 128, 108, 192])],
        image_width=720,
        image_height=1280,
    )
    assert comparison["coordinate_transform"]["reliable"] is False
    assert comparison["coordinate_transform"]["reason"] == (
        "qwen3vl_full_page_coordinate_contract_not_audited"
    )
    assert comparison["summary"] == {"UNMATCHED_GEOMETRY": 2}


def test_qwen3vl_normalized1000_out_of_range_fails_closed() -> None:
    qwen = _production_qwen3vl_result(("30", 1001, 100, 1050, 150)).to_dict()
    comparison = compare_ppocr_to_qwen(
        qwen,
        [_pp("30", [720, 128, 756, 192])],
        image_width=720,
        image_height=1280,
    )
    assert comparison["coordinate_transform"]["reliable"] is False
    assert comparison["coordinate_transform"]["reason"] == "qwen3vl_normalized_1000_out_of_range"
    assert not any(record["classification"] in {"AGREE", "DISAGREE"} for record in comparison["records"])


def test_comparison_does_not_mutate_tokens_bboxes_or_preprocessing_numbers() -> None:
    result = _sample011_s02_result()
    before = copy.deepcopy(result.to_dict())
    compare_ppocr_to_qwen(
        result.to_dict(),
        [_pp("30.35.36.38×1", [30, 269, 334, 347])],
        image_width=720,
        image_height=1280,
    )
    assert result.to_dict() == before
    assert result.preprocessing["qwen_response"]["sections"][1]["rows"][0]["numbers"] == [
        ["34"], ["35"], ["36"], ["38"]
    ]


def test_low_confidence_pp_is_flagged_without_overriding_qwen() -> None:
    comparison = compare_ppocr_to_qwen(
        _result(("30", 10, 10, 30, 30)).to_dict(),
        [_pp("30", [10, 10, 30, 30], confidence=0.4)],
        image_width=1000,
        image_height=600,
    )
    assert comparison["records"][0]["classification"] == "LOW_CONFIDENCE_PP"
    assert comparison["records"][0]["authority"] == "qwen-dashscope"


def test_invalid_geometry_is_unmatched_and_never_borrowed() -> None:
    comparison = compare_ppocr_to_qwen(
        _result().to_dict(),
        [{**_pp("34", [10, 10, 30, 30]), "bbox": None}],
        image_width=1000,
        image_height=600,
    )
    assert comparison["records"][0]["classification"] == "UNMATCHED_GEOMETRY"
    assert comparison["records"][0]["warning"] == "pp_bbox_invalid"


def test_qwen_and_pp_success_run_in_parallel_and_preserve_primary(monkeypatch) -> None:
    qwen_result = _result(("34", 10, 10, 30, 30))
    before = copy.deepcopy(qwen_result.to_dict())

    class StubProvider:
        def recognize(self, request):
            time.sleep(0.15)
            return qwen_result

    def shadow(request, *, config):
        time.sleep(0.15)
        return _shadow_result(_pp("30", [10, 10, 30, 30]))

    reconstruction = [{"structure_id": "S01", "reconstructed_candidate": {"number_groups": [["34"]], "multiplier_rules": []}}]
    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: copy.deepcopy(reconstruction))
    _enable_shadow(monkeypatch, shadow)

    response = service.run_job("image-id", "qwen-dashscope", game="539")

    assert response["ok"] is True
    assert response["result"] == before
    assert qwen_result.to_dict() == before
    assert response["structure_evidence"] == reconstruction
    assert response["shadow_evidence"]["comparison"]["summary"] == {"DISAGREE": 1}
    latency = response["vision_latency"]
    assert latency["parallel_execution"] is True
    assert latency["total_vision_latency_ms"] < latency["pp_latency_ms"] + latency["qwen_latency_ms"] - 20
    assert latency["comparison_latency_ms"] >= 0


def test_pp_failure_and_unexpected_exception_do_not_fail_qwen(monkeypatch) -> None:
    qwen_result = _result()

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: [{"status": "incomplete"}])

    _enable_shadow(monkeypatch, lambda request, *, config: _shadow_result(status="failed"))
    failed = service.run_job("image-id", "qwen-dashscope", game="539")
    assert failed["ok"] is True
    assert failed["result"] == qwen_result.to_dict()
    assert failed["shadow_evidence"]["status"] == "failed"

    def raises(request, *, config):
        raise RuntimeError("cuda/model failure")

    _enable_shadow(monkeypatch, raises)
    unexpected = service.run_job("image-id", "qwen-dashscope", game="539")
    assert unexpected["ok"] is True
    assert unexpected["result"] == qwen_result.to_dict()
    assert unexpected["shadow_evidence"]["error"]["code"] == "PPOCR_SHADOW_INTERNAL_FAILURE"


def test_shadow_config_and_comparison_failures_are_optional(monkeypatch) -> None:
    qwen_result = _result()

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: [])
    monkeypatch.setattr(
        service,
        "get_ppocr_shadow_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config")),
    )
    config_failure = service.run_job("image-id", "qwen-dashscope", game="539")
    assert config_failure["ok"] is True
    assert config_failure["result"] == qwen_result.to_dict()
    assert config_failure["shadow_evidence"]["error"]["code"] == "PPOCR_SHADOW_INTERNAL_FAILURE"

    _enable_shadow(
        monkeypatch,
        lambda request, *, config: _shadow_result(_pp("34", [10, 10, 30, 30])),
    )
    monkeypatch.setattr(
        service,
        "compare_ppocr_to_qwen",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("compare")),
    )
    comparison_failure = service.run_job("image-id", "qwen-dashscope", game="539")
    assert comparison_failure["ok"] is True
    assert comparison_failure["result"] == qwen_result.to_dict()
    assert comparison_failure["shadow_evidence"]["comparison"]["error"]["code"] == "PPOCR_COMPARISON_FAILED"


def test_shadow_is_explicit_opt_in_and_not_a_selectable_provider(monkeypatch) -> None:
    monkeypatch.delenv("BETGUARD_PPOCR_SHADOW_ENABLED", raising=False)
    assert get_ppocr_shadow_config().enabled is False
    providers = service.list_providers()["providers"]
    item = next(provider for provider in providers if provider["id"] == PROVIDER_ID)
    assert item["selectable"] is False
    assert item["evidence_only"] is True
    assert item["external_network"] is False
    assert item["routing_provider"] == "runtime-reader-router"


def test_pp_cache_is_atomic_validated_and_separate_from_qwen(tmp_path) -> None:
    identity = PPShadowCacheIdentity(
        image_sha256="abc",
        provider=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        model=MODEL_NAME,
        model_version=MODEL_VERSION,
        adapter_version=ADAPTER_VERSION,
    )
    cache = PPShadowCache(tmp_path / "pp")
    result = _worker_result()
    target = cache.put_validated(identity, result)
    assert target.name == f"{identity.key()}.json"
    assert cache.get(identity)["regions"][0]["text"] == "30"
    assert not list((tmp_path / "pp").glob("*.tmp"))
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["cache_schema_version"] == CACHE_SCHEMA_VERSION
    assert "api_key" not in json.dumps(record).lower()
    assert default_ppocr_shadow_cache_dir() != default_qwen_cache_dir()
    with pytest.raises(ValueError):
        cache.put_validated(identity, {"schema_version": WORKER_SCHEMA_VERSION, "status": "failed", "regions": []})


def test_subprocess_environment_excludes_cloud_credentials(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-qwen")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    config = get_ppocr_shadow_config()
    env = _sanitized_subprocess_env(config)
    assert "DASHSCOPE_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["PADDLE_PDX_CACHE_HOME"] == str(config.model_cache)


def test_isolated_subprocess_success_then_separate_cache_hit(tmp_path, monkeypatch) -> None:
    python = tmp_path / "isolated-python.exe"
    worker = tmp_path / "worker.py"
    image = tmp_path / "image.png"
    for path in (python, worker, image):
        path.write_bytes(b"test")
    (tmp_path / "models" / "official_models" / "PP-OCRv6_medium_det").mkdir(parents=True)
    (tmp_path / "models" / "official_models" / "PP-OCRv6_medium_rec").mkdir(parents=True)
    config = PPShadowConfig(
        enabled=True,
        python_executable=python,
        worker_script=worker,
        timeout_seconds=5.0,
        device="gpu:0",
        model_cache=tmp_path / "models",
        cache_dir=tmp_path / "cache",
    )
    request = RecognitionRequest(
        request_id="job-test",
        image_id="image-id",
        image_path=str(image),
        metadata={"sha256": "image-sha"},
    )
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text(json.dumps(_worker_result()), encoding="utf-8")
        assert kwargs["shell"] is False
        assert "DASHSCOPE_API_KEY" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("betguard.vision.ppocr_shadow.subprocess.run", fake_run)
    first = run_ppocr_shadow(request, config=config)
    second = run_ppocr_shadow(request, config=config)
    assert first["status"] == "completed"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert calls == 1


def test_timeout_is_optional_failure_and_never_cached(tmp_path, monkeypatch) -> None:
    import subprocess

    python = tmp_path / "isolated-python.exe"
    worker = tmp_path / "worker.py"
    image = tmp_path / "image.png"
    for path in (python, worker, image):
        path.write_bytes(b"test")
    (tmp_path / "models" / "official_models" / "PP-OCRv6_medium_det").mkdir(parents=True)
    (tmp_path / "models" / "official_models" / "PP-OCRv6_medium_rec").mkdir(parents=True)
    config = PPShadowConfig(
        enabled=True,
        python_executable=python,
        worker_script=worker,
        timeout_seconds=1.0,
        device="gpu:0",
        model_cache=tmp_path / "models",
        cache_dir=tmp_path / "cache",
    )
    request = RecognitionRequest(
        request_id="job-test",
        image_id="image-id",
        image_path=str(image),
        metadata={"sha256": "image-sha"},
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1.0)

    monkeypatch.setattr("betguard.vision.ppocr_shadow.subprocess.run", timeout)
    result = run_ppocr_shadow(request, config=config)
    assert result["status"] == "timeout"
    assert result["error"]["code"] == "PPOCR_TIMEOUT"
    assert not list(config.cache_dir.glob("*.json"))


def test_missing_local_models_fail_before_subprocess_or_download(tmp_path, monkeypatch) -> None:
    python = tmp_path / "isolated-python.exe"
    worker = tmp_path / "worker.py"
    image = tmp_path / "image.png"
    for path in (python, worker, image):
        path.write_bytes(b"test")
    config = PPShadowConfig(
        enabled=True,
        python_executable=python,
        worker_script=worker,
        timeout_seconds=5.0,
        device="gpu:0",
        model_cache=tmp_path / "missing-models",
        cache_dir=tmp_path / "cache",
    )
    request = RecognitionRequest(
        request_id="job-test",
        image_id="image-id",
        image_path=str(image),
        metadata={"sha256": "image-sha"},
    )
    monkeypatch.setattr(
        "betguard.vision.ppocr_shadow.subprocess.run",
        lambda *args, **kwargs: pytest.fail("missing models must block before subprocess"),
    )
    result = run_ppocr_shadow(request, config=config)
    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "PPOCR_SHADOW_NOT_CONFIGURED"


def test_shadow_payload_has_no_candidate_queue_draft_or_webfill_side_effects(monkeypatch) -> None:
    qwen_result = _result()

    class StubProvider:
        def recognize(self, request):
            return qwen_result

    monkeypatch.setattr(service, "get_metadata", lambda image_id: _meta())
    monkeypatch.setattr(service, "QwenDashScopeProvider", StubProvider)
    monkeypatch.setattr(service, "reconstruct_structure", lambda result, *, game: [])
    _enable_shadow(monkeypatch, lambda request, *, config: _shadow_result(_pp("34", [10, 10, 30, 30])))
    response = service.run_job("image-id", "qwen-dashscope", game="539")
    serialized = json.dumps(response, ensure_ascii=False).lower()
    for forbidden in (
        "manual_candidate_id",
        "accepted_by_human",
        "approved_fill_queue",
        "queue_entry",
        "draft_write",
        "webfill_call",
        "executable",
        "exportable",
    ):
        assert forbidden not in serialized
    assert response["shadow_evidence"]["auto_apply"] is False
    assert response["shadow_evidence"]["auto_confirm"] is False
    assert response["shadow_evidence"]["auto_submit"] is False


def test_production_runtime_never_imports_paddle_and_ui_is_advanced_only() -> None:
    adapter_source = (REPO / "src/betguard/vision/ppocr_shadow.py").read_text(encoding="utf-8")
    service_source = (REPO / "src/betguard/vision/service.py").read_text(encoding="utf-8")
    ui_source = (REPO / "src/betguard/webui/assist_panel_vision_html.py").read_text(encoding="utf-8")
    assert "import paddle" not in adapter_source.lower()
    assert "import paddle" not in service_source.lower()
    advanced_start = ui_source.index("function _renderPpocrShadowEvidence")
    render_call = ui_source.index("html += _renderPpocrShadowEvidence();")
    advanced_details = ui_source.rfind("qwen-advanced-evidence", 0, render_call)
    assert advanced_start < render_call
    assert advanced_details >= 0
    review_session = ui_source[ui_source.index("function _createQwenReviewSession"):ui_source.index("function _renderQwenCompletion")]
    assert "ppocrShadowEvidence" not in review_session
    assert "另一辨識來源判定不同，請人工確認" in ui_source


def test_demo_scan_is_untouched() -> None:
    path = REPO / "demo_scan.py"
    if not path.exists():
        return  # clean MVP worktree intentionally excludes this local file
    assert hashlib.sha256(path.read_bytes()).hexdigest() == DEMO_SHA256
