from __future__ import annotations

import json
import sys

from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_mock_queue import (
    NEEDS_REVIEW,
    READY_FOR_QUEUE,
    WAITING_FOR_HUMAN_CONFIRM,
    accept_valid_candidates_for_mock_queue,
    build_batch_mock_queue,
    run_current_mock_queue_item,
)
from betguard.webfill.review_console import (
    build_review_console_model,
    render_review_console_html,
    write_review_console_html,
)


TWO_THREE = "\u4e8c\u4e09"
CAR = "\u8eca"
YUAN = "\u5143"


def test_review_console_model_from_all_valid_batch_is_ready() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..32{CAR}10{YUAN}")

    model = build_review_console_model(queue, queue_path="queue_state.json")

    assert model["status"] == READY_FOR_QUEUE
    assert model["preprocessing"]["status"] == "READY"
    assert model["preprocessing"]["valid_count"] == 2
    assert model["valid_candidates"][0]["original_fragment"] == f"06.13.23.22 {TWO_THREE}50"
    assert model["safety"]["real_site_operation"] is False
    assert model["safety"]["auto_submit"] is False
    assert model["safety"]["danger_buttons_clicked"] == []


def test_review_console_model_from_mixed_batch_shows_needs_review_sections() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")

    model = build_review_console_model(queue)

    assert model["status"] == NEEDS_REVIEW
    assert model["preprocessing"]["status"] == "NEEDS_REVIEW"
    assert [item["original_fragment"] for item in model["valid_candidates"]] == [
        f"06.13.23.22 {TWO_THREE}50",
        f"32{CAR}10{YUAN}",
    ]
    assert model["invalid_fragments"][0]["original_fragment"] == f"40{CAR}10{YUAN}"
    assert "number out of range 40" in model["invalid_fragments"][0]["reason"]


def test_review_console_after_accept_valid_has_waiting_queue_view() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}..32{CAR}10{YUAN}")
    accepted = accept_valid_candidates_for_mock_queue(queue, run_first=True)

    model = build_review_console_model(accepted)

    assert model["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert model["queue_view"]["current_item"]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert model["queue_view"]["current_item"]["original_fragment"] == f"06.13.23.22 {TWO_THREE}50"
    assert model["audit"]["invalid_fragments_count"] == 1


def test_review_console_after_mock_next_shows_last_mock_result_waiting() -> None:
    queue = run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50")))

    model = build_review_console_model(queue)

    assert model["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert model["queue_view"]["last_mock_result"]["selected_numbers"] == ["06", "13", "23", "22"]
    assert model["queue_view"]["last_mock_result"]["danger_buttons_clicked"] == []


def test_review_console_html_contains_sections_and_no_live_selector() -> None:
    queue = build_batch_mock_queue(f"\u4e0a\u534812:04\n06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "正確候選" in html
    assert "需人工確認" in html
    assert "審核狀態" in html
    assert "未連真網站" in html
    assert "未連真網站" in html
    assert "未送出" in html
    assert "live selector" not in html.lower()
    assert "selector_report" not in html


def test_review_console_html_shows_attached_star_single_digit_as_unit() -> None:
    queue = build_batch_mock_queue(f"04.32.33{TWO_THREE}2")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "200" in html
    assert "0.02" not in html


def test_review_console_html_shows_confirmed_shorthand_amount_not_literal_code() -> None:
    queue = build_batch_mock_queue("08 28 33 39 440")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "50" in html
    assert "440" not in queue["items"][0]["parsed_summary"]
    assert "4.4" not in html


def test_review_console_html_shows_three_number_640_as_two_units_not_literal_code() -> None:
    queue = build_batch_mock_queue("11 22 33 640")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "200" in html
    assert "640" not in queue["items"][0]["parsed_summary"]
    assert "6.4" not in html


def test_review_console_actions_include_accept_reject_mock_next_and_audit_export() -> None:
    needs_review = build_review_console_model(
        build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}"),
        queue_path="queue_state.json",
    )
    waiting = build_review_console_model(
        run_current_mock_queue_item(accept_valid_candidates_for_mock_queue(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50"))),
        queue_path="queue_state.json",
    )

    assert any("--batch-review-accept-valid" in action for action in needs_review["actions"])
    assert any("--batch-review-reject" in action for action in needs_review["actions"])
    assert any("--batch-audit-export" in action for action in needs_review["actions"])
    assert any("--batch-mock-next" in action for action in waiting["actions"])


def test_review_console_model_puts_missing_money_in_watchlist() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")

    model = build_review_console_model(queue)

    assert model["status"] == NEEDS_REVIEW
    assert model["preprocessing"]["watchlist_count"] == 1
    fragment = model["watchlist"][0]
    assert fragment["original_fragment"] == "10.25"
    assert fragment["numbers"] == [10, 25]
    assert fragment["stars"] == [2]
    assert fragment["parsed_summary"]
    assert fragment["is_missing_money"] is True
    assert fragment["accepted_automatically"] is False
    assert "缺金額" in fragment["reason"]
    assert "10.25" not in [item["original_fragment"] for item in model["valid_candidates"]]
    assert "10.25" not in [item["original_fragment"] for item in model["invalid_fragments"]]


def test_review_console_html_shows_watchlist_wording_not_as_valid() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "待觀察" in html
    assert "缺金額" in html
    assert "10.25" in html
    valid_section = html.split("需要人工確認")[0]
    assert "10.25" not in valid_section


def test_write_review_console_html_and_cli_command(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    html_path = tmp_path / "review.html"
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..32{CAR}10{YUAN}")
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    model = write_review_console_html(queue, html_path, queue_path=str(queue_path))
    assert model["status"] == READY_FOR_QUEUE
    assert "Betguard 本地審核台" in html_path.read_text(encoding="utf-8")

    cli_html_path = tmp_path / "review_cli.html"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--review-report-html",
            "--queue",
            str(queue_path),
            "--out",
            str(cli_html_path),
            "--pretty",
        ],
    )
    webfill_cli.main()

    assert "Betguard 本地審核台" in cli_html_path.read_text(encoding="utf-8")


def test_error_status_item_stays_in_needs_review_not_watchlist() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n11.28.400")

    model = build_review_console_model(queue)

    invalid_raws = [item["original_fragment"] for item in model["invalid_fragments"]]
    watchlist_raws = [item["original_fragment"] for item in model["watchlist"]]
    assert "11.28.400" in invalid_raws
    assert "11.28.400" not in watchlist_raws


def test_watchlist_original_fragment_appears_in_html_but_not_in_valid_section() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25\n11.28.400")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "待觀察" in html
    assert "10.25" in html
    assert "不會自動接受" in html
    valid_section = html.split("待觀察</span> 待觀察")[0] if "待觀察</span> 待觀察" in html else html
    assert "10.25" not in valid_section


def test_watchlist_items_never_enter_approved_fill_queue() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25\n11.28.400")
    model = build_review_console_model(queue)
    watchlist_raws = [item["original_fragment"] for item in model["watchlist"]]
    assert "10.25" in watchlist_raws

    assert queue["status"] == NEEDS_REVIEW
    accepted = accept_valid_candidates_for_mock_queue(queue)

    accepted_originals = [item.get("original") for item in accepted["items"]]
    approved = accepted.get("approved_fill_queue") or []
    approved_fragments = [entry.get("original_fragment") for entry in approved]
    for raw in watchlist_raws:
        assert raw not in accepted_originals
        assert raw not in approved_fragments
    assert accepted["audit"]["safety"]["real_site_operation"] is False
    assert accepted["audit"]["safety"]["auto_submit"] is False


def test_valid_candidate_behavior_unchanged_with_watchlist_present() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")

    model = build_review_console_model(queue)

    valid_raws = [item["original_fragment"] for item in model["valid_candidates"]]
    assert valid_raws == [f"06.13.23.22 {TWO_THREE}50"]
