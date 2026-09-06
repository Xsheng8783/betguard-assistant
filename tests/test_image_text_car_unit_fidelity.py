"""Synthetic text-contract regressions; these do not measure image accuracy."""

from decimal import Decimal
from copy import deepcopy
from types import SimpleNamespace

import pytest

from betguard.vision import service
from betguard.vision.openai_luna_transcription import normalize_parser_safe_literals
from betguard.webfill.assisted_fill_mock import build_mock_fill_report


@pytest.mark.parametrize("number", ["01", "17", "33", "39"])
@pytest.mark.parametrize("amount", ["0.1", "0.5", "1", "3", "5", "10", "50"])
def test_explicit_car_quantity_survives_formatter_parser_and_mock(number, amount):
    source = f"{number}X{amount}車"
    text, changes = normalize_parser_safe_literals(source)
    assert changes == 1
    assert text == f"{number}車{amount}支"
    preview = service.preflight_image_text(text, game="539")
    assert preview["all_parseable"]
    mock = build_mock_fill_report(text, game="539")
    assert mock["status"] == "COMPLETED_MOCK_ONLY"
    assert mock["selected_car_number"] == int(number)
    assert Decimal(str(mock["car_units"])) == Decimal(amount)
    assert Decimal(str(mock["filled_amount"])) == Decimal(amount) * 100
    assert mock["danger_buttons_clicked"] == []
    assert mock["final_decision"]["human_required"] is True


@pytest.mark.parametrize("text", [
    "33車10", "33車10元", "33×?車", "11 33×1車", "? 33×10車",
    "33×10車 (crossed out)", "11 33 各0.5車", "11 半車",
    "03 × 16 × ?尾 2,3×1", "05 17 19 2×5\n05 17 19 3×0.5",
    "\tunknown / ?尾\r\n\r\n", "",
])
def test_other_units_unknown_and_multiline_source_are_not_rewritten(text):
    assert normalize_parser_safe_literals(text) == (text, 0)


@pytest.mark.parametrize("game,number,valid", [
    ("539", "39", True), ("539", "40", False), ("539", "49", False),
    ("六合", "39", True), ("六合", "40", True), ("六合", "49", True),
])
def test_gemma_uses_same_explicit_unit_contract_without_changing_game(game, number, valid):
    raw = f"{number}×10車"
    evidence = {"status": "completed", "items": [{
        "raw_text": raw, "special_text": "10車", "cancelled": "no", "uncertain": False,
    }]}
    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)
    assert text == f"{number}車10支"
    assert evidence["items"][0]["raw_text"] == raw
    assert notices[0]["before"] == raw
    assert notices[0]["after"] == text
    assert notices[0]["human_confirmed"] is False
    assert service.preflight_image_text(text, game=game)["all_parseable"] is valid


@pytest.mark.parametrize("flags", [
    {"uncertain": True}, {"special_text": "5車"},
    {"cancelled": "yes"}, {"cancelled": "unclear"},
])
def test_gemma_ambiguous_record_not_made_more_executable(flags):
    raw = "33×10車"
    evidence = {"status": "completed", "items": [{"raw_text": raw, **flags}]}
    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)
    assert raw in text
    assert not any(n["code"] == "CAR_LITERAL_REFORMATTED" for n in notices)
    assert not service.preflight_image_text(text, game="539")["all_parseable"]


def test_rejected_gemma_record_not_formatted():
    evidence = {"status": "completed", "items": [{"raw_text": "33×10車"}],
                "rejected_items": [{"item_index": 1}]}
    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)
    assert "33×10車" in text
    assert not any(n["code"] == "CAR_LITERAL_REFORMATTED" for n in notices)
    assert not service.preflight_image_text(text, game="539")["all_parseable"]


def test_explicit_unit_rendering_is_idempotent_and_preserves_line_endings():
    source = "  01 X 10車\t\r\n\r\nunknown / ?尾\r\n"
    expected = "  01車10支\t\r\n\r\nunknown / ?尾\r\n"
    assert normalize_parser_safe_literals(source) == (expected, 1)
    assert normalize_parser_safe_literals(expected) == (expected, 0)


@pytest.mark.parametrize("amount", ["0", "0.0"])
def test_zero_quantity_stays_invalid(amount):
    text, _ = normalize_parser_safe_literals(f"17×{amount}車")
    assert not service.preflight_image_text(text, game="539")["all_parseable"]
    assert build_mock_fill_report(text, game="539")["status"] == "BLOCKED"


@pytest.mark.parametrize("reader", ["gemma", "luna"])
def test_cached_source_layers_not_overwritten_or_human_confirmed(reader, tmp_path, monkeypatch):
    from betguard.vision import image_text_acceptance
    from betguard.vision.image_text_literals import FORMATTER_VERSION

    raw = "17×10車"
    wire = {"archived_provider_text": raw}
    record = {"raw_text": raw, "cancelled": "no", "uncertain": False}
    evidence = {"status": "completed", "items": [record], "text": raw,
                "native_ocr_text": raw, "source_records": [record],
                "provider_raw_response": wire, "cache_hit": True, "external_call_count": 0}
    original = deepcopy(evidence)
    monkeypatch.setattr(service, "get_metadata", lambda _: SimpleNamespace(
        storage_path=tmp_path / "image.png", mime_type="image/png", sha256="e" * 64,
        width=1000, height=1500, byte_size=1, is_expired=lambda: False))
    monkeypatch.setattr(service, "run_gemma_shadow", lambda *a, **k: evidence)
    monkeypatch.setattr(service, "transcribe_with_luna", lambda **k: evidence)
    captures = []
    monkeypatch.setattr(image_text_acceptance, "record_machine_transcription",
                        lambda _id, **kwargs: captures.append(kwargs))

    result = service.transcribe_image_to_text("test-image", reader=reader, game="539")
    assert result["text"] == "17車10支"
    assert result["parser_preflight"]["all_parseable"] is True
    assert result["external_call_count"] == 0
    assert result["auto_apply"] is result["auto_confirm"] is result["auto_submit"] is False
    assert evidence == original
    assert len(captures) == 1
    captured = captures[0]
    assert captured["game"] == "539"
    assert captured["provider_raw_response"] == wire
    assert captured["native_ocr_text"] == captured["ai_original_text"] == raw
    assert captured["adapter_text"] == result["text"]
    assert captured["source_records"] == [record]
    assert captured.get("human_corrected_text") is None
    assert captured["model_cache_identity"]["text_formatter_version"] == FORMATTER_VERSION
    assert any(t.get("before") == raw and t.get("after") == result["text"]
               for t in captured["transformations"])
