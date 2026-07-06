"""Tests for Needs Review Classification v1a — review-only metadata labels."""
from __future__ import annotations

import pytest
from betguard.input_preprocessor import (
    _review_classification_labels,
    preprocess_batch_input,
)


# ── Unit tests: _review_classification_labels ──

def test_non_539_candidate_hk_prefix():
    labels = _review_classification_labels("港23/1車")
    assert "review_label:non_539_candidate" in labels


def test_non_539_candidate_liuhe():
    labels = _review_classification_labels("六合23-24-25")
    assert "review_label:non_539_candidate" in labels


def test_non_539_candidate_hk_lower():
    labels = _review_classification_labels("hk 六合 23/1車")
    assert "review_label:non_539_candidate" in labels


def test_suspected_non_539_due_to_range_44():
    labels = _review_classification_labels("06-24-44/100")
    assert "review_label:suspected_non_539_due_to_range" in labels


def test_suspected_non_539_due_to_range_47():
    labels = _review_classification_labels("31-32-47/100")
    assert "review_label:suspected_non_539_due_to_range" in labels


def test_suspected_non_539_no_range_39():
    labels = _review_classification_labels("01-02-39/100")
    assert "review_label:suspected_non_539_due_to_range" not in labels


def test_suspected_tiantianle_suffix():
    labels = _review_classification_labels("01.16.15=100天")
    assert "review_label:suspected_tiantianle" in labels


def test_suspected_tiantianle_tt():
    labels = _review_classification_labels("12.34.56 天天樂")
    assert "review_label:suspected_tiantianle" in labels


def test_person_name_suffix_arm():
    labels = _review_classification_labels("01 39 -2000改")
    assert "review_label:person_name_suffix" in labels


def test_person_name_suffix_xian():
    labels = _review_classification_labels("08-15/100嫌")
    assert "review_label:person_name_suffix" in labels


def test_ambiguous_long_token_5digit():
    labels = _review_classification_labels("12.20.30.02.36.35234.100")
    assert "review_label:ambiguous_long_token" in labels


def test_ambiguous_long_token_4digit_no():
    labels = _review_classification_labels("01.02.03.1500")
    assert "review_label:ambiguous_long_token" in labels


def test_ambiguous_long_token_short():
    labels = _review_classification_labels("01.02.999/100")
    assert "review_label:ambiguous_long_token" not in labels


def test_per_star_amount_split():
    labels = _review_classification_labels("19.21.27.28 23星各2 4星X1")
    assert "review_label:per_star_amount_split" in labels


def test_per_star_amount_split_chinese():
    labels = _review_classification_labels("01.02.03 二三星各100")
    assert "review_label:per_star_amount_split" in labels


def test_multiple_labels():
    labels = _review_classification_labels("港06-24-44/100天")
    assert len(labels) >= 2
    assert "review_label:non_539_candidate" in labels
    assert "review_label:suspected_non_539_due_to_range" in labels
    assert "review_label:suspected_tiantianle" in labels


def test_no_labels_on_normal():
    labels = _review_classification_labels("01.02.03 二星100")
    assert labels == []


# ── Integration tests: labels flow into preprocessing notes ──

def _find_notes_for_line(text: str, line_index: int = 0):
    """Run preprocessor and extract notes for a candidate bet line."""
    result = preprocess_batch_input(text)
    lines = result.get("candidate_bet_lines", [])
    if line_index < len(lines):
        return lines[line_index].get("preprocessing_notes", [])
    return []


def _extract_labels_from_notes(notes: list[str]) -> list[str]:
    return [n[len("review_label:"):] for n in notes
            if isinstance(n, str) and n.startswith("review_label:")]


def test_non_539_candidate_in_pipeline():
    notes = _find_notes_for_line("港23/1車")
    labels = _extract_labels_from_notes(notes)
    assert "non_539_candidate" in labels
    # Must NOT be valid
    result = preprocess_batch_input("港23/1車")
    valid = result.get("summary", {}).get("valid_count", 0)
    assert valid == 0


def test_suspected_non_539_range_in_pipeline():
    notes = _find_notes_for_line("31-32-47/100")
    labels = _extract_labels_from_notes(notes)
    assert "suspected_non_539_due_to_range" in labels


def test_ambiguous_long_token_in_pipeline():
    notes = _find_notes_for_line("12.20.30.02.36.35234.100")
    labels = _extract_labels_from_notes(notes)
    assert "ambiguous_long_token" in labels


def test_per_star_amount_split_in_pipeline():
    notes = _find_notes_for_line("19.21.27.28 23星各2 4星X1")
    labels = _extract_labels_from_notes(notes)
    assert "per_star_amount_split" in labels


# ── Safety: parser.py / validator.py / normalizer.py not modified ──

def test_parser_not_imported_in_preprocessor_directly():
    """Verify parser is not imported in input_preprocessor.py."""
    import ast
    from pathlib import Path
    fpath = Path(__file__).parent.parent / "src" / "betguard" / "input_preprocessor.py"
    tree = ast.parse(fpath.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    for imp in imports:
        if isinstance(imp, ast.ImportFrom):
            if imp.module and "parser" in imp.module:
                # OK if it's not betguard.parser (test files use _parser)
                if "betguard.parser" == imp.module:
                    pytest.fail("input_preprocessor imports betguard.parser — not allowed")
        elif isinstance(imp, ast.Import):
            for alias in imp.names:
                if "parser" in alias.name:
                    pytest.fail(f"input_preprocessor imports {alias.name} — not allowed")
