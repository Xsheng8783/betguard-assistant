import json
import sys

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
    run_real_site_assisted_fill_with_page,
    validate_real_site_action,
)


TWO_THREE = "\u4e8c\u4e09"
TWO_STAR = "\u4e8c\u661f"
THREE_STAR = "\u4e09\u661f"


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


class FakePage:
    frames = []

    def __init__(self, elements: dict[str, dict]):
        self.elements = elements
        self.clicked: list[str] = []
        self.filled: dict[str, str] = {}

    def locator(self, selector: str):
        metadata = self.elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector: {selector}")
        return FakeLocator(self, selector, metadata)


def queue_for(text: str) -> dict:
    return build_batch_queue(attach_summaries(review_text(text).to_dict()))


def candidate(selector: str, text: str = "") -> dict:
    return {
        "tag": "button",
        "text": text,
        "candidate_selectors": [selector],
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
            candidate('button[data-danger="submit"]', "\u9001\u51fa\u6ce8\u55ae"),
            candidate('button[data-danger="confirm"]', "\u78ba\u8a8d"),
        ],
    }


def fake_page() -> FakePage:
    elements = {
        'button[data-number="06"]': {"text": "06", "value": ""},
        'button[data-number="13"]': {"text": "13", "value": ""},
        'button[data-number="23"]': {"text": "23", "value": ""},
        'button[data-number="22"]': {"text": "22", "value": ""},
        'input[data-amount-field="two"]': {"text": TWO_STAR, "value": ""},
        'input[data-amount-field="three"]': {"text": THREE_STAR, "value": ""},
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


def test_element_text_or_value_danger_is_blocked_before_operation() -> None:
    page = fake_page()
    page.elements['button[data-number="06"]'] = {"text": "\u9001\u51fa\u6ce8\u55ae", "value": "\u78ba\u8a8d"}

    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert page.clicked == []
    assert page.filled == {}
    assert any("danger text detected" in error for error in report["errors"])


def test_execution_marks_queue_waiting_for_human_confirm() -> None:
    page = fake_page()

    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
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
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        fake_page(),
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
        queue_for(text),
        full_selector_report(),
        fake_page(),
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


def property_first_page() -> PropertyFirstPage:
    return PropertyFirstPage(fake_page().elements)


def raising_page(raise_on: str) -> RaisingPage:
    return RaisingPage(fake_page().elements, raise_on)


def test_locator_first_as_method_still_fills_and_waits() -> None:
    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        fake_page(),
        risk_acknowledged=True,
    )

    assert report["status"] == WAITING_FOR_HUMAN_CONFIRM
    assert [action["type"] for action in report["actions_executed"]].count("SELECT_NUMBER") == 4


def test_locator_first_as_property_also_fills_and_waits() -> None:
    page = property_first_page()

    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
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
        'input[data-amount-field="two"]': "50",
        'input[data-amount-field="three"]': "50",
    }


def test_locator_lookup_exception_becomes_safe_blocked() -> None:
    page = LocatorLookupRaisingPage(fake_page().elements)

    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
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
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
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
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        page,
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert any("fill failed" in error for error in report["errors"])
    assert page.filled == {}
    assert report["danger_buttons_clicked"] == []


def test_queue_does_not_advance_on_execution_failure() -> None:
    queue = queue_for(f"06.13.23.22 {TWO_THREE}50")

    report = run_real_site_assisted_fill_with_page(
        queue,
        full_selector_report(),
        raising_page("click"),
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert queue["items"][0]["status"] == CURRENT
    assert all(item["status"] != "DONE" for item in queue["items"])
    assert queue["status"] != WAITING_FOR_HUMAN_CONFIRM


def test_execution_failure_never_submits_or_auto_confirms() -> None:
    report = run_real_site_assisted_fill_with_page(
        queue_for(f"06.13.23.22 {TWO_THREE}50"),
        full_selector_report(),
        raising_page("fill"),
        risk_acknowledged=True,
    )

    assert report["status"] == "BLOCKED"
    assert report["danger_buttons_clicked"] == []
    assert report["final_decision"]["real_site_auto_submit"] is False
    assert report["final_decision"]["human_required"] is True
