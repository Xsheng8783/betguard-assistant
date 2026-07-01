import json
import sys

from betguard.webfill import cli as webfill_cli
from betguard.webfill.assisted_fill_real import (
    SAFETY_LOCK_MESSAGE,
    build_acceptance_report,
    build_assist_fill_execution_plan,
    format_execution_safety_statement,
    format_pretty_assist_fill_plan,
    is_safe_execution_action,
)


TWO_THREE = "\u4e8c\u4e09"


def candidate(selector: str, text: str = "") -> dict:
    return {
        "tag": "button",
        "text": text,
        "frame_name": "mainFrame",
        "candidate_selectors": [selector],
    }


def full_selector_report() -> dict:
    return {
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "\u4e8c\u4e09\u56db\u661f",
            "games": {
                "539": {"game_id": 13, "is_open": True},
            },
        },
        "route_probe": {
            "built_url": "http://w1.gts362.com/token/Front/B/B03",
        },
        "number_candidates": {
            "06": [candidate('button[data-number="06"]', "06")],
            "13": [candidate('button[data-number="13"]', "13")],
            "23": [candidate('button[data-number="23"]', "23")],
            "22": [candidate('button[data-number="22"]', "22")],
        },
        "amount_field_candidates": {
            "\u4e8c\u661f": [candidate('input[data-amount-field="二星"]', "\u4e8c\u661f")],
            "\u4e09\u661f": [candidate('input[data-amount-field="三星"]', "\u4e09\u661f")],
            "\u56db\u661f": [candidate('input[data-amount-field="四星"]', "\u56db\u661f")],
        },
        "danger_candidates": [
            candidate('button[data-danger="true"]', "\u9001\u51fa\u6ce8\u55ae"),
            candidate('button[data-danger="true"]', "\u78ba\u8a8d"),
        ],
    }


def write_selector_report(tmp_path) -> str:
    path = tmp_path / "selector_report.json"
    path.write_text(json.dumps(full_selector_report(), ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_safe_mapping_can_enter_assist_fill_ready_state() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    assert plan["status"] == "READY"
    assert [action["type"] for action in plan["execution_plan"]] == [
        "select_number",
        "select_number",
        "select_number",
        "select_number",
        "set_amount",
        "set_amount",
    ]
    assert plan["target_url"] == "http://w1.gts362.com/token/Front/B/B03"


def test_blocked_mapping_cannot_execute() -> None:
    selector_report = full_selector_report()
    selector_report["number_candidates"].pop("06")

    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", selector_report)

    assert plan["status"] == "BLOCKED"
    assert plan["execution_plan"] == []
    assert any("number 06 selector missing" in error for error in plan["errors"])


def test_market_closed_blocks_assist_fill() -> None:
    selector_report = full_selector_report()
    selector_report["market_state"]["games"]["539"]["is_open"] = False

    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", selector_report)

    assert plan["status"] == "BLOCKED"
    assert plan["execution_plan"] == []
    assert "market closed for 539" in plan["errors"]


def test_unknown_market_state_blocks_assist_fill() -> None:
    selector_report = full_selector_report()
    selector_report["market_state"].pop("games")

    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", selector_report)

    assert plan["status"] == "BLOCKED"
    assert plan["execution_plan"] == []
    assert "market state unknown for 539" in plan["errors"]


def test_missing_page_route_blocks_assist_fill() -> None:
    selector_report = full_selector_report()
    selector_report["route_probe"] = {
        "label": "\u4e8c\u4e09\u56db\u661f",
        "status": "route_not_found",
    }

    plan = build_assist_fill_execution_plan(
        f"06.13.23.22 {TWO_THREE}50",
        selector_report,
        page="\u4e8c\u4e09\u56db\u661f",
    )

    assert plan["status"] == "BLOCKED"
    assert plan["execution_plan"] == []
    assert "route not found for page: \u4e8c\u4e09\u56db\u661f" in plan["errors"]


def test_danger_selector_is_never_used_for_click() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    selectors = [action["selector"] for action in plan["execution_plan"]]
    assert all("data-danger" not in selector for selector in selectors)
    assert all("\u9001\u51fa" not in selector for selector in selectors)
    assert plan["danger_buttons_clicked"] == []


def test_danger_words_are_not_safe_click_targets() -> None:
    for word in ["\u9001\u51fa", "\u78ba\u8a8d", "\u78ba\u5b9a", "\u4e0b\u6ce8", "\u52a0\u5165\u6ce8\u55ae", "\u9001\u51fa\u6ce8\u55ae"]:
        action = {
            "type": "select_number",
            "label": "06",
            "selector": f"text={word}",
            "candidate": {
                "text": word,
                "candidate_selectors": [f"text={word}"],
            },
        }

        assert is_safe_execution_action(action) is False


def test_four_star_without_amount_is_skipped() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    assert plan["skipped"] == ["四星"]
    amount_stars = [action.get("star") for action in plan["execution_plan"] if action["type"] == "set_amount"]
    assert "四星" not in amount_stars


def test_final_decision_keeps_human_required_and_real_site_not_executable() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    assert plan["final_decision"]["real_site_executable"] is False
    assert plan["final_decision"]["human_required"] is True


def test_final_submit_is_never_in_execution_plan() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    action_types = [action["type"] for action in plan["execution_plan"]]
    assert "submit" not in action_types
    assert "confirm" not in action_types
    assert "send_bet" not in action_types
    assert plan["final_decision"]["submit_executed"] is False


def test_cli_without_human_final_confirm_flag_stops(capsys, monkeypatch, tmp_path) -> None:
    selector_path = write_selector_report(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--assist-fill",
            "--text",
            f"06.13.23.22 {TWO_THREE}50",
            "--selector-report",
            selector_path,
        ],
    )

    webfill_cli.main()

    output = capsys.readouterr().out
    assert SAFETY_LOCK_MESSAGE in output
    assert "Execution Plan" not in output


def test_cli_with_human_final_confirm_and_safe_mapping_reaches_safety_statement(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    selector_path = write_selector_report(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--assist-fill",
            "--text",
            f"06.13.23.22 {TWO_THREE}50",
            "--selector-report",
            selector_path,
            "--i-understand-human-final-confirm",
            "--pretty",
        ],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "NO")

    webfill_cli.main()

    output = capsys.readouterr().out
    assert "Status: READY" in output
    assert "\u5b89\u5168\u8072\u660e" in output
    assert "\u7cfb\u7d71\u53ea\u6703\u9078\u865f\u8207\u586b\u91d1\u984d" in output
    assert "\u9001\u51fa\u6ce8\u55ae" in output
    assert "\u78ba\u8a8d" in output


def test_acceptance_report_marks_submit_and_confirm_not_clicked() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())
    report = build_acceptance_report(plan, plan["execution_plan"])

    assert report["status"] == "completed_before_human_confirm"
    assert report["submit_clicked"] is False
    assert report["confirm_clicked"] is False
    assert report["human_required"] is True
    assert report["final_decision"]["submit_clicked"] is False
    assert report["final_decision"]["confirm_clicked"] is False


def test_acceptance_report_skips_four_star() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())
    report = build_acceptance_report(plan, plan["execution_plan"])

    assert report["selected_numbers"] == ["06", "13", "23", "22"]
    assert report["filled_amounts"] == {"二星": 50, "三星": 50}
    assert report["skipped"] == ["四星"]


def test_acceptance_checklist_contains_numbers_amounts_and_safety_items() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())
    report = build_acceptance_report(plan, plan["execution_plan"])

    checklist = report["acceptance_checklist"]
    assert "號碼 06 已選取" in checklist
    assert "號碼 13 已選取" in checklist
    assert "號碼 23 已選取" in checklist
    assert "號碼 22 已選取" in checklist
    assert "二星金額 50 正確" in checklist
    assert "三星金額 50 正確" in checklist
    assert "四星未填" in checklist
    assert "未自動按送出" in checklist
    assert "未自動按確認" in checklist


def test_acceptance_report_keeps_danger_buttons_not_clicked() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())
    report = build_acceptance_report(plan, plan["execution_plan"])

    assert report["danger_buttons_not_clicked"] is True
    assert report["danger_buttons_clicked"] == []


def test_execution_safety_statement_lists_danger_candidates_only() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())

    statement = format_execution_safety_statement(plan)

    assert "\u9001\u51fa\u6ce8\u55ae" in statement
    assert "\u78ba\u8a8d" in statement
    assert "data-danger" not in "\n".join(action["selector"] for action in plan["execution_plan"])
    assert plan["danger_buttons_clicked"] == []


def test_pretty_acceptance_report_contains_checklist() -> None:
    plan = build_assist_fill_execution_plan(f"06.13.23.22 {TWO_THREE}50", full_selector_report())
    report = build_acceptance_report(plan, plan["execution_plan"])

    pretty = format_pretty_assist_fill_plan(report)

    assert "Real-site Assisted Fill Completed" in pretty
    assert "[ ] 號碼 06 已選取" in pretty
    assert "[ ] 二星金額 50 正確" in pretty
    assert "[ ] 四星未填" in pretty
    assert "- submit_clicked: false" in pretty
    assert "- confirm_clicked: false" in pretty
