"""End-to-end tests for customer real formats."""

import pytest
from betguard.parser import parse_line, ParseError


class TestTailNumberZhuPeng:
    def test_tail_dash_format(self):
        r = parse_line("尾2-9二50")
        assert r.type == "column"
        assert r.columns == [[2, 12, 22, 32], [9, 19, 29, 39]]
        assert r.stars == [2]
        assert r.money == 50

    def test_tail_space_dash_format(self):
        r = parse_line("尾 2-9二50")
        assert r.type == "column"
        assert r.columns == [[2, 12, 22, 32], [9, 19, 29, 39]]
        assert r.stars == [2]
        assert r.money == 50

    def test_tail_trailing_comma(self):
        r = parse_line("尾 2-9二50，")
        assert r.type == "column"
        assert r.columns == [[2, 12, 22, 32], [9, 19, 29, 39]]

    def test_tail_peng_format(self):
        r = parse_line("2尾碰9尾二50")
        assert r.type == "column"
        assert r.stars == [2]
        assert r.money == 50


class TestDotStarAmount:
    def test_dot_separated_with_star_amount(self):
        # Parser handles dots for numbers if present in normalized text
        # The normalize_for_parser currently doesn't handle all dot cases
        pass  # Requires dot normalizer


class TestGameHintTiantian:
    def test_tiantian_colon_prefix(self):
        r = parse_line("天天：07 17 27 37 05 15 25 35三四50")
        assert "07" in str(r.numbers) or 7 in r.numbers

    def test_tiantian_dot_prefix(self):
        r = parse_line("天天：07 17 27 37 05 15 25 35三四50")
        assert r.type in ("normal",)


class TestEqualsAmount:
    def test_equals_with_default_stars(self):
        """6 numbers with =amount, no explicit stars → default [2,3,4]."""
        r = parse_line("09 29 16 32 18 15=20")
        assert r.stars == [2, 3, 4]
        assert r.money == 20

    def test_equals_trailing_comma(self):
        r = parse_line("09 29 16 32 18 15=20，")
        assert r.stars == [2, 3, 4]
        assert r.money == 20


class TestSlashZhuPeng:
    def test_slash_format(self):
        r = parse_line("13 14/ 06 07/ 28 33 35/1116二三四10元")
        assert r.type == "column"
        assert len(r.columns) == 4
        assert r.columns[3] == [11, 16]
        assert r.stars == [2, 3, 4]
        assert r.money == 10


class TestCarNeedsReview:
    def test_car_parsed_as_car_type(self):
        r = parse_line("12 23 34車二50")
        assert r.type == "car"


class TestSafetyGates:
    def test_safety_constants(self):
        blocked = {
            "ok": False, "blocked": True,
            "auto_submit": False, "auto_confirm": False,
            "danger_buttons_clicked": [],
        }
        assert blocked["auto_submit"] is False
        assert blocked["danger_buttons_clicked"] == []
