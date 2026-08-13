from __future__ import annotations

import copy
import json
import re
from collections import Counter
from datetime import datetime, timezone
from json import JSONDecoder
from pathlib import Path

import pytest

from betguard.vision.contracts import (
    ProviderMetadata,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
)
from betguard.vision.providers.qwen_dashscope import _lines_from_full_page_response
from betguard.vision.structure_reconstruction import reconstruct_structure
from tools.vision.full_page_contract_shadow import (
    ShadowContractValidationError,
    DENSE_EVIDENCE_PROMPT_CONTRACT,
    DENSE_EVIDENCE_SCHEMA_VERSION,
    DENSE_EVIDENCE_SHADOW_PROMPT,
    DENSE_EVIDENCE_TRANSPORT_VERSION,
    DENSE_EVIDENCE_VALIDATOR_SHA256,
    V2_RESPONSE_SCHEMA_VERSION,
    V3_OBJECT_SCHEMA_VERSION,
    V3_OCR_SEMANTIC_PROMPT,
    V3_REFERENCE_PROMPT_CONTRACT,
    V3_REFERENCE_SCHEMA_VERSION,
    V3_REFERENCE_TRANSPORT_VERSION,
    V3_REFERENCE_VALIDATOR_SHA256,
    V3_REFERENCE_VALIDATOR_VERSION,
    V3_SHADOW_PROMPT,
    V3_TUPLE_SCHEMA_VERSION,
    V4_HYBRID_PROMPT_CONTRACT,
    V4_HYBRID_SCHEMA_VERSION,
    V4_HYBRID_TRANSPORT_VERSION,
    V4_HYBRID_VALIDATOR_SHA256,
    V4_HYBRID_VALIDATOR_VERSION,
    V4_SHADOW_PROMPT,
    V41_COMPACT_PROMPT_CONTRACT,
    V41_COMPACT_SCHEMA_VERSION,
    V41_COMPACT_TRANSPORT_VERSION,
    V41_COMPACT_VALIDATOR_SHA256,
    V41_COMPACT_VALIDATOR_VERSION,
    V41_SHADOW_PROMPT,
    adapt_v2_response_to_runtime,
    ablate_recognition_to_row_bbox,
    audit_dense_simplified_evidence,
    audit_full_page_response,
    audit_v2_response,
    audit_v3_live_preflight,
    audit_v3_semantic_coverage,
    audit_v3_shadow_prompt,
    canonicalize_legacy_response,
    compact_size_metrics,
    compact_token_objects,
    decode_v3_object,
    decode_v3_token_reference,
    decode_v3_tuple,
    decode_v4_hybrid,
    decode_v41_compact,
    decode_dense_token_evidence,
    encode_v3_object,
    encode_v3_token_reference,
    encode_v3_tuple,
    encode_v4_hybrid,
    encode_v41_compact,
    encode_dense_simplified_evidence,
    expand_token_tuples,
    load_cache_response,
    production_prompt_audit_matrix,
    validate_full_page_response,
    validate_v2_response,
    v3_reference_wrapper_metrics,
    v3_reference_validator_module_sha256,
)


CACHE_ROOT = Path.home() / "AppData" / "Local" / "Betguard Assistant" / "vision" / "qwen-dashscope-cache"
CACHE_FILES = {
    "sample-010": "69f120993a395ed08f01aec8e160b01816a854578aef29d4c2c4e72f46ba0068.json",
    "sample-011": "dd1a77be44f150d989172b1e8aa6e78e76260e4ed33e97dd9e8da1cc869dbfbf.json",
    "sample-014": "24ac3f852d8125d5c8b88a7d26bbe6fa82f4e33d61095f09cd654eb0a04eda37.json",
    "sample-006-shadow": "1207760cb54d500d321e45e9835c1d95ab0c5a95829e6702053637921b3cc4d6.json",
}
SAMPLE006_V2_TRUNCATED_RESPONSE = (
    Path.home()
    / "AppData"
    / "Local"
    / "Betguard Assistant"
    / "vision"
    / "shadow-experiments"
    / "combined-bbox-minified-contract-v2-shadow"
    / "shadow-v2-sample006-2d02170813e0492a"
    / "response.txt"
)


def _canonical_fixture() -> dict:
    return {
        "sections": [
            {
                "rows": [
                    {
                        "tokens": [
                            {"text": "01", "bbox": [10, 20, 30, 40]},
                            {"text": "2X1", "bbox": [40, 20, 70, 40]},
                        ],
                        "numbers": [["01"]],
                        "multiplier": "2X1",
                        "layout_hint": "normal_row",
                        "collision": None,
                    }
                ],
                "shared_multiplier": {
                    "text": "2/3X0.5",
                    "bbox": [80, 20, 130, 40],
                },
            }
        ]
    }


def _codes(value: dict) -> list[str]:
    return [issue.code for issue in audit_full_page_response(value)]


def _load_local_cache(sample: str) -> dict:
    path = CACHE_ROOT / CACHE_FILES[sample]
    if not path.exists():
        pytest.skip(f"persistent cache unavailable: {path}")
    return load_cache_response(path)


def _load_sample006_v2_complete_prefix() -> dict:
    if not SAMPLE006_V2_TRUNCATED_RESPONSE.exists():
        pytest.skip(f"V2 truncated artifact unavailable: {SAMPLE006_V2_TRUNCATED_RESPONSE}")
    raw = SAMPLE006_V2_TRUNCATED_RESPONSE.read_text(encoding="utf-8")
    position = raw.find("[", raw.find('"sections"')) + 1
    decoder = JSONDecoder()
    sections: list[dict] = []
    while position > 0:
        while position < len(raw) and raw[position].isspace():
            position += 1
        try:
            section, end = decoder.raw_decode(raw, position)
        except json.JSONDecodeError:
            break
        if not isinstance(section, dict):
            break
        sections.append(section)
        position = end
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == ",":
            position += 1
            continue
        break
    assert len(sections) == 42
    return {"sections": sections}


def _recognition_result(response: dict, *, sample: str) -> RecognitionResult:
    lines = _lines_from_full_page_response(response)
    return RecognitionResult(
        recognition_id=f"shadow-contract:{sample}",
        request_id=f"shadow-contract:{sample}",
        status=RecognitionStatus.COMPLETED,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        provider=ProviderMetadata(
            id="qwen-dashscope",
            mode="paid_api",
            model_name="qwen3-vl-plus",
            adapter_version="shadow-audit",
        ),
        source_image=SourceImage(
            image_id=sample,
            sha256="0" * 64,
            mime_type="image/png",
            width=720,
            height=1280,
        ),
        preprocessing={
            "qwen_response": response,
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
        raw_text="\n".join(line.text for line in lines),
        lines=lines,
        warnings=["human confirmation required"],
    )


def test_strict_shadow_contract_accepts_canonical_fixture() -> None:
    validate_full_page_response(_canonical_fixture())


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value["sections"][0].__setitem__("shared_multiplier", "x0.5"),
            "shared_multiplier_wrong_type",
        ),
        (
            lambda value: value["sections"][0]["rows"][0].__setitem__("layout_hint", "row_bet"),
            "layout_hint_invalid_enum",
        ),
        (
            lambda value: value["sections"][0]["rows"][0]["tokens"][0].pop("bbox"),
            "token_bbox_missing",
        ),
        (
            lambda value: value["sections"][0]["rows"][0].__setitem__("multiplier", ["2X1"]),
            "row_multiplier_wrong_type",
        ),
        (
            lambda value: value["sections"][0]["rows"][0].__setitem__("numbers", ["01"]),
            "number_group_wrong_type",
        ),
    ],
)
def test_strict_shadow_contract_rejects_known_drift(mutate, expected: str) -> None:
    fixture = _canonical_fixture()
    mutate(fixture)
    assert expected in _codes(fixture)
    with pytest.raises(ShadowContractValidationError):
        validate_full_page_response(fixture)


def test_sample006_minified_shadow_has_exact_contract_drift() -> None:
    response = _load_local_cache("sample-006-shadow")
    issues = audit_full_page_response(response)
    counts = Counter(issue.code for issue in issues)

    assert counts == {
        "layout_hint_invalid_enum": 37,
        "shared_multiplier_wrong_type": 35,
    }


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("sample-010", {"layout_hint_invalid_enum": 3}),
        ("sample-011", {"layout_hint_invalid_enum": 19}),
        ("sample-014", {"layout_hint_invalid_enum": 22}),
    ],
)
def test_existing_success_cache_reports_versioned_layout_compatibility_gap(
    sample: str,
    expected: dict[str, int],
) -> None:
    response = _load_local_cache(sample)
    counts = Counter(issue.code for issue in audit_full_page_response(response))
    assert counts == expected


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_tuple_c_roundtrip_is_lossless_for_cached_recognition_and_reconstruction(
    sample: str,
) -> None:
    response = _load_local_cache(sample)
    original_response = copy.deepcopy(response)
    original_result = _recognition_result(response, sample=sample)
    original_result_before = copy.deepcopy(original_result.to_dict())
    original_reconstruction = reconstruct_structure(original_result, game="539")

    compact = compact_token_objects(response)
    expanded = expand_token_tuples(compact)
    roundtrip_result = _recognition_result(expanded, sample=sample)
    roundtrip_reconstruction = reconstruct_structure(roundtrip_result, game="539")

    assert response == original_response
    assert expanded == response
    assert roundtrip_result.to_dict() == original_result.to_dict()
    assert roundtrip_reconstruction == original_reconstruction
    assert original_result.to_dict() == original_result_before


def test_tuple_c_fails_closed_instead_of_dropping_extra_token_evidence() -> None:
    fixture = _canonical_fixture()
    fixture["sections"][0]["rows"][0]["tokens"][0]["confidence"] = 0.5

    with pytest.raises(ShadowContractValidationError) as caught:
        compact_token_objects(fixture)

    assert caught.value.issues[0].code == "tuple_c_token_fields_not_lossless"


def _legacy_row_fixture(
    *,
    layout_hint: str = "row_bet",
    tokens: list[dict] | None = None,
    numbers: list[list[str]] | None = None,
    multiplier: str | None = "2X1",
) -> dict:
    return {
        "sections": [
            {
                "rows": [
                    {
                        "tokens": tokens
                        or [
                            {"text": "01", "bbox": [10, 10, 30, 30]},
                            {"text": ".", "bbox": [35, 10, 40, 30]},
                            {"text": "02", "bbox": [45, 10, 65, 30]},
                            {"text": "2", "bbox": [80, 10, 90, 30]},
                            {"text": "X", "bbox": [95, 10, 105, 30]},
                            {"text": "1", "bbox": [110, 10, 120, 30]},
                        ],
                        "numbers": numbers if numbers is not None else [["01", "02"]],
                        "multiplier": multiplier,
                        "layout_hint": layout_hint,
                    }
                ],
                "shared_multiplier": None,
            }
        ]
    }


def _row_meta(v2: dict, section_index: int = 0, row_index: int = 0) -> dict:
    return v2["sections"][section_index]["rows"][row_index]["_v2"]


def _structure_semantics(structures: list[dict]) -> list[dict]:
    keys = {
        "structure_id",
        "primary_line_id",
        "member_line_ids",
        "line_role",
        "status",
        "warnings",
    }
    return [
        {
            **{key: copy.deepcopy(item.get(key)) for key in keys},
            "number_groups": copy.deepcopy(
                (item.get("reconstructed_candidate") or {}).get("number_groups")
            ),
            "multiplier_rules": copy.deepcopy(
                (item.get("reconstructed_candidate") or {}).get("multiplier_rules")
            ),
            "layout": copy.deepcopy(
                (item.get("reconstructed_candidate") or {}).get("layout")
            ),
        }
        for item in structures
    ]


def test_v2_clear_normal_row_uses_number_separator_geometry() -> None:
    legacy = _legacy_row_fixture()
    v2 = canonicalize_legacy_response(legacy)

    validate_v2_response(v2)
    assert v2["response_schema_version"] == V2_RESPONSE_SCHEMA_VERSION
    assert v2["sections"][0]["rows"][0]["layout_hint"] == "normal_row"
    assert _row_meta(v2)["layout_resolution"] == {
        "status": "resolved",
        "source": "number_separator_geometry",
        "reason": "punctuation_separator_between_matched_number_tokens",
        "evidence": _row_meta(v2)["layout_resolution"]["evidence"],
    }
    assert adapt_v2_response_to_runtime(v2) == legacy


def test_v2_clear_column_row_uses_x_separator_geometry() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": "x", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [45, 10, 65, 30]},
            {"text": "2", "bbox": [80, 10, 90, 30]},
            {"text": "X", "bbox": [95, 10, 105, 30]},
            {"text": "1", "bbox": [110, 10, 120, 30]},
        ],
        numbers=[["01"], ["02"]],
    )
    v2 = canonicalize_legacy_response(legacy)

    assert v2["sections"][0]["rows"][0]["layout_hint"] == "column_bet"
    assert _row_meta(v2)["layout_resolution"]["source"] == "number_separator_geometry"
    assert adapt_v2_response_to_runtime(v2) == legacy


@pytest.mark.parametrize(
    ("separator_bbox", "expected_reason"),
    [
        ([35, 200, 40, 220], "separator_bbox_outside_number_y_band"),
        ([80, 10, 90, 30], "separator_bbox_not_between_number_bboxes"),
    ],
)
def test_v2_row_bet_rejects_separator_without_matching_spatial_geometry(
    separator_bbox: list[int],
    expected_reason: str,
) -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": "x", "bbox": separator_bbox},
            {"text": "02", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)
    row = v2["sections"][0]["rows"][0]
    evidence = row["_v2"]["layout_resolution"]["evidence"]

    assert row["layout_hint"] is None
    assert row["_v2"]["entry_kind"] == "unresolved_row"
    assert row["_v2"]["layout_resolution"]["status"] == "unresolved"
    assert evidence["column_separator_count"] == 0
    assert evidence["rejected_separator_token_indices"] == [1]
    assert evidence["rejected_separator_reasons"] == {"1": expected_reason}
    assert adapt_v2_response_to_runtime(v2) == legacy


def test_v2_row_bet_rejects_token_and_bbox_order_conflict() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [45, 10, 65, 30]},
            {"text": "x", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [10, 10, 30, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)
    row = v2["sections"][0]["rows"][0]
    evidence = row["_v2"]["layout_resolution"]["evidence"]

    assert row["layout_hint"] is None
    assert evidence["number_bbox_order_consistent"] is False
    assert evidence["rejected_separator_reasons"] == {
        "1": "matched_number_bbox_order_conflicts_with_token_order"
    }
    assert adapt_v2_response_to_runtime(v2) == legacy


def test_v2_ambiguous_row_bet_fails_closed() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": " ", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)
    row = v2["sections"][0]["rows"][0]

    assert row["layout_hint"] is None
    assert row["_v2"]["entry_kind"] == "unresolved_row"
    assert row["_v2"]["layout_resolution"]["status"] == "unresolved"
    assert row["_v2"]["attachment"]["executable"] is False
    assert audit_v2_response(v2) == []
    assert adapt_v2_response_to_runtime(v2) == legacy


def test_v2_multiplier_only_attaches_as_non_executable_section_evidence() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": "x", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    legacy["sections"][0]["rows"].append(
        {
            "tokens": [
                {"text": "3", "bbox": [80, 40, 90, 60]},
                {"text": "x", "bbox": [95, 40, 105, 60]},
                {"text": "1", "bbox": [110, 40, 120, 60]},
            ],
            "numbers": [],
            "multiplier": "3x1",
            "layout_hint": "multiplier_only",
        }
    )
    v2 = canonicalize_legacy_response(legacy)
    row = v2["sections"][0]["rows"][1]

    assert row["layout_hint"] is None
    assert row["_v2"]["entry_kind"] == "multiplier_evidence"
    assert row["_v2"]["attachment"] == {
        "status": "section_scoped_evidence",
        "section_id": "S01",
        "target_row_ids": ["S01-R01"],
        "executable": False,
    }
    assert row["_v2"]["executable"] is False
    assert adapt_v2_response_to_runtime(v2) == legacy

def test_v2_note_is_unresolved_scope_evidence() -> None:
    legacy = _legacy_row_fixture(
        layout_hint="note",
        tokens=[{"text": "各半車", "bbox": [10, 10, 70, 30]}],
        numbers=[],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)
    row = v2["sections"][0]["rows"][0]

    assert row["layout_hint"] is None
    assert row["_v2"]["entry_kind"] == "note_evidence"
    assert row["_v2"]["layout_resolution"]["status"] == "unresolved"
    assert (
        row["_v2"]["layout_resolution"]["reason"]
        == "note_scope_requires_human_resolution"
    )
    assert row["_v2"]["attachment"]["status"] == "unresolved_scope_evidence"
    assert row["_v2"]["attachment"]["target_row_ids"] == []
    assert (
        row["_v2"]["attachment"]["reason"]
        == "no_deterministic_target_structure"
    )
    assert row["_v2"]["executable"] is False
    assert adapt_v2_response_to_runtime(v2) == legacy

    tampered = copy.deepcopy(v2)
    tampered_attachment = tampered["sections"][0]["rows"][0]["_v2"]["attachment"]
    tampered_attachment["status"] = "non_bet_visual_evidence"
    tampered_attachment["target_row_ids"] = ["S01-R99"]
    assert [issue.code for issue in audit_v2_response(tampered)] == [
        "v2_note_scope_not_unresolved"
    ]


def test_sample014_note_rows_remain_unresolved_scope_evidence_without_target() -> None:
    legacy = _load_local_cache("sample-014")
    legacy_before = copy.deepcopy(legacy)
    v2 = canonicalize_legacy_response(legacy)

    for section_index, expected_section_id in ((13, "S14"), (14, "S15")):
        legacy_row = legacy["sections"][section_index]["rows"][0]
        row = v2["sections"][section_index]["rows"][0]
        metadata = row["_v2"]

        assert metadata["row_id"] == f"{expected_section_id}-R01"
        assert metadata["legacy_layout_hint"] == "note"
        assert metadata["entry_kind"] == "note_evidence"
        assert metadata["layout_resolution"]["status"] == "unresolved"
        assert (
            metadata["layout_resolution"]["reason"]
            == "note_scope_requires_human_resolution"
        )
        assert metadata["attachment"] == {
            "status": "unresolved_scope_evidence",
            "section_id": expected_section_id,
            "target_row_ids": [],
            "reason": "no_deterministic_target_structure",
            "executable": False,
        }
        assert metadata["evidence_only"] is True
        assert metadata["executable"] is False
        assert row["tokens"] == legacy_row["tokens"]
        assert row["numbers"] == legacy_row["numbers"]
        assert row["multiplier"] == legacy_row["multiplier"]

    assert legacy == legacy_before
    assert adapt_v2_response_to_runtime(v2) == legacy


@pytest.mark.parametrize(
    ("shared", "expected_status", "expected_reason", "expected_rules"),
    [
        (
            {"text": "x0.5", "bbox": [80, 10, 120, 30]},
            "unresolved",
            "shared_multiplier_category_or_value_incomplete",
            [],
        ),
        (
            {"text": "2/3X0.5", "bbox": [80, 10, 150, 30]},
            "complete_visual_evidence",
            "runtime_scope_and_validator_still_required",
            ["2/3X0.5"],
        ),
    ],
)
def test_v2_shared_multiplier_is_typed_evidence_never_executable(
    shared: dict,
    expected_status: str,
    expected_reason: str,
    expected_rules: list[str],
) -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = shared
    v2 = canonicalize_legacy_response(legacy)
    section = v2["sections"][0]
    resolution = section["_v2"]["shared_multiplier_resolution"]

    assert section["shared_multiplier"] == shared
    assert resolution["status"] == expected_status
    assert resolution["reason"] == expected_reason
    assert resolution["canonical_rules"] == expected_rules
    assert resolution["evidence_only"] is True
    assert resolution["executable"] is False
    assert section["_v2"]["executable"] is False
    assert adapt_v2_response_to_runtime(v2) == legacy

    tampered = copy.deepcopy(v2)
    tampered_resolution = tampered["sections"][0]["_v2"][
        "shared_multiplier_resolution"
    ]
    tampered_resolution["evidence_only"] = False
    tampered_resolution["executable"] = True
    issues = audit_v2_response(tampered)
    assert [issue.code for issue in issues] == [
        "v2_shared_resolution_not_fail_closed"
    ]
    with pytest.raises(ShadowContractValidationError):
        validate_v2_response(tampered)


def test_v2_legacy_string_shared_multiplier_is_preserved_but_unresolved() -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = "x0.5"
    v2 = canonicalize_legacy_response(legacy)
    section = v2["sections"][0]

    assert section["shared_multiplier"] is None
    assert section["_v2"]["legacy_shared_multiplier"] == "x0.5"
    assert section["_v2"]["shared_multiplier_resolution"]["status"] == "unresolved"
    assert section["_v2"]["shared_multiplier_resolution"]["executable"] is False
    assert adapt_v2_response_to_runtime(v2) == legacy


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_legacy_v2_runtime_replay_preserves_every_runtime_semantic(sample: str) -> None:
    legacy = _load_local_cache(sample)
    legacy_before = copy.deepcopy(legacy)
    baseline_result = _recognition_result(legacy, sample=sample)
    baseline_result_before = copy.deepcopy(baseline_result.to_dict())
    baseline_reconstruction = reconstruct_structure(baseline_result, game="539")

    v2 = canonicalize_legacy_response(legacy)
    validate_v2_response(v2)
    runtime = adapt_v2_response_to_runtime(v2)
    roundtrip_result = _recognition_result(runtime, sample=sample)
    roundtrip_reconstruction = reconstruct_structure(roundtrip_result, game="539")

    assert legacy == legacy_before
    assert runtime == legacy
    assert roundtrip_result.to_dict() == baseline_result.to_dict()
    assert _structure_semantics(roundtrip_reconstruction) == _structure_semantics(
        baseline_reconstruction
    )
    assert roundtrip_reconstruction == baseline_reconstruction
    assert baseline_result.to_dict() == baseline_result_before


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_tuple_c_at_v2_boundary_is_lossless_and_fail_closed(sample: str) -> None:
    legacy = _load_local_cache(sample)
    v2 = canonicalize_legacy_response(legacy)
    compact = compact_token_objects(v2)
    expanded = expand_token_tuples(compact)

    validate_v2_response(expanded)
    assert expanded == v2
    assert adapt_v2_response_to_runtime(expanded) == legacy

    tampered = copy.deepcopy(v2)
    tampered["sections"][0]["rows"][0]["tokens"][0]["confidence"] = 0.5
    with pytest.raises(ShadowContractValidationError) as caught:
        compact_token_objects(tampered)
    assert caught.value.issues[0].code == "tuple_c_token_fields_not_lossless"


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_v3_a_b_c_roundtrip_preserves_v2_runtime_and_reconstruction(sample: str) -> None:
    legacy = _load_local_cache(sample)
    v2 = canonicalize_legacy_response(legacy)
    baseline_runtime = adapt_v2_response_to_runtime(v2)
    baseline_result = _recognition_result(baseline_runtime, sample=sample)
    baseline_reconstruction = reconstruct_structure(baseline_result, game="539")
    variants = [
        (encode_v3_object(v2), decode_v3_object, V3_OBJECT_SCHEMA_VERSION),
        (encode_v3_tuple(v2), decode_v3_tuple, V3_TUPLE_SCHEMA_VERSION),
        (
            encode_v3_token_reference(v2),
            decode_v3_token_reference,
            V3_REFERENCE_SCHEMA_VERSION,
        ),
    ]

    for encoded, decoder, version in variants:
        if decoder is decode_v3_token_reference:
            assert encoded["v"] == V3_REFERENCE_TRANSPORT_VERSION
            assert version == V3_REFERENCE_SCHEMA_VERSION
        else:
            assert (encoded["version"] if isinstance(encoded, dict) else encoded[0]) == version
        decoded_v2 = decoder(encoded)
        runtime = adapt_v2_response_to_runtime(decoded_v2)
        result = _recognition_result(runtime, sample=sample)
        reconstruction = reconstruct_structure(result, game="539")

        assert decoded_v2 == v2
        assert runtime == baseline_runtime == legacy
        assert result.to_dict() == baseline_result.to_dict()
        assert reconstruction == baseline_reconstruction
        assert _structure_semantics(reconstruction) == _structure_semantics(
            baseline_reconstruction
        )


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_v3_size_metrics_are_deterministic_and_candidate_c_is_smallest(sample: str) -> None:
    v2 = canonicalize_legacy_response(_load_local_cache(sample))
    metrics = {
        "A": compact_size_metrics(encode_v3_object(v2)),
        "B": compact_size_metrics(encode_v3_tuple(v2)),
        "C": compact_size_metrics(encode_v3_token_reference(v2)),
    }

    assert metrics["A"]["field_key_overhead_chars"] > 0
    assert metrics["B"]["field_key_overhead_chars"] == 0
    assert metrics["C"]["field_key_overhead_chars"] == 8
    assert metrics["B"]["serialized_bytes"] < metrics["A"]["serialized_bytes"]
    assert metrics["C"]["serialized_bytes"] < metrics["B"]["serialized_bytes"]


def _duplicate_number_v2() -> dict:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "05", "bbox": [10, 10, 30, 30]},
            {"text": ".", "bbox": [35, 10, 40, 30]},
            {"text": "05", "bbox": [45, 10, 65, 30]},
            {"text": "3", "bbox": [80, 10, 90, 30]},
            {"text": "/", "bbox": [95, 10, 100, 30]},
            {"text": "4", "bbox": [105, 10, 115, 30]},
            {"text": "X", "bbox": [120, 10, 130, 30]},
            {"text": "1", "bbox": [135, 10, 145, 30]},
        ],
        numbers=[["05", "05"]],
        multiplier="3/4X1",
    )
    return canonicalize_legacy_response(legacy)


def test_v3_token_references_distinguish_duplicate_text_by_exact_index() -> None:
    v2 = _duplicate_number_v2()
    encoded = encode_v3_token_reference(v2)

    assert encoded["s"][0][0][0][1] == [[0, 2]]
    decoded = decode_v3_token_reference(encoded)
    assert decoded == v2
    assert decoded["sections"][0]["rows"][0]["multiplier"] == "3/4X1"
    assert decoded["sections"][0]["rows"][0]["tokens"] == v2["sections"][0]["rows"][0]["tokens"]


def test_v3_token_references_reject_same_value_physical_index_swap() -> None:
    encoded = encode_v3_token_reference(_duplicate_number_v2())
    encoded["s"][0][0][0][1] = [[2, 0]]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v3_token_reference(encoded)
    assert caught.value.issues[0].code == "v3_number_reference_identity_mismatch"


def test_v3_token_references_reject_same_value_cross_group_swap() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "05", "bbox": [10, 10, 30, 30]},
            {"text": "x", "bbox": [35, 10, 40, 30]},
            {"text": "05", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["05"], ["05"]],
        multiplier=None,
    )
    encoded = encode_v3_token_reference(canonicalize_legacy_response(legacy))
    assert encoded["s"][0][0][0][1] == [[0], [2]]
    encoded["s"][0][0][0][1] = [[2], [0]]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v3_token_reference(encoded)
    assert caught.value.issues[0].code == "v3_number_reference_identity_mismatch"


def test_v3_token_references_preserve_sample011_column_continuation_identity() -> None:
    v2 = canonicalize_legacy_response(_load_local_cache("sample-011"))
    encoded = encode_v3_token_reference(v2)
    decoded = decode_v3_token_reference(encoded)
    continuation_rows = [
        row
        for section in decoded["sections"]
        if len(section["rows"]) > 1
        for row in section["rows"][1:]
        if row["_v2"]["legacy_layout_hint"] == "row_bet"
    ]

    assert continuation_rows
    assert decoded == v2
    assert all(
        row["_v2"]["layout_resolution"]["evidence"]["matched_number_token_indices"]
        == v2["sections"][int(row["_v2"]["row_id"][1:3]) - 1]["rows"][
            int(row["_v2"]["row_id"][-2:]) - 1
        ]["_v2"]["layout_resolution"]["evidence"]["matched_number_token_indices"]
        for row in continuation_rows
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda encoded: encoded["s"][0][0][0].__setitem__(1, [[0, 0]]),
            "v3_number_reference_duplicate_index",
        ),
        (
            lambda encoded: encoded["s"][0][0][0].__setitem__(1, [[0, 99]]),
            "v3_number_reference_out_of_range",
        ),
        (
            lambda encoded: encoded["s"][0][0][0].__setitem__(3, "C"),
            "v3_layout_claim_mismatch",
        ),
        (
            lambda encoded: encoded["s"][0].__setitem__(4, True),
            "v3_section_executable",
        ),
    ],
)
def test_v3_reference_tampering_fails_closed(mutate, expected: str) -> None:
    encoded = encode_v3_token_reference(_duplicate_number_v2())
    mutate(encoded)

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v3_token_reference(encoded)
    assert caught.value.issues[0].code == expected


@pytest.mark.parametrize("encoder", [encode_v3_object, encode_v3_tuple, encode_v3_token_reference])
def test_v3_encoders_reject_extra_token_evidence_instead_of_dropping_it(encoder) -> None:
    v2 = _duplicate_number_v2()
    v2["sections"][0]["rows"][0]["tokens"][0]["confidence"] = 0.9

    with pytest.raises(ShadowContractValidationError) as caught:
        encoder(v2)
    assert caught.value.issues[0].code == "v3_token_fields_not_lossless"


@pytest.mark.parametrize("encoder", [encode_v3_object, encode_v3_tuple, encode_v3_token_reference])
@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["_v2"].__setitem__("new_evidence", "root"),
        lambda value: value["sections"][0]["_v2"].__setitem__("new_evidence", "section"),
        lambda value: value["sections"][0]["rows"][0]["_v2"].__setitem__(
            "new_evidence", "row"
        ),
        lambda value: value["sections"][0]["rows"][0]["_v2"]["layout_resolution"].__setitem__(
            "new_evidence", "resolution"
        ),
    ],
)
def test_v3_encoders_reject_unknown_v2_metadata_instead_of_dropping_it(
    encoder, mutate
) -> None:
    v2 = _duplicate_number_v2()
    mutate(v2)
    assert audit_v2_response(v2) == []

    with pytest.raises(ShadowContractValidationError) as caught:
        encoder(v2)
    assert caught.value.issues[0].code == "v3_fields_not_lossless"


@pytest.mark.parametrize("encoder", [encode_v3_object, encode_v3_tuple, encode_v3_token_reference])
@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["_v2"].__setitem__("source_schema_version", "tampered"),
        lambda value: value["sections"][0]["_v2"].__setitem__("section_id", "S99"),
        lambda value: value["sections"][0]["rows"][0]["_v2"].__setitem__("row_id", "S99-R99"),
        lambda value: value["sections"][0]["rows"][0]["_v2"]["layout_resolution"][
            "evidence"
        ].__setitem__("matched_number_token_indices", [2, 0]),
    ],
)
def test_v3_encoders_reject_non_reproducible_v2_metadata_values(encoder, mutate) -> None:
    v2 = _duplicate_number_v2()
    mutate(v2)
    assert audit_v2_response(v2) == []

    with pytest.raises(ShadowContractValidationError) as caught:
        encoder(v2)
    assert caught.value.issues[0].code == "v3_metadata_not_reproducible"


@pytest.mark.parametrize(
    "shared",
    [
        {"text": "x0.5", "bbox": [160, 10, 200, 30]},
        {"text": "2/3X0.5", "bbox": [160, 10, 230, 30]},
        {"text": "2/3/4X0.1", "bbox": [160, 10, 250, 30]},
    ],
)
def test_v3_preserves_shared_evidence_and_non_executable_metadata(shared: dict) -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = shared
    v2 = canonicalize_legacy_response(legacy)

    for encoded, decoder in (
        (encode_v3_object(v2), decode_v3_object),
        (encode_v3_tuple(v2), decode_v3_tuple),
        (encode_v3_token_reference(v2), decode_v3_token_reference),
    ):
        decoded = decoder(encoded)
        section = decoded["sections"][0]
        assert decoded == v2
        assert section["shared_multiplier"] == shared
        assert section["_v2"]["shared_multiplier_resolution"]["evidence_only"] is True
        assert section["_v2"]["shared_multiplier_resolution"]["executable"] is False


def test_v3_preserves_multiplier_only_note_and_unresolved_roles() -> None:
    for sample in ("sample-011", "sample-014"):
        v2 = canonicalize_legacy_response(_load_local_cache(sample))
        encoded = encode_v3_token_reference(v2)
        decoded = decode_v3_token_reference(encoded)
        roles_before = [
            row["_v2"]["entry_kind"]
            for section in v2["sections"]
            for row in section["rows"]
        ]
        roles_after = [
            row["_v2"]["entry_kind"]
            for section in decoded["sections"]
            for row in section["rows"]
        ]
        assert roles_after == roles_before
        assert decoded == v2


def test_sample006_minified_v1_v3_size_only_roundtrip_is_lossless() -> None:
    # The V1 response is complete JSON but its visual-evidence contract failed;
    # this test proves encoding losslessness only, never OCR truth/accuracy.
    legacy = _load_local_cache("sample-006-shadow")
    v2 = canonicalize_legacy_response(legacy)

    assert decode_v3_object(encode_v3_object(v2)) == v2
    assert decode_v3_tuple(encode_v3_tuple(v2)) == v2
    assert decode_v3_token_reference(encode_v3_token_reference(v2)) == v2
    assert compact_size_metrics(encode_v3_token_reference(v2))["serialized_chars"] < len(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))
    )


def test_sample006_v2_truncated_prefix_is_size_only_never_full_page_truth() -> None:
    # Only complete top-level prefix sections are decoded.  The synthetic root
    # is explicitly unsuitable for replay/accuracy; it exists solely to bound
    # representation pressure without salvaging a valid full-page result.
    prefix = _load_sample006_v2_complete_prefix()
    v2 = canonicalize_legacy_response(prefix)
    candidate_c = encode_v3_token_reference(v2)

    assert len(prefix["sections"]) == 42
    assert decode_v3_object(encode_v3_object(v2)) == v2
    assert decode_v3_tuple(encode_v3_tuple(v2)) == v2
    assert decode_v3_token_reference(candidate_c) == v2
    assert compact_size_metrics(candidate_c)["serialized_chars"] == 8024


def test_v3_reference_prompt_contract_is_self_contained_shadow_only_instruction() -> None:
    import hashlib

    previous_qa_contract = """V3C minified JSON only.
root=["full-page-response-v3-c-token-reference-shadow",S,sections]
S=[human_required,auto_apply,auto_confirm,auto_submit,executable]=[true,false,false,false,false].
section=[rows,canonical_shared,legacy_shared,shared_state,section_executable].
row=[tokens,number_refs,multiplier,layout,collision_slot,legacy_layout_role,role,evidence_only,row_executable].
token=[text,x1,y1,x2,y2]. number_refs=grouped exact 0-based token indexes; never reuse,reorder,guess.
layout=N(normal_row),C(column_bet),null(unresolved); collision_slot=[0] or [1,text].
role=B(bet),U(unresolved),M(multiplier evidence),S(note/scope evidence); evidence_only=(B:false,U/M/S:true).
Direct V3 legacy_layout_role: B=>layout; U=>R(row_bet); M=>M(multiplier_only); S=>S(note). null only for unknown legacy source.
canonical_shared=[0] or [2,text,x1,y1,x2,y2]. legacy_shared=[0],[1,text],or[2,text,x1,y1,x2,y2]; direct V3 sets both identical [0]/[2].
shared_state: both [0]=>A(absent); bbox evidence without complete category+value=>U(unresolved); complete category+value+bbox=>V(complete evidence). U/V remain evidence-only,non-executable.
section_executable=row_executable=false. Preserve every visible token+bbox and raw multiplier string/null, including stacked 2/3/4. Never invent/omit."""

    assert previous_qa_contract.splitlines()[3:] == V3_REFERENCE_PROMPT_CONTRACT.splitlines()[2:]
    assert len(V3_REFERENCE_PROMPT_CONTRACT) == 1269
    assert hashlib.sha256(V3_REFERENCE_PROMPT_CONTRACT.encode("utf-8")).hexdigest() == (
        "5d0b25557f46cecee68bd3556baec20bd220de044440c3ed4d3a2587c3cbdd8b"
    )
    assert "01-39" not in V3_REFERENCE_PROMPT_CONTRACT
    assert "01-49" not in V3_REFERENCE_PROMPT_CONTRACT
    assert "Never invent/omit" in V3_REFERENCE_PROMPT_CONTRACT
    assert "canonical_shared" in V3_REFERENCE_PROMPT_CONTRACT
    assert "legacy_shared" in V3_REFERENCE_PROMPT_CONTRACT
    assert "legacy_layout_role" in V3_REFERENCE_PROMPT_CONTRACT
    assert "evidence_only" in V3_REFERENCE_PROMPT_CONTRACT
    assert "evidence_only=(B:false,U/M/S:true)" in V3_REFERENCE_PROMPT_CONTRACT
    assert "B=>layout; U=>R(row_bet); M=>M(multiplier_only); S=>S(note)" in (
        V3_REFERENCE_PROMPT_CONTRACT
    )
    assert "both [0]=>A(absent)" in V3_REFERENCE_PROMPT_CONTRACT
    assert "=>U(unresolved)" in V3_REFERENCE_PROMPT_CONTRACT
    assert "=>V(complete evidence)" in V3_REFERENCE_PROMPT_CONTRACT


def test_v3_transport_root_is_json_object_and_roundtrips() -> None:
    v2 = _duplicate_number_v2()
    encoded = encode_v3_token_reference(v2)

    assert encoded.keys() == {"v", "s"}
    assert encoded["v"] == 3
    assert isinstance(encoded["s"], list)
    assert json.loads(json.dumps(encoded, separators=(",", ":"))) == encoded
    assert decode_v3_token_reference(encoded) == v2


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ({"sections": []}, "legacy_representation_not_allowed"),
        ([3, []], "v3_root_must_be_object"),
        ({"v": 4, "s": []}, "v3_version_invalid"),
        ({"v": 3}, "v3_sections_missing"),
        ({"v": 3, "s": [], "extra": True}, "v3_root_unknown_key"),
    ],
)
def test_v3_transport_root_fails_closed(wire, expected: str) -> None:
    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v3_token_reference(wire)
    assert caught.value.issues[0].code == expected


def test_production_prompt_line_audit_preserves_every_ocr_semantic_rule() -> None:
    from betguard.vision.qwen_prompts import PROMPT

    matrix = production_prompt_audit_matrix(PROMPT)
    assert len(matrix) == len(PROMPT.splitlines()) == 18
    assert [record["text"] for record in matrix] == PROMPT.splitlines()
    assert {record["classification"] for record in matrix} == {
        "semantic",
        "output-format",
        "mixed",
    }
    assert all(record["v3_disposition"] in {"preserve", "replace"} for record in matrix)
    assert audit_v3_semantic_coverage(matrix) == []
    assert "539 (01-39)" in V3_OCR_SEMANTIC_PROMPT
    assert "Mark Six/六合彩 (01-49)" in V3_OCR_SEMANTIC_PROMPT
    assert "stacked category digits" in V3_OCR_SEMANTIC_PROMPT
    assert "Keep leading zeros" in V3_OCR_SEMANTIC_PROMPT
    assert "Do not expand combinations" in V3_OCR_SEMANTIC_PROMPT


@pytest.mark.parametrize(
    "replacement",
    [[], ["Reverse the original business meaning."]],
)
def test_v3_semantic_audit_rejects_deleted_or_rewritten_mixed_rule(replacement) -> None:
    matrix = copy.deepcopy(production_prompt_audit_matrix())
    matrix[1]["semantic_replacements"] = replacement

    assert "v3_semantic_replacement_mismatch" in {
        issue.code for issue in audit_v3_semantic_coverage(matrix)
    }


def test_v3_semantic_audit_rejects_output_format_semantic_injection() -> None:
    matrix = copy.deepcopy(production_prompt_audit_matrix())
    matrix[5]["semantic_replacements"] = ["Invent a replacement meaning."]

    assert "v3_output_format_semantic_injection" in {
        issue.code for issue in audit_v3_semantic_coverage(matrix)
    }


def test_v3_shadow_prompt_has_no_legacy_schema_contradiction() -> None:
    import hashlib

    from betguard.vision.qwen_prompts import PROMPT

    assert len(V3_SHADOW_PROMPT) == 2497
    assert len(V3_SHADOW_PROMPT.encode("utf-8")) == 2513
    assert hashlib.sha256(V3_SHADOW_PROMPT.encode("utf-8")).hexdigest() == (
        "792de533792f071c3bab04e9d2b2d21cc9e1c52f55c10356615fa82f1f225426"
    )
    assert V3_REFERENCE_VALIDATOR_SHA256 == (
        "f7dfffb113c4de47c918ab462db0f60ee3122ead0d29ef7b0b841b9d18ee1a62"
    )
    assert audit_v3_shadow_prompt(V3_SHADOW_PROMPT) == []
    assert not any(
        legacy_key in V3_SHADOW_PROMPT
        for legacy_key in (
            '"sections":',
            '"rows":',
            '"tokens":',
            '"numbers":',
            '"multiplier":',
            '"layout_hint":',
            '"shared_multiplier":',
        )
    )
    assert "root=[" not in V3_SHADOW_PROMPT
    assert "Output ONLY JSON:" not in V3_SHADOW_PROMPT
    assert "legacy_schema_vocabulary_present" in {
        issue.code for issue in audit_v3_shadow_prompt(PROMPT + "\n" + V3_SHADOW_PROMPT)
    }


@pytest.mark.parametrize(
    "legacy_instruction",
    [
        "Use sections, rows, tokens, numbers, multiplier, layout_hint, shared_multiplier.",
        'Do not emit "sections" or "rows".',
        'The forbidden key is "sections".',
        "Do not emit {'sections':[]}.",
        'Do not emit {"sections":[]}.',
    ],
)
def test_v3_shadow_prompt_rejects_legacy_vocabulary_in_any_context(
    legacy_instruction: str,
) -> None:
    bad_prompt = V3_SHADOW_PROMPT + "\n" + legacy_instruction

    assert "legacy_schema_vocabulary_present" in {
        issue.code for issue in audit_v3_shadow_prompt(bad_prompt)
    }


def _aligned_preflight_issues(**overrides):
    import hashlib

    values = {
        "response_format": {"type": "json_object"},
        "expected_root": "object",
        "prompt": V3_SHADOW_PROMPT,
        "expected_prompt_sha256": hashlib.sha256(V3_SHADOW_PROMPT.encode("utf-8")).hexdigest(),
        "validator_version": V3_REFERENCE_VALIDATOR_VERSION,
        "validator_sha256": V3_REFERENCE_VALIDATOR_SHA256,
        "validator_module_sha256": v3_reference_validator_module_sha256(),
        "expected_validator_module_sha256": (
            "ee6de59bfd66d3c6c06833a1ac22c56f0e29b09dcfb27d08f38012d049f98e7f"
        ),
        "schema_version": V3_REFERENCE_SCHEMA_VERSION,
        "transport_version": V3_REFERENCE_TRANSPORT_VERSION,
        "external_call_count": 0,
    }
    values.update(overrides)
    return audit_v3_live_preflight(**values)


def test_v3_live_preflight_aligned_contract_passes_before_transport() -> None:
    assert _aligned_preflight_issues() == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"response_format": {"type": "text"}}, "preflight_response_format_mismatch"),
        ({"expected_root": "array"}, "preflight_root_transport_mismatch"),
        ({"expected_prompt_sha256": "0" * 64}, "preflight_prompt_sha_mismatch"),
        ({"validator_version": "old"}, "preflight_validator_version_mismatch"),
        ({"validator_sha256": "0" * 64}, "preflight_validator_sha_mismatch"),
        (
            {"validator_module_sha256": "0" * 64},
            "preflight_validator_module_sha_mismatch",
        ),
        ({"schema_version": "old"}, "preflight_schema_version_mismatch"),
        ({"transport_version": 2}, "preflight_transport_version_mismatch"),
        ({"external_call_count": 1}, "preflight_external_counter_not_zero"),
    ],
)
def test_v3_live_preflight_blocks_mismatch_before_transport(overrides, expected: str) -> None:
    assert expected in {issue.code for issue in _aligned_preflight_issues(**overrides)}


def test_v3_live_preflight_blocks_prompt_contradiction_before_transport() -> None:
    import hashlib

    bad_prompt = V3_SHADOW_PROMPT + '\n{"sections":[]}'
    issues = _aligned_preflight_issues(
        prompt=bad_prompt,
        expected_prompt_sha256=hashlib.sha256(bad_prompt.encode("utf-8")).hexdigest(),
    )
    assert "legacy_schema_vocabulary_present" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "sample",
    ["sample-010", "sample-011", "sample-014", "sample-006-shadow"],
)
def test_v3_transport_object_wrapper_overhead_is_measured(sample: str) -> None:
    v2 = canonicalize_legacy_response(_load_local_cache(sample))
    metrics = v3_reference_wrapper_metrics(v2)

    assert metrics["object_minus_minimal_array_chars"] == 8
    assert metrics["object_minus_minimal_array_bytes"] == 8
    assert metrics["object_minus_previous_qa_array_chars"] == -70
    assert metrics["object_minus_previous_qa_array_bytes"] == -70


def test_v3_preserves_unresolved_row_as_non_executable() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": " ", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)

    for encoded, decoder in (
        (encode_v3_object(v2), decode_v3_object),
        (encode_v3_tuple(v2), decode_v3_tuple),
        (encode_v3_token_reference(v2), decode_v3_token_reference),
    ):
        decoded = decoder(encoded)
        metadata = decoded["sections"][0]["rows"][0]["_v2"]
        assert decoded == v2
        assert metadata["entry_kind"] == "unresolved_row"
        assert metadata["evidence_only"] is True
        assert metadata["executable"] is False


def test_v4_contract_is_hierarchical_short_key_object_without_token_references() -> None:
    v2 = _duplicate_number_v2()
    encoded = encode_v4_hybrid(v2)
    section = encoded["s"][0]
    row = section["r"][0]

    assert V4_HYBRID_SCHEMA_VERSION == "full-page-response-v4-hybrid-object-shadow"
    assert V4_HYBRID_TRANSPORT_VERSION == 4
    assert V4_HYBRID_VALIDATOR_VERSION == "full-page-v4-hybrid-object-validator-v1"
    assert len(V4_HYBRID_VALIDATOR_SHA256) == 64
    assert encoded.keys() == {"v", "s"}
    assert encoded["v"] == 4
    assert section.keys() == {"r", "s", "x"}
    assert row.keys() == {"t", "n", "m", "l", "h", "k", "e", "x"}
    assert row["t"][0] == ["05", 10, 10, 30, 30]
    assert row["n"] == [["05", "05"]]
    assert row["n"] != [[0, 2]]
    assert row["m"] == "3/4X1"
    assert row["l"] == "N"
    assert row["x"] is False
    assert "never token indexes" in V4_HYBRID_PROMPT_CONTRACT
    assert V4_SHADOW_PROMPT.endswith(V4_HYBRID_PROMPT_CONTRACT)


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_v4_three_cache_roundtrip_is_deep_equal_through_reconstruction(sample: str) -> None:
    legacy = _load_local_cache(sample)
    legacy_before = copy.deepcopy(legacy)
    v2 = canonicalize_legacy_response(legacy)
    baseline_runtime = adapt_v2_response_to_runtime(v2)
    baseline_result = _recognition_result(baseline_runtime, sample=sample)
    baseline_result_before = copy.deepcopy(baseline_result.to_dict())
    baseline_reconstruction = reconstruct_structure(baseline_result, game="539")

    wire = encode_v4_hybrid(v2)
    decoded = decode_v4_hybrid(wire)
    runtime = adapt_v2_response_to_runtime(decoded)
    result = _recognition_result(runtime, sample=sample)
    result_before = copy.deepcopy(result.to_dict())
    reconstruction = reconstruct_structure(result, game="539")

    assert decoded == v2
    assert runtime == baseline_runtime == legacy
    assert result.to_dict() == baseline_result.to_dict()
    assert reconstruction == baseline_reconstruction
    assert _structure_semantics(reconstruction) == _structure_semantics(
        baseline_reconstruction
    )
    assert legacy == legacy_before
    assert baseline_result.to_dict() == baseline_result_before
    assert result.to_dict() == result_before


def test_v4_preserves_multiplier_note_and_unresolved_evidence_roles() -> None:
    for sample in ("sample-011", "sample-014"):
        v2 = canonicalize_legacy_response(_load_local_cache(sample))
        decoded = decode_v4_hybrid(encode_v4_hybrid(v2))
        before = [
            (
                row["_v2"]["entry_kind"],
                row["_v2"]["attachment"],
                row["_v2"]["evidence_only"],
                row["_v2"]["executable"],
                row["multiplier"],
            )
            for section in v2["sections"]
            for row in section["rows"]
        ]
        after = [
            (
                row["_v2"]["entry_kind"],
                row["_v2"]["attachment"],
                row["_v2"]["evidence_only"],
                row["_v2"]["executable"],
                row["multiplier"],
            )
            for section in decoded["sections"]
            for row in section["rows"]
        ]
        assert after == before
        assert decoded == v2


@pytest.mark.parametrize(
    "shared",
    [
        {"text": "x0.5", "bbox": [160, 10, 200, 30]},
        {"text": "2/3X0.5", "bbox": [160, 10, 230, 30]},
    ],
)
def test_v4_shared_is_null_or_text_bbox_and_remains_non_executable(shared: dict) -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = shared
    v2 = canonicalize_legacy_response(legacy)
    encoded = encode_v4_hybrid(v2)
    decoded = decode_v4_hybrid(encoded)

    assert encoded["s"][0]["s"] == [shared["text"], *shared["bbox"]]
    assert decoded == v2
    resolution = decoded["sections"][0]["_v2"]["shared_multiplier_resolution"]
    assert resolution["evidence_only"] is True
    assert resolution["executable"] is False


def test_v4_unresolved_legacy_shared_text_is_losslessly_preserved() -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = "x0.5"
    v2 = canonicalize_legacy_response(legacy)
    encoded = encode_v4_hybrid(v2)
    decoded = decode_v4_hybrid(encoded)

    assert encoded["s"][0]["s"] is None
    assert encoded["s"][0]["u"] == "x0.5"
    assert decoded == v2
    assert adapt_v2_response_to_runtime(decoded) == legacy
    assert decoded["sections"][0]["_v2"]["shared_multiplier_resolution"]["status"] == "unresolved"


@pytest.mark.parametrize(
    "fused",
    [
        "05x08",
        "05 x 08",
        "05.05",
        "2/3X0.1",
        "X1",
        "123",
        "x/",
        "XX",
        "/.",
        "×/",
    ],
)
def test_v4_encoder_rejects_fused_numeric_token_without_splitting_or_guessing(
    fused: str,
) -> None:
    legacy = _legacy_row_fixture(
        tokens=[{"text": fused, "bbox": [10, 10, 65, 30]}],
        numbers=[],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)

    with pytest.raises(ShadowContractValidationError) as caught:
        encode_v4_hybrid(v2)
    assert caught.value.issues[0].code == "v4_token_granularity_invalid"


@pytest.mark.parametrize(
    "fused",
    ["05x08", "05 05", "05.05", "2/3X0.1", "X1", "123", "x/", "XX", "/.", "×/"],
)
def test_v4_decoder_rejects_fused_numeric_or_operator_token(fused: str) -> None:
    wire = encode_v4_hybrid(_duplicate_number_v2())
    row = wire["s"][0]["r"][0]
    row["t"][0] = [fused, 10, 10, 65, 30]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == "v4_token_granularity_invalid"


@pytest.mark.parametrize("symbol", ["x", "X", "×", "/", ".", ","])
def test_v4_single_separator_or_operator_token_remains_valid(symbol: str) -> None:
    legacy = _legacy_row_fixture(
        tokens=[{"text": symbol, "bbox": [10, 10, 20, 30]}],
        numbers=[],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)

    assert decode_v4_hybrid(encode_v4_hybrid(v2)) == v2


def test_v4_separate_stacked_multiplier_fragments_remain_valid() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "05", "bbox": [10, 10, 30, 30]},
            {"text": "2", "bbox": [80, 10, 90, 30]},
            {"text": "/", "bbox": [95, 10, 100, 30]},
            {"text": "3", "bbox": [105, 10, 115, 30]},
            {"text": "X", "bbox": [120, 10, 130, 30]},
            {"text": "0", "bbox": [135, 10, 145, 30]},
            {"text": ".", "bbox": [150, 10, 155, 30]},
            {"text": "1", "bbox": [160, 10, 170, 30]},
        ],
        numbers=[["05"]],
        multiplier="2/3X0.1",
    )
    v2 = canonicalize_legacy_response(legacy)

    assert decode_v4_hybrid(encode_v4_hybrid(v2)) == v2


def test_v4_grouped_number_multiset_cannot_exceed_standalone_token_evidence() -> None:
    valid = encode_v4_hybrid(_duplicate_number_v2())
    assert decode_v4_hybrid(valid) == _duplicate_number_v2()

    for invalid_numbers in ([["05", "05", "05"]], [["05", "99"]]):
        wire = copy.deepcopy(valid)
        wire["s"][0]["r"][0]["n"] = invalid_numbers
        with pytest.raises(ShadowContractValidationError) as caught:
            decode_v4_hybrid(wire)
        assert caught.value.issues[0].code == "v4_number_evidence_exceeds_tokens"


def test_v4_unresolved_layout_null_roundtrips_as_evidence_only_nonexecutable() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "01", "bbox": [10, 10, 30, 30]},
            {"text": " ", "bbox": [35, 10, 40, 30]},
            {"text": "02", "bbox": [45, 10, 65, 30]},
        ],
        numbers=[["01"], ["02"]],
        multiplier=None,
    )
    v2 = canonicalize_legacy_response(legacy)
    wire = encode_v4_hybrid(v2)
    decoded = decode_v4_hybrid(wire)

    assert wire["s"][0]["r"][0]["l"] is None
    assert decoded == v2
    metadata = decoded["sections"][0]["rows"][0]["_v2"]
    assert metadata["entry_kind"] == "unresolved_row"
    assert metadata["evidence_only"] is True
    assert metadata["executable"] is False


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda wire: wire.__setitem__("v", 3), "v4_version_invalid"),
        (lambda wire: wire.__setitem__("extra", True), "v3_fields_not_lossless"),
        (lambda wire: wire["s"][0].__setitem__("x", True), "v4_section_executable"),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("x", True), "v4_row_executable"),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("n", [[0]]), "v4_number_group_invalid"),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("l", "R"), "v4_layout_invalid"),
    ],
)
def test_v4_schema_and_safety_tampering_fails_closed(mutate, expected: str) -> None:
    wire = encode_v4_hybrid(_duplicate_number_v2())
    mutate(wire)

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == expected


def test_v4_rejects_unknown_section_and_row_keys() -> None:
    valid = encode_v4_hybrid(_duplicate_number_v2())
    for target in (valid["s"][0], valid["s"][0]["r"][0]):
        wire = copy.deepcopy(valid)
        actual_target = wire["s"][0] if target is valid["s"][0] else wire["s"][0]["r"][0]
        actual_target["unknown"] = True
        with pytest.raises(ShadowContractValidationError) as caught:
            decode_v4_hybrid(wire)
        assert caught.value.issues[0].code == "v3_fields_not_lossless"


@pytest.mark.parametrize(
    "token",
    [
        ["05", 10, 10, 30, 30, "extra"],
        ["05", 10, 10, 30],
        ["05", "10", 10, 30, 30],
        ["05", 10, 10, float("inf"), 30],
        ["05", 30, 10, 10, 30],
        ["05", 10, 30, 30, 10],
    ],
)
def test_v4_token_tuple_and_bbox_tampering_fails_closed(token) -> None:
    wire = encode_v4_hybrid(_duplicate_number_v2())
    wire["s"][0]["r"][0]["t"][0] = token

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == "v3_token_tuple_invalid"


@pytest.mark.parametrize(
    "bbox",
    [
        [-1, 10, 30, 30],
        [10, -1, 30, 30],
        [10, 10, 10, 30],
        [10, 10, 30, 10],
    ],
)
def test_v4_token_pixel_bbox_negative_or_degenerate_fails_closed(bbox) -> None:
    wire = encode_v4_hybrid(_duplicate_number_v2())
    wire["s"][0]["r"][0]["t"][0] = ["05", *bbox]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == "v4_token_bbox_invalid"

    invalid_source = _duplicate_number_v2()
    invalid_source["sections"][0]["rows"][0]["tokens"][0]["bbox"] = bbox
    with pytest.raises(ShadowContractValidationError) as encode_error:
        encode_v4_hybrid(invalid_source)
    assert encode_error.value.issues[0].code == "v4_token_bbox_invalid"


def test_v4_nested_shared_bbox_shape_fails_closed() -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = {
        "text": "2/3X0.5",
        "bbox": [160, 10, 230, 30],
    }
    wire = encode_v4_hybrid(canonicalize_legacy_response(legacy))
    assert wire["s"][0]["s"] == ["2/3X0.5", 160, 10, 230, 30]
    wire["s"][0]["s"] = ["2/3X0.5", [160, 10, 230, 30]]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == "v4_shared_invalid"


@pytest.mark.parametrize(
    "bbox",
    [
        [-1, 10, 230, 30],
        [160, -1, 230, 30],
        [160, 10, 160, 30],
        [160, 10, 230, 10],
    ],
)
def test_v4_shared_pixel_bbox_negative_or_degenerate_fails_closed(bbox) -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = {
        "text": "2/3X0.5",
        "bbox": [160, 10, 230, 30],
    }
    wire = encode_v4_hybrid(canonicalize_legacy_response(legacy))
    wire["s"][0]["s"] = ["2/3X0.5", *bbox]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v4_hybrid(wire)
    assert caught.value.issues[0].code == "v4_shared_invalid"

    invalid_source = _legacy_row_fixture()
    invalid_source["sections"][0]["shared_multiplier"] = {
        "text": "2/3X0.5",
        "bbox": bbox,
    }
    with pytest.raises(ShadowContractValidationError) as encode_error:
        encode_v4_hybrid(canonicalize_legacy_response(invalid_source))
    assert encode_error.value.issues[0].code == "v4_shared_bbox_invalid"


@pytest.mark.parametrize("encoder", [encode_v4_hybrid])
def test_v4_encoder_rejects_unknown_token_or_metadata_evidence(encoder) -> None:
    token_tampered = _duplicate_number_v2()
    token_tampered["sections"][0]["rows"][0]["tokens"][0]["confidence"] = 0.9
    with pytest.raises(ShadowContractValidationError):
        encoder(token_tampered)

    metadata_tampered = _duplicate_number_v2()
    metadata_tampered["sections"][0]["rows"][0]["_v2"]["new_evidence"] = True
    with pytest.raises(ShadowContractValidationError):
        encoder(metadata_tampered)


@pytest.mark.parametrize(
    "sample",
    ["sample-010", "sample-011", "sample-014", "sample-006-shadow"],
)
def test_v4_size_metrics_are_deterministic_against_candidate_c(sample: str) -> None:
    v2 = canonicalize_legacy_response(_load_local_cache(sample))
    candidate_c = compact_size_metrics(encode_v3_token_reference(v2))
    v4 = compact_size_metrics(encode_v4_hybrid(v2))

    assert v4 == compact_size_metrics(encode_v4_hybrid(v2))
    assert v4["field_key_overhead_chars"] > candidate_c["field_key_overhead_chars"]
    assert v4["serialized_chars"] > candidate_c["serialized_chars"]
    assert v4["serialized_chars"] < compact_size_metrics(adapt_v2_response_to_runtime(v2))[
        "serialized_chars"
    ]


def test_v41_contract_is_simpler_than_candidate_c_and_omits_fixed_overhead() -> None:
    v2 = _duplicate_number_v2()
    encoded = encode_v41_compact(v2)
    section = encoded["s"][0]
    row = section["r"][0]

    assert V41_COMPACT_SCHEMA_VERSION == "full-page-response-v4.1-compact-object-shadow"
    assert V41_COMPACT_TRANSPORT_VERSION == 41
    assert V41_COMPACT_VALIDATOR_VERSION == "full-page-v4.1-compact-object-validator-v1"
    assert len(V41_COMPACT_VALIDATOR_SHA256) == 64
    assert encoded.keys() == {"v", "s"}
    assert section.keys() == {"r"}
    assert row.keys() == {"t", "n", "m", "l", "k"}
    assert row["t"][0] == ["05", 10, 10, 30, 30]
    assert row["n"] == [["05", "05"]]
    assert row["m"] == "3/4X1"
    assert row["l"] == "N"
    assert row["k"] == "R"
    assert not ({"e", "x", "h", "s"} & set(row))
    assert len(V41_COMPACT_PROMPT_CONTRACT) < len(V3_REFERENCE_PROMPT_CONTRACT)
    assert "never token indexes" in V41_COMPACT_PROMPT_CONTRACT
    assert V41_SHADOW_PROMPT.endswith(V41_COMPACT_PROMPT_CONTRACT)


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_v41_three_cache_roundtrip_is_deep_equal_through_reconstruction(sample: str) -> None:
    legacy = _load_local_cache(sample)
    legacy_before = copy.deepcopy(legacy)
    v2 = canonicalize_legacy_response(legacy)
    baseline_runtime = adapt_v2_response_to_runtime(v2)
    baseline_result = _recognition_result(baseline_runtime, sample=sample)
    baseline_result_before = copy.deepcopy(baseline_result.to_dict())
    baseline_reconstruction = reconstruct_structure(baseline_result, game="539")

    wire = encode_v41_compact(v2)
    decoded = decode_v41_compact(wire)
    runtime = adapt_v2_response_to_runtime(decoded)
    result = _recognition_result(runtime, sample=sample)
    result_before = copy.deepcopy(result.to_dict())
    reconstruction = reconstruct_structure(result, game="539")

    assert decoded == v2
    assert runtime == baseline_runtime == legacy
    assert result.to_dict() == baseline_result.to_dict()
    assert reconstruction == baseline_reconstruction
    assert _structure_semantics(reconstruction) == _structure_semantics(
        baseline_reconstruction
    )
    assert legacy == legacy_before
    assert baseline_result.to_dict() == baseline_result_before
    assert result.to_dict() == result_before


def test_v41_preserves_multiplier_note_unresolved_and_omitted_nulls() -> None:
    for sample in ("sample-011", "sample-014", "sample-006-shadow"):
        v2 = canonicalize_legacy_response(_load_local_cache(sample))
        wire = encode_v41_compact(v2)
        decoded = decode_v41_compact(wire)

        assert decoded == v2
        assert all("m" not in row for section in wire["s"] for row in section["r"] if not row.get("m"))
        assert [
            (
                row["_v2"]["entry_kind"],
                row["_v2"]["attachment"],
                row["_v2"]["evidence_only"],
                row["_v2"]["executable"],
                row["multiplier"],
            )
            for section in decoded["sections"]
            for row in section["rows"]
        ] == [
            (
                row["_v2"]["entry_kind"],
                row["_v2"]["attachment"],
                row["_v2"]["evidence_only"],
                row["_v2"]["executable"],
                row["multiplier"],
            )
            for section in v2["sections"]
            for row in section["rows"]
        ]


@pytest.mark.parametrize(
    "shared",
    [
        {"text": "x0.5", "bbox": [160, 10, 200, 30]},
        {"text": "2/3X0.5", "bbox": [160, 10, 230, 30]},
    ],
)
def test_v41_complete_shared_is_flat_and_nonexecutable(shared: dict) -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = shared
    v2 = canonicalize_legacy_response(legacy)
    encoded = encode_v41_compact(v2)
    decoded = decode_v41_compact(encoded)

    assert encoded["s"][0]["s"] == [shared["text"], *shared["bbox"]]
    assert decoded == v2
    resolution = decoded["sections"][0]["_v2"]["shared_multiplier_resolution"]
    assert resolution["evidence_only"] is True
    assert resolution["executable"] is False


def test_v41_unresolved_shared_and_unknown_row_layout_are_lossless() -> None:
    legacy = _legacy_row_fixture(layout_hint="unsupported_visual_role")
    legacy["sections"][0]["shared_multiplier"] = "x0.5"
    v2 = canonicalize_legacy_response(legacy)
    encoded = encode_v41_compact(v2)
    decoded = decode_v41_compact(encoded)

    assert encoded["s"][0]["u"] == "x0.5"
    row = encoded["s"][0]["r"][0]
    assert row["k"] == "Q"
    assert row["l"] is None
    assert row["u"] == "unsupported_visual_role"
    assert decoded == v2
    assert decoded["sections"][0]["rows"][0]["_v2"]["entry_kind"] == "unresolved_row"
    assert decoded["sections"][0]["rows"][0]["_v2"]["evidence_only"] is True


def test_v41_stacked_multiplier_collision_and_safety_metadata_are_lossless() -> None:
    legacy = _legacy_row_fixture(
        tokens=[
            {"text": "05", "bbox": [10, 10, 30, 30]},
            {"text": "2", "bbox": [80, 10, 90, 30]},
            {"text": "/", "bbox": [95, 10, 100, 30]},
            {"text": "3", "bbox": [105, 10, 115, 30]},
            {"text": "X", "bbox": [120, 10, 130, 30]},
            {"text": "0", "bbox": [135, 10, 145, 30]},
            {"text": ".", "bbox": [150, 10, 155, 30]},
            {"text": "1", "bbox": [160, 10, 170, 30]},
        ],
        numbers=[["05"]],
        multiplier="2/3X0.1",
    )
    legacy["sections"][0]["rows"][0]["collision"] = "2/3"
    v2 = canonicalize_legacy_response(legacy)
    wire = encode_v41_compact(v2)
    decoded = decode_v41_compact(wire)

    row = wire["s"][0]["r"][0]
    assert row["m"] == "2/3X0.1"
    assert row["c"] == "2/3"
    assert [token[0] for token in row["t"]] == ["05", "2", "/", "3", "X", "0", ".", "1"]
    assert decoded == v2
    assert decoded["_v2"]["safety"] == {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    }
    assert decoded["sections"][0]["rows"][0]["_v2"]["executable"] is False


def test_v41_encoder_rejects_unknown_evidence_metadata_instead_of_dropping_it() -> None:
    token_tampered = _duplicate_number_v2()
    token_tampered["sections"][0]["rows"][0]["tokens"][0]["confidence"] = 0.9
    with pytest.raises(ShadowContractValidationError):
        encode_v41_compact(token_tampered)

    metadata_tampered = _duplicate_number_v2()
    metadata_tampered["sections"][0]["rows"][0]["_v2"]["new_evidence"] = True
    with pytest.raises(ShadowContractValidationError):
        encode_v41_compact(metadata_tampered)


def test_v41_shared_conflict_and_nested_shape_fail_closed() -> None:
    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = {
        "text": "2/3X0.5",
        "bbox": [160, 10, 230, 30],
    }
    wire = encode_v41_compact(canonicalize_legacy_response(legacy))
    nested = copy.deepcopy(wire)
    nested["s"][0]["s"] = ["2/3X0.5", [160, 10, 230, 30]]
    with pytest.raises(ShadowContractValidationError):
        decode_v41_compact(nested)

    conflict = copy.deepcopy(wire)
    conflict["s"][0]["u"] = "x0.5"
    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v41_compact(conflict)
    assert caught.value.issues[0].code == "v41_unresolved_shared_conflict"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda wire: wire.__setitem__("v", 4), "v41_version_invalid"),
        (lambda wire: wire.__setitem__("extra", True), "v41_fields_not_lossless"),
        (lambda wire: wire["s"][0].__setitem__("x", False), "v41_fields_not_lossless"),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("x", False), "v41_fields_not_lossless"),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("n", [["05", "05", "05"]]), "v4_number_evidence_exceeds_tokens"),
        (
            lambda wire: wire["s"][0]["r"][0].update({"k": "N", "l": "C"}),
            "v41_state_layout_mismatch",
        ),
        (lambda wire: wire["s"][0]["r"][0].__setitem__("k", "Z"), "v41_row_state_invalid"),
    ],
)
def test_v41_schema_safety_and_evidence_tampering_fails_closed(mutate, expected: str) -> None:
    wire = encode_v41_compact(_duplicate_number_v2())
    mutate(wire)

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v41_compact(wire)
    assert caught.value.issues[0].code == expected


@pytest.mark.parametrize("fused", ["05x08", "05.05", "2/3X0.1", "X1", "x/", "XX", "/."])
def test_v41_fused_token_fails_closed_without_guessing(fused: str) -> None:
    wire = encode_v41_compact(_duplicate_number_v2())
    wire["s"][0]["r"][0]["t"][0] = [fused, 10, 10, 65, 30]

    with pytest.raises(ShadowContractValidationError) as caught:
        decode_v41_compact(wire)
    assert caught.value.issues[0].code == "v4_token_granularity_invalid"


@pytest.mark.parametrize(
    "bbox",
    [[-1, 10, 30, 30], [10, -1, 30, 30], [10, 10, 10, 30], [10, 10, 30, 10]],
)
def test_v41_token_and_shared_bbox_fail_closed(bbox) -> None:
    wire = encode_v41_compact(_duplicate_number_v2())
    wire["s"][0]["r"][0]["t"][0] = ["05", *bbox]
    with pytest.raises(ShadowContractValidationError) as token_error:
        decode_v41_compact(wire)
    assert token_error.value.issues[0].code == "v41_token_bbox_invalid"

    legacy = _legacy_row_fixture()
    legacy["sections"][0]["shared_multiplier"] = {
        "text": "2/3X0.5",
        "bbox": [160, 10, 230, 30],
    }
    shared_wire = encode_v41_compact(canonicalize_legacy_response(legacy))
    shared_wire["s"][0]["s"] = ["2/3X0.5", *bbox]
    with pytest.raises(ShadowContractValidationError):
        decode_v41_compact(shared_wire)


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014", "sample-006-shadow"])
def test_v41_is_smaller_than_v4_and_candidate_c_without_token_references(sample: str) -> None:
    v2 = canonicalize_legacy_response(_load_local_cache(sample))
    candidate_c = compact_size_metrics(encode_v3_token_reference(v2))
    v4 = compact_size_metrics(encode_v4_hybrid(v2))
    v41 = compact_size_metrics(encode_v41_compact(v2))

    assert v41 == compact_size_metrics(encode_v41_compact(v2))
    assert v41["serialized_chars"] < v4["serialized_chars"]
    assert v41["serialized_chars"] < candidate_c["serialized_chars"]
    assert all(
        isinstance(number, str)
        for section in encode_v41_compact(v2)["s"]
        for row in section["r"]
        for group in row["n"]
        for number in group
    )


def test_dense_simplified_contract_has_explicit_row_and_token_modes() -> None:
    v2 = _duplicate_number_v2()
    row_wire = encode_dense_simplified_evidence(v2, mode="dense_row")
    token_wire = encode_dense_simplified_evidence(v2, mode="token")
    row = row_wire["s"][0]["r"][0]
    token_row = token_wire["s"][0]["r"][0]

    assert DENSE_EVIDENCE_SCHEMA_VERSION == "dense-page-simplified-evidence-v1-shadow"
    assert DENSE_EVIDENCE_TRANSPORT_VERSION == "D1"
    assert len(DENSE_EVIDENCE_VALIDATOR_SHA256) == 64
    assert row_wire.keys() == {"v", "s"}
    assert row_wire["s"][0]["id"] == "S01"
    assert row["id"] == "S01-L01"
    assert row["q"] == "R"
    assert "t" not in row
    assert row["x"] == "05 . 05 3 / 4 X 1"
    assert row["b"] == [10, 10, 145, 30]
    assert row["n"] == [["05", "05"]]
    assert token_row["q"] == "T"
    assert token_row["t"][0] == ["05", 10, 10, 30, 30]
    assert "MUST NOT emit or imply token/sub-bboxes" in DENSE_EVIDENCE_PROMPT_CONTRACT
    assert DENSE_EVIDENCE_SHADOW_PROMPT.endswith(DENSE_EVIDENCE_PROMPT_CONTRACT)


@pytest.mark.parametrize("sample", ["sample-010", "sample-011", "sample-014"])
def test_dense_token_mode_roundtrips_three_caches_losslessly(sample: str) -> None:
    legacy = _load_local_cache(sample)
    v2 = canonicalize_legacy_response(legacy)
    wire = encode_dense_simplified_evidence(v2, mode="token")
    decoded = decode_dense_token_evidence(wire)
    baseline_runtime = adapt_v2_response_to_runtime(v2)
    runtime = adapt_v2_response_to_runtime(decoded)
    baseline_result = _recognition_result(baseline_runtime, sample=sample)
    result = _recognition_result(runtime, sample=sample)

    assert decoded == v2
    assert runtime == baseline_runtime == legacy
    assert result.to_dict() == baseline_result.to_dict()
    assert reconstruct_structure(result, game="539") == reconstruct_structure(
        baseline_result, game="539"
    )


def test_dense_row_mode_is_evidence_only_and_cannot_synthesize_tokens() -> None:
    v2 = _duplicate_number_v2()
    wire = encode_dense_simplified_evidence(v2, mode="dense_row")
    audit = audit_dense_simplified_evidence(wire)

    assert audit["valid"] is True
    assert audit["mode_counts"] == {"R": 1}
    assert audit["safety"] == {
        "needs_review": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
        "executable": False,
    }
    assert audit["capabilities"][0]["token_geometry_available"] is False
    assert audit["capabilities"][0]["deterministic_geometry_reconstruction_available"] is False
    with pytest.raises(ShadowContractValidationError) as caught:
        decode_dense_token_evidence(wire)
    assert caught.value.issues[0].code == "dense_geometry_verification_required"


def test_dense_row_mode_rejects_manufactured_child_token_bboxes() -> None:
    wire = encode_dense_simplified_evidence(_duplicate_number_v2(), mode="dense_row")
    wire["s"][0]["r"][0]["t"] = [["05", 10, 10, 30, 30]]

    with pytest.raises(ShadowContractValidationError) as caught:
        audit_dense_simplified_evidence(wire)
    assert caught.value.issues[0].code == "dense_row_mode_must_not_have_tokens"


def test_dense_token_mode_rejects_row_bbox_or_raw_text_drift() -> None:
    original = encode_dense_simplified_evidence(_duplicate_number_v2(), mode="token")
    for key, value, expected in (
        ("b", [10, 10, 64, 30], "dense_row_bbox_mismatch"),
        ("x", "05x05", "dense_token_text_mismatch"),
    ):
        wire = copy.deepcopy(original)
        wire["s"][0]["r"][0][key] = value
        with pytest.raises(ShadowContractValidationError) as caught:
            audit_dense_simplified_evidence(wire)
        assert caught.value.issues[0].code == expected


def test_current_deterministic_parsers_do_not_safely_agree_on_05x08() -> None:
    from betguard.parser import parse_line
    from betguard.semantic_parser import parse_ocr_text

    production = parse_line("05x08", default_game="539")
    semantic = parse_ocr_text("05x08", game="539")

    assert production.type == "car"
    assert production.numbers == [5]
    assert production.car_units == 8
    assert semantic.type == "column"
    assert semantic.columns == [[5], [8]]
    assert production.type != semantic.type


def test_23x1_5_requires_scope_review_without_geometry() -> None:
    from betguard.parser import ParseError, parse_line
    from betguard.semantic_parser import parse_ocr_text

    with pytest.raises(ParseError):
        parse_line("23x1.5", default_game="539")
    semantic = parse_ocr_text("23x1.5", game="539")

    assert semantic.type == "shared_multiplier"
    assert semantic.numbers == []
    assert semantic.stars == [2, 3]
    assert semantic.executable is False
    assert "scope=current_group" in semantic.parse_notes


@pytest.mark.parametrize(
    ("sample", "expected_statuses", "expected_primary", "expected_model_numbers", "expected_model_multiplier"),
    [
        ("sample-010", {"incomplete": 9}, 9, 9, 9),
        ("sample-011", {"incomplete": 19}, 11, 6, 11),
        ("sample-014", {"incomplete": 22}, 15, 6, 13),
    ],
)
def test_row_bbox_only_ablation_fails_closed_without_mutating_source(
    sample: str,
    expected_statuses: dict[str, int],
    expected_primary: int,
    expected_model_numbers: int,
    expected_model_multiplier: int,
) -> None:
    legacy = _load_local_cache(sample)
    result = _recognition_result(legacy, sample=sample)
    before = copy.deepcopy(result.to_dict())
    ablated = ablate_recognition_to_row_bbox(before)
    reconstruction = reconstruct_structure(ablated, game="539")
    primary = [item for item in reconstruction if item.get("line_role") == "primary"]

    assert Counter(item["status"] for item in reconstruction) == Counter(expected_statuses)
    assert len(primary) == expected_primary
    assert all(item["needs_review"] is True for item in reconstruction)
    assert all(item["human_confirmation_required"] is True for item in reconstruction)
    assert all(item["auto_apply"] is False for item in reconstruction)
    assert all(item["auto_confirm"] is False for item in reconstruction)
    assert all(item["auto_submit"] is False for item in reconstruction)
    assert all(not (item.get("reconstructed_candidate") or {}).get("number_groups") for item in primary)
    assert all(not (item.get("reconstructed_candidate") or {}).get("multiplier_rules") for item in primary)
    assert sum(bool((item.get("model_candidate") or {}).get("numbers")) for item in primary) == expected_model_numbers
    assert sum(bool((item.get("model_candidate") or {}).get("multiplier")) for item in primary) == expected_model_multiplier
    assert result.to_dict() == before
    assert all(line.get("bounding_box") is not None for line in ablated["lines"])
    assert all(line.get("tokens") == [] for line in ablated["lines"])


def test_dense_modes_preserve_note_shared_and_unresolved_evidence() -> None:
    for sample in ("sample-011", "sample-014", "sample-006-shadow"):
        v2 = canonicalize_legacy_response(_load_local_cache(sample))
        row_wire = encode_dense_simplified_evidence(v2, mode="dense_row")
        token_wire = encode_dense_simplified_evidence(v2, mode="token")

        row_audit = audit_dense_simplified_evidence(row_wire)
        assert row_audit["valid"] is True
        assert decode_dense_token_evidence(token_wire) == v2
        assert all(
            capability["needs_review"] is True
            for capability in row_audit["capabilities"]
        )


def test_sample006_v41_raw_is_diagnostic_only_but_all_rows_are_reviewable() -> None:
    response_path = (
        Path.home()
        / "AppData/Local/Betguard Assistant/vision/shadow-experiments"
        / "v4.1-one-shot-live-shadow"
        / "shadow-v41-sample006-f4ab57c55fae4bb581743f8b050b72d5"
        / "response.txt"
    )
    raw = json.loads(response_path.read_text(encoding="utf-8"))
    rows = [row for section in raw["s"] for row in section.get("r", [])]
    tokens = [token for row in rows for token in row.get("t", [])]
    fused = [
        token[0]
        for token in tokens
        if isinstance(token, list)
        and token
        and isinstance(token[0], str)
        and (
            bool(re.search(r"\d(?:[xX/\.>]|\s)|(?:[xX/\.>]|\s)\d", token[0]))
            or (token[0].isdigit() and len(token[0]) > 2)
        )
    ]
    rows_with_closed_539_numbers = 0
    for row in rows:
        values = [value for group in row.get("n", []) for value in group]
        if values and all(
            isinstance(value, str)
            and re.fullmatch(r"\d{2}", value)
            and 1 <= int(value) <= 39
            for value in values
        ):
            rows_with_closed_539_numbers += 1

    assert set(raw) == {"v", "s", "u"}  # strict V4.1 failure remains immutable
    assert raw["v"] == 41
    assert len(raw["s"]) == 1
    assert len(rows) == 51
    assert len(tokens) == 154
    assert len(fused) == 23
    assert rows_with_closed_539_numbers == 22
    assert len(rows) - rows_with_closed_539_numbers == 29
    assert sum("m" in row for row in rows) == 0
    assert all(isinstance(row.get("n"), list) for row in rows)
