"""Tests for zhu_peng_fill — unit tests (no browser required)."""
import pytest
from betguard.webfill.safety import DANGER_WORDS, is_dangerous_action
from betguard.webfill.zhu_peng_fill import (
    build_zhu_peng_plan,
    _b03_js,
)


class TestSafety:
    def test_all_danger_words_blocked(self):
        """Every word in DANGER_WORDS must be blocked by is_dangerous_action."""
        for word in DANGER_WORDS:
            assert is_dangerous_action(word), f"'{word}' should be blocked"

    def test_zhu_peng_critical_words_blocked(self):
        """Words ZhuPeng specifically relied on must all be blocked."""
        for word in ("送出", "確認", "確定", "取消", "完成", "送出注單",
                     "下注", "刪除", "清除全部", "加入注單"):
            assert is_dangerous_action(word), f"'{word}' should be blocked"

    def test_safe_text_ok(self):
        assert not is_dangerous_action("11")
        assert not is_dangerous_action("二星")
        assert not is_dangerous_action("新增一柱")


class TestB03JS:
    def test_replaces_prefix(self):
        result = _b03_js("__B03__.document.querySelector('td')")
        assert "window.frames[2]" in result
        assert "__B03__" not in result

    def test_no_prefix_no_change(self):
        result = _b03_js("console.log(1)")
        assert "window.frames[2]" not in result


class TestBuildZhuPengPlan:
    def test_basic_plan(self):
        item = {
            "numbers": [[11], [22], [33], [13, 23]],
            "amounts": {"二星": 100, "三星": 100, "四星": 100},
        }
        plan = build_zhu_peng_plan(item)
        assert plan["numbers"] == [[11], [22], [33], [13, 23]]
        assert plan["amounts"] == {"二星": 100, "三星": 100, "四星": 100}

    def test_plan_no_amounts(self):
        item = {"numbers": [[10, 19], [23, 33], [26, 37]]}
        plan = build_zhu_peng_plan(item)
        assert plan["amounts"] == {}

    def test_plan_empty_numbers_raises(self):
        with pytest.raises(ValueError):
            build_zhu_peng_plan({})

    def test_plan_from_parsed_format(self):
        """Simulate the real parsed format: 天天1019x2333x2637."""
        item = {
            "numbers": [[10, 19], [23, 33], [26, 37]],
            "amounts": {"二星": 100, "三星": 100},
        }
        plan = build_zhu_peng_plan(item)
        assert len(plan["numbers"]) == 3
        assert plan["numbers"][0] == [10, 19]
        assert plan["numbers"][2] == [26, 37]


class TestCombinationMath:
    """Verify the combination calculations from the successful test."""

    @staticmethod
    def calc_combs(col_lengths: list[int]) -> dict[str, int]:
        """Calculate 二星/三星/四星 combinations from column lengths.
        
        col_lengths: list of number counts per column, e.g. [1,1,1,2].
        Returns dict of star→count.
        """
        n = len(col_lengths)
        two = three = four = 0
        for i in range(n):
            for j in range(i + 1, n):
                two += col_lengths[i] * col_lengths[j]
                for k in range(j + 1, n):
                    three += col_lengths[i] * col_lengths[j] * col_lengths[k]
                    for l in range(k + 1, n):
                        four += col_lengths[i] * col_lengths[j] * col_lengths[k] * col_lengths[l]
        return {"二星": two, "三星": three, "四星": four}

    def test_4cols_1_1_1_2(self):
        """Test the exact combination from the live test."""
        result = self.calc_combs([1, 1, 1, 2])
        assert result["二星"] == 9
        assert result["三星"] == 7
        assert result["四星"] == 2

    def test_3cols_2_2_2(self):
        """Three columns with 2 numbers each → 12 combos for 二星."""
        result = self.calc_combs([2, 2, 2])
        assert result["二星"] == 12  # 2*2 + 2*2 + 2*2

    def test_2cols_3_3(self):
        result = self.calc_combs([3, 3])
        assert result["二星"] == 9  # 3*3
        assert result["三星"] == 0

    def test_total_bet(self):
        """二星100 * 9 + 三星100 * 7 + 四星100 * 2 = 1800."""
        combos = self.calc_combs([1, 1, 1, 2])
        total = combos["二星"] * 100 + combos["三星"] * 100 + combos["四星"] * 100
        assert total == 1800
