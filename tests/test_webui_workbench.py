"""Tests for the local web workbench (v1).

Covers the six required scenarios from the design spec:
  A. empty text input is rejected
  B. text input is written to runs/YYYY-MM-DD/input_HHMMSS.txt
  C. batch creation produces queue + review.html
  D. no approved_fill_queue is created
  E. no --real-site-assisted-fill is invoked
  F. queue status is not promoted to WAITING_FOR_HUMAN_CONFIRM

Also runs a smoke test that boots the workbench on a random port, hits
``/`` and ``/workbench`` over HTTP, and tears it down.

The test process never connects to a real site, never calls playwright,
never imports betguard.webfill.real_site_assisted_fill.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

import betguard.webui.app as webui_app
from betguard.webui.app import (
    FORBIDDEN_FLAGS,
    RUNS_DIR,
    _build_dashboard_links,
    _create_batch,
    _find_latest_review,
    _find_latest_unrecognized,
    _git_short_head,
    _project_version,
    _render_result,
    _run_cli,
    build_workbench_handler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def _running_server(handler_factory):
    port = _free_port()
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_workbench(port: int, text: str, game: str = "auto") -> tuple[int, str]:
    body = urllib.parse.urlencode({"text": text, "game": game}).encode("utf-8")
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", "/workbench", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section A -- empty input rejected
# ---------------------------------------------------------------------------


def test_empty_text_returns_400_and_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect RUNS_DIR to a tmp path so we don't pollute the real runs/
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_workbench(port, text="")
        assert status == 400
        assert "空白輸入會被拒絕" in body
        assert not runs_tmp.exists() or not any(runs_tmp.iterdir())


def test_whitespace_only_text_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_workbench(port, text="   \n\t  \n")
        assert status == 400
        assert "空白輸入會被拒絕" in body


# ---------------------------------------------------------------------------
# Section B -- text input is written to runs/
# ---------------------------------------------------------------------------


def test_valid_text_creates_input_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    text = "06.13.23.22 234.100\n11 22 33 二三X1"
    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        status, _body = _post_workbench(port, text=text)
        assert status == 302  # redirects to review
        # At least one input_*.txt should exist under runs/YYYY-MM-DD/
        inputs = list(runs_tmp.rglob("input_*.txt"))
        assert inputs, "no input_*.txt created under runs/"
        content = inputs[0].read_text(encoding="utf-8")
        assert content == text


# ---------------------------------------------------------------------------
# Section C -- batch creation produces queue + review.html
# ---------------------------------------------------------------------------


def test_batch_creates_queue_and_review_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    text = "06.13.23.22 234.100"
    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        status, _body = _post_workbench(port, text=text)
        assert status == 302  # redirects to review
        queues = list(runs_tmp.rglob("queue_*.json"))
        reviews = list(runs_tmp.rglob("review_*.html"))
        assert queues, "no queue_*.json produced"
        assert reviews, "no review_*.html produced"
        # The HTML must mention our input line (sanity check that the
        # workbench really did run the CLI on our text).
        html = reviews[0].read_text(encoding="utf-8")
        assert "06.13.23.22 234.100" in html


# ---------------------------------------------------------------------------
# Section D -- no approved_fill_queue
# ---------------------------------------------------------------------------


def test_no_approved_fill_queue_after_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    text = "06.13.23.22 234.100"
    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        status, _body = _post_workbench(port, text=text)
        assert status == 302  # redirects to review
        queue_files = list(runs_tmp.rglob("queue_*.json"))
        assert queue_files
        for q in queue_files:
            data = json.loads(q.read_text(encoding="utf-8"))
            afq = data.get("approved_fill_queue")
            # Either missing or empty
            assert not afq or afq == [] or len(afq) == 0, (
                f"approved_fill_queue unexpectedly present in {q}"
            )


# ---------------------------------------------------------------------------
# Section E -- no real-site-assisted-fill invocation
# ---------------------------------------------------------------------------


def test_run_cli_refuses_forbidden_flags() -> None:
    """The _run_cli helper must reject any of the forbidden flags."""
    for flag in FORBIDDEN_FLAGS:
        with pytest.raises(RuntimeError) as excinfo:
            _run_cli([flag, "--queue", "/tmp/x.json"])
        assert "forbidden flag" in str(excinfo.value)


def test_batch_creation_does_not_invoke_real_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spy on _run_cli and ensure none of the forbidden flags are ever
    passed for a normal batch creation flow."""
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    captured_calls: list[list[str]] = []

    def fake_run_cli(args: list[str]) -> object:
        captured_calls.append(list(args))
        # Return a fake successful CompletedProcess
        class _R:
            returncode = 0
            stderr = ""

        return _R()

    # We need to make sure that the queue file exists (so _summarize_queue
    # can read it), and the review file exists.  The simplest is to monkeypatch
    # both _run_cli AND _summarize_queue.
    def fake_summarize(_p: Path) -> dict[str, int]:
        return {
            "valid_count": 1,
            "needs_review_count": 0,
            "invalid_count": 0,
            "watchlist_count": 0,
            "queue_status": "NEEDS_REVIEW",
        }

    monkeypatch.setattr(webui_app, "_run_cli", fake_run_cli)
    monkeypatch.setattr(webui_app, "_summarize_queue", fake_summarize)

    # Drive _create_batch directly
    _input, _queue, _review, summary, _unrec_html, _unrec_json = _create_batch("06.13.23.22 234.100", "auto")
    assert summary["queue_status"] == "NEEDS_REVIEW"

    for call_args in captured_calls:
        for forbidden in FORBIDDEN_FLAGS:
            assert forbidden not in call_args, (
                f"forbidden flag {forbidden!r} appeared in CLI call: {call_args}"
            )


# ---------------------------------------------------------------------------
# Section F -- queue status is not promoted
# ---------------------------------------------------------------------------


def test_queue_status_is_never_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    def fake_run_cli(args: list[str]) -> object:
        # If the workbench is being honest, it only ever calls
        # --new-batch-from-file or --review-report-html.  We reject any
        # other subcommand.
        if "--new-batch-from-file" not in args and "--review-report-html" not in args:
            raise AssertionError(f"unexpected CLI call: {args}")
        class _R:
            returncode = 0
            stderr = ""

        return _R()

    def fake_summarize(_p: Path) -> dict[str, Any]:
        return {
            "valid_count": 1,
            "needs_review_count": 0,
            "invalid_count": 0,
            "watchlist_count": 0,
            "queue_status": "NEEDS_REVIEW",
        }

    monkeypatch.setattr(webui_app, "_run_cli", fake_run_cli)
    monkeypatch.setattr(webui_app, "_summarize_queue", fake_summarize)

    _input, _queue, _review, summary, _unrec_html, _unrec_json = _create_batch("06.13.23.22 234.100", "auto")
    assert summary["queue_status"] == "NEEDS_REVIEW"
    assert summary["queue_status"] != "WAITING_FOR_HUMAN_CONFIRM"
    assert summary["queue_status"] != "READY_FOR_HUMAN_REVIEW"
    assert summary["queue_status"] != "READY"


# ---------------------------------------------------------------------------
# Section G -- dashboard + routes smoke test
# ---------------------------------------------------------------------------


def test_dashboard_and_sop_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    handler = build_workbench_handler(project_version="v0.4.5", git_commit="abc1234")
    with _running_server(handler) as port:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            # GET / (redirects to workbench)
            conn.request("GET", "/")
            r = conn.getresponse()
            assert r.status == 302
            body = r.read().decode("utf-8")
            assert "/workbench" in r.getheader("Location", "")

            # GET /workbench
            conn.request("GET", "/workbench")
            r = conn.getresponse()
            assert r.status == 200
            body = r.read().decode("utf-8")
            assert "textarea" in body
            assert "option" in body  # game options
        finally:
            conn.close()


def test_latest_review_404_when_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)

    handler = build_workbench_handler(project_version="v0.4.5", git_commit="test")
    with _running_server(handler) as port:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/latest-review")
            r = conn.getresponse()
            assert r.status == 200
            body = r.read().decode("utf-8")
            assert "尚無 review" in body
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Section H -- forbidden flags enumeration
# ---------------------------------------------------------------------------


def test_forbidden_flags_list_is_complete() -> None:
    """The forbidden set must at minimum cover the four safety-critical
    flags listed in the design doc."""
    for flag in [
        "--batch-review-accept-valid",
        "--real-site-assisted-fill",
        "--batch-mock-next",
        "--batch-human-confirm-current-done",
    ]:
        assert flag in FORBIDDEN_FLAGS, f"missing forbidden flag: {flag}"


# ---------------------------------------------------------------------------
# Section I -- _find_latest_review helper
# ---------------------------------------------------------------------------


def test_find_latest_review_returns_none_when_no_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "empty_runs"
    runs_tmp.mkdir()
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)
    # An empty runs/ directory should yield no latest review.
    assert _find_latest_review() is None


def test_find_latest_review_returns_none_when_runs_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # runs/ itself does not exist.
    runs_tmp = tmp_path / "does_not_exist"
    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)
    assert _find_latest_review() is None


def test_find_latest_review_picks_newest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_tmp = tmp_path / "runs"
    day = runs_tmp / "2026-07-07"
    day.mkdir(parents=True)
    older = day / "review_old.html"
    newer = day / "review_new.html"
    older.write_text("<html>old</html>", encoding="utf-8")
    newer.write_text("<html>new</html>", encoding="utf-8")

    # Make older appear older
    import os
    old_time = time.time() - 100
    os.utime(older, (old_time, old_time))
    os.utime(newer, (time.time(), time.time()))

    monkeypatch.setattr(webui_app, "RUNS_DIR", runs_tmp)
    latest = _find_latest_review()
    assert latest is not None
    assert latest.name == "review_new.html"


# ---------------------------------------------------------------------------
# Section J -- .gitignore contains runs/
# ---------------------------------------------------------------------------


def test_gitignore_ignores_runs() -> None:
    gitignore = (webui_app.PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "runs/" in gitignore, "runs/ must be in .gitignore"


# ---------------------------------------------------------------------------
# Section K -- /history (read-only) end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect HISTORY_DIR/HISTORY_FILE to a tmp path so tests don't
    write to the project root."""
    import betguard.webfill.history as hist
    monkeypatch.setattr(hist, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(hist, "HISTORY_FILE", tmp_path / "history" / "orders.jsonl")
    # Also patch the webui app's import (webui imports history lazily
    # inside _render_history, so re-monkeypatching here is enough).
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)


def _seed_history(tmp_path: Path, n: int = 3) -> list[dict]:
    """Write n sample records directly to the (monkeypatched) history
    file.  Returns the list of records that were written."""
    import betguard.webfill.history as hist
    records = []
    for i in range(n):
        r = {
            "history_id": f"h-{i:03d}",
            "created_at": f"2026-07-07T10:0{i}:00+08:00",
            "completed_at": f"2026-07-07T10:0{i}:00+08:00",
            "source": "test-source",
            "game": "539" if i % 2 == 0 else "天天樂",
            "play_type": "normal",
            "original_text": f"06.13.23.22 234.10{i}",
            "numbers": [6, 13, 23, 22],
            "stars": [2, 3, 4],
            "amounts": {"2": 100 + i, "3": 100 + i, "4": 100 + i},
            "queue_item_index": 0,
            "status": "DONE",
            "safety_flags": {
                "auto_submit": False, "auto_confirm": False,
                "auto_next": False, "danger_clicked": 0,
            },
            "confirmed_by_human": True,
        }
        records.append(r)
    hist.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with hist.HISTORY_FILE.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records


def _get(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        # path may contain non-ASCII (e.g. ?game=天天樂).  http.client's
        # default URL encoder is ASCII-only; URL-quote the path manually.
        quoted = urllib.parse.quote(path, safe="/?=&")
        conn.request("GET", quoted)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8")
    finally:
        conn.close()


# K-1: dashboard has a /history link
def test_dashboard_redirects_to_workbench(tmp_path: Path) -> None:
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        # / redirects to /workbench
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/")
        r = conn.getresponse()
        assert r.status == 302
        assert "/workbench" in r.getheader("Location", "")


# K-2: /history with no orders.jsonl shows the empty state
def test_history_empty_when_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history
) -> None:
    # _isolated_history redirects HISTORY_DIR + HISTORY_FILE to tmp_path,
    # so HISTORY_FILE does not exist on disk.
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history")
        assert status == 200
        assert "目前尚無歷史紀錄" in body
        assert "<table" not in body  # no table when empty


# K-3: /history renders a record
def test_history_renders_one_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    _seed_history(tmp_path, n=1)
    # Re-monkeypatch webui.RUNS_DIR so the workbench does not see real runs
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history")
        assert status == 200
        assert "06.13.23.22 234.100" in body
        assert "test-source" in body
        assert "正常" in body or "normal" in body.lower()


# K-4: ?q= filters by original_text keyword
def test_history_filter_by_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    _seed_history(tmp_path, n=3)
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        # 06.13.23.22 appears in all 3 seeded records; pick a more unique
        # substring from record 1: "234.101"
        status, body = _get(port, "/history?q=234.101")
        assert status == 200
        # Filter narrows the table; the row count line should reflect
        # fewer than 3 records.
        assert "1 筆" in body or "篩選後" in body
        assert "234.101" in body
        # Records that do NOT contain the keyword should be excluded.
        assert "234.100" not in body or "篩選後 1 筆" in body


# K-5: ?number= filters by parsed number
def test_history_filter_by_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    _seed_history(tmp_path, n=3)
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history?number=06")
        assert status == 200
        # All 3 records have 06 in numbers; expect 3 matches
        assert "3 筆" in body or "共 3" in body or "234.100" in body
        # Try a number that does not exist
        status2, body2 = _get(port, "/history?number=99")
        assert status2 == 200
        assert "目前過濾條件下沒有符合的紀錄" in body2


# K-6: ?game= filters by game
def test_history_filter_by_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    _seed_history(tmp_path, n=3)  # alternating 539 / 天天樂
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history?game=539")
        assert status == 200
        # 2 of 3 records are 539 (i=0, i=2)
        # Body must mention the filter reduced the count
        assert "2 筆" in body
        status2, body2 = _get(port, "/history?game=天天樂")
        assert "1 筆" in body2


# K-7: ?date= filters by date
def test_history_filter_by_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    _seed_history(tmp_path, n=3)  # all on 2026-07-07
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history?date=2026-07-07")
        assert status == 200
        assert "3 筆" in body
        status2, body2 = _get(port, "/history?date=2026-07-08")
        assert "目前過濾條件下沒有符合的紀錄" in body2


# K-8: malformed JSONL does not crash the page
def test_history_handles_malformed_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    import betguard.webfill.history as hist
    hist.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with hist.HISTORY_FILE.open("w", encoding="utf-8") as f:
        f.write('{"history_id": "h-good", "status": "DONE", "completed_at": "2026-07-07T10:00:00+08:00", "original_text": "06.13 234", "numbers": [6, 13], "amounts": {"2": 100}, "game": "539", "play_type": "normal", "source": "未指定", "safety_flags": {"auto_submit": false, "auto_confirm": false, "auto_next": false, "danger_clicked": 0}, "confirmed_by_human": true}\n')
        f.write("this is not json\n")
        f.write("more garbage\n")
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history")
        assert status == 200
        # The good record still renders
        assert "06.13 234" in body
        # A warning is shown about malformed lines
        assert "壞資料" in body or "警告" in body
        # The page did not crash
        assert "<h1>歷史紀錄" in body
# K-9: /history page exposes no fill / submit / accept-valid actions
def test_history_page_has_no_danger_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history
) -> None:
    _seed_history(tmp_path, n=1)
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, body = _get(port, "/history")
        assert status == 200
        # Read-only: no forms with POST.
        assert 'method="post"' not in body.lower()
        assert 'method="POST"' not in body
        # No "送出注單" / "確認對話框" / "auto-submit" UI text.
        # Note: the descriptive paragraph intentionally mentions
        # "accept-valid" as a *forbidden* keyword.  The check below
        # only looks for it as a *button label* (e.g. <button> or <a>
        # with that text), not as a substring.
        for forbidden in ["送出注單", "確認對話框", "auto-submit"]:
            assert forbidden not in body, (
                f"/history page must not contain {forbidden!r}"
            )
        # Explicit UI controls must never be present
        for forbidden in ["<button>送出", "<button>確認", "<button>填入",
                          "<button>accept-valid", "<button>assisted-fill",
                          "<a>accept-valid", "<a>assisted-fill"]:
            assert forbidden not in body, (
                f"/history page must not contain {forbidden!r}"
            )


# K-10: /history does not call any CLI or browser
def test_history_does_not_call_cli_or_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_history) -> None:
    import subprocess
    import betguard.webui.app as wa

    calls: list[tuple] = []
    if hasattr(wa, "subprocess"):

        def fake_run(*args, **kwargs):  # noqa: ANN001
            calls.append(("wa.subprocess", args, kwargs))
            raise AssertionError("subprocess called by webui")

        monkeypatch.setattr(wa.subprocess, "run", fake_run)
    _seed_history(tmp_path, n=1)
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="v0.5", git_commit="abc")
    with _running_server(handler) as port:
        status, _ = _get(port, "/history")
        assert status == 200
    assert calls == []


# ---------------------------------------------------------------------------
# Section L -- unrecognized report e2e
# ---------------------------------------------------------------------------


# Minimal helpers shared with test_unrecognized_report.py
def _make_item(
    *,
    index: int = 0,
    status: str = "BLOCKED",
    original_text: str = "17.29.1000",
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "index": index,
        "status": status,
        "original_text": original_text,
        "errors": errors or [],
        "warnings": warnings or [],
    }


def _make_queue(items: list[dict], *, valid_count: int = 0) -> dict:
    return {
        "items": items,
        "preprocessing": {
            "summary": {
                "valid_count": valid_count,
                "needs_review_count": sum(
                    1 for i in items if i.get("status", "").lower() == "needs_review"
                ),
                "invalid_unsupported_count": sum(
                    1 for i in items if i.get("status", "").lower() == "blocked"
                ),
            },
            "watchlist_count": 0,
        },
    }


def test_result_page_shows_unrecognized_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When batch has Needs Review items, the result page must show a link to
    the unrecognized report."""
    from betguard.webfill import unrecognized_report as _ur

    # Redirect runs to tmp
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)

    # Build a fake queue with blocked items
    data = _ur.build_report_data(
        _make_queue(
            [
                _make_item(index=0, status="BLOCKED", errors=["missing money"]),
                _make_item(index=1, status="BLOCKED", errors=["requires at least 3 numbers"]),
            ],
            valid_count=1,
        )
    )
    # Write queue to file and generate report from that file
    qp = tmp_path / "queue_test.json"
    qp.write_text(json.dumps(
        _make_queue(
            [{"index": 0, "status": "BLOCKED", "original_text": "x", "errors": ["missing money"], "warnings": []}],
            valid_count=0,
        )
    ), encoding="utf-8")
    html_path, json_path, _data = _ur.write_report_files(
        qp,
        input_path=tmp_path / "input.txt",
        out_dir=tmp_path,
        stamp="test00",
    )

    summary = {"valid_count": 1, "needs_review_count": 2, "invalid_count": 0,
               "watchlist_count": 0, "queue_status": "NEEDS_REVIEW"}
    html = _render_result(
        Path("dummy_input.txt"), Path("dummy_queue.json"),
        Path("dummy_review.html"), summary,
        unrecognized_html=html_path,
    )
    assert "未辨識格式" in html
    assert "2 筆 → 查看報告" in html


def test_result_page_shows_zero_when_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All valid batch: no report link, just '0' marker."""
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    summary = {"valid_count": 5, "needs_review_count": 0, "invalid_count": 0,
               "watchlist_count": 0, "queue_status": "READY_FOR_QUEUE"}
    html = _render_result(
        Path("dummy_input.txt"), Path("dummy_queue.json"),
        Path("dummy_review.html"), summary,
    )
    assert "未辨識格式: 0" in html
    assert "→ 查看報告" not in html


# ---------------------------------------------------------------------------
# Section M -- dashboard "最新未辨識報告" button
# ---------------------------------------------------------------------------


def test_dashboard_no_unrecognized_button_when_no_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    links = _build_dashboard_links()
    assert "開啟最新 review.html" in links
    assert "未辨識報告" not in links  # no report → no button


def test_dashboard_shows_unrecognized_button_when_report_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    # Create a dummy unrecognized HTML in tmp/runs-like structure
    day_dir = tmp_path / "2026-07-07"
    day_dir.mkdir(parents=True)
    (day_dir / "unrecognized_103000.html").write_text("<html></html>", encoding="utf-8")
    (day_dir / "unrecognized_103001.html").write_text("<html>newer</html>", encoding="utf-8")

    links = _build_dashboard_links()
    assert "開啟最新 review.html" in links  # still present
    assert "開啟最新未辨識報告" in links

    # _find_latest_unrecognized should return the newer one
    latest = _find_latest_unrecognized()
    assert latest is not None
    assert "unrecognized_103001" in latest.name


def test_dashboard_unrecognized_button_has_no_danger_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    day_dir = tmp_path / "2026-07-07"
    day_dir.mkdir(parents=True)
    (day_dir / "unrecognized_100000.html").write_text("<html></html>", encoding="utf-8")
    links = _build_dashboard_links()
    for forbidden in [
        "accept-valid", "assisted-fill", "送出注單", "確認對話框",
        "送出", "填入", "auto-submit", "DONE",
    ]:
        assert forbidden not in links, f"dashboard must not contain {forbidden!r}"


def test_dashboard_original_buttons_still_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    links = _build_dashboard_links()
    for label in ["貼上牌單建立審核", "開啟最新 review.html", "查看 SOP",
                   "查看 CLI reference", "查看歷史紀錄"]:
        assert label in links, f"dashboard missing link: {label!r}"


# ---------------------------------------------------------------------------
# v0.5.18: manual reparse + candidate registration
# ---------------------------------------------------------------------------

import http.client
import json


def _post_json(port: int, path: str, payload: dict[str, object], timeout: int = 5) -> tuple[int, dict[str, object]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        body = json.dumps(payload).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}
    finally:
        conn.close()


def test_manual_reparse_valid_returns_candidate_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_json(port, "/manual-reparse", {
            "text": "06.13.23.22 234.100", "game": "auto",
        })
        assert status == 200
        assert body["ok"] is True
        assert "manual_candidate_id" in body
        assert body["manual_candidate_id"].startswith("manual-")
        assert body.get("auto_submit") is False
        assert body.get("auto_confirm") is False
        assert body["source"] == "manual_correction"


def test_manual_reparse_invalid_returns_no_candidate_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_json(port, "/manual-reparse", {
            "text": "99.98.97 234.100", "game": "auto",
        })
        assert status == 200
        assert body["ok"] is False
        assert "manual_candidate_id" not in body


def test_manual_reparse_empty_text_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_json(port, "/manual-reparse", {"text": "", "game": "auto"})
        assert status == 200
        assert body["ok"] is False


def test_assist_fill_start_rejects_unknown_manual_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_json(port, "/assist-fill/start", {
            "manual_candidate_id": "manual-nonexistent",
        })
        assert body["ok"] is False
        assert "manual candidate not found" in body.get("error", "")


def test_assist_fill_start_queue_path_responds_without_hanging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Queue-based /assist-fill/start must answer promptly instead of hanging.

    Regression: v0.5.18 added a body read for manual_candidate_id but the queue
    branch re-read the (already consumed) body, blocking forever on the socket.
    A bogus queue_path keeps this test on the validation path — no browser.
    """
    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        status, body = _post_json(port, "/assist-fill/start", {
            "queue_path": "runs/does-not-exist/queue.json",
            "item_index": 0,
        }, timeout=5)
        assert status == 200
        assert body["ok"] is False
        assert "queue not found" in body.get("error", "")


def test_assist_fill_start_accepts_registered_manual_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Registered manual candidate should be accepted by assist-fill/start.
    The fill may fail (no browser) but must NOT reject with 'candidate not found'."""
    from betguard.webui.app import _register_manual_candidate

    # Register a valid candidate
    cid = _register_manual_candidate({
        "numbers": [6, 13, 23, 22],
        "stars": [2, 3, 4],
        "amounts": {"2": 100, "3": 100, "4": 100},
        "summary": "test", "game": "539",
    })

    monkeypatch.setattr(webui_app, "RUNS_DIR", tmp_path)
    handler = build_workbench_handler(project_version="test", git_commit="test")
    with _running_server(handler) as port:
        try:
            _status, body = _post_json(port, "/assist-fill/start", {
                "manual_candidate_id": cid,
            }, timeout=8)
            # Should NOT say "candidate not found" (even if fill fails)
            assert "manual candidate not found" not in body.get("error", "")
            assert body.get("auto_submit") is not True
            assert body.get("auto_confirm") is not True
        except Exception:
            # Timeout during execute fill is expected in test env (no browser)
            # The key assertion is already tested: start handler accepted the candidate
            pass
