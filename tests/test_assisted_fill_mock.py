from pathlib import Path

from betguard.webfill.assisted_fill_mock import build_mock_fill_report, format_pretty_mock_report
from betguard.webfill.mock_page import build_mock_page_html


TWO_THREE = "\u4e8c\u4e09"
TWO_THREE_FOUR = "\u4e8c\u4e09\u56db"


def test_normal_mock_fill_selects_numbers_and_fills_two_three_only() -> None:
    report = build_mock_fill_report(f"06.13.23.22 {TWO_THREE}50")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["selected_numbers"] == ["06", "13", "23", "22"]
    assert report["filled_amounts"] == {"二星": 50, "三星": 50}
    assert report["skipped"] == ["四星"]
    assert report["danger_buttons_detected"] == ["送出注單", "確認"]
    assert report["danger_buttons_clicked"] == []
    assert report["final_decision"]["real_site_executable"] is False


def test_four_star_mock_fill_fills_all_three_amount_fields() -> None:
    report = build_mock_fill_report(f"06.13.23.22 {TWO_THREE_FOUR}100")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["filled_amounts"] == {
        "二星": 100,
        "三星": 100,
        "四星": 100,
    }
    assert report["skipped"] == []


def test_duplicate_number_blocks_before_mock_fill() -> None:
    report = build_mock_fill_report(f"13.13 {TWO_THREE}100")

    assert report["status"] == "BLOCKED"
    assert report["selected_numbers"] == []
    assert report["filled_amounts"] == {}
    assert "duplicate number 13" in report["errors"][0]


def test_danger_buttons_clicked_is_always_empty() -> None:
    report = build_mock_fill_report(f"06.13.23.22 {TWO_THREE}50")

    assert report["danger_buttons_clicked"] == []


def test_mock_page_contains_required_controls() -> None:
    html = build_mock_page_html()

    assert "539 二三四星 連碰 Mock Page" in html
    assert 'data-number="01"' in html
    assert 'data-number="39"' in html
    assert 'data-amount-field="二星"' in html
    assert 'data-amount-field="三星"' in html
    assert 'data-amount-field="四星"' in html
    assert 'data-danger="true">送出注單' in html
    assert 'data-danger="true">確認' in html


def test_mock_code_does_not_click_danger_selector() -> None:
    source = Path("src/betguard/webfill/assisted_fill_mock.py").read_text(encoding="utf-8")
    click_lines = [line for line in source.splitlines() if ".click(" in line]

    assert click_lines
    assert all("data-danger" not in line for line in click_lines)


def test_pretty_mock_report_contains_expected_sections() -> None:
    report = build_mock_fill_report(f"06.13.23.22 {TWO_THREE}50")

    pretty = format_pretty_mock_report(report)

    assert "Assisted Fill Mock Report" in pretty
    assert "Status: COMPLETED_MOCK_ONLY" in pretty
    assert "- 06 selected" in pretty
    assert "- 二星: 50" in pretty
    assert "- 送出注單 detected, not clicked" in pretty
    assert "- real_site_executable: false" in pretty
