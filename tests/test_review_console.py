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
    queue = run_current_mock_queue_item(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50"))

    model = build_review_console_model(queue)

    assert model["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert model["queue_view"]["last_mock_result"]["selected_numbers"] == ["06", "13", "23", "22"]
    assert model["queue_view"]["last_mock_result"]["danger_buttons_clicked"] == []


def test_review_console_html_contains_sections_and_no_live_selector() -> None:
    queue = build_batch_mock_queue(f"\u4e0a\u534812:04\n06.13.23.22 {TWO_THREE}50..40{CAR}10{YUAN}")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "Valid Candidates" in html
    assert "Needs Review / Invalid" in html
    assert "Ignored Metadata" in html
    assert "No live site operation" in html
    assert "real_site_operation=false" in html
    assert "auto_submit=false" in html
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
        run_current_mock_queue_item(build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50")),
        queue_path="queue_state.json",
    )

    assert any("--batch-review-accept-valid" in action for action in needs_review["actions"])
    assert any("--batch-review-reject" in action for action in needs_review["actions"])
    assert any("--batch-audit-export" in action for action in needs_review["actions"])
    assert any("--batch-mock-next" in action for action in waiting["actions"])


def test_review_console_model_shows_clear_missing_money_wording() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")

    model = build_review_console_model(queue)

    assert model["status"] == NEEDS_REVIEW
    fragment = model["invalid_fragments"][0]
    assert fragment["original_fragment"] == "10.25"
    assert fragment["numbers"] == [10, 25]
    assert fragment["stars"] == [2]
    assert fragment["parsed_summary"]
    assert fragment["is_missing_money"] is True
    assert "missing money" in fragment["reason"]
    assert "10.25" not in [item["original_fragment"] for item in model["valid_candidates"]]


def test_review_console_html_shows_clear_missing_money_wording_not_as_valid() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")

    html = render_review_console_html(queue, queue_path="queue_state.json")

    assert "missing money" in html
    assert "缺金額" in html
    assert "Needs Review: missing money" in html
    valid_section = html.split("Needs Review / Invalid")[0]
    assert "10.25" not in valid_section


def test_write_review_console_html_and_cli_command(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "queue_state.json"
    html_path = tmp_path / "review.html"
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50..32{CAR}10{YUAN}")
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    model = write_review_console_html(queue, html_path, queue_path=str(queue_path))
    assert model["status"] == READY_FOR_QUEUE
    assert "Betguard Local Review Console" in html_path.read_text(encoding="utf-8")

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

    assert "Betguard Local Review Console" in cli_html_path.read_text(encoding="utf-8")
