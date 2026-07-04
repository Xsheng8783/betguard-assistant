"""Execution Dry-run Verification v1.

Local-only, FakePage/FakeLocator proof that the full real-site execution
path (preflight -> build_execution_actions_from_preflight ->
execute_actions_on_page) behaves safely end to end. This never opens a
browser or touches a real website -- every check below runs against fake
page/locator objects, exactly like the rest of this test suite.

Each test below maps 1:1 to one of the required checks for this task; the
docstring on each test names which check it proves.
"""

from __future__ import annotations

import copy

import pytest

import betguard.webfill.real_site_assisted_fill as real_site_assisted_fill_module
from betguard.webfill.batch_queue import PENDING, WAITING_FOR_HUMAN_CONFIRM
from betguard.webfill.real_site_assisted_fill import (
    RISK_LOCK_MESSAGE,
    execute_actions_on_page,
    run_real_site_assisted_fill_with_page,
    validate_real_site_action,
)
from betguard.webfill.real_site_fill_preflight import (
    BLOCKED,
    READY_FOR_HUMAN_REVIEW,
    build_execution_actions_from_preflight,
    build_real_site_fill_preflight_report,
)
from tests.test_real_site_assisted_fill import (
    TWO_THREE,
    LocatorCallCountingPage,
    approved_queue_for,
    clean_profile,
    fake_page,
    queue_for,
)


ALLOWED_ACTION_TYPES = {"SELECT_NUMBER", "SET_AMOUNT"}
FORBIDDEN_ACTION_TYPES = {"submit", "confirm", "send_bet", "click_danger_button"}


def ready_preflight(text: str = f"06.13.23.22 {TWO_THREE}50") -> dict:
    report = build_real_site_fill_preflight_report(
        approved_queue_for(text), clean_profile(), item_index=0
    )
    assert report["status"] == READY_FOR_HUMAN_REVIEW
    return report


def test_check_1_ready_preflight_can_produce_execution_actions() -> None:
    """Check 1: READY preflight can produce execution actions."""
    report = ready_preflight()
    actions = build_execution_actions_from_preflight(report)
    assert actions
    assert len(actions) == 6  # 4 numbers + 2 amounts (二星/三星 only, per bet text)


def test_check_2_execution_actions_contain_only_allowed_types() -> None:
    """Check 2: execution actions only ever carry select_number/set_amount."""
    report = ready_preflight()
    actions = build_execution_actions_from_preflight(report)
    assert actions
    assert all(action["type"] in ALLOWED_ACTION_TYPES for action in actions)
    assert {action["type"] for action in actions} <= ALLOWED_ACTION_TYPES


def test_check_3_forbidden_action_types_can_never_execute() -> None:
    """Check 3: submit/confirm/send_bet/click_danger_button can never run.

    Proven at two independent layers:
    (a) the converter refuses to build an execution action for any of these
        step types at all (defense at the preflight->action boundary), and
    (b) even a hand-crafted action of one of these types is rejected by
        validate_real_site_action before execute_actions_on_page would ever
        act on it (defense at the execution boundary itself).
    """
    report = ready_preflight()
    for forbidden in FORBIDDEN_ACTION_TYPES:
        tampered = copy.deepcopy(report)
        tampered["amounts"][0]["type"] = forbidden
        with pytest.raises(ValueError, match=forbidden):
            build_execution_actions_from_preflight(tampered)

    for forbidden in FORBIDDEN_ACTION_TYPES:
        hand_crafted_action = {"type": forbidden, "selector": "text=送出注單"}
        error = validate_real_site_action(hand_crafted_action)
        assert error is not None, forbidden
        assert "unsupported action type" in error


def test_check_4_groupset_value_never_appears_in_selectors() -> None:
    """Check 4: #GroupSet_Value never survives into an execution action."""
    report = ready_preflight()
    tampered = copy.deepcopy(report)
    tampered["amounts"][0]["selector"] = "#GroupSet_Value"
    with pytest.raises(ValueError, match="GroupSet_Value"):
        build_execution_actions_from_preflight(tampered)

    clean_actions = build_execution_actions_from_preflight(report)
    assert all("GroupSet_Value" not in action["selector"] for action in clean_actions)


def test_check_5_amount_actions_require_position_verified_true() -> None:
    """Check 5: every SET_AMOUNT action originates from a position_verified=true entry."""
    report = ready_preflight()
    assert report["amounts"]
    assert all(amount["position_verified"] is True for amount in report["amounts"])

    tampered = copy.deepcopy(report)
    tampered["amounts"][0]["position_verified"] = False
    with pytest.raises(ValueError, match="position_verified"):
        build_execution_actions_from_preflight(tampered)


def test_check_6_no_auto_submit() -> None:
    """Check 6: final_decision.real_site_auto_submit/auto_submit is always false."""
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )
    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["final_decision"]["real_site_auto_submit"] is False


def test_check_7_no_auto_next_item() -> None:
    """Check 7: a second queued item never auto-advances past the current one."""
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(text),
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )
    assert report["queue"]["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue"]["items"][1]["status"] == PENDING
    assert all(item["status"] != "DONE" for item in report["queue"]["items"])


def test_check_8_one_item_at_a_time_only() -> None:
    """Check 8: only the current item's actions are ever executed/mapped."""
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = fake_page()
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(text),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )
    assert report["item"]["index"] == 0
    # Only item 0's four numbers were ever clicked -- nothing from item 1.
    assert page.clicked == [
        'button[data-number="06"]',
        'button[data-number="13"]',
        'button[data-number="23"]',
        'button[data-number="22"]',
    ]


def test_check_9_execute_actions_on_page_called_only_after_ready(monkeypatch) -> None:
    """Check 9: execute_actions_on_page is invoked only once preflight is READY."""
    calls: list = []
    real_execute = execute_actions_on_page

    def spy(page, actions):
        calls.append(actions)
        return real_execute(page, actions)

    monkeypatch.setattr(real_site_assisted_fill_module, "execute_actions_on_page", spy)

    blocked_queue = queue_for(f"06.13.23.22 {TWO_THREE}50")  # no approved_fill_queue
    blocked_report = run_real_site_assisted_fill_with_page(
        blocked_queue,
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )
    assert blocked_report["status"] == BLOCKED
    assert calls == []  # never called while BLOCKED

    ready_report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )
    assert ready_report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert len(calls) == 1  # called exactly once, only for the READY run


def test_check_10_blocked_preflight_stops_before_fake_click_or_fill() -> None:
    """Check 10: a BLOCKED preflight never reaches the fake page at all."""
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")  # no approved_fill_queue -> BLOCKED
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == BLOCKED
    assert page.locator_calls == 0
    assert page.clicked == []
    assert page.filled == {}


def test_check_11_fake_page_records_expected_clicks_and_fills() -> None:
    """Check 11: FakePage records exactly the expected number clicks/amount fills."""
    page = fake_page()

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert page.clicked == [
        'button[data-number="06"]',
        'button[data-number="13"]',
        'button[data-number="23"]',
        'button[data-number="22"]',
    ]
    assert page.filled == {
        'input[data-bind*="PengBet.Value"] >> nth=0': "50",
        'input[data-bind*="PengBet.Value"] >> nth=1': "50",
    }


def test_check_12_risk_lock_still_blocks_without_acknowledgement() -> None:
    """Check 12 (existing-tests-still-pass companion): risk lock is unaffected."""
    page = LocatorCallCountingPage(fake_page().elements)
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=False,
    )
    assert report["status"] == BLOCKED
    assert RISK_LOCK_MESSAGE in " ".join(report["errors"])
    assert page.locator_calls == 0
