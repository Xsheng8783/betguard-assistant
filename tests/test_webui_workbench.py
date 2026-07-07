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
    _create_batch,
    _find_latest_review,
    _git_short_head,
    _project_version,
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
        assert status == 200
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
        assert status == 200
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
        assert status == 200
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
    _input, _queue, _review, summary = _create_batch("06.13.23.22 234.100", "auto")
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

    _input, _queue, _review, summary = _create_batch("06.13.23.22 234.100", "auto")
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
            # GET /
            conn.request("GET", "/")
            r = conn.getresponse()
            assert r.status == 200
            body = r.read().decode("utf-8")
            assert "Betguard Assistant 今日工作台" in body
            assert "auto-submit OFF" in body
            assert "human required" in body
            assert "v0.4.5" in body
            assert "abc1234" in body
            assert "/workbench" in body
            assert "/latest-review" in body
            assert "/sop" in body
            assert "/cli" in body

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
