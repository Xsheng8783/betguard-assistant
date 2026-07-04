import copy
import json
import sys

import pytest

import betguard.webfill.real_site_assisted_fill as real_site_assisted_fill_module
from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill import cli as webfill_cli
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    CURRENT,
    PENDING,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
)
from betguard.webfill.real_site_assisted_fill import (
    RISK_LOCK_MESSAGE,
    build_real_site_assisted_fill_preflight,
    execute_actions_on_page,
    run_real_site_assisted_fill_with_page,
    validate_real_site_action,
)
from betguard.webfill.real_site_fill_preflight import build_real_site_fill_preflight_report


TWO_THREE = "二三"
TWO_STAR = "二星"
THREE_STAR = "三星"


class FakeLocator:
    def __init__(self, page, selector: str, metadata: dict):
        self.page = page
        self.selector = selector
        self.metadata = metadata

    def first(self):
        return self

    def evaluate(self, _script: str):
        return self.metadata

    def click(self):
        self.page.clicked.append(self.selector)

    def fill(self, value: str):
        self.page.filled[self.selector] = value


class FakeFrame:
    """Minimal Playwright-Frame-like double.

    By default (``elements=None``) a frame delegates straight to the owning
    page's own ``.locator()`` -- this keeps every pre-existing FakePage-based
    test working unchanged now that execution actions carry a real ``frame``
    value. Pass ``elements`` explicitly to model a frame whose document is
    genuinely distinct from the top-level page (the real gts362 B03 iframe
    case this fix targets).
    """

    def __init__(self, name: str, page: "FakePage", *, url: str | None = None, elements: dict | None = None):
        self.name = name
        self.url = url or f"https://example.invalid/Front/B/B03?frame={name}"
        self._page = page
        self._own_elements = elements

    def locator(self, selector: str):
        if self._own_elements is None:
            return self._page.locator(selector)
        metadata = self._own_elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector in frame '{self.name}': {selector}")
        return FakeLocator(self._page, selector, metadata)


class FakePage:
    def __init__(self, elements: dict[str, dict]):
        self.elements = elements
        self.clicked: list[str] = []
        self.filled: dict[str, str] = {}
        # A self-delegating "mainFrame" by default, matching every real
        # selector_report's frame_name -- see FakeFrame docstring.
        self.frames = [FakeFrame("mainFrame", self)]

    def locator(self, selector: str):
        metadata = self.elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector: {selector}")
        return FakeLocator(self, selector, metadata)


def queue_for(text: str) -> dict:
    return build_batch_queue(attach_summaries(review_text(text).to_dict()))


def approved_queue_for(text: str) -> dict:
    """A queue that also carries a human-accepted approved_fill_queue.

    This is the shape real_site_fill_preflight (v1) requires as its only
    source of truth: no bare batch queue without approved_fill_queue can ever
    reach READY_FOR_HUMAN_REVIEW.
    """
    queue = queue_for(text)
    entries = []
    for item in queue.get("items", []):
        entries.append(
            {
                "index": item.get("index"),
                "original_fragment": item.get("original"),
                "bet_type": (item.get("review_result") or {}).get("type"),
                "review_result": item.get("review_result"),
                "accepted_by_human": True,
            }
        )
    queue["approved_fill_queue"] = entries
    return queue


def candidate(selector: str, text: str = "", *, frame: str = "mainFrame") -> dict:
    return {
        "tag": "button",
        "text": text,
        "candidate_selectors": [selector],
        "frame_name": frame,
    }


def full_selector_report() -> dict:
    return {
        "number_candidates": {
            "06": [candidate('button[data-number="06"]', "06")],
            "13": [candidate('button[data-number="13"]', "13")],
            "23": [candidate('button[data-number="23"]', "23")],
            "22": [candidate('button[data-number="22"]', "22")],
        },
        "amount_field_candidates": {
            TWO_STAR: [candidate('input[data-amount-field="two"]', TWO_STAR)],
            THREE_STAR: [candidate('input[data-amount-field="three"]', THREE_STAR)],
        },
        "danger_candidates": [
            candidate('button[data-danger="submit"]', "送出注單"),
            candidate('button[data-danger="confirm"]', "確認"),
        ],
    }


def pengbet_candidate(left: float) -> dict:
    return {
        "tag": "input",
        "visible": True,
        "hidden": False,
        "id": "",
        "outerHTML": '<input data-bind="value: PengBet.Value" />',
        "box": {"top": 245, "left": left, "w": 60, "h": 20},
        "candidate_selectors": [],
        "frame_name": "mainFrame",
    }


_SPECIFIC_NUMBER_SELECTORS = {
    "06": 'button[data-number="06"]',
    "13": 'button[data-number="13"]',
    "23": 'button[data-number="23"]',
    "22": 'button[data-number="22"]',
    "08": 'button[data-number="08"]',
    "09": 'button[data-number="09"]',
    "10": 'button[data-number="10"]',
    "11": 'button[data-number="11"]',
}


def clean_profile() -> dict:
    """A full site_profile (v1 preflight input): 39 numbers, the verified
    three-input PengBet.Value row, and danger candidates.
    """
    number_candidates = {}
    for n in range(1, 40):
        label = f"{n:02d}"
        selector = _SPECIFIC_NUMBER_SELECTORS.get(label, f"text={label}")
        number_candidates[label] = [candidate(selector, label)]

    return {
        "profile_version": 1,
        "site_name": "gts362",
        "page_name": "539",
        "captured_at": "2026-07-05",
        "number_candidates": number_candidates,
        "amount_field_candidates": {
            "二星": [pengbet_candidate(65)],
            "三星": [pengbet_candidate(138)],
            "四星": [pengbet_candidate(211)],
        },
        "danger_candidates": [
            candidate('button[data-danger="submit"]', "送出注單"),
            candidate('button[data-danger="confirm"]', "確認"),
        ],
        "market_state": {
            "can_probe_bet_page": True,
            "current_game_name": "539",
            "selected_route": "二三四星",
        },
    }


def fake_page() -> FakePage:
    elements = {
        'button[data-number="06"]': {"text": "06", "value": ""},
        'button[data-number="13"]': {"text": "13", "value": ""},
        'button[data-number="23"]': {"text": "23", "value": ""},
        'button[data-number="22"]': {"text": "22", "value": ""},
        'input[data-bind*="PengBet.Value"] >> nth=0': {"text": TWO_STAR, "value": ""},
        'input[data-bind*="PengBet.Value"] >> nth=1': {"text": THREE_STAR, "value": ""},
    }
    return FakePage(elements)


def test_cli_without_risk_flag_refuses_to_run(capsys, monkeypatch, tmp_path) -> None:
    queue_path = tmp_path / "queue.json"
    selector_path = tmp_path / "selector_report.json"
    queue_path.write_text(json.dumps(queue_for(f"06.13.23.22 {TWO_THREE}50"), ensure_ascii=False), encoding="utf-8")
    selector_path.write_text(json.dumps(full_selector_report(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "betguard.webfill.cli",
            "--real-site-assisted-fill",
            "--queue",
            str(queue_path),
            "--selector-report",
            str(selector_path),
            "--url",
            "http://example.invalid",
        ],
    )

    webfill_cli.main()

    assert RISK_LOCK_MESSAGE in capsys.readouterr().out


# --- Legacy preflight (build_real_site_assisted_fill_preflight): unchanged,
# still tested in isolation. run_real_site_assisted_fill_with_page/
# run_real_site_assisted_fill no longer call this function (see v1b tests
# below) -- it remains importable/tested on its own merits only. ---


def test_danger_candidates_empty_refuses_execution() -> None:
    selector_report = full_selector_report()
    selector_report["danger_candidates"] = []

    preflight = build_real_site_assisted_fill_preflight(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        selector_report,
        risk_acknowledged=True,
    )

    assert preflight["status"] == "BLOCKED"
    assert "danger candidates" in " ".join(preflight["errors"])


def test_real_site_fill_plan_not_ready_refuses_execution() -> None:
    queue = queue_for(f"13.13 {TWO_THREE}100")

    preflight = build_real_site_assisted_fill_preflight(queue, full_selector_report(), risk_acknowledged=True)

    assert queue["status"] == BATCH_BLOCKED
    assert preflight["status"] == "BLOCKED"
    assert "real_site_fill_plan is not READY_FOR_HUMAN_REVIEW" in preflight["errors"]


def test_select_number_only_allows_non_danger_selector() -> None:
    preflight = build_real_site_assisted_fill_preflight(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        risk_acknowledged=True,
    )

    number_actions = [action for action in preflight["execution_actions"] if action["type"] == "SELECT_NUMBER"]
    assert number_actions
    assert all(validate_real_site_action(action) is None for action in number_actions)
    assert all("danger" not in action["selector"] for action in number_actions)


def test_set_amount_only_allows_non_danger_selector() -> None:
    preflight = build_real_site_assisted_fill_preflight(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        risk_acknowledged=True,
    )

    amount_actions = [action for action in preflight["execution_actions"] if action["type"] == "SET_AMOUNT"]
    assert amount_actions
    assert all(validate_real_site_action(action) is None for action in amount_actions)
    assert all("danger" not in action["selector"] for action in amount_actions)


def test_danger_selector_text_is_blocked_before_click() -> None:
    selector_report = full_selector_report()
    selector_report["number_candidates"]["06"][0]["candidate_selectors"] = ['button[data-danger="true"]']

    preflight = build_real_site_assisted_fill_preflight(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        selector_report,
        risk_acknowledged=True,
    )

    assert preflight["status"] == "BLOCKED"
    assert any("unsafe selector" in error for error in preflight["errors"])


# --- run_real_site_assisted_fill_with_page: rewired (v1b) to require the v1
# preflight (real_site_fill_preflight.build_real_site_fill_preflight_report)
# and to source execution actions only from build_execution_actions_from_preflight.
# All fixtures below use approved_queue_for()/clean_profile() -- the shapes v1
# preflight actually requires (approved_fill_queue + a full site profile). ---


def test_element_text_or_value_danger_is_blocked_before_operation() -> None:
    page = fake_page()
    page.elements['button[data-number="06"]'] = {"text": "送出注單", "value": "確認"}

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert page.clicked == []
    assert page.filled == {}
    assert any("danger text detected" in error for error in report["errors"])


def test_execution_marks_queue_waiting_for_human_confirm() -> None:
    page = fake_page()

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue_status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue"]["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["actions_executed"] == [
        {"type": "SELECT_NUMBER", "number": "06", "executed": True},
        {"type": "SELECT_NUMBER", "number": "13", "executed": True},
        {"type": "SELECT_NUMBER", "number": "23", "executed": True},
        {"type": "SELECT_NUMBER", "number": "22", "executed": True},
        {"type": "SET_AMOUNT", "star": TWO_STAR, "amount": 50, "executed": True},
        {"type": "SET_AMOUNT", "star": THREE_STAR, "amount": 50, "executed": True},
    ]


def test_danger_buttons_clicked_and_auto_submit_are_always_false() -> None:
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["danger_buttons_clicked"] == []
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["final_decision"]["human_required"] is True


def test_does_not_mark_done_or_advance_to_next_item() -> None:
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

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue"]["items"][0]["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue"]["items"][1]["status"] == PENDING
    assert all(item["status"] != "DONE" for item in report["queue"]["items"])


# --- Execution guard v1: locator.first compatibility and safe failure ---


class PropertyFirstLocator:
    def __init__(self, page, selector: str, metadata: dict):
        self.page = page
        self.selector = selector
        self.metadata = metadata

    @property
    def first(self):
        return self

    def evaluate(self, _script: str):
        return self.metadata

    def click(self):
        self.page.clicked.append(self.selector)

    def fill(self, value: str):
        self.page.filled[self.selector] = value


class PropertyFirstPage(FakePage):
    def locator(self, selector: str):
        metadata = self.elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector: {selector}")
        return PropertyFirstLocator(self, selector, metadata)


class LocatorLookupRaisingPage(FakePage):
    def locator(self, selector: str):
        raise ValueError(f"locator boom: {selector}")


class RaisingLocator(FakeLocator):
    def __init__(self, page, selector: str, metadata: dict, raise_on: str):
        super().__init__(page, selector, metadata)
        self.raise_on = raise_on

    def click(self):
        if self.raise_on == "click":
            raise ValueError("click boom")
        super().click()

    def fill(self, value: str):
        if self.raise_on == "fill":
            raise ValueError("fill boom")
        super().fill(value)


class RaisingPage(FakePage):
    def __init__(self, elements: dict[str, dict], raise_on: str):
        super().__init__(elements)
        self.raise_on = raise_on

    def locator(self, selector: str):
        metadata = self.elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector: {selector}")
        return RaisingLocator(self, selector, metadata, self.raise_on)


class LocatorCallCountingPage(FakePage):
    def __init__(self, elements: dict[str, dict]):
        super().__init__(elements)
        self.locator_calls = 0

    def locator(self, selector: str):
        self.locator_calls += 1
        return super().locator(selector)


def property_first_page() -> PropertyFirstPage:
    return PropertyFirstPage(fake_page().elements)


def raising_page(raise_on: str) -> RaisingPage:
    return RaisingPage(fake_page().elements, raise_on)


def test_locator_first_as_method_still_fills_and_waits() -> None:
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert [action["type"] for action in report["actions_executed"]].count("SELECT_NUMBER") == 4


def test_locator_first_as_property_also_fills_and_waits() -> None:
    page = property_first_page()

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


def test_locator_lookup_exception_becomes_safe_blocked() -> None:
    page = LocatorLookupRaisingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("locator lookup failed" in error for error in report["errors"])
    assert page.clicked == []
    assert page.filled == {}
    assert report["danger_buttons_clicked"] == []


def test_click_exception_becomes_safe_blocked() -> None:
    page = raising_page("click")

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("click failed" in error for error in report["errors"])
    assert page.clicked == []
    assert page.filled == {}
    assert report["danger_buttons_clicked"] == []


def test_fill_exception_becomes_safe_blocked() -> None:
    page = raising_page("fill")

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("fill failed" in error for error in report["errors"])
    assert page.filled == {}
    assert report["danger_buttons_clicked"] == []


def test_queue_does_not_advance_on_execution_failure() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        raising_page("click"),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert queue["items"][0]["status"] == CURRENT
    assert all(item["status"] != "DONE" for item in queue["items"])
    assert queue["status"] != WAITING_FOR_HUMAN_CONFIRM


def test_validate_real_site_action_rejects_groupset_value_selector_even_without_danger_words() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": "#GroupSet_Value",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_validate_real_site_action_rejects_groupset_value_via_candidate_id() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": 'input[data-bind*="PengBet.Value"] >> nth=0',
        "candidate": {"id": "GroupSet_Value", "text": TWO_STAR},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_validate_real_site_action_rejects_groupset_value_via_candidate_selectors_list() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": 'input[data-bind*="PengBet.Value"] >> nth=0',
        "candidate": {"candidate_selectors": ["#GroupSet_Value"], "text": TWO_STAR},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_validate_real_site_action_allows_safe_amount_selector() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": 'input[data-bind*="PengBet.Value"] >> nth=0',
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    assert validate_real_site_action(action) is None


def test_execution_failure_never_submits_or_auto_confirms() -> None:
    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(f"06.13.23.22 {TWO_THREE}50"),
        clean_profile(),
        raising_page("fill"),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert report["danger_buttons_clicked"] == []
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["final_decision"]["human_required"] is True


# --- Real-site Execution Path Hardening v1b: cannot bypass v1 preflight ---


def test_execution_refuses_without_v1_preflight_when_approved_fill_queue_missing() -> None:
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")  # no approved_fill_queue attached
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert page.locator_calls == 0
    assert page.clicked == []
    assert page.filled == {}


def test_execution_refuses_blocked_preflight_needs_review_state() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    queue["approved_fill_queue"][0]["status"] = "NEEDS_REVIEW"
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert page.locator_calls == 0


def test_execution_refuses_when_item_not_accepted_by_human() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    queue["approved_fill_queue"][0]["accepted_by_human"] = False
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert page.locator_calls == 0


def test_execution_refuses_unsafe_final_decision_flags(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert good_report["status"] == "READY_FOR_HUMAN_REVIEW"
    tampered = copy.deepcopy(good_report)
    tampered["final_decision"]["real_site_execute"] = True

    monkeypatch.setattr(
        real_site_assisted_fill_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("real_site_execute" in error for error in report["errors"])
    assert page.locator_calls == 0


def test_execution_refuses_groupset_value_selector(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    tampered = copy.deepcopy(good_report)
    tampered["amounts"][0]["selector"] = "#GroupSet_Value"

    monkeypatch.setattr(
        real_site_assisted_fill_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("GroupSet_Value" in error for error in report["errors"])
    assert page.locator_calls == 0


def test_execution_refuses_amount_without_position_verified(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    good_report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    tampered = copy.deepcopy(good_report)
    tampered["amounts"][0]["position_verified"] = False

    monkeypatch.setattr(
        real_site_assisted_fill_module,
        "build_real_site_fill_preflight_report",
        lambda *args, **kwargs: tampered,
    )
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("position_verified" in error for error in report["errors"])
    assert page.locator_calls == 0


def test_execute_actions_on_page_not_called_unless_preflight_ready(monkeypatch) -> None:
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")  # no approved_fill_queue -> BLOCKED
    calls = []
    monkeypatch.setattr(
        real_site_assisted_fill_module,
        "execute_actions_on_page",
        lambda *args, **kwargs: (calls.append(args) or []),
    )

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert calls == []


def test_ready_preflight_uses_build_execution_actions_from_preflight(monkeypatch) -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    calls = []
    real_builder = real_site_assisted_fill_module.build_execution_actions_from_preflight

    def spy(report):
        calls.append(report)
        return real_builder(report)

    monkeypatch.setattr(real_site_assisted_fill_module, "build_execution_actions_from_preflight", spy)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        fake_page(),
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert len(calls) == 1
    assert calls[0]["status"] == "READY_FOR_HUMAN_REVIEW"


def test_risk_acknowledgement_still_required_for_v1b_path() -> None:
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    page = LocatorCallCountingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue,
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=False,
    )

    assert report["status"] == "BLOCKED"
    assert RISK_LOCK_MESSAGE in " ".join(report["errors"])
    assert page.locator_calls == 0


# --- Frame-aware locator resolution v1 ---
#
# Root cause of the real BLOCKED trial: execution actions never carried a
# ``frame`` value, so _locator_for_action always fell back to the top-level
# page even though the bet controls live inside a child frame (mainFrame /
# B03 route). build_execution_actions_from_preflight now threads the
# selector's discovered frame through; these tests prove
# execute_actions_on_page actually uses it, and that a selector genuinely
# missing everywhere still fails safely rather than silently guessing.


def test_execution_finds_selector_only_present_in_named_child_frame() -> None:
    page = FakePage({})  # nothing at top-level
    page.frames = [
        FakeFrame("mainFrame", page, elements={"text=23": {"text": "23", "value": ""}})
    ]
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    executed = execute_actions_on_page(page, [action])

    assert executed == [{"type": "SELECT_NUMBER", "number": "23", "executed": True}]
    assert page.clicked == ["text=23"]


def test_top_level_missing_selector_no_longer_blocks_when_child_frame_has_it() -> None:
    # Top-level page carries an unrelated element but NOT "text=23" -- exactly
    # the shape of the real bug report (page text clearly shows 01~39 etc.,
    # but the plain top-level locator still can't see it).
    page = FakePage({"text=99": {"text": "99", "value": ""}})
    page.frames = [
        FakeFrame("mainFrame", page, elements={"text=23": {"text": "23", "value": ""}})
    ]
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_selector_missing_in_every_frame_still_blocked() -> None:
    page = FakePage({})
    page.frames = [FakeFrame("mainFrame", page, elements={})]
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_frame_not_found_fails_safely_not_silently() -> None:
    page = FakePage({"text=23": {"text": "23", "value": ""}})
    page.frames = []  # no frames at all, including no mainFrame
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_frame_scoped_selector_string_danger_still_rejected() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "mainFrame",
            page,
            elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_frame_scoped_element_danger_text_still_blocks_before_click() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "mainFrame",
            page,
            elements={"text=23": {"text": "送出注單", "value": "確認"}},
        )
    ]
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    with pytest.raises(RuntimeError, match="danger text detected"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_groupset_value_still_rejected_even_with_frame_set() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_full_run_with_child_frame_still_has_no_auto_submit_or_auto_next() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "mainFrame",
            page,
            elements={
                'button[data-number="06"]': {"text": "06", "value": ""},
                'button[data-number="13"]': {"text": "13", "value": ""},
                'button[data-number="23"]': {"text": "23", "value": ""},
                'button[data-number="22"]': {"text": "22", "value": ""},
                'input[data-bind*="PengBet.Value"] >> nth=0': {"text": TWO_STAR, "value": ""},
                'input[data-bind*="PengBet.Value"] >> nth=1': {"text": THREE_STAR, "value": ""},
            },
        )
    ]

    report = run_real_site_assisted_fill_with_page(
        approved_queue_for(text),
        clean_profile(),
        page,
        item_index=0,
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert report["queue"]["items"][1]["status"] == PENDING
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["danger_buttons_clicked"] == []
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
