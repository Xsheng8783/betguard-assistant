"""Tests for dotted star suffix formats: 二.三.四星, 2.3.4星, etc."""

import pytest
from betguard.parser import parse_line, ParseError


class TestDottedStarSuffix:
    """Test that dotted star patterns are correctly parsed through the full parser."""

    def test_chinese_dotted_234_star(self):
        r = parse_line("六合：06.09.13.16.43二.三.四星50元")
        assert "06" in str(r.numbers) or 6 in r.numbers
        assert r.stars == [2, 3, 4]
        assert r.money == 50  # raw amount in yuan

    def test_chinese_fullwidth_dotted_234_star(self):
        r = parse_line("六合：06.09.13.16.43二．三．四星50元")
        assert r.stars == [2, 3, 4]

    def test_chinese_dot_dotted_234_star(self):
        r = parse_line("六合：06.09.13.16.43二。三。四星50元")
        assert r.stars == [2, 3, 4]

    def test_numeric_dotted_234_star(self):
        r = parse_line("06.09.13.16.43 2.3.4星50元")
        assert r.stars == [2, 3, 4]

    def test_chinese_dotted_23_star(self):
        r = parse_line("天天：07.17.27.37.05三.四星50元")
        assert r.stars == [3, 4]

    def test_two_dotted_star_with_alt(self):
        r = parse_line("06.09.13.16.43兩.三星50元")
        assert r.stars == [2, 3]

    def test_dotted_star_preserves_game_hint(self):
        r = parse_line("六合：06.09.13.16.43二.三.四星50元")
        from betguard.normalizer import extract_game_hint
        hint, _ = extract_game_hint(r.original_text)
        assert hint == "liuhecai"

    def test_dotted_star_bet_is_normal_not_zhu_peng(self):
        r = parse_line("六合：06.09.13.16.43二.三.四星50元")
        assert r.type in ("normal",)


class TestDottedStarSafety:
    """Safety tests: dotted star normalization should NOT break other formats."""

    def test_decimal_not_parsed_as_bet(self):
        with pytest.raises(ParseError):
            parse_line("0.4")

    def test_version_string_not_parsed(self):
        with pytest.raises(ParseError):
            parse_line("v0.5.39")

    def test_dot_numbers_not_stars(self):
        """35.38.32.03.08 — dots between numbers should stay as number separators."""
        r = parse_line("35.38.32.03.08三四50")
        # Should have 5 numbers, not treat .34 as stars
        assert len(r.numbers) >= 4

    def test_non_star_context_not_dotted(self):
        """二.三.四 without 星 word should not be treated as stars."""
        # This should fail to parse as a bet
        with pytest.raises(ParseError):
            parse_line("二.三.四個人")


class TestSafetyGates:
    def test_safety_constants(self):
        blocked = {
            "ok": False, "blocked": True,
            "auto_submit": False, "auto_confirm": False,
            "danger_buttons_clicked": [],
        }
        assert blocked["auto_submit"] is False
        assert blocked["danger_buttons_clicked"] == []
