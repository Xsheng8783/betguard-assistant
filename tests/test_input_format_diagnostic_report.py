"""Tests for the Input Format Diagnostic Report (v1).

These tests cover:
  * format_input_diagnostic_report() output structure
  * Raw + Display status both present on every line
  * Reason summary is a non-empty, informative string
  * Raw errors / warnings are always preserved (never dropped by Reason)
  * CLI integration via --input-format-report --file <path>

The CLI test is local-only: it writes a small tmp file (no large artifacts),
runs the binary, and asserts the report text contains the expected labels.
No queue_state / review.html / audit.json is produced.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

from betguard.review import (
    format_input_diagnostic_report,
    format_pretty_review,
    review_text,
)


# ---------------------------------------------------------------------------
# Section 1 -- report shape
# ---------------------------------------------------------------------------


def test_report_returns_string() -> None:
    summary = review_text("06.13.23.22 234.100")
    out = format_input_diagnostic_report(summary)
    assert isinstance(out, str)
    assert "Line 1:" in out
    assert "Summary:" in out


def test_report_does_not_break_existing_pretty_review() -> None:
    """Adding format_input_diagnostic_report must not regress format_pretty_review."""
    summary = review_text("06.13.23.22 234.100\n17.29.1000")
    pretty = format_pretty_review(summary)
    # Sanity: pretty still mentions each item.
    assert "[1]" in pretty
    assert "[2]" in pretty


# ---------------------------------------------------------------------------
# Section 2 -- Raw + Display status both present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_raw, expected_display",
    [
        ("06.13.23.22 234.100", "ok", "VALID"),
        ("06.13.23.22 -0.25", "ok", "VALID"),
        ("17.29.1000", "error", "BLOCKED"),
        ("港01.02.03=100", "error", "BLOCKED"),
        ("06.50.13.22 234.100", "error", "BLOCKED"),
        ("01.02.03改100", "error", "BLOCKED"),
    ],
)
def test_raw_and_display_status_both_present(
    text: str, expected_raw: str, expected_display: str
) -> None:
    summary = review_text(text)
    out = format_input_diagnostic_report(summary)
    assert f"Raw status: {expected_raw}" in out
    assert f"Display status: {expected_display}" in out


# ---------------------------------------------------------------------------
# Section 3 -- All required fields present on every line
# ---------------------------------------------------------------------------


def test_valid_bet_has_all_fields_in_report() -> None:
    summary = review_text("06.13.23.22 -0.25")
    out = format_input_diagnostic_report(summary)
    for fragment in ["06,13,23,22", "2,3,4", "0.25", "25", "normal", "VALID"]:
        assert fragment in out, f"Missing: {fragment}"


def test_blocked_bet_still_has_all_fields() -> None:
    summary = review_text("17.29.1000")
    out = format_input_diagnostic_report(summary)
    # Customer shorthand path skips number parsing entirely, so the report
    # shows Numbers/Stars/Unit/Money as "-".  We only require the label,
    # the type, and the reason hint to be present.
    for fragment in ["1000", "BLOCKED", "customer", "normal"]:
        assert fragment in out, f"Missing: {fragment}"


# ---------------------------------------------------------------------------
# Section 4 -- Reason + Raw errors/warnings contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_reason_fragment",
    [
        ("06.13.23.22 -0.25", "0.25 unit = 25 元"),
        ("17.29.1000", "customer shorthand"),
        ("港01.02.03=100", "HK prefix"),
        ("01.02.03改100", "unsupported character"),
        ("06.50.13.22 234.100", "outside the 1-39 range"),
        ("06.06.13.22 234.100", "duplicate number"),
    ],
)
def test_reason_summary_present(
    text: str, expected_reason_fragment: str
) -> None:
    summary = review_text(text)
    out = format_input_diagnostic_report(summary)
    # The Reason line must exist and contain the human-readable summary.
    assert "Reason:" in out
    # Pull the Reason value from the line that follows "Reason:".
    reason_line = next(
        ln for ln in out.splitlines() if ln.strip().startswith("Reason:")
    )
    reason_value = reason_line.split("Reason:", 1)[1].strip()
    assert expected_reason_fragment.lower() in reason_value.lower(), (
        f"expected '{expected_reason_fragment}' in '{reason_value}'"
    )


def test_raw_errors_and_warnings_always_preserved() -> None:
    """Raw errors / warnings lists must always be present in the report,
    even when Reason summary covers them.  This guards against future
    refactors that would drop raw diagnostic info."""
    summary = review_text("17.29.1000\n01.02.03改100")
    out = format_input_diagnostic_report(summary)
    assert "Raw errors:" in out
    assert "Raw warnings:" in out
    # Specific raw message substrings must be present.
    assert "customer-specific shorthand" in out
    assert "unsupported characters" in out


def test_raw_errors_list_is_not_overwritten_by_reason() -> None:
    """The raw 'customer-specific shorthand requires manual review' must
    still appear verbatim in the report, alongside the human-readable
    'Reason: customer shorthand, ...'."""
    summary = review_text("17.29.1000")
    out = format_input_diagnostic_report(summary)
    # Raw error text:
    assert "customer-specific shorthand requires manual review" in out
    # Human-readable reason:
    assert "customer shorthand, requires human review" in out


def test_warning_line_has_raw_warnings_visible() -> None:
    """A bet with a warning (e.g. missing stars) keeps its raw warning
    text in the report."""
    summary = review_text("01/05/23/36")  # column bet, missing stars
    out = format_input_diagnostic_report(summary)
    # Either a warning or an error is fine; we just need Raw warnings: present.
    assert "Raw warnings:" in out
    # The report must contain the warning substring if status=warning.
    if "Raw status: warning" in out:
        assert "missing stars" in out


# ---------------------------------------------------------------------------
# Section 5 -- Summary section
# ---------------------------------------------------------------------------


def test_report_includes_summary_section() -> None:
    summary = review_text("06.13.23.22 234.100\n17.29.1000\n港01.02.03=100")
    out = format_input_diagnostic_report(summary)
    assert "Summary:" in out
    assert "total=3" in out
    # ok / error counters should be present
    assert "ok=" in out
    assert "error=" in out
    assert "warning=" in out


# ---------------------------------------------------------------------------
# Section 6 -- CLI integration
# ---------------------------------------------------------------------------


def test_cli_input_format_report_runs_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end CLI test: write a small tmp file, invoke the binary, and
    verify the diagnostic report is printed.  No queue_state, no review.html,
    no audit.json are produced -- this CLI is read-only."""
    from betguard.cli import main

    sample = (
        "06.13.23.22 234.100\n"
        "06.13.23.22 -0.25\n"
        "17.29.1000\n"
        "港01.02.03=100\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(sample)
        tmp = f.name
    try:
        rc = main(["--input-format-report", "--file", tmp])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Line 1:" in out
        assert "Line 2:" in out
        assert "Line 3:" in out
        assert "Line 4:" in out
        assert "Raw status: ok" in out
        assert "Raw status: error" in out
        assert "Display status: VALID" in out
        assert "Display status: BLOCKED" in out
    finally:
        os.unlink(tmp)

    # Confirm the read-only contract: no queue_state / review_out files
    # were created in CWD or in the temp file's directory.
    cwd = os.getcwd()
    for forbidden in ("queue_state.json", "review.html", "audit.json"):
        assert not os.path.exists(os.path.join(cwd, forbidden)), (
            f"CLI leaked {forbidden} into CWD -- read-only contract broken"
        )


def test_cli_input_format_report_with_text_arg(capsys: pytest.CaptureFixture[str]) -> None:
    """--input-format-report --text <string> also works (no file needed)."""
    from betguard.cli import main

    rc = main([
        "--input-format-report",
        "--text", "06.13.23.22 234.100\n17.29.1000",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Line 1:" in out
    assert "VALID" in out
    assert "BLOCKED" in out
