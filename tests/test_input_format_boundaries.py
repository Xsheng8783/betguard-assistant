"""Input 格式邊界測試 v1.

Documentation-only. This file does NOT change parser or preprocessor behavior;
it records, for a batch of boundary cases (negative amounts, customer
shorthand, suspicious Chinese chars, number range edges, HK prefix), what
the parser/review does TODAY (locked, so we notice regressions), and what it
SHOULD do once the corresponding rule lands (``xfail``).

The findings below map 1:1 to the bullet list in README.md -> "Needs Review
說明" (and the equivalent safety guarantees in AGENTS.md). If a case here
ever flips to "ok" unexpectedly, a new unsafe format silently leaked into
Valid -- which this batch explicitly forbids.
"""
from __future__ import annotations

import pytest

from betguard.review import build_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def status_of(text: str) -> str:
    return build_report(text).to_dict()["status"]


# ---------------------------------------------------------------------------
# Section 1 -- 負數小數 amount (README: "小數 hyphen amount 例如
# 02-03-05-16-20 -0.25" -> Needs Review / Invalid)
# ---------------------------------------------------------------------------

# -0.25 走 standalone 0.5 amount branch, 觸發 number=0 不在 range -> error
#   today. Locks existing safe behavior.
def test_negative_decimal_amount_standalone_blocked() -> None:
    assert status_of("-0.25") == "error"


# "06.13.23.22 234.-0.25" hits confirmed_spaced_decimal_hyphen with a
# negative amount, which parser surfaces as "missing or unclear amount".
# Locks existing safe behavior.
def test_negative_decimal_amount_with_star_blocked() -> None:
    assert status_of("06.13.23.22 234.-0.25") == "error"


# 06.13.23.22 -0.25 (hyphen, no star) -- KNOWN BUG: parser currently accepts
# this as a Valid bet because confirmed_spaced_decimal_hyphen does not
# reject a leading "-" on the amount. README clearly states this is Needs
# Review territory. xfail marks the existing unsafe behavior; once the
# parser gains a signed-amount guard this should XPASS.
@pytest.mark.xfail(
    reason="parser accepts '-0.25' as valid money via confirmed_spaced_decimal_hyphen; "
           "needs a signed-amount guard. See README 'Needs Review' bullet on "
           "小數 hyphen amount (e.g. 02-03-05-16-20 -0.25).",
    strict=True,
)
def test_negative_decimal_amount_hyphen_blocked() -> None:
    assert status_of("06.13.23.22 -0.25") == "error"


# ---------------------------------------------------------------------------
# Section 2 -- Customer-specific 簡碼 (README: "Customer-specific 簡碼
# 1000 / 600 / 400 這類客戶專屬尾碼")
# ---------------------------------------------------------------------------

# Standalone shorthand never parses to Valid (no numbers + "missing
# numbers" or "customer-specific shorthand requires manual review").
@pytest.mark.parametrize("text", ["400", "600", "1000"])
def test_customer_shorthand_standalone_blocked(text: str) -> None:
    assert status_of(text) == "error"


# Two-number line ending in 400/600/1000 must NOT auto-resolve. Today
# these trip "customer-specific shorthand requires manual review". This
# closes the coverage gap left by Finding 6 (which only tested 600/1000).
@pytest.mark.parametrize(
    "text",
    [
        "17.29.400",
        "17.29.600",
        "17.29.1000",
        "11.28.1000",
        "11.37.1000",
    ],
)
def test_customer_shorthand_two_number_line_blocked(text: str) -> None:
    assert status_of(text) == "error"


# ---------------------------------------------------------------------------
# Section 3 -- 可疑中文字 (README: "含「改」/「臂」/「各10」/「寫」/「港」")
# ---------------------------------------------------------------------------

# Standalone suspicious chars: parser rejects as unsupported character.
@pytest.mark.parametrize("text", ["改", "臂", "嫌", "寫"])
def test_suspicious_chinese_char_standalone_blocked(text: str) -> None:
    assert status_of(text) == "error"


# Suspicious chars at the end of a bet line: never auto-Valid.
@pytest.mark.parametrize(
    "text",
    [
        "01.02.03改100",
        "01.02.03嫌100",
        "01.02.03臂100",
        "01.02.03寫100",
        "01.02.03改",
        "01.02.03嫌",
    ],
)
def test_suspicious_chinese_char_at_end_blocked(text: str) -> None:
    assert status_of(text) == "error"


# ---------------------------------------------------------------------------
# Section 4 -- 號碼邊界 (README: "號碼超出範圍（539 有效範圍 1-39）")
# ---------------------------------------------------------------------------

# Single-number boundary cases: 0, 00 -> 0; 40 -> out of range; 056 ->
# not a 2-digit number, parse fails.
@pytest.mark.parametrize("text", ["0", "00", "40", "056"])
def test_number_out_of_range_or_malformed_standalone_blocked(text: str) -> None:
    assert status_of(text) == "error"


# Number embedded in a complete bet line: each variant must NOT silently
# round-trip to Valid.
@pytest.mark.parametrize(
    "text",
    [
        "01.02.40=100",
        "01.02.056=100",
        "01.02.0=100",
        "01.02.00=100",
    ],
)
def test_number_out_of_range_in_line_blocked(text: str) -> None:
    assert status_of(text) == "error"


# ---------------------------------------------------------------------------
# Section 5 -- 港 / HK 前綴 (README: "含「港」前綴的項目")
# ---------------------------------------------------------------------------

# Front-prefixed bet line: never auto-Valid.
@pytest.mark.parametrize(
    "text",
    [
        "港01.02.03=100",
        "HK01.02.03=100",
        "hk 01.02.03=100",
        "港 01.02.03=100",
    ],
)
def test_hk_prefixed_line_blocked(text: str) -> None:
    assert status_of(text) == "error"


# Standalone 港 / HK label: never resolves to a Valid bet.
@pytest.mark.parametrize("text", ["港", "HK"])
def test_hk_prefix_standalone_blocked(text: str) -> None:
    assert status_of(text) == "error"


# ---------------------------------------------------------------------------
# Section 6 -- Sanity references (the well-known Valid / Invalid cases
# that anchor the rest of the suite).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "06.13.23.22 234.100",
        "06.13 23.100",
        "06.13.23.22=100",
    ],
)
def test_reference_valid_bets_stay_ok(text: str) -> None:
    assert status_of(text) == "ok"


@pytest.mark.parametrize(
    "text",
    [
        "06.50.13.22 234.100",  # 50 out of 1-39
        "06.06.13.22 234.100",  # duplicate 6
    ],
)
def test_reference_invalid_bets_stay_blocked(text: str) -> None:
    assert status_of(text) == "error"
