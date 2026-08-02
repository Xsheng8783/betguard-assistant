from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from betguard.vision.contracts import (
    Confidence,
    Line,
    ProviderMetadata,
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
    SourceImage,
)
from betguard.vision.image_intake import ImageMetadata
from betguard.vision.paid_fallback import (
    PaidFallbackProviders,
    build_pending_human_confirmation,
    can_send_ocr_to_review,
    run_paid_vision_fallback_job,
)


def _metadata(tmp_path: Path, *, width: int = 800, height: int = 600) -> ImageMetadata:
    image_id = uuid4().hex
    filename = "sample.png"
    image_dir = tmp_path / image_id
    image_dir.mkdir()
    (image_dir / filename).write_bytes(b"fake")
    meta = ImageMetadata(
        image_id=image_id,
        original_filename=filename,
        stored_filename=filename,
        sha256="sha",
        mime_type="image/png",
        width=width,
        height=height,
        byte_size=4,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return meta


def test_build_pending_human_confirmation_surfaces_closed_set_validation():
    """needs_human_confirmation + validation_issues must reach the UI lines."""
    from betguard.vision.paid_fallback import build_pending_human_confirmation
    result = _result("r1", "3B ×1", warnings=["needs_human_confirmation"])
    result.preprocessing = {
        "openai_lines": [{
            "line_id": "L01",
            "entry_id": "E01",
            "layout_hint": "normal_like",
            "number_groups": [["3B"]],
            "multiplier_text": "×1",
            "uncertain": False,
            "uncertain_reason": None,
            "needs_human_confirmation": True,
            "validation_issues": ["disallowed_character"],
            "token_validations": [{"token": "3B", "canonical": None,
                                   "issues": ["disallowed_character"],
                                   "requires_human_confirmation": True}],
        }],
    }
    pc = build_pending_human_confirmation(result, quality_passed=True, selected_source="paid_vision")
    line = pc["lines"][0]
    assert line["needs_human_confirmation"] is True
    assert "disallowed_character" in line["validation_issues"]
    assert line["token_validations"][0]["requires_human_confirmation"] is True
    assert pc["requires_human_confirmation"] is True
    assert pc["can_enter_review"] is False
    assert pc["can_assisted_fill"] is False
    assert pc["safety"]["auto_submit"] is False
    assert pc["safety"]["auto_confirm"] is False


def test_clean_line_still_requires_human_confirmation():
    """Policy: even a fully clean line requires human confirmation."""
    from betguard.vision.paid_fallback import build_pending_human_confirmation
    result = _result("r2", "18 26 ×1")
    result.preprocessing = {
        "openai_lines": [{
            "line_id": "L01",
            "entry_id": "E01",
            "layout_hint": "normal_like",
            "number_groups": [["18", "26"]],
            "multiplier_text": "×1",
            "uncertain": False,
            "uncertain_reason": None,
            "needs_human_confirmation": True,
            "validation_issues": [],
            "token_validations": [],
        }],
    }
    pc = build_pending_human_confirmation(result, quality_passed=True, selected_source="paid_vision")
    assert pc["lines"][0]["needs_human_confirmation"] is True
    assert pc["lines"][0]["validation_issues"] == []


def _result(request_id: str, text: str, *, provider_id: str = "fake-local", warnings=None):
    warnings = warnings or []
    return RecognitionResult(
        recognition_id=f"rec-{request_id}",
        request_id=request_id,
        status=RecognitionStatus.COMPLETED,
        provider=ProviderMetadata(id=provider_id, mode="fake"),
        source_image=SourceImage(image_id="img"),
        raw_text=text,
        lines=[
            Line(
                line_id="L01",
                order=1,
                text=text,
                confidence=Confidence(value=None, source=provider_id),
                warnings=list(warnings),
            )
        ],
    )


class StaticProvider:
    def __init__(self, text: str, *, provider_id: str = "static", warnings=None):
        self.text = text
        self.provider_id = provider_id
        self.warnings = warnings or []
        self.calls = []
        self.requests = []

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        self.calls.append(request.request_id)
        self.requests.append(request)
        return _result(request.request_id, self.text, provider_id=self.provider_id, warnings=self.warnings)


def test_no_api_key_safely_falls_back_to_local_ocr(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BETGUARD_VISION_MODEL", "test-model")
    local = StaticProvider("06 13 23 22", provider_id="local")

    report = run_paid_vision_fallback_job(
        _metadata(tmp_path),
        providers=PaidFallbackProviders(local_provider=local),
    )

    assert report["ok"] is True
    assert report["selected_source"] == "local_ocr"
    assert "OPENAI_API_KEY" in report["paid_skipped_reason"]
    pending = report["pending_confirmation"]
    assert pending["status"] == "PENDING_HUMAN_CONFIRMATION"
    assert pending["can_enter_review"] is False
    assert pending["can_assisted_fill"] is False
    assert pending["safety"]["auto_submit"] is False
    assert pending["safety"]["auto_confirm"] is False


def test_paid_result_still_requires_human_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-token")
    monkeypatch.setenv("BETGUARD_VISION_MODEL", "test-model")
    local = StaticProvider("06 13 23 22", provider_id="local")
    paid = StaticProvider("06 13 23 22", provider_id="paid")

    report = run_paid_vision_fallback_job(
        _metadata(tmp_path),
        providers=PaidFallbackProviders(local_provider=local, paid_provider=paid),
    )

    assert report["selected_source"] == "paid_vision"
    assert report["pending_confirmation"]["status"] == "PENDING_HUMAN_CONFIRMATION"
    assert can_send_ocr_to_review(report["pending_confirmation"]) is False
    report["pending_confirmation"]["lines"][0]["human_confirmed"] = True
    assert can_send_ocr_to_review(report["pending_confirmation"]) is True


def test_human_document_mode_is_forwarded_to_paid_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-token")
    monkeypatch.setenv("BETGUARD_VISION_MODEL", "test-model")
    local = StaticProvider("17 20 28 34", provider_id="local")
    paid = StaticProvider("17.20/28/34 二三x1", provider_id="paid")

    report = run_paid_vision_fallback_job(
        _metadata(tmp_path),
        providers=PaidFallbackProviders(local_provider=local, paid_provider=paid),
        document_mode="column",
    )

    assert report["document_mode"] == "column"
    assert paid.requests[0].metadata["document_mode"] == "column"


def test_quality_gate_blocks_review_even_after_human_text_confirmation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    local = StaticProvider("06 13")

    report = run_paid_vision_fallback_job(
        _metadata(tmp_path, width=100, height=100),
        providers=PaidFallbackProviders(local_provider=local),
    )

    pending = report["pending_confirmation"]
    assert pending["quality_passed"] is False
    pending["lines"][0]["human_confirmed"] = True
    assert can_send_ocr_to_review(pending) is False


def test_uncertain_paid_result_attempts_field_crop_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-token")
    monkeypatch.setenv("BETGUARD_VISION_MODEL", "test-model")
    local = StaticProvider("06 13", provider_id="local")
    paid = StaticProvider("06 13", provider_id="paid", warnings=["uncertain"])
    crop_meta = _metadata(tmp_path, width=300, height=200)

    report = run_paid_vision_fallback_job(
        _metadata(tmp_path),
        providers=PaidFallbackProviders(local_provider=local, paid_provider=paid),
        field_crops=[crop_meta],
    )

    assert len(paid.calls) == 2
    assert report["crop_results"]
    assert "field crops were read" in report["crop_fallback_reason"]


def test_pending_confirmation_does_not_allow_unconfirmed_review():
    result = _result("req", "18 26 x1")
    pending = build_pending_human_confirmation(result, quality_passed=True, selected_source="local")

    assert pending["status"] == "PENDING_HUMAN_CONFIRMATION"
    assert pending["lines"][0]["human_confirmed"] is False
    assert can_send_ocr_to_review(pending) is False


def test_pending_confirmation_preserves_layout_observations():
    result = _result("req-layout", "17.20/28/34 二三x1")
    result.preprocessing["openai_lines"] = [
        {
            "line_id": "L01",
            "entry_id": "E01",
            "layout_hint": "column_like",
            "number_groups": [["17", "20"], ["28"], ["34"]],
            "multiplier_text": "二三x1",
            "uncertain": False,
        }
    ]

    pending = build_pending_human_confirmation(
        result,
        quality_passed=True,
        selected_source="paid_vision",
    )

    line = pending["lines"][0]
    assert line["entry_id"] == "E01"
    assert line["layout_hint"] == "column_like"
    assert line["number_groups"] == [["17", "20"], ["28"], ["34"]]
    assert line["multiplier_text"] == "二三x1"
    assert line["human_confirmed"] is False


def test_paid_aid_image_does_not_bypass_original_quality_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-token")
    monkeypatch.setenv("BETGUARD_VISION_MODEL", "test-model")
    original = _metadata(tmp_path, width=346, height=477)
    aided = _metadata(tmp_path, width=1800, height=1420)
    local = StaticProvider("01 20 x1", provider_id="local")
    paid = StaticProvider("01.20 x1", provider_id="paid")

    report = run_paid_vision_fallback_job(
        original,
        request_id="req-aid",
        providers=PaidFallbackProviders(
            local_provider=local,
            paid_provider=paid,
        ),
        paid_image_metadata=aided,
    )

    assert report["paid_input_aided"] is True
    assert report["selected_source"] == "paid_vision"
    assert report["quality"]["pass"] is False
    assert report["pending_confirmation"]["quality_passed"] is False
    assert report["safety"]["webfill_allowed"] is False
