from pathlib import Path

from betguard.webfill.dry_run_pipeline import (
    build_dry_run_pipeline_from_file,
    build_dry_run_pipeline_from_text,
    format_pretty_pipeline_report,
)


def candidate(label: str) -> dict:
    return {
        "tag": "button",
        "text": label,
        "frame_name": "mainFrame",
        "candidate_selectors": [f"text={label}"],
    }


def full_selector_report() -> dict:
    return {
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
        "number_candidates": {
            "06": [candidate("06")],
            "13": [candidate("13")],
            "23": [candidate("23")],
            "22": [candidate("22")],
        },
        "amount_field_candidates": {
            "二星": [candidate("二星")],
            "三星": [candidate("三星")],
            "四星": [candidate("四星")],
        },
        "danger_candidates": [candidate("送出注單")],
    }


def test_single_ok_pipeline_is_safe_and_never_executable() -> None:
    report = build_dry_run_pipeline_from_text("06-13-23-22/50", full_selector_report())

    assert report["mode"] == "assisted_fill_dry_run_pipeline"
    assert report["summary"] == {
        "total": 1,
        "safe": 1,
        "blocked": 0,
        "executable": False,
    }
    assert report["items"][0]["status"] == "SAFE"
    assert report["final_decision"]["executable"] is False


def test_parser_duplicate_error_blocks_pipeline_item() -> None:
    report = build_dry_run_pipeline_from_text("13.13 二100", full_selector_report())

    assert report["summary"]["safe"] == 0
    assert report["summary"]["blocked"] == 1
    assert report["items"][0]["status"] == "BLOCKED"
    assert "duplicate number 13" in report["items"][0]["reason"]


def test_missing_number_selector_blocks_pipeline_item() -> None:
    selector_report = full_selector_report()
    selector_report["number_candidates"].pop("06")

    report = build_dry_run_pipeline_from_text("06-13-23-22/50", selector_report)

    assert report["items"][0]["status"] == "BLOCKED"
    assert "number 06 selector missing" in report["items"][0]["reason"]


def test_empty_danger_candidates_blocks_pipeline_item() -> None:
    selector_report = full_selector_report()
    selector_report["danger_candidates"] = []

    report = build_dry_run_pipeline_from_text("06-13-23-22/50", selector_report)

    assert report["items"][0]["status"] == "BLOCKED"
    assert "danger buttons not verified" in report["items"][0]["reason"]


def test_file_pipeline_supports_multiple_items(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("06-13-23-22/50\n13.13 二100\n", encoding="utf-8")

    report = build_dry_run_pipeline_from_file(input_file, full_selector_report())

    assert report["summary"]["total"] == 2
    assert report["summary"]["safe"] == 1
    assert report["summary"]["blocked"] == 1
    assert [item["status"] for item in report["items"]] == ["SAFE", "BLOCKED"]


def test_pipeline_final_executable_is_always_false() -> None:
    report = build_dry_run_pipeline_from_text("06-13-23-22/50", full_selector_report())

    assert report["summary"]["executable"] is False
    assert report["final_decision"]["executable"] is False
    assert all(item["final_decision"]["executable"] is False for item in report["items"])


def test_pretty_pipeline_report_contains_summary_and_items() -> None:
    report = build_dry_run_pipeline_from_text("06-13-23-22/50", full_selector_report())

    pretty = format_pretty_pipeline_report(report)

    assert "Assisted Fill Dry-run Pipeline" in pretty
    assert "Summary:" in pretty
    assert "[1] SAFE" in pretty
    assert "final: executable=false" in pretty
