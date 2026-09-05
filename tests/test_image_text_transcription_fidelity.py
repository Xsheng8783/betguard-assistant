"""Regression contract for whole-image transcription into editable text."""

from __future__ import annotations

import hashlib

from betguard.vision import service
from betguard.vision.gemma_shadow import RAW_READER_PROMPT
from betguard.vision.image_text_preflight import preview_image_text_with_existing_parser
from betguard.webfill.batch_mock_queue import build_batch_mock_queue


def _item(raw_text: str, **overrides):
    return {
        "raw_text": raw_text,
        "numbers": "literal",
        "multiplier_text": "none",
        "layout_guess": "normal",
        "continuation": "no",
        "special_text": "none",
        "cancelled": "no",
        "uncertain": False,
        "uncertain_reason": "none",
        **overrides,
    }


def test_sample007_physical_records_stay_separated_by_blank_line() -> None:
    evidence = {
        "status": "completed",
        "items": [
            _item("05 × 08 09 23 × 10 20 29\n2,3 × 2"),
            _item("11 20 14 36 38 26\n2,3,4 × 0.5", multiplier_text="0.5"),
            _item("06 × 04 36 12 × 28 39\n2,3 × 0.5", multiplier_text="0.5"),
            _item("12 × 03 22 31\n2,3 × 0.5", multiplier_text="0.5"),
        ],
    }

    text = service.gemma_evidence_to_betguard_text(evidence)
    records = text.split("\n\n")

    assert records == [item["raw_text"] for item in evidence["items"]]
    assert "11 20 14 36 38 26" not in records[0]
    assert "12 × 03 22 31" not in records[2]


def test_decimal_contract_preserves_conflicting_literal_without_repair() -> None:
    evidence = {
        "status": "completed",
        "items": [
            _item(
                "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 05",
                multiplier_text="2,3,4 × 0.5",
                layout_guess="column",
            )
        ],
    }

    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)

    assert text.endswith("2,3,4 × 05")
    assert any(item["code"] == "MODEL_FIELD_CONFLICT" for item in notices)


def test_column_examples_are_betguard_parser_compatible() -> None:
    examples = (
        "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5",
        "12 08 × 24 14 × 36 38\n2,3 × 0.5",
    )

    for text in examples:
        queue = build_batch_mock_queue(text, game="六合")
        assert queue["preprocessing"]["status"] == "READY"
        result = queue["preprocessing"]["valid_candidates"][0]["result"]
        assert result["type"] == "column"


def test_special_tail_digit_conflict_is_not_repaired_from_model_evidence() -> None:
    evidence = {
        "status": "completed",
        "items": [
            _item(
                "03 × 16 × 尾\n2,3 × 1",
                special_text="7尾",
                layout_guess="column",
            )
        ],
    }

    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)

    assert text == "03 × 16 × 尾\n2,3 × 1"
    assert any(item["code"] == "MODEL_FIELD_CONFLICT" for item in notices)


def test_cancelled_record_preserves_source_and_cannot_parse_as_active() -> None:
    evidence = {
        "status": "completed",
        "items": [
            _item("04 × 19 × 39\n2,3 × 0.5", multiplier_text="0.5"),
            _item("(crossed out with red ink)", cancelled="yes"),
            _item("(cancelled bet)"),
        ],
    }

    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)

    assert text.startswith("04 × 19 × 39\n2,3 × 0.5")
    assert "(crossed out with red ink)" in text
    assert "(cancelled bet)" in text
    assert not service.preflight_image_text(text)["all_parseable"]
    assert len([n for n in notices if n["code"] == "CANCELLATION_UNCONFIRMED"]) == 2


def test_prompt_contains_generic_fidelity_rules_without_sample_special_case() -> None:
    for required in (
        "PHYSICAL RECORD BOUNDARIES ARE HARD TRANSCRIPTION BOUNDARIES",
        "sides of a clear separator",
        "0.5",
        "must never become 05",
        "36 38 × 07 17 × 08 18 × 06 13",
        "12 08 × 24 14 × 36 38",
        "03 × 16 × 7尾",
        "(crossed out)",
        "exactly ONE transposed number line",
        "do not append any neighboring record's row",
    ):
        assert required in RAW_READER_PROMPT
    assert "sample-007" not in RAW_READER_PROMPT
    assert "sha256" not in RAW_READER_PROMPT.lower()


def test_parser_preflight_is_read_only_for_valid_text() -> None:
    text = "36 38 × 07 17 × 08 18 × 06 13\n2,3,4 × 0.5"
    original = text

    result = preview_image_text_with_existing_parser(text)

    assert text == original
    assert result["all_parseable"] is True
    assert result["parsed_bet_count"] == 1
    assert result["unresolved_count"] == 0
    assert result["text_mutated"] is False
    assert result["input_text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert result["auto_apply"] is False
    assert result["auto_submit"] is False


def test_parser_preflight_reports_line_and_never_repairs_invalid_text() -> None:
    text = "05 × 08 09 23 × 10 20 29\n2,3 × 2\n\n03 × 16 × ?尾\n2,3 × 1"
    original = text

    result = preview_image_text_with_existing_parser(text)

    assert text == original
    assert result["all_parseable"] is False
    assert result["unresolved_count"] == 1
    assert result["issues"][0]["line_no"] == 4
    assert "?尾" in result["issues"][0]["raw"]
    assert result["text_mutated"] is False
