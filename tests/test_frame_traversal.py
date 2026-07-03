"""Read-only frameset / child-frame traversal for run_selector_discovery.

Uses lightweight fake page/frame doubles (no live browser). Any attempt to
interact with the page (click/fill/press/...) raises, so these tests also prove
the scan path is strictly read-only.
"""
from pathlib import Path

from betguard.webfill.selector_discovery import (
    _collect_all_frames,
    _collect_scan_frames,
    _collect_selector_elements,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "betguard" / "webfill" / "selector_discovery.py"


class FakeFrame:
    def __init__(self, url="", name="", children=None, items=None, evaluate_error=False):
        self.url = url
        self.name = name
        self._children = list(children or [])
        self._items = list(items) if items is not None else []
        self._evaluate_error = evaluate_error

    @property
    def child_frames(self):
        return list(self._children)

    def evaluate(self, script):  # read-only DOM read
        if self._evaluate_error:
            raise RuntimeError("frame document not accessible")
        return [dict(item) for item in self._items]

    # Interaction must never happen during a read-only scan.
    def _forbid(self, *args, **kwargs):
        raise AssertionError("interaction attempted on frame")

    click = fill = press = type = check = uncheck = select_option = tap = _forbid


class FakePage:
    def __init__(self, frames_sequence=None, frames=None, main_frame=None):
        self._frames_sequence = frames_sequence
        self._frames = frames
        self._read = 0
        self.main_frame = main_frame
        self.settled = 0

    @property
    def frames(self):
        if self._frames_sequence is not None:
            idx = min(self._read, len(self._frames_sequence) - 1)
            self._read += 1
            return list(self._frames_sequence[idx])
        return list(self._frames or [])

    def wait_for_load_state(self, *args, **kwargs):
        self.settled += 1

    def wait_for_timeout(self, *args, **kwargs):
        pass

    def _forbid(self, *args, **kwargs):
        raise AssertionError("interaction attempted on page")

    click = fill = press = type = _forbid


def input_item(**over) -> dict:
    base = {
        "tag": "input",
        "id": "GroupSet_Value",
        "value": "1",
        "parentText": "每碰金額 二星",
        "visible": True,
        "box": {"w": 80, "h": 20, "top": 5, "left": 5},
        "display": "inline-block",
        "visibilityCss": "visible",
        "hidden": False,
        "ariaHidden": "",
        "amountTableAnchor": "amountTbl",
        "amountTableInputIndex": 0,
        "containerLabel": "每碰金額 二星 三星 四星",
        "index": 3,
    }
    base.update(over)
    return base


# 1. child frame documents are included (recursion)
def test_collect_all_frames_includes_child_frames() -> None:
    c1 = FakeFrame(url="https://s/Front/B/B03", name="content")
    c2 = FakeFrame(url="https://s/menu", name="menu")
    main = FakeFrame(url="https://s/", name="", children=[c1, c2])
    page = FakePage(frames=[main], main_frame=main)

    frames = _collect_all_frames(page, [])
    assert frames == [main, c1, c2]
    assert len(frames) > 1  # root frameset alone is not the whole story


# 2. late-attaching frameset children are captured by re-polling
def test_scan_frames_repolls_for_late_frameset_children() -> None:
    main = FakeFrame(url="https://s/", name="")  # no children on first read
    c1 = FakeFrame(url="https://s/Front/B/B03", name="content")
    c2 = FakeFrame(url="https://s/menu", name="menu")
    # First enumeration sees only the root; after a settle, children appear.
    page = FakePage(frames_sequence=[[main], [main, c1, c2]], main_frame=main)

    frames = _collect_scan_frames(page, [])
    assert len(frames) == 3
    assert c1 in frames and c2 in frames
    assert page.settled >= 1  # a read-only settle happened before re-polling


# 3. frame metadata + enrichment fields pass through for input elements
def test_child_frame_elements_preserve_metadata_and_enrichment() -> None:
    frame = FakeFrame(url="https://s/Front/B/B03", name="mainFrame", items=[input_item()])
    elements = _collect_selector_elements(
        frame,
        "https://s/Front/B/B03",
        1,
        2,
        [],
        source_url="https://s/",
        source_kind="active_page",
        frame_name="mainFrame",
    )
    assert len(elements) == 1
    el = elements[0]
    # metadata preserved
    assert el["frame_url"] == "https://s/Front/B/B03"
    assert el["source_url"] == "https://s/"
    assert el["frame_name"] == "mainFrame"
    assert el["frame_index"] == 2
    assert el["source_kind"] == "active_page"
    # enrichment passes through
    assert el["visible"] is True
    assert el["box"] == {"w": 80, "h": 20, "top": 5, "left": 5}
    assert el["amountTableAnchor"] == "amountTbl"
    assert el["amountTableInputIndex"] == 0
    assert el["id"] == "GroupSet_Value"


# 4. inaccessible frame fails closed (empty + bounded warning), no crash
def test_inaccessible_frame_fails_closed() -> None:
    bad = FakeFrame(url="https://s/x", name="x", evaluate_error=True)
    warnings: list[str] = []
    elements = _collect_selector_elements(
        bad, "https://s/x", 1, 3, warnings, source_url="https://s/", source_kind="active_page", frame_name="x"
    )
    assert elements == []
    assert any("element scan failed" in w for w in warnings)
    assert len(warnings) == 1


# 5. root frameset alone is not sufficient: child frame's inputs get captured
def test_child_frame_inputs_captured_when_root_has_none() -> None:
    root = FakeFrame(url="https://s/", name="", items=[])  # frameset shell: no inputs
    child = FakeFrame(url="https://s/Front/B/B03", name="content", items=[input_item()])
    root._children = [child]
    page = FakePage(frames=[root], main_frame=root)

    all_ids: list[str] = []
    for i, frame in enumerate(_collect_scan_frames(page, []), start=1):
        for el in _collect_selector_elements(
            frame, frame.url, 1, i, [], source_url="https://s/", source_kind="active_page", frame_name=frame.name
        ):
            all_ids.append(el["id"])
    assert "GroupSet_Value" in all_ids  # only reachable by descending into the child frame


# 6. the scan path never interacts with the page
def test_scan_path_is_read_only() -> None:
    child = FakeFrame(url="https://s/Front/B/B03", name="content", items=[input_item()])
    root = FakeFrame(url="https://s/", name="", children=[child])
    page = FakePage(frames=[root], main_frame=root)
    # FakePage/FakeFrame raise AssertionError on any interaction; completing the
    # scan without raising proves read-only behavior.
    for i, frame in enumerate(_collect_scan_frames(page, []), start=1):
        _collect_selector_elements(
            frame, frame.url, 1, i, [], source_url="https://s/", source_kind="active_page", frame_name=frame.name
        )
    # Static backstop: the discovery module itself contains no interaction calls.
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in (".click(", ".fill(", ".press(", ".type(", ".check(", ".select_option(", ".submit(", ".tap("):
        assert token not in source
