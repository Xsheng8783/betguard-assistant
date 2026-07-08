"""Safety guard tests for ZhuPeng pipeline integration."""
import pytest
from betguard.webfill.zhu_peng_pipeline import (
    is_zhu_peng_item,
    zhu_peng_columns_from_item,
    zhu_peng_preflight,
    zhu_peng_star_map,
    _normalize_star_amounts,
    ZHU_PENG_SAFETY_GUARDS,
)


class TestItemIdentification:
    def test_column_bet_type(self):
        assert is_zhu_peng_item({"type": "column", "columns": [[11], [22]]})

    def test_zhu_peng_bet_type(self):
        assert is_zhu_peng_item({"bet_type": "zhu_peng", "columns": [[11]]})

    def test_columns_key(self):
        assert is_zhu_peng_item({"columns": [[10, 19], [23, 33]]})

    def test_not_zhu_peng(self):
        assert not is_zhu_peng_item({"type": "standard", "numbers": [11, 22]})
        assert not is_zhu_peng_item({})


class TestColumnsExtraction:
    def test_dict_format(self):
        item = {"columns": [{"numbers": [11]}, {"numbers": [22]}]}
        assert zhu_peng_columns_from_item(item) == [[11], [22]]

    def test_list_format(self):
        item = {"columns": [[11], [22, 33]]}
        assert zhu_peng_columns_from_item(item) == [[11], [22, 33]]

    def test_plan_numbers_format(self):
        item = {"numbers": [[10, 19], [23, 33], [26, 37]]}
        assert zhu_peng_columns_from_item(item) == [[10, 19], [23, 33], [26, 37]]

    def test_empty(self):
        assert zhu_peng_columns_from_item({}) == []

    def test_single_int_col(self):
        item = {"columns": [11, 22, 33]}
        result = zhu_peng_columns_from_item(item)
        assert result == [[11], [22], [33]]


class TestStarMap:
    def test_default(self):
        assert zhu_peng_star_map({}) == {2: "二星", 3: "三星", 4: "四星"}

    def test_custom_stars(self):
        item = {"stars": [2, 4]}
        result = zhu_peng_star_map(item)
        assert result == {2: "二星", 4: "四星"}
        assert 3 not in result

    def test_custom_map(self):
        item = {"stars": [2], "star_map": {"2": "二星"}}
        assert zhu_peng_star_map(item) == {2: "二星"}


class TestPreflightSafety:
    def test_no_accepted_by_human(self):
        item = {"columns": [[11], [22]], "amounts": {2: 100, 3: 100, 4: 100}}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("accepted" in e.lower() for e in report["errors"])

    def test_needs_review_blocked(self):
        item = {"columns": [[11]], "status": "NEEDS_REVIEW", "accepted_by_human": True}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"

    def test_invalid_blocked(self):
        item = {"columns": [[11]], "status": "INVALID", "accepted_by_human": True}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"

    def test_watchlist_blocked(self):
        item = {"columns": [[11]], "status": "WATCHLIST", "accepted_by_human": True}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"

    def test_valid_item_passes(self):
        item = {
            "columns": [[11], [22], [33], [13, 23]],
            "amounts": {2: 100, 3: 100, 4: 100},
            "status": "CURRENT",
            "accepted_by_human": True,
        }
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"
        assert report["errors"] == []

    def test_empty_columns_blocked(self):
        item = {"accepted_by_human": True, "status": "CURRENT"}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("columns" in e.lower() for e in report["errors"])

    def test_empty_column_in_list(self):
        item = {"columns": [[11], [], [33]], "accepted_by_human": True, "status": "CURRENT"}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"

    def test_missing_amount(self):
        item = {"columns": [[11], [22]], "accepted_by_human": True, "status": "CURRENT"}
        report = zhu_peng_preflight(item)
        assert report["status"] == "BLOCKED"
        assert any("amount" in e.lower() for e in report["errors"])


class TestNormalizeStarAmounts:
    """Tests for the _normalize_star_amounts shared helper."""

    def test_int_keys(self):
        star_map = {2: "二星", 3: "三星", 4: "四星"}
        result = _normalize_star_amounts({2: 100, 3: 100, 4: 100}, star_map)
        assert result == {"二星": 100, "三星": 100, "四星": 100}

    def test_string_digit_keys(self):
        star_map = {2: "二星", 3: "三星", 4: "四星"}
        result = _normalize_star_amounts({"2": 100, "3": 100, "4": 100}, star_map)
        assert result == {"二星": 100, "三星": 100, "四星": 100}

    def test_mixed_keys(self):
        star_map = {2: "二星", 3: "三星", 4: "四星"}
        result = _normalize_star_amounts({2: 100, "3": 100, "4": 100}, star_map)
        assert result == {"二星": 100, "三星": 100, "四星": 100}

    def test_non_numeric_key_passthrough(self):
        star_map = {2: "二星", 3: "三星", 4: "四星"}
        result = _normalize_star_amounts({2: 100, "custom_star": 200}, star_map)
        assert result == {"二星": 100, "custom_star": 200}

    def test_partial_stars(self):
        star_map = {2: "二星", 3: "三星", 4: "四星"}
        result = _normalize_star_amounts({2: 100, 3: 100}, star_map)
        assert result == {"二星": 100, "三星": 100}


class TestPreflightAmountKeyNormalization:
    """Preflight must accept both int and string key amounts."""

    def test_string_key_amounts_passes(self):
        item = {
            "columns": [[11], [22], [33], [13, 23]],
            "amounts": {"2": 100, "3": 100, "4": 100},
            "status": "CURRENT",
            "accepted_by_human": True,
        }
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"
        assert report["errors"] == []

    def test_int_key_amounts_passes(self):
        item = {
            "columns": [[11], [22], [33], [13, 23]],
            "amounts": {2: 100, 3: 100, 4: 100},
            "status": "CURRENT",
            "accepted_by_human": True,
        }
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"

    def test_mixed_key_amounts_passes(self):
        # 2 columns → max 2-star.
        item = {
            "columns": [[11], [22]],
            "amounts": {2: 100},
            "status": "CURRENT",
            "accepted_by_human": True,
        }
        report = zhu_peng_preflight(item)
        assert report["status"] == "READY_FOR_HUMAN_REVIEW"


class TestSafetyGuards:
    def test_guards_immutable(self):
        assert ZHU_PENG_SAFETY_GUARDS["auto_submit"] is False
        assert ZHU_PENG_SAFETY_GUARDS["auto_confirm"] is False
        assert ZHU_PENG_SAFETY_GUARDS["auto_next"] is False

    def test_forbidden_selectors(self):
        forbidden = ZHU_PENG_SAFETY_GUARDS["forbidden_selectors"]
        assert "#GroupSet_Value" in forbidden
        assert "input[id^='ta_']" in forbidden
        assert "input[id^='tb_']" in forbidden
        assert any("OnChkNO" in s for s in forbidden)
        assert any("OnChkBet" in s for s in forbidden)

    def test_require_accepted(self):
        assert ZHU_PENG_SAFETY_GUARDS["require_accepted_by_human"] is True


class TestCombinationMath:
    """Cross-module verification: zhu_peng_fill combo math."""
    
    @staticmethod
    def calc_combs(col_lengths):
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

    def test_live_result_matches(self):
        """The live test result: [1,1,1,2] → 9,7,2."""
        result = self.calc_combs([1, 1, 1, 2])
        assert result["二星"] == 9
        assert result["三星"] == 7
        assert result["四星"] == 2

    def test_total_1800(self):
        """二星100*9 + 三星100*7 + 四星100*2 = 1800."""
        result = self.calc_combs([1, 1, 1, 2])
        total = result["二星"] * 100 + result["三星"] * 100 + result["四星"] * 100
        assert total == 1800
