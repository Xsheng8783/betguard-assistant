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
    AMOUNT_FIELD_QUERY_SELECTOR,
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

    def __init__(
        self,
        name: str,
        page: "FakePage",
        *,
        url: str | None = None,
        elements: dict | None = None,
        rendered_text: str = "",
        amount_field_elements: list[dict] | None = None,
        empty_frame_diagnostic_data: dict | None = None,
        empty_frame_diagnostic_raises: bool = False,
        child_frames: list | None = None,
    ):
        self.name = name
        self.url = url if url is not None else f"https://example.invalid/Front/B/B03?frame={name}"
        self.rendered_text = rendered_text
        self._page = page
        self._own_elements = elements
        self._amount_field_elements = amount_field_elements
        self._empty_frame_diagnostic_data = empty_frame_diagnostic_data
        self._empty_frame_diagnostic_raises = empty_frame_diagnostic_raises
        if child_frames is not None:
            self.child_frames = child_frames

    def locator(self, selector: str):
        if selector == AMOUNT_FIELD_QUERY_SELECTOR and self._amount_field_elements is not None:
            return FakeMultiLocator(self._amount_field_elements)
        if self._own_elements is None:
            return self._page.locator(selector)
        metadata = self._own_elements.get(selector)
        if metadata is None:
            raise AssertionError(f"unexpected selector in frame '{self.name}': {selector}")
        return FakeLocator(self._page, selector, metadata)

    def evaluate(self, _script: str):
        """Used only by the empty-Shared/Index structural diagnostics --
        distinct from ``.locator(...).evaluate()`` used for rendered text and
        per-element checks.
        """
        if self._empty_frame_diagnostic_raises:
            raise RuntimeError("simulated frame.evaluate failure")
        if self._empty_frame_diagnostic_data is not None:
            return self._empty_frame_diagnostic_data
        return {}


class FakeElementHandle:
    """Minimal Playwright-Locator-like double for a single already-found
    element -- only what ``_frame_has_verified_amount_triple`` needs:
    ``.evaluate()`` for the element id and ``.bounding_box()`` for position.
    """

    def __init__(self, data: dict):
        self._data = data

    def evaluate(self, _script: str):
        return self._data.get("id", "")

    def bounding_box(self):
        return self._data.get("box")


class FakeMultiLocator:
    """Minimal Playwright-Locator-like double for a multi-element query
    (``.count()`` / ``.nth(i)``), used only for the PengBet.Value amount
    field re-verification query.
    """

    def __init__(self, elements: list[dict]):
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> FakeElementHandle:
        return FakeElementHandle(self._elements[index])


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


B03_FRAME_URL = "https://www.gts362.com/Front/B/B03"


def candidate(selector: str, text: str = "", *, frame: str = "mainFrame") -> dict:
    return {
        "tag": "button",
        "text": text,
        "candidate_selectors": [selector],
        "frame_name": frame,
        "frame_url": B03_FRAME_URL,
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
        "frame_url": B03_FRAME_URL,
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


# --- Frame resolution v2: exact name -> URL/path -> rendered-content markers ---
#
# Root cause of the second live BLOCKED trial: real Playwright's live frame
# ``name`` did not match the recorded "mainFrame" name, so exact-name
# matching alone always failed with "frame not found: mainFrame" -- even
# though the previous fix (frame threading v1) was otherwise working. The
# resolver now falls back to frame URL/path metadata, then (only as a last
# resort) genuine rendered betting-page content -- never to the top-level
# page, and never just because a frame happens to contain the selector text.

BETTING_PAGE_RENDERED_TEXT = (
    "539 - 下注資訊\n二星 三星 四星\n連碰\n"
    "01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 "
    "21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39"
)


def test_exact_frame_name_still_works() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame("mainFrame", page, elements={"text=23": {"text": "23", "value": ""}})
    ]
    action = {"type": "SELECT_NUMBER", "number": "23", "selector": "text=23", "frame": "mainFrame"}

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_frame_name_missing_but_url_contains_b03_route_works() -> None:
    page = FakePage({})
    # Runtime frame name is unstable/different ("frame3"), but its URL is the
    # known B03 betting route -- must be found via URL/path fallback.
    page.frames = [
        FakeFrame(
            "frame3",
            page,
            url="https://www.gts362.com/Front/B/B03?x=1",
            elements={"text=23": {"text": "23", "value": ""}},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # exact-name match will fail
        "frame_url": "https://www.gts362.com/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_frame_name_and_url_missing_but_rendered_markers_identify_betting_frame() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame7",
            page,
            url="https://www.gts362.com/some/other/path",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=BETTING_PAGE_RENDERED_TEXT,
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # name fails
        "frame_url": "/Front/B/B03",  # url fails (frame's URL doesn't contain it)
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_selector_present_in_wrong_frame_alone_is_not_enough() -> None:
    # A decoy frame contains "text=23" but is neither name/URL matched nor
    # rendered-content matched as the real betting frame -- it must never be
    # picked just because the selector text happens to exist there.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "decoyFrame",
            page,
            url="https://www.gts362.com/ads/banner",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text="這是廣告 frame，剛好也有文字 23 但不是下注頁",
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_ambiguous_multiple_betting_like_frames_blocked() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameA",
            page,
            url="https://www.gts362.com/other/a",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=BETTING_PAGE_RENDERED_TEXT,
        ),
        FakeFrame(
            "frameB",
            page,
            url="https://www.gts362.com/other/b",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=BETTING_PAGE_RENDERED_TEXT,
        ),
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # neither frame matches by name
        "frame_url": "/Front/B/B03",  # neither frame matches by url
    }

    with pytest.raises(RuntimeError, match="ambiguous frame match"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_no_matching_frame_at_all_blocked() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "unrelatedFrame",
            page,
            url="https://www.gts362.com/unrelated",
            elements={},
            rendered_text="首頁廣告內容，與下注頁無關",
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_danger_selector_still_rejected_inside_fallback_frame() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame3",
            page,
            url="https://www.gts362.com/Front/B/B03?x=1",
            elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_groupset_value_still_rejected_inside_fallback_frame() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_full_run_via_url_fallback_frame_still_one_item_no_auto_submit_no_auto_next() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame3",  # runtime name does not match recorded "mainFrame"
            page,
            url="https://www.gts362.com/Front/B/B03?x=1",
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


# --- Frame resolution v3: diagnose why 0b08f0b still showed
# "frame not found: mainFrame" on the live third trial ---
#
# Investigation confirmed (via direct build_execution_actions_from_preflight
# inspection against the real local queue/profile) that the SELECT_NUMBER 23
# action for item 1 genuinely carries both frame="mainFrame" and a populated
# frame_url containing /Front/B/B03, and that _resolve_frame's control flow
# already falls through name -> url -> rendered-markers correctly (it never
# raises early on a name-match miss). Neither of the two suspected causes
# reproduced locally. These tests lock in that diagnosis and add two real
# hardenings found along the way: case-insensitive URL/path matching (the
# server may assign a different-case or different-subdomain/session-token
# URL each session) and a diagnostic-rich "frame not found" message so a
# future BLOCKED trial's error text alone reveals which fallback stage
# actually failed, instead of requiring another investigation round-trip.


def test_execution_action_always_carries_frame_url_when_candidate_has_one() -> None:
    """Diagnostic assertion (task requirement 5): proves frame_url is not
    silently dropped anywhere between the mapping candidate and the final
    execution action -- the exact thing the bug report suspected.
    """
    queue = approved_queue_for(f"06.13.23.22 {TWO_THREE}50")
    v1_report = build_real_site_fill_preflight_report(queue, clean_profile(), item_index=0)
    assert v1_report["status"] == "READY_FOR_HUMAN_REVIEW"

    actions = real_site_assisted_fill_module.build_execution_actions_from_preflight(v1_report)

    assert actions
    for action in actions:
        assert action.get("frame"), action
        assert action.get("frame_url"), action
        assert B03_FRAME_URL in action["frame_url"]


def test_frame_url_fallback_is_case_insensitive() -> None:
    # The recorded frame_url uses one case; the live frame's URL happens to
    # differ in case (a real possibility with server-side URL normalization).
    # Case must not defeat the B03-path fallback.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame9",
            page,
            url="https://W0.GTS362.com/DifferentSessionToken/FRONT/b/B03",
            elements={"text=23": {"text": "23", "value": ""}},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "https://w0.gts362.com/tUYuf3oHWEqPmxRrmIIFgg/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_frame_url_fallback_survives_different_session_token_and_subdomain() -> None:
    # Reproduces the exact real-world shape: the profile was captured on one
    # session (w0.gts362.com/<tokenA>/Front/B/B03); the live trial runs on a
    # different load-balanced subdomain and session token
    # (w3.gts362.com/<tokenB>/Front/B/B03). Only the shared /Front/B/B03
    # path segment is common -- that must still be enough.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame3",
            page,
            url="https://w3.gts362.com/CompletelyDifferentToken99/Front/B/B03?ts=123",
            elements={"text=23": {"text": "23", "value": ""}},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # exact name will not be found
        "frame_url": "https://w0.gts362.com/tUYuf3oHWEqPmxRrmIIFgg/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_frame_not_found_message_is_diagnostic() -> None:
    """When every fallback genuinely fails, the error must say what was tried
    (was frame_url present, how many frames existed, their name/url) instead
    of a bare, undiagnosable "frame not found: mainFrame".
    """
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "unrelatedFrame",
            page,
            url="https://www.gts362.com/unrelated/path",
            elements={},
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "https://w0.gts362.com/tUYuf3oHWEqPmxRrmIIFgg/Front/B/B03",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "url_ref=" in message
    assert "url_fallback_attempted=True" in message
    assert "frames_seen=1" in message
    assert "unrelatedFrame" in message


def test_frame_resolution_still_produces_no_submit_no_confirm_no_auto_next() -> None:
    """Regression guard: the frame-resolution investigation/fix must not
    have touched any of the existing forbidden-action or auto-next guards.
    """
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frame3",
            page,
            url="https://w3.gts362.com/OtherToken/Front/B/B03",
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

    groupset_action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "https://w3.gts362.com/OtherToken/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    assert validate_real_site_action(groupset_action) is not None


# --- Frame resolution v4: Tiantianle rendered-marker compatibility ---
#
# Confirmed gap: the rendered-content fallback previously required the
# literal "539 - 下注資訊" marker, so a genuine Tiantianle frame (whose page
# reads "天天樂 - 下注資訊") could never be recognized as the betting frame
# via the content-marker last-resort step -- even though 539 and Tiantianle
# share the same page template and B03 route. Fixed by accepting either
# game's name marker while keeping every other requirement (all three star
# labels, 連碰, a genuine 01~39 number board) exactly as strict as before.

TIANTIANLE_PAGE_RENDERED_TEXT = (
    "天天樂 - 下注資訊\n二星 三星 四星\n連碰\n"
    "01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 "
    "21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39"
)


def test_539_rendered_marker_still_works() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameX",
            page,
            url="https://www.gts362.com/some/other/path",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=BETTING_PAGE_RENDERED_TEXT,  # "539 - 下注資訊" flavor
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # name fails
        "frame_url": "/Front/B/B03",  # url fails (frame's URL doesn't contain it)
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_tiantianle_rendered_marker_now_works() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameY",
            page,
            url="https://www.gts362.com/some/other/path",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",  # name fails
        "frame_url": "/Front/B/B03",  # url fails (frame's URL doesn't contain it)
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=23"]


def test_wrong_or_random_page_marker_still_blocked() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameZ",
            page,
            url="https://www.gts362.com/ads/banner",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text="這是首頁廣告內容，剛好也有數字 23 但完全不是下注頁",
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_ambiguous_539_and_tiantianle_frames_both_matching_blocked() -> None:
    # Two frames each independently look like a genuine betting page (one
    # 539-flavored, one Tiantianle-flavored) -- neither name nor URL
    # disambiguates them, so this must BLOCK rather than guess either one.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameA",
            page,
            url="https://www.gts362.com/other/a",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=BETTING_PAGE_RENDERED_TEXT,
        ),
        FakeFrame(
            "frameB",
            page,
            url="https://www.gts362.com/other/b",
            elements={"text=23": {"text": "23", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
        ),
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "23",
        "selector": "text=23",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="ambiguous frame match"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_groupset_value_still_rejected_with_tiantianle_frame() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 50,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_danger_selector_still_rejected_inside_tiantianle_fallback_frame() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "frameY",
            page,
            url="https://www.gts362.com/some/other/path",
            elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
        "frame_url": "/Front/B/B03",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


# --- Frame resolution v3: Shared/Index DOM-signature fallback ---
#
# Root cause of the live Tiantianle BLOCKED trial: the runtime page only
# exposed one frame, unnamed, whose URL was /Front/Shared/Index -- not the
# recorded /Front/B/B03. Neither exact-name, recorded-URL, nor (apparently)
# the plain rendered-marker fallback resolved it. /Front/Shared/Index is used
# by many pages on this site, so it is never trusted on URL alone -- it must
# also independently prove it is the real bet page via rendered text markers
# *and* a live re-check of the exact 3-input PengBet.Value row structure the
# offline mapping already required.


def tiantianle_amount_field_elements() -> list[dict]:
    return [
        {"id": "", "box": {"x": 65, "y": 245, "width": 60, "height": 20}},
        {"id": "", "box": {"x": 138, "y": 245, "width": 60, "height": 20}},
        {"id": "", "box": {"x": 211, "y": 245, "width": 60, "height": 20}},
    ]


def test_shared_index_frame_with_full_signature_resolves_and_clicks() -> None:
    # Live shape from the bug report: recorded frame="mainFrame" /
    # frame_url=/Front/B/B03, but runtime only has one unnamed
    # /Front/Shared/Index frame -- with a genuine Tiantianle betting DOM.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/TC4S9h9ZvUyeIDSorO-1kA/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=11"]


def test_shared_index_frame_without_any_signature_blocked() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=None,
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_shared_index_frame_with_only_selector_text_but_no_signature_blocked() -> None:
    # The target selector genuinely exists in this frame, but it has neither
    # rendered betting-page text nor the verified amount-field triple --
    # matching the selector alone must never be enough.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text="",  # no betting markers at all
            amount_field_elements=None,  # no amount fields either
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_shared_index_frame_with_markers_but_wrong_amount_field_count_blocked() -> None:
    # Rendered text looks like a genuine bet page, but the amount-field DOM
    # re-check finds only 2 PengBet.Value inputs (not the required 3) --
    # text markers alone must never be enough either.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements()[:2],
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_shared_index_groupset_value_as_target_still_rejected() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 100,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_shared_index_danger_selector_still_rejected() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_ambiguous_multiple_shared_index_betting_frames_blocked() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/tokenA/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        ),
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/tokenB/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        ),
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="ambiguous frame match"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_full_run_via_shared_index_fallback_one_item_no_auto_submit_no_auto_next() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",  # unnamed, exactly like the live bug report
            page,
            url="https://w0.gts362.com/TC4S9h9ZvUyeIDSorO-1kA/Front/Shared/Index",
            elements={
                'button[data-number="06"]': {"text": "06", "value": ""},
                'button[data-number="13"]': {"text": "13", "value": ""},
                'button[data-number="23"]': {"text": "23", "value": ""},
                'button[data-number="22"]': {"text": "22", "value": ""},
                'input[data-bind*="PengBet.Value"] >> nth=0': {"text": TWO_STAR, "value": ""},
                'input[data-bind*="PengBet.Value"] >> nth=1': {"text": THREE_STAR, "value": ""},
            },
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
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


# --- Shared/Index signature diagnostics v1 ---
#
# The live Tiantianle trial BLOCKED again even after the Shared/Index
# fallback landed: the single /Front/Shared/Index frame did not pass the
# strong DOM signature, but the error text could not say WHICH condition
# failed. These tests lock in the new per-condition diagnostics: when a
# Shared/Index frame fails the signature, the "frame not found" message now
# lists every check result (booleans/counts only -- never page text), so
# the next live BLOCKED run is self-diagnosing. Pass criteria are unchanged
# and exactly as strict as before.


def shared_index_action() -> dict:
    return {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }


def test_shared_index_failure_message_lists_every_diagnostic_field() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=None,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    for key in (
        "shared_index_url_match",
        "rendered_text_available",
        "game_marker_found",
        "star_markers_found",
        "lianpeng_marker_found",
        "number_board_token_count",
        "amount_query_count",
        "amount_visible_count",
        "amount_non_groupset_count",
        "amount_same_row_result",
        "amount_x_positions_count",
        "final_shared_index_signature_passed",
    ):
        assert f"{key}=" in message, key
    assert "final_shared_index_signature_passed=False" in message
    assert page.clicked == []


def test_shared_index_diagnostics_show_missing_rendered_text() -> None:
    # Amount triple is perfect, but the rendered text is empty -- the message
    # must point at the text checks, not the amount checks.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=tiantianle_amount_field_elements(),
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "rendered_text_available=False" in message
    assert "amount_query_count=3" in message
    assert "amount_visible_count=3" in message
    assert "amount_same_row_result=True" in message


def test_shared_index_diagnostics_show_wrong_amount_count() -> None:
    # Text markers are all present, but only 2 PengBet.Value inputs exist --
    # the message must point at amount_query_count.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements()[:2],
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "game_marker_found=True" in message
    assert "star_markers_found=True" in message
    assert "amount_query_count=2" in message
    assert "final_shared_index_signature_passed=False" in message


def test_shared_index_diagnostics_show_not_same_row() -> None:
    elements = tiantianle_amount_field_elements()
    elements[2]["box"]["y"] = 400  # third input on a different row
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=elements,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "amount_query_count=3" in message
    assert "amount_same_row_result=False" in message


def test_shared_index_diagnostics_show_groupset_value_contamination() -> None:
    elements = tiantianle_amount_field_elements()
    elements[0]["id"] = "GroupSet_Value"
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=elements,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "amount_non_groupset_count=2" in message
    assert "final_shared_index_signature_passed=False" in message
    assert page.clicked == []


def test_shared_index_diagnostics_show_invisible_amount_field() -> None:
    elements = tiantianle_amount_field_elements()
    elements[1]["box"] = None  # not rendered -> no bounding box
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=elements,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "amount_query_count=3" in message
    assert "amount_visible_count=2" in message


def test_shared_index_diagnostics_never_dump_page_text() -> None:
    secret_text = "天天樂 - 下注資訊 SECRET-PAGE-CONTENT-MUST-NOT-LEAK"
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text=secret_text,  # fails star/連碰/board checks
            amount_field_elements=None,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "SECRET-PAGE-CONTENT-MUST-NOT-LEAK" not in message
    assert "game_marker_found=True" in message
    assert "star_markers_found=False" in message


def test_no_shared_index_frames_means_no_shared_index_checks_in_message() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "unrelatedFrame",
            page,
            url="https://www.gts362.com/unrelated/path",
            elements={},
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action()])

    message = str(excinfo.value)
    assert "shared_index_checks" not in message
    assert "frames_seen=1" in message


def test_shared_index_passing_frame_still_resolves_after_diagnostics_refactor() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        )
    ]

    executed = execute_actions_on_page(page, [shared_index_action()])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=11"]


# --- Empty Shared/Index frame diagnostics v1 ---
#
# The live Tiantianle trial hit a Shared/Index frame whose rendered_text was
# empty AND whose PengBet.Value query found 0 elements -- the previous
# diagnostics could confirm THAT both were empty but not WHY. These tests
# lock in the new bounded, read-only structural diagnostics (readyState,
# body_exists, nested frame/iframe count and truncated src/name/id entries,
# and Playwright's own child_frames list) that only activate in exactly that
# situation, and never change acceptance -- the passing-signature test at
# the bottom proves that.


def shared_index_action_for_11() -> dict:
    return {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }


def test_empty_shared_index_reports_body_missing() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_data={
                "bodyExists": False,
                "readyState": "loading",
                "frameIframeCount": 0,
                "framesetFrameTagCount": 0,
                "entries": [],
            },
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    assert "body_exists=False" in message
    assert "document_ready_state=loading" in message
    assert page.clicked == []


def test_empty_shared_index_reports_frameset_with_b03_src() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_data={
                "bodyExists": True,
                "readyState": "complete",
                "frameIframeCount": 1,
                "framesetFrameTagCount": 1,
                "entries": [
                    {
                        "tag": "FRAME",
                        "src": "https://w1.gts362.com/tok/Front/B/B03",
                        "name": "mainFrame",
                        "id": "",
                    }
                ],
            },
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    assert "frameset_frame_tag_count=1" in message
    assert "b03_src_found=True" in message
    assert "front_b_src_found=True" in message
    assert "b03_token_found=True" in message
    assert "'name': 'mainFrame'" in message
    assert page.clicked == []


def test_empty_shared_index_reports_iframe_with_b03_src() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_data={
                "bodyExists": True,
                "readyState": "complete",
                "frameIframeCount": 1,
                "framesetFrameTagCount": 0,
                "entries": [
                    {
                        "tag": "IFRAME",
                        "src": "/Front/B/B03?x=1",
                        "name": "",
                        "id": "betFrame",
                    }
                ],
            },
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    assert "frame_iframe_count=1" in message
    assert "b03_src_found=True" in message
    assert "'tag': 'IFRAME'" in message
    assert "'id': 'betFrame'" in message


def test_empty_shared_index_diagnostics_are_bounded_not_full_html() -> None:
    huge_src = "https://w0.gts362.com/" + ("x" * 5000) + "/Front/B/B03"
    many_entries = [
        {"tag": "IFRAME", "src": f"/some/other/path/{i}", "name": "", "id": ""} for i in range(50)
    ]
    many_entries[0]["src"] = huge_src

    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_data={
                "bodyExists": True,
                "readyState": "complete",
                "frameIframeCount": 50,
                "framesetFrameTagCount": 0,
                "entries": many_entries,
            },
            child_frames=[FakeFrame(f"child{i}", page, url=f"/child/{i}") for i in range(30)],
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    # Full 5000-char src must never appear verbatim; only a bounded prefix.
    assert huge_src not in message
    assert len(message) < 8000
    # At most 10 nested entries and at most 10 child_frames are ever surfaced.
    assert message.count("'tag':") <= 10
    assert "child_frames_count=30" in message


def test_empty_shared_index_reports_child_frames_when_available() -> None:
    # child_frames is now also part of the real candidate pool (recursive
    # traversal), so this child must be a plain, non-matching sibling frame
    # (not named mainFrame, no B03 in its URL, no rendered signature) --
    # otherwise it would legitimately resolve instead of staying BLOCKED,
    # which is a different test (see test_child_frame_* below).
    page = FakePage({})
    child = FakeFrame("gmenu", page, url="https://w1.gts362.com/tok/Front/Shared/Menu")
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_data={
                "bodyExists": True,
                "readyState": "complete",
                "frameIframeCount": 0,
                "framesetFrameTagCount": 0,
                "entries": [],
            },
            child_frames=[child],
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    assert "child_frames_count=1" in message
    assert "gmenu" in message


def test_empty_shared_index_evaluate_exception_reported_safely() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={},
            rendered_text="",
            amount_field_elements=[],
            empty_frame_diagnostic_raises=True,
        )
    ]

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [shared_index_action_for_11()])

    message = str(excinfo.value)
    assert "body_evaluate_raised=True" in message
    assert page.clicked == []


def test_empty_shared_index_diagnostics_do_not_change_acceptance() -> None:
    # Same passing scenario as before this diagnostic was added: valid
    # rendered text + valid amount triple -- must still resolve exactly the
    # same way regardless of the new (unused, since not empty) diagnostics.
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={"text=11": {"text": "11", "value": ""}},
            rendered_text=TIANTIANLE_PAGE_RENDERED_TEXT,
            amount_field_elements=tiantianle_amount_field_elements(),
        )
    ]

    executed = execute_actions_on_page(page, [shared_index_action_for_11()])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=11"]


def test_danger_selector_still_rejected_with_empty_shared_index_diagnostics() -> None:
    page = FakePage({})
    page.frames = [
        FakeFrame(
            "",
            page,
            url="https://w0.gts362.com/token/Front/Shared/Index",
            elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
            rendered_text="",
            amount_field_elements=[],
        )
    ]
    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_groupset_value_still_rejected_with_empty_shared_index_diagnostics() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 100,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/token2/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


# --- Recursive child_frames traversal v1 ---
#
# Root cause of the live Tiantianle BLOCKED trial: page.frames only listed
# the parent /Front/Shared/Index frame. The real bet frame (name=mainFrame,
# url=.../Front/B/B03) only appeared under that parent frame's own
# child_frames -- never directly in page.frames. _resolve_frame() now
# collects page.frames plus every frame's child_frames recursively (bounded,
# deduplicated) before running the exact same name/URL/marker/Shared-Index
# checks. No acceptance rule changed; only the candidate pool got deeper.


def test_child_frame_mainframe_b03_resolves_and_clicks() -> None:
    # Exact live shape: page.frames has only the unnamed parent Shared/Index
    # frame; the real mainFrame/B03 frame is only reachable via its
    # child_frames.
    page = FakePage({})
    main_child = FakeFrame(
        "mainFrame",
        page,
        url="https://w0.gts362.com/P755session/Front/B/B03",
        elements={"text=11": {"text": "11", "value": ""}},
    )
    menu_child = FakeFrame("gmenu", page, url="https://w0.gts362.com/P755session/Front/Shared/Menu")
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[menu_child, main_child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=11"]


def test_child_frame_exact_name_match_works() -> None:
    page = FakePage({})
    child = FakeFrame(
        "mainFrame",
        page,
        url="https://w0.gts362.com/whatever/unrelated/path",  # deliberately not B03-shaped
        elements={"text=22": {"text": "22", "value": ""}},
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "22",
        "selector": "text=22",
        "frame": "mainFrame",  # exact name match against the child
        "frame_url": "",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=22"]


def test_child_frame_url_b03_path_match_survives_different_token_and_subdomain() -> None:
    page = FakePage({})
    child = FakeFrame(
        "frame9",  # runtime name unstable/different from recorded "mainFrame"
        page,
        url="https://w3.gts362.com/CompletelyDifferentToken/Front/B/B03?ts=1",
        elements={"text=33": {"text": "33", "value": ""}},
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "33",
        "selector": "text=33",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=33"]


def test_ambiguous_multiple_child_b03_frames_blocked() -> None:
    page = FakePage({})
    child_a = FakeFrame(
        "frameA",
        page,
        url="https://w0.gts362.com/tokenA/Front/B/B03",
        elements={"text=11": {"text": "11", "value": ""}},
    )
    child_b = FakeFrame(
        "frameB",
        page,
        url="https://w0.gts362.com/tokenB/Front/B/B03",
        elements={"text=11": {"text": "11", "value": ""}},
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[child_a, child_b],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",  # neither child named mainFrame
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="ambiguous frame match"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_no_child_b03_frame_blocked_with_diagnostics() -> None:
    page = FakePage({})
    menu_child = FakeFrame("gmenu", page, url="https://w0.gts362.com/P755session/Front/Shared/Menu")
    print_child = FakeFrame("gprint", page, url="https://w0.gts362.com/P755session/Front/Shared/BetList")
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        empty_frame_diagnostic_data={
            "bodyExists": True,
            "readyState": "complete",
            "frameIframeCount": 2,
            "framesetFrameTagCount": 2,
            "entries": [
                {"tag": "FRAME", "src": "/P755session/Front/Shared/Menu", "name": "gmenu", "id": "gmenu"},
                {"tag": "FRAME", "src": "/P755session/Front/Shared/BetList", "name": "gprint", "id": "gprint"},
            ],
        },
        child_frames=[menu_child, print_child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "final_shared_index_signature_passed=False" in message
    assert "child_frames_count=2" in message
    assert "gmenu" in message
    assert page.clicked == []


def test_danger_selector_still_rejected_in_child_frame() -> None:
    page = FakePage({})
    child = FakeFrame(
        "mainFrame",
        page,
        url="https://w0.gts362.com/P755session/Front/B/B03",
        elements={'button[data-danger="true"]': {"text": "06", "value": ""}},
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "06",
        "selector": 'button[data-danger="true"]',
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_groupset_value_still_rejected_in_child_frame_context() -> None:
    action = {
        "type": "SET_AMOUNT",
        "star": TWO_STAR,
        "amount": 100,
        "selector": "#GroupSet_Value",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
        "candidate": {"text": TWO_STAR, "value": ""},
    }
    error = validate_real_site_action(action)
    assert error is not None
    assert "GroupSet_Value" in error


def test_full_run_through_child_frame_one_item_no_auto_submit_no_auto_next() -> None:
    text = "\n".join(
        [
            f"06.13.23.22 {TWO_THREE}50",
            f"08.09.10.11 {TWO_THREE}100",
        ]
    )
    page = FakePage({})
    main_child = FakeFrame(
        "mainFrame",
        page,
        url="https://w0.gts362.com/P755session/Front/B/B03",
        elements={
            'button[data-number="06"]': {"text": "06", "value": ""},
            'button[data-number="13"]': {"text": "13", "value": ""},
            'button[data-number="23"]': {"text": "23", "value": ""},
            'button[data-number="22"]': {"text": "22", "value": ""},
            'input[data-bind*="PengBet.Value"] >> nth=0': {"text": TWO_STAR, "value": ""},
            'input[data-bind*="PengBet.Value"] >> nth=1': {"text": THREE_STAR, "value": ""},
        },
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/P755session/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[main_child],
    )
    page.frames = [parent]

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


def test_recursive_traversal_deduplicates_and_is_bounded() -> None:
    # A frame that (implausibly) reports itself as its own child must not
    # cause infinite recursion or duplicate entries in the candidate pool.
    page = FakePage({})
    cyclic = FakeFrame(
        "cyclicFrame",
        page,
        url="https://w0.gts362.com/cyclic",
        elements={},
    )
    cyclic.child_frames = [cyclic]  # self-referential
    page.frames = [cyclic]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    # Must terminate (not hang) and fail safely -- no frame matches.
    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


# --- page.frame(url=...) B03 glob fallback v2 ---
#
# Live Tiantianle trials showed that on certain frameset layouts neither
# page.frames nor frame.child_frames expose the betting frame through
# the getattr path. _collect_all_frames now uses direct attribute access
# (frame.child_frames) and also seeds the queue via page.frame(url="**/Front/B/B03").


def test_page_frame_url_glob_resolves_b03() -> None:
    """Frame reachable via page.frame(url='**/Front/B/B03'), not in page.frames."""
    page = FakePage({})
    b03_frame = FakeFrame(
        "mainFrame",
        page,
        url="https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
        elements={"text=11": {"text": "11", "value": ""}},
    )
    # page.frames has only a stub parent; b03 is only via page.frame(url=...)
    parent = FakeFrame(
        "",
        page,
        url="https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
    )
    page.frames = [parent]

    # page.frame(url="**/Front/B/B03") returns b03_frame
    page.frame = lambda name=None, url=None: b03_frame if url and "B/B03" in url else None

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    executed = execute_actions_on_page(page, [action])

    assert executed[0]["executed"] is True
    assert page.clicked == ["text=11"]


def test_page_frame_url_glob_returns_none_gracefully() -> None:
    """page.frame(url=...) returns None -- should not crash."""
    page = FakePage({})
    page.frames = []

    page.frame = lambda name=None, url=None: None

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_page_frame_url_glob_survives_exception() -> None:
    """page.frame(url=...) raises -- should not crash."""
    page = FakePage({})
    page.frames = []

    def _raiser(name=None, url=None):
        raise RuntimeError("boom")

    page.frame = _raiser

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


def test_page_without_frame_method_still_works_v2() -> None:
    """Pages without a .frame() method (e.g. mocks) should not crash."""
    page = FakePage({})
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
    )
    page.frames = [parent]
    # No page.frame attribute at all.

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError, match="locator lookup failed"):
        execute_actions_on_page(page, [action])
    assert page.clicked == []


# --- v4 collector diagnostics ---
#
# _collect_all_frames now returns (frames, diagnostics_dict) and
# _frame_not_found_message includes raw_page_frames, collected_frames,
# page.frame(...) results, and per-raw-frame child_frames counts.


def test_collector_diagnostics_show_collected_includes_mainframe() -> None:
    """When page.frames has only Shared/Index but child_frames contains
    mainFrame/B03, the error message must show collected_frames includes
    mainFrame. Frame name must NOT match so resolution fails through
    all steps and produces collector diagnostics."""
    page = FakePage({})
    main_child = FakeFrame(
        "mainFrame",
        page,
        url="https://w0.gts362.com/token/Front/B/B03",
        elements={},
    )
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/token/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[main_child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "nonexistentFrame",  # deliberate wrong name
        "frame_url": "https://w1.gts362.com/badtoken/Front/NotB03",  # deliberate non-B03 URL
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "raw_page_frames_count=1" in message
    assert "collected_frames_count=2" in message  # parent + main_child
    assert "mainFrame|" in message
    assert "/Front/B/B03" in message
    assert "child_frames_count=1" in message
    assert page.clicked == []


def test_collector_diagnostics_show_page_frame_by_name_result() -> None:
    """page.frame(name='mainFrame') result is included in diagnostics.
    Use a non-matching frame name so resolution fails and diagnostics appear."""
    page = FakePage({})
    page.frames = []

    def _fake_frame(name=None, url=None):
        if name == "mainFrame":
            return FakeFrame("mainFrame", page, url="https://w0.gts362.com/token/Front/B/B03")
        return None
    page.frame = _fake_frame

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "nonexistentFrame",
        "frame_url": "",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "page_frame_by_name_mainFrame=mainFrame|" in message
    assert "page_frame_method_exists=True" in message
    assert page.clicked == []


def test_collector_diagnostics_show_page_frame_by_url_result() -> None:
    """page.frame(url='**/Front/B/B03') result is included."""
    page = FakePage({})
    page.frames = []

    b03 = FakeFrame("b03frame", page, url="https://w0.gts362.com/token/Front/B/B03")
    def _fake_frame(name=None, url=None):
        if url and "B/B03" in url:
            return b03
        return None
    page.frame = _fake_frame

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "nonexistentFrame",
        "frame_url": "",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "page_frame_by_url_b03=b03frame|" in message
    assert page.clicked == []


def test_collector_diagnostics_show_raw_frame_child_frames() -> None:
    """Per-raw-frame diagnostics show child_frames for each page frame.
    Use non-matching frame name so resolution fails through all steps."""
    page = FakePage({})
    child = FakeFrame("mainFrame", page, url="https://w0.gts362.com/token/Front/B/B03")
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/token/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
        child_frames=[child],
    )
    page.frames = [parent]

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "nonexistentFrame",
        "frame_url": "https://w1.gts362.com/bad/token/Front/NotB03",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "raw_frame[0]=" in message
    assert "child_frames_count=1" in message
    assert "mainFrame|" in message
    assert "/Front/B/B03" in message
    assert page.clicked == []


def test_collector_diagnostics_with_fake_page_no_frame_method() -> None:
    """A page without .frame() method shows page_frame_method_exists=False."""
    page = FakePage({})
    parent = FakeFrame(
        "",
        page,
        url="https://w0.gts362.com/Front/Shared/Index",
        elements={},
        rendered_text="",
        amount_field_elements=[],
    )
    page.frames = [parent]
    # No page.frame attribute

    action = {
        "type": "SELECT_NUMBER",
        "number": "11",
        "selector": "text=11",
        "frame": "mainFrame",
        "frame_url": "https://w1.gts362.com/6u_rmq1xO0G6m4bNfwLvsQ/Front/B/B03",
    }

    with pytest.raises(RuntimeError) as excinfo:
        execute_actions_on_page(page, [action])

    message = str(excinfo.value)
    assert "page_frame_method_exists=False" in message
    assert page.clicked == []
