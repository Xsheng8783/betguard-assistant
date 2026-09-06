"""Synthetic scope regressions; no customer slip text or OCR accuracy claims."""
import pytest

from betguard.models import BetReport
from betguard.parser import ParseError, parse_line
from betguard.validator import validate_bet
from betguard.vision.service import preflight_image_text
from betguard.webfill.assisted_fill_mock import build_mock_fill_report


def checked(text, game="539"):
    bet = parse_line(text, default_game=game)
    report = BetReport(bet, validate_bet(bet)).to_dict()
    assert report["status"] == "ok", report
    return report


@pytest.mark.parametrize("operator", ["x", "X", "×", "*"])
@pytest.mark.parametrize("prefix,kind,groups", [
    ("11 14 35 38", "normal", [11, 14, 35, 38]),
    ("11 × 14 × 32 × 07 21", "column", [[11], [14], [32], [7, 21]]),
    ("01 × 02 12 × 03 13 23", "column", [[1], [2, 12], [3, 13, 23]]),
])
def test_inline_multiplier_scope_in_direct_parser_and_image_preflight(operator, prefix, kind, groups):
    text = f"{prefix} 2{operator}3 3{operator}1"
    result = checked(text)
    assert result["type"] == kind
    assert result["numbers" if kind == "normal" else "columns"] == groups
    assert result["bets"] == {"2": {"unit": 3, "money": 300}, "3": {"unit": 1, "money": 100}}
    preview = preflight_image_text(text, game="539")
    assert preview["all_parseable"]
    assert preview["parsed_bet_count"] == 1
    actual = preview["parser_normalized_result"]["bets"][0]
    assert actual["result"]["bets"] == result["bets"]
    assert actual["original_lines"] == [text]
    assert actual["result"]["numbers" if kind == "normal" else "columns"] == groups
    mock = build_mock_fill_report(text, game="539")
    assert mock["status"] == "COMPLETED_MOCK_ONLY"
    assert mock["filled_amounts"] == {"二星": 300, "三星": 100}
    assert mock["danger_buttons_clicked"] == []


@pytest.mark.parametrize("star_text,stars", [("2/3", [2, 3]), ("2/3/4", [2, 3, 4]), ("3/4", [3, 4])])
def test_slash_categories_never_enter_numbers(star_text, stars):
    text = f"01 06 11 14 32 {star_text}×1"
    for result in [checked(text), preflight_image_text(text, game="539")["parser_normalized_result"]["bets"][0]["result"]]:
        assert result["type"] == "normal"
        assert result["numbers"] == [1, 6, 11, 14, 32]
        assert result["stars"] == stars
    assert preflight_image_text(text, game="539")["all_parseable"]


@pytest.mark.parametrize("text,rules", [
    ("01 06 11 14 32 2×5 3×0.5", {"2": {"unit": 5, "money": 500}, "3": {"unit": 0.5, "money": 50}}),
    ("01 06 11 14 32 2,3×0.1 4×5", {"2": {"unit": 0.1, "money": 10}, "3": {"unit": 0.1, "money": 10}, "4": {"unit": 5, "money": 500}}),
    ("01 06 11 14 32 2×50元 3×0.5支", {"2": {"money": 50}, "3": {"unit": 0.5, "money": 50}}),
])
def test_explicit_rule_amounts_keep_units_and_decimals(text, rules):
    result = checked(text)
    assert result["numbers"] == [1, 6, 11, 14, 32]
    assert result["bets"] == rules
    assert preflight_image_text(text, game="539")["all_parseable"]


@pytest.mark.parametrize("text", [
    "01 06 11 14 32 2×3 2×1", "01 06 11 14 32 2/2×1",
    "01 06 11 14 32 2×3 3×?", "01 06 11 14 32 2×3 3×",
    "11 × 14 × 32 × 2x3 3x1", "01 06 11 14 32 2/5×1",
    "01 06 11 14 32 2×0 3×1", "01 06 11 14 32 2×3 5×1",
    "01 06 11 14 32 2×3 3×1 ?", "01 06 11 14 32 2×3 3×1 (cancelled)",
])
def test_ambiguous_conflicting_or_incomplete_rules_fail_closed(text):
    try:
        result = BetReport(parse_line(text), validate_bet(parse_line(text))).to_dict()
    except ParseError:
        pass
    else:
        assert result["status"] != "ok", result
    assert not preflight_image_text(text, game="539")["all_parseable"]


@pytest.mark.parametrize("game,number,accepted", [
    ("539", 39, True), ("539", 40, False), ("539", 49, False),
    ("六合", 39, True), ("六合", 40, True), ("六合", 49, True),
])
def test_game_boundaries_survive_inline_rules(game, number, accepted):
    assert preflight_image_text(f"01 12 {number} 2×3 3×1", game=game)["all_parseable"] is accepted


@pytest.mark.parametrize("text", ["14 32 各半車", "01 14 32 各 半車", "14,32 各半車"])
def test_each_half_car_expands_with_original_scope_and_units(text):
    preview = preflight_image_text(text, game="539")
    assert preview["all_parseable"]
    expected = [1, 14, 32] if text.startswith("01") else [14, 32]
    candidates = preview["parser_normalized_result"]["bets"]
    assert [c["result"]["number"] for c in candidates] == expected
    for candidate in candidates:
        assert candidate["original_lines"] == [text]
        assert candidate["result"]["car_units"] == 0.5
        assert candidate["result"]["money"] == 50


@pytest.mark.parametrize("text", ["14 32 半車", "14 14 各半車", "14 ? 各半車", "14 40 各半車", "14 32 各?車", "? 14 32 各半車"])
def test_half_car_does_not_guess_scope_or_numbers(text):
    assert not preflight_image_text(text, game="539")["all_parseable"]


def test_existing_single_car_and_real_02_stay_unchanged():
    assert checked("03×5")["car_units"] == 5
    assert checked("01 02 05 2×0.5")["numbers"] == [1, 2, 5]


def test_model_error_is_not_repaired_by_parser_and_blocks_image_handoff():
    raw = "14 32 各半車\n\n06 11 14 32 2/3x1\n\n11 07 x 14 21 x 32 x 2x3 3x1\n\n11 14 35 38 2x3 3x1"
    preview = preflight_image_text(raw, game="539")
    assert not preview["all_parseable"]
    assert preview["unresolved_count"] == 1
    assert preview["issues"][0]["raw"] == "11 07 x 14 21 x 32 x 2x3 3x1"
    numbers = preview["parser_normalized_result"]["bets"][2]["result"]["numbers"]
    assert numbers == [6, 11, 14, 32]  # Do not invent the model's missing 01.
    assert preview["text_mutated"] is False
    assert preview["auto_apply"] is preview["auto_submit"] is False


@pytest.mark.parametrize("categories", ["2,3", "2/3", "2 3", "23"])
def test_grouped_multi_rules_are_idempotent_and_keep_real_02(categories):
    from betguard.normalizer import normalize_for_parser
    source = f"01 02 05 18 29 {categories}×0.1 4×5"
    first = normalize_for_parser(source)
    second = normalize_for_parser(first.normalized_text)
    assert first.original_text == source
    assert second.normalized_text == first.normalized_text
    report = checked(source)
    assert report["numbers"] == [1, 2, 5, 18, 29]
    assert report["bets"] == {"2": {"unit": 0.1, "money": 10}, "3": {"unit": 0.1, "money": 10}, "4": {"unit": 5, "money": 500}}


def test_explicit_draft_keeps_physical_scope_and_all_multiplier_values():
    draft = "14 32 各半車\n\n01 06 11 14 32 2/3×1\n\n11 × 14 × 32 × 07 21 2×3 3×1\n\n11 14 35 38 2×3 3×1"
    preview = preflight_image_text(draft, game="539")
    assert preview["all_parseable"]
    assert preview["parsed_bet_count"] == 5  # 4 physical scopes, one each-car scope expands to two.
    candidates = preview["parser_normalized_result"]["bets"]
    assert candidates[0]["original_lines"] == candidates[1]["original_lines"] == ["14 32 各半車"]
    assert candidates[3]["result"]["columns"] == [[11], [14], [32], [7, 21]]
    for candidate in candidates[3:]:
        assert candidate["result"]["bets"] == {"2": {"unit": 3, "money": 300}, "3": {"unit": 1, "money": 100}}


@pytest.mark.parametrize("suffix", ["2 × 1", "3 × 0.2", "4 × 0.5", "2×3 3×1", "2/3×0.1 4×5"])
def test_real_two_digit_column_members_are_not_mistaken_for_rule_codes(suffix):
    text = f"19 35 × 23 × 34 × 38 {suffix}"
    report = checked(text)
    assert report["columns"] == [[19, 35], [23], [34], [38]]
    preview = preflight_image_text(text, game="539")
    assert preview["all_parseable"]
    assert preview["parser_normalized_result"]["bets"][0]["result"]["columns"] == report["columns"]


@pytest.mark.parametrize("member", [22, 23, 24, 32, 33, 34, 42, 43, 44])
@pytest.mark.parametrize("suffix", ["2×1", "2×3 3×1", "2/3×0.5"])
def test_two_digit_column_members_keep_numeric_role(member, suffix):
    game = "539" if member <= 39 else "六合"
    text = f"01 15 × {member} × 37 {suffix}"
    expected = [[1, 15], [member], [37]]
    assert checked(text, game=game)["columns"] == expected
    preview = preflight_image_text(text, game=game)
    assert preview["all_parseable"]
    assert preview["parser_normalized_result"]["bets"][0]["result"]["columns"] == expected


@pytest.mark.parametrize("star", [2, 3, 4])
def test_orphan_single_category_is_not_a_car_number(star):
    orphan = f"{star}×1"
    # Do not change the legacy pasted-text parser's legal car shorthand.
    assert checked(orphan)["type"] == "car"
    preview = preflight_image_text(orphan, game="539")
    assert not preview["all_parseable"]
    assert preview["issues"][0]["code"] == "IMAGE_SINGLE_DIGIT_ROLE_AMBIGUOUS"
    assert preview["issues"][0]["raw"] == orphan
    assert not preflight_image_text("01 12 14 32 2×3\n" + orphan, game="539")["all_parseable"]
    # Explicit car wording or a zero-padded number removes this role ambiguity.
    for explicit in [f"0{star}×1", f"{star}車1支"]:
        assert checked(explicit)["number"] == star
        assert checked(explicit)["car_units"] == 1
        assert preflight_image_text(explicit, game="539")["all_parseable"]
