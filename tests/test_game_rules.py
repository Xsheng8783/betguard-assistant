import json

from betguard.cli import main as cli_main
from betguard.game_rules import GAME_RULES
from betguard.review import build_report


TWO = "\u4e8c"
TIANTIAN = "\u5929\u5929\u6a02"
DALETOU = "\u5927\u6a02"
HK = "\u516d\u5408"


def report_for(text: str, game: str) -> dict:
    return build_report(text, game=game).to_dict()


def test_game_rules_metadata() -> None:
    assert GAME_RULES["539"].game_id == 13
    assert GAME_RULES[TIANTIAN].game_id == 22
    assert GAME_RULES[DALETOU].number_max == 49
    assert GAME_RULES[HK].number_max == 49


def test_539_blocks_40() -> None:
    report = report_for(f"40.12 {TWO}100", "539")

    assert report["game"] == "539"
    assert report["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in report["errors"]


def test_daletou_allows_40() -> None:
    report = report_for(f"40.12 {TWO}100", DALETOU)

    assert report["game"] == DALETOU
    assert report["status"] == "ok"
    assert report["numbers"] == [40, 12]


def test_daletou_blocks_50() -> None:
    report = report_for(f"50.12 {TWO}100", DALETOU)

    assert report["status"] == "error"
    assert "number out of range 50; valid range is 1-49" in report["errors"]


def test_tiantian_blocks_40() -> None:
    report = report_for(f"40.12 {TWO}100", TIANTIAN)

    assert report["game"] == TIANTIAN
    assert report["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in report["errors"]


def test_hk_allows_49() -> None:
    report = report_for(f"49.12 {TWO}100", HK)

    assert report["game"] == HK
    assert report["status"] == "ok"
    assert report["numbers"] == [49, 12]


def test_hk_blocks_50() -> None:
    report = report_for(f"50.12 {TWO}100", HK)

    assert report["status"] == "error"
    assert "number out of range 50; valid range is 1-49" in report["errors"]


def test_539_single_40_reports_range_and_star_count() -> None:
    report = report_for(f"40 {TWO}100", "539")

    assert report["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in report["errors"]
    assert "\u4e8c\u661f requires at least 2 numbers" in report["errors"]


def test_daletou_single_40_reports_star_count_not_range() -> None:
    report = report_for(f"40 {TWO}100", DALETOU)

    assert report["status"] == "error"
    assert "\u4e8c\u661f requires at least 2 numbers" in report["errors"]
    assert all("number out of range 40" not in error for error in report["errors"])


def test_daletou_two_numbers_with_40_is_ok() -> None:
    report = report_for(f"40.41 {TWO}100", DALETOU)

    assert report["status"] == "ok"
    assert report["numbers"] == [40, 41]


def test_daletou_50_reports_valid_range() -> None:
    report = report_for(f"50.41 {TWO}100", DALETOU)

    assert report["status"] == "error"
    assert "number out of range 50; valid range is 1-49" in report["errors"]


def test_cli_game_switches_number_range(capsys) -> None:
    exit_code = cli_main(["--game", DALETOU, f"40.12 {TWO}100"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["game"] == DALETOU
    assert output["status"] == "ok"
