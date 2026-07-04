"""Parser / review EXPECTED-BEHAVIOR documentation tests.

Documentation-only. This file does NOT change parser or preprocessor behavior;
it records, for a batch of human-reviewed Needs-Review / Invalid inputs:

  * what the parser/review does today (locked, so we notice regressions), and
  * what it SHOULD do once the corresponding rule is implemented (``xfail``).

Nothing here promotes a new format into Valid. The ``xfail`` cases are the
future targets; when a rule lands, the matching xfail will XPASS and the marker
can be removed. Findings are numbered to match the review notes (1..7).
"""
from __future__ import annotations

import pytest

from betguard.input_preprocessor import preprocess_batch_input
from betguard.review import build_report


def status_of(text: str) -> str:
    return build_report(text).to_dict()["status"]


def report_of(text: str) -> dict:
    return build_report(text).to_dict()


def batch_fragments(text: str) -> list[dict]:
    """(raw, status) for each preprocessor candidate fragment of a pasted input."""
    result = preprocess_batch_input(text)
    return [
        {"raw": c["raw"], **build_report(c["raw"]).to_dict()}
        for c in result.get("candidate_bet_lines", [])
    ]


# ---------------------------------------------------------------------------
# Section A — CURRENT SAFE BEHAVIOR (passes today; guards against regressions)
# These inputs are correctly blocked and MUST stay blocked until a real rule
# exists. If one of these ever flips to "ok", a new format silently leaked into
# Valid — which this batch explicitly forbids.
# ---------------------------------------------------------------------------


# Finding 1: the 住碰 header fragment alone is (correctly) blocked.
def test_finding1_zhupeng_header_fragment_blocked() -> None:
    assert status_of("天天1019x2333x2637") == "error"


# Finding 2 guard: a standalone "08.05.02 234.100" must NOT be auto-corrected to
# 23.100; on its own it is correctly blocked (four-star needs >=4 numbers).
def test_finding2_standalone_short_fourstar_stays_blocked() -> None:
    report = report_of("08.05.02 234.100")
    assert report["status"] == "error"


# Finding 3: concatenated customer format stays blocked for now.
def test_finding3_concatenated_customer_format_blocked() -> None:
    assert status_of("11.09,1000.11.21.31.01.234.100") == "error"


# Finding 4: two-number amount + single car continuation stays blocked for now.
def test_finding4_car_continuation_blocked() -> None:
    assert status_of("25.06.1000.06.1.車臂") == "error"


# Finding 6: 600 / 1000 two-number shorthand stays manual review (blocked).
@pytest.mark.parametrize("text", ["17.29.600", "11.37.1000", "11.28.1000"])
def test_finding6_two_number_shorthand_stays_manual_review(text: str) -> None:
    assert status_of(text) == "error"


# Finding 7: Tiantianle slash-group 注碰/住碰 stays blocked (no naive slash split).
def test_finding7_slash_group_zhupeng_blocked() -> None:
    assert status_of("天天15.05.02/09.03.31/11.06.08/01.13.28三四10元") == "error"


# Finding 2 (parser already correct on the JOINED form): the raw dot-dot line
# parses to the intended grouping. The divergence is only in the preprocessor
# split (see the xfail below), which this step does not modify.
def test_finding2_joined_line_parses_to_intended_grouping() -> None:
    report = report_of("06.01.32..08.05.02..234.100")
    assert report["status"] == "ok"
    assert report["numbers"] == [6, 1, 32, 8, 5, 2]
    assert report["stars"] == [2, 3, 4]
    assert report["money"] == 100


# ---------------------------------------------------------------------------
# Section B — BATCH 1 FIXES (now implemented; previously xfail).
# These encode the human-confirmed intended meaning and now pass.
# ---------------------------------------------------------------------------


# Finding 1 (priority #1): the continuation line "2-3-100" belongs to the
# previous 天天樂 住碰 fragment. It must NOT stand alone as an independent Valid
# 2-star bet; the pair is merged and blocked for manual review.
def test_finding1_zhupeng_continuation_not_independent_valid() -> None:
    frags = batch_fragments("天天1019x2333x2637\n2-3-100")
    # No fragment may be an independent Valid 2-3-100 bet.
    assert not any(
        f["status"] == "ok" and f.get("numbers") == [2, 3] for f in frags
    )
    # The merged fragment is blocked with the explicit 住碰 reason.
    assert any(
        f["status"] == "error"
        and "Tiantianle 住碰 continuation requires manual review" in f.get("errors", [])
        for f in frags
    )


# Finding 2: dot-dot grouped continuation stays one bet
# (06,01,32,08,05,02 / 234 / 100) instead of broken fragments.
def test_finding2_dotdot_grouped_continuation_merges() -> None:
    frags = batch_fragments("06.01.32..08.05.02..234.100")
    assert all(f["status"] != "error" for f in frags)
    all_numbers = [n for f in frags for n in (f.get("numbers") or [])]
    assert all_numbers == [6, 1, 32, 8, 5, 2]
    assert any(f["status"] == "ok" and f.get("money") == 100 for f in frags)


# Finding 5: five-number equals-amount with trailing Chinese punctuation is now
# valid after trailing-punctuation cleanup.
def test_finding5_equals_amount_five_numbers_valid() -> None:
    report = report_of("01.03.05.23.36=100，")
    assert report["status"] == "ok"
    assert report["numbers"] == [1, 3, 5, 23, 36]
    assert report["stars"] == [2, 3, 4]
    assert report["money"] == 100


# Guard: a standalone "2-3-100" WITHOUT a 住碰 header is a legitimate 二星 bet and
# must remain Valid (the fix is context-gated, not a blanket block).
def test_standalone_2_3_100_without_zhupeng_stays_valid() -> None:
    report = report_of("2-3-100")
    assert report["status"] == "ok"
    assert report["numbers"] == [2, 3]
