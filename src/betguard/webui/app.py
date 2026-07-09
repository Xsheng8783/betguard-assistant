"""Betguard local web workbench.

Pure-stdlib HTTP server that:

  * Serves a small dashboard at ``GET /``
  * Lets the user paste betting text and pick a game at ``GET /workbench``
  * On ``POST /workbench`` runs the existing ``--new-batch-from-file`` and
    ``--review-report-html`` flows and writes the artifacts to
    ``runs/YYYY-MM-DD/`` next to the project root
  * Serves the docs at ``/sop`` and ``/cli`` for quick reference

HARD RULES enforced in this file:

  * No ``--batch-review-accept-valid`` is ever invoked
  * No ``--real-site-assisted-fill`` / ``--real-site-fill-plan`` /
    ``--real-site-fill-preflight`` / ``--real-site-assisted-fill-all``
  * No ``--batch-mock-next`` / ``--batch-human-confirm-current-done``
  * Queue status is never promoted past ``NEEDS_REVIEW``
  * No browser / playwright / Selenium is imported
  * Bound to loopback by default; ``--host 0.0.0.0`` prints a warning

The server is intentionally tiny: one handler class, HTML strings inline,
no template engine, no external dependency.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
SOP_PATH = PROJECT_ROOT / "docs" / "sop_assisted_fill_complete.md"
CLI_REF_PATH = PROJECT_ROOT / "docs" / "cli_reference.md"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

ALLOWED_GAME_OPTIONS = {"auto", "539", "天天樂", "zhupeng"}

# Flags that the workbench will NEVER pass to the CLI, even if a test or
# refactor accidentally tries to.  See HARD RULES in the module docstring.
FORBIDDEN_FLAGS = frozenset(
    {
        "--batch-review-accept-valid",
        "--batch-mock-next",
        "--batch-human-confirm-current-done",
        "--real-site-assisted-fill",
        "--real-site-assisted-fill-all",
        "--real-site-fill-plan",
        "--real-site-fill-preflight",
        "--real-site-fill-readiness",
        "--assist-fill",
        "--mock-assisted-fill",
    }
)


# ---------------------------------------------------------------------------
# HTML helpers (minimal, inline CSS)
# ---------------------------------------------------------------------------

_HTML_HEAD = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
         margin: 2em auto; max-width: 880px; padding: 0 1em; color: #222; }}
  h1 {{ font-size: 1.4em; border-bottom: 2px solid #444; padding-bottom: 0.2em; }}
  .safety {{ background: #fff5f5; border: 1px solid #c33; border-radius: 6px;
            padding: 0.8em 1.2em; margin: 1em 0; }}
  .safety ul {{ margin: 0.3em 0; padding-left: 1.4em; }}
  .links a {{ display: inline-block; margin: 0.3em 0.6em 0.3em 0;
             padding: 0.4em 1em; background: #2563eb; color: #fff;
             border-radius: 4px; text-decoration: none; }}
  .links a.danger {{ background: #6b7280; }}
  .links a:hover {{ background: #1d4ed8; }}
  pre {{ background: #f3f4f6; padding: 0.8em; border-radius: 4px; overflow-x: auto; }}
  table {{ border-collapse: collapse; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: left; }}
  th {{ background: #f3f4f6; }}
  textarea {{ width: 100%; min-height: 12em; font-family: ui-monospace, monospace; }}
  select, button {{ font-size: 1em; padding: 0.3em 0.6em; }}
  button {{ background: #2563eb; color: #fff; border: 0; border-radius: 4px;
            padding: 0.5em 1.2em; cursor: pointer; }}
  button:hover {{ background: #1d4ed8; }}
  .footer {{ margin-top: 2em; color: #6b7280; font-size: 0.9em; }}
</style>
</head>
<body>
"""

_HTML_FOOTER = """
<div class="footer">
  本工作台只建立審核批次, 不會接觸真網站, 不會自動送出 / 確認 / 換下一筆.
  <a href="/">回到首頁</a>
</div>
</body>
</html>
"""


def _runs_url(rel: Path) -> str:
    """Convert a path relative to RUNS_DIR into a POSIX-style URL segment.

    On Windows, ``Path.relative_to()`` produces backslashes which must be
    normalised to forward slashes for valid HTTP URLs.
    """
    return str(rel).replace("\\", "/")


def _build_dashboard_links() -> str:
    """Return the dashboard button bar HTML, with optional unrecognized link."""
    lines = [
        '<div class="links">',
        '  <a href="/workbench">貼上牌單建立審核</a>',
        '  <a class="danger" href="/latest-review">開啟最新 review.html</a>',
    ]
    latest_unrec = _find_latest_unrecognized()
    if latest_unrec:
        rel = _runs_url(latest_unrec.relative_to(RUNS_DIR))
        lines.append(
            f'  <a class="danger" href="/runs/{urllib.parse.quote(rel)}">'
            f"開啟最新未辨識報告</a>"
        )
    lines += [
        '  <a class="danger" href="/sop">查看 SOP</a>',
        '  <a class="danger" href="/cli">查看 CLI reference</a>',
        '  <a class="danger" href="/history">查看歷史紀錄</a>',
        '</div>',
    ]
    return "\n".join(lines)


def _render_dashboard(version: str, git_commit: str) -> str:
    safety_html = """
<div class="safety">
  <strong>安全提醒 (本工具永遠不啟用):</strong>
  <ul>
    <li>auto-submit OFF</li>
    <li>auto-confirm OFF</li>
    <li>auto-next OFF</li>
    <li>human required (送出 / 確認 / 換下一筆 必須由人工執行)</li>
  </ul>
</div>
"""
    links_html = _build_dashboard_links()
    version_html = f"""
<p>
  <strong>版本:</strong> {version}<br>
  <strong>git commit:</strong> <code>{git_commit}</code>
</p>
"""
    body = f"""
<h1>Betguard Assistant 今日工作台</h1>
{version_html}
{safety_html}
{links_html}
"""
    return _HTML_HEAD.format(title="Betguard 本地工作台") + body + _HTML_FOOTER


def _render_workbench_form(error: str | None = None) -> str:
    error_block = (
        f'<p style="color:#c33"><strong>{error}</strong></p>' if error else ""
    )
    form = """
<form method="post" action="/workbench">
  <p>
    <label for="text">牌單文字 (LINE / 聊天室內容):</label><br>
    <textarea name="text" id="text" placeholder="例: 06.13.23.22 234.100
11 22 33 二三X1"></textarea>
  </p>
  <p>
    <label for="game">遊戲類型:</label>
    <select name="game" id="game">
      <option value="auto">自動判斷</option>
      <option value="539">539</option>
      <option value="天天樂">天天樂</option>
      <option value="zhupeng">ZhuPeng / 柱碰</option>
    </select>
  </p>
  <p><button type="submit">建立審核批次</button></p>
</form>
"""
    body = f"""
<h1>貼上牌單建立審核</h1>
{error_block}
{form}
"""
    return _HTML_HEAD.format(title="建立審核批次") + body + _HTML_FOOTER


def _render_result(
    input_path: Path,
    queue_path: Path,
    review_path: Path,
    summary: dict[str, Any],
    *,
    unrecognized_html: Path | None = None,
) -> str:
    valid = summary.get("valid_count", 0)
    needs_review = summary.get("needs_review_count", 0)
    invalid = summary.get("invalid_count", 0)
    watchlist = summary.get("watchlist_count", 0)
    queue_status = summary.get("queue_status", "?")
    review_url = f"/runs/{urllib.parse.quote(queue_path.parent.name)}/{urllib.parse.quote(queue_path.name)}".replace(
        f"/{queue_path.name}", f"/{review_path.name}"
    )
    total_unrecognized = needs_review + invalid + watchlist
    unrecognized_block_html = ""
    if total_unrecognized > 0 and unrecognized_html and unrecognized_html.exists():
        rel = _runs_url(unrecognized_html.relative_to(RUNS_DIR))
        unrecognized_block_html = (
            f'<p>未辨識格式: <a href="/runs/{urllib.parse.quote(rel)}">'
            f"{total_unrecognized} 筆 → 查看報告</a></p>"
        )
    else:
        unrecognized_block_html = "<p>未辨識格式: 0</p>"
    body = f"""
<h1>審核批次已建立</h1>
{unrecognized_block_html}
<table>
  <tr><th>input 檔</th><td><code>{input_path}</code></td></tr>
  <tr><th>queue 檔</th><td><code>{queue_path}</code></td></tr>
  <tr><th>review.html</th><td><a href="/runs/{queue_path.parent.name}/{review_path.name}">開啟</a></td></tr>
  <tr><th>queue status</th><td>{queue_status}</td></tr>
  <tr><th>valid 數量</th><td>{valid}</td></tr>
  <tr><th>needs review 數量</th><td>{needs_review}</td></tr>
  <tr><th>invalid 數量</th><td>{invalid}</td></tr>
  <tr><th>watchlist 數量</th><td>{watchlist}</td></tr>
</table>
<div class="safety">
  <strong>安全提醒:</strong> 這次批次 status 仍是 <code>{queue_status}</code>.
  不會自動升級到 approved_fill_queue, 不會進真站, 不會自動送出.
</div>
<div class="links">
  <a href="/workbench">再貼一個</a>
  <a class="danger" href="/runs/{queue_path.parent.name}/{review_path.name}">開啟 review.html</a>
  <a class="danger" href="/">回首頁</a>
</div>
"""
    return _HTML_HEAD.format(title="審核批次結果") + body + _HTML_FOOTER


def _render_doc(title: str, body: str) -> str:
    return _HTML_HEAD.format(title=title) + f"<pre>{body}</pre>" + _HTML_FOOTER


# ---------------------------------------------------------------------------
# History rendering
#
# /history is a strict read-only view.  It only ever *reads* the JSONL
# produced by betguard.webfill.history.record_human_done.  There is no
# POST / PUT / DELETE handler for history and no button that triggers
# fill / submit / accept-valid.  The page is also responsible for
# rendering a clean empty state and tolerating malformed JSONL lines.
# ---------------------------------------------------------------------------


def _render_history(
    *,
    q: str,
    number: int | str | None,
    game: str,
    date: str,
) -> str:
    # Local import to avoid import-time cycle (webui is loaded before
    # tests in some test setups).
    from betguard.webfill import history as _history

    malformed = 0  # number of lines load_all_records() had to skip
    try:
        all_records = _history.load_all_records()
    except OSError:
        all_records = []
    if not _history.HISTORY_FILE.exists():
        all_records = []

    # We have to know how many lines were skipped for the warning.
    # load_all_records() already swallows malformed lines internally;
    # we re-scan to count them for the user-facing warning.
    if _history.HISTORY_FILE.exists():
        try:
            with _history.HISTORY_FILE.open("r", encoding="utf-8") as f:
                for raw in f:
                    if not raw.strip():
                        continue
                    try:
                        import json as _json
                        _json.loads(raw)
                    except _json.JSONDecodeError:
                        malformed += 1
        except OSError:
            pass

    records = _history.filter_records(
        all_records,
        query=q or None,
        number=number,
        game=game or None,
        date=date or None,
    )

    # Filter form (always visible at the top of the page)
    q_value = _html_escape(q or "")
    game_value = _html_escape(game or "")
    number_value = _html_escape(str(number) if number is not None else "")
    date_value = _html_escape(date or "")
    filter_form = f"""
<form method="get" action="/history" class="links">
  <input type="text" name="q" value="{q_value}" placeholder="原文關鍵字" size="20">
  <input type="text" name="number" value="{number_value}" placeholder="號碼 (e.g. 06)" size="6">
  <input type="text" name="game" value="{game_value}" placeholder="玩法 (e.g. 539)" size="6">
  <input type="text" name="date" value="{date_value}" placeholder="日期 (YYYY-MM-DD)" size="10">
  <button type="submit">篩選</button>
  <a class="danger" href="/history">清除</a>
</form>
"""

    if not all_records:
        body = f"""
<h1>歷史紀錄 (v1)</h1>
{filter_form}
<p style="color:#6b7280;">目前尚無歷史紀錄. 只有「人工逐筆確認 DONE」後的單會寫入這裡, 自動批次 (--real-site-assisted-fill-all) 的 DONE 不會出現.</p>
"""
        return _HTML_HEAD.format(title="歷史紀錄") + body + _HTML_FOOTER

    if not records:
        body = f"""
<h1>歷史紀錄 (v1)</h1>
{filter_form}
<p style="color:#6b7280;">目前過濾條件下沒有符合的紀錄. 共 {len(all_records)} 筆, 篩選後 0 筆.</p>
"""
        return _HTML_HEAD.format(title="歷史紀錄") + body + _HTML_FOOTER

    rows = []
    for r in records:
        completed = _html_escape(str(r.get("completed_at", "")))
        source = _html_escape(str(r.get("source", "未指定")))
        play = _html_escape(str(r.get("play_type", "未指定")))
        original = str(r.get("original_text", ""))
        original_short = _html_escape(
            (original[:50] + "…") if len(original) > 50 else original
        )
        # numbers / columns summary
        numbers = r.get("numbers") or []
        columns = r.get("columns") or []
        if columns:
            numbers_summary = "; ".join(
                ",".join(str(int(n)) for n in col) for col in columns
            )
        else:
            numbers_summary = ",".join(str(int(n)) for n in numbers)
        numbers_summary = _html_escape(numbers_summary)
        # amount summary
        amounts = r.get("amounts") or {}
        if amounts:
            amount_summary = ", ".join(
                f"{star}={amt}" for star, amt in amounts.items()
            )
        elif r.get("money_total") is not None:
            amount_summary = _html_escape(str(r.get("money_total")))
        else:
            amount_summary = "-"
        amount_summary = _html_escape(amount_summary)
        status = _html_escape(str(r.get("status", "")))
        # Expand row: show full original_text and a one-line details dump
        detail_id = f"d-{_html_escape(str(r.get('history_id', '')))}"
        detail = _html_escape(
            "{"
            + ", ".join(
                f'"{k}": {_json_dumps(r.get(k))}'
                for k in (
                    "history_id", "created_at", "completed_at",
                    "source", "game", "play_type", "original_text",
                    "numbers", "stars", "amounts", "unit", "money_total",
                    "queue_item_index", "status", "safety_flags",
                    "queue_path", "run_folder", "confirmed_by_human",
                )
                if k in r
            )
            + "}"
        )
        rows.append(f"""
<tr>
  <td>{completed}</td>
  <td>{source}</td>
  <td>{play}</td>
  <td title="{_html_escape(original)}">{original_short}</td>
  <td>{numbers_summary}</td>
  <td>{amount_summary}</td>
  <td>{status}</td>
  <td><a href="#{detail_id}" onclick="document.getElementById('{detail_id}').style.display='block';return false;">展開</a></td>
</tr>
<tr id="{detail_id}" style="display:none;">
  <td colspan="8"><pre style="background:#f9fafb;padding:0.5em;">{detail}</pre></td>
</tr>
""")

    table_html = f"""
<table>
  <tr>
    <th>完成時間</th>
    <th>來源</th>
    <th>玩法</th>
    <th>原文摘要</th>
    <th>號碼 / columns</th>
    <th>金額</th>
    <th>狀態</th>
    <th>查看詳情</th>
  </tr>
  {''.join(rows)}
</table>
"""

    warning_block = ""
    if malformed:
        warning_block = (
            f'<p style="color:#c33;"><strong>警告:</strong> '
            f'runs/history/orders.jsonl 有 {malformed} 行壞資料, 已略過. '
            f'請人工檢查.</p>'
        )

    body = f"""
<h1>歷史紀錄 (v1)</h1>
{filter_form}
{warning_block}
<p>共 {len(records)} 筆 (過濾後) / {len(all_records)} 筆 (全部). 這是唯讀查詢頁: 沒有任何「送出」、「確認」、「填入」、「下一筆」、「accept-valid」按鈕.</p>
{table_html}
<div class="links">
  <a class="danger" href="/">回首頁</a>
</div>
"""
    return _HTML_HEAD.format(title="歷史紀錄") + body + _HTML_FOOTER


def _json_dumps(value: Any) -> str:
    """Render a Python value as a compact JSON fragment for inline display."""
    import json as _json

    return _json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _python_executable() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a betguard.webfill.cli subcommand and return the completed process.

    The workbench never passes any flag in FORBIDDEN_FLAGS.  This is
    enforced here as a defence-in-depth check.
    """
    forbidden_present = FORBIDDEN_FLAGS.intersection(args)
    if forbidden_present:
        raise RuntimeError(
            f"refused to run CLI with forbidden flag(s): {sorted(forbidden_present)}"
        )
    cmd = [_python_executable(), "-X", "utf8", "-m", "betguard.webfill.cli", *args]
    return subprocess.run(  # noqa: S603  -- args is a list, no shell
        cmd,
        cwd=str(PROJECT_ROOT),
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git_short_head() -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _project_version() -> str:
    """Return the closest matching tag, or 'unknown'."""
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Per-request batch creation
# ---------------------------------------------------------------------------


def _create_batch(text: str, game: str) -> tuple[
    Path, Path, Path, dict[str, Any], Path | None, Path | None
]:
    """Create a review batch via the existing CLI flows.

    Returns: (input_path, queue_path, review_path, summary_dict, unrecognized_html_path|None, unrecognized_json_path|None)
    Raises on failure.
    """
    if game not in ALLOWED_GAME_OPTIONS:
        raise ValueError(f"unsupported game: {game!r}")

    now = datetime.now(timezone.utc).astimezone()
    day_dir = RUNS_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%H%M%S")
    input_path = day_dir / f"input_{stamp}.txt"
    queue_path = day_dir / f"queue_{stamp}.json"
    review_path = day_dir / f"review_{stamp}.html"

    input_path.write_text(text, encoding="utf-8")

    # Step 1: --new-batch-from-file
    # The CLI has no --game flag; the parser detects the game per line.
    # Passing one made every non-auto selection fail with rc=2 (usage error).
    new_batch_args = [
        "--new-batch-from-file", str(input_path),
        "--queue", str(queue_path),
        "--overwrite",
        "--pretty",
    ]
    proc1 = _run_cli(new_batch_args)
    if proc1.returncode != 0:
        raise RuntimeError(
            f"--new-batch-from-file failed (rc={proc1.returncode}): "
            f"{proc1.stderr.strip()[:200]}"
        )

    # Step 2: --review-report-html
    review_args = [
        "--review-report-html",
        "--queue", str(queue_path),
        "--out", str(review_path),
        "--pretty",
    ]
    proc2 = _run_cli(review_args)
    if proc2.returncode != 0:
        raise RuntimeError(
            f"--review-report-html failed (rc={proc2.returncode}): "
            f"{proc2.stderr.strip()[:200]}"
        )

    # Step 3: extract summary (read the queue.json directly; safe because
    # we only ever set NEEDS_REVIEW and never call accept-valid)
    summary = _summarize_queue(queue_path)

    # Step 4: auto-generate unrecognized-format report (optional, never blocks)
    unrecognized_html: Path | None = None
    unrecognized_json: Path | None = None
    try:
        from betguard.webfill.unrecognized_report import write_report_files

        unrec_html, unrec_json, _unrec_data = write_report_files(
            queue_path,
            input_path=input_path,
        )
        unrecognized_html = unrec_html
        unrecognized_json = unrec_json
    except Exception:
        # Report generation is best-effort; never fail the batch for it.
        pass

    return input_path, queue_path, review_path, summary, unrecognized_html, unrecognized_json


def _summarize_queue(queue_path: Path) -> dict[str, Any]:
    """Read the queue.json produced by the CLI and return summary fields.

    Defensive: if the file is missing or malformed, return zeros.  Never
    mutates the queue.

    Note on the status field: v0.4.5 build_batch_mock_queue returns
    ``READY_FOR_QUEUE`` when the entire batch is valid (no Needs Review),
    and ``NEEDS_REVIEW`` when there are any review/invalid items.  We
    pass the actual status through unchanged so the dashboard reflects
    the real queue state.
    """
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "valid_count": 0,
            "needs_review_count": 0,
            "invalid_count": 0,
            "watchlist_count": 0,
            "queue_status": "UNKNOWN",
        }
    preprocessing = queue.get("preprocessing", {}) or {}
    summary = preprocessing.get("summary", {}) or {}
    return {
        "valid_count": int(summary.get("valid_count", 0)),
        "needs_review_count": int(summary.get("needs_review_count", 0)),
        "invalid_count": int(summary.get("invalid_unsupported_count", 0)),
        "watchlist_count": int(preprocessing.get("watchlist_count", 0) or 0),
        "queue_status": str(queue.get("status", "UNKNOWN")),
    }


def _find_latest_review() -> Path | None:
    """Return the most recent review_*.html under runs/, or None."""
    if not RUNS_DIR.exists():
        return None
    candidates = sorted(RUNS_DIR.glob("*/*review_*.html"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    return candidates[-1]


def _find_latest_unrecognized() -> Path | None:
    """Return the most recent unrecognized_*.html under runs/, or None."""
    if not RUNS_DIR.exists():
        return None
    candidates = sorted(RUNS_DIR.glob("*/*unrecognized_*.html"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    return candidates[-1]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


# In-memory manual correction candidate registry (keyed by candidate_id)
_manual_candidates: dict[str, dict[str, Any]] = {}

def _register_manual_candidate(candidate: dict[str, Any]) -> str:
    """Register a manually corrected candidate and return its unique ID."""
    import uuid
    cid = f"manual-{uuid.uuid4().hex[:8]}"
    _manual_candidates[cid] = dict(candidate)
    return cid

def _lookup_manual_candidate(cid: str) -> dict[str, Any] | None:
    """Look up a registered manual candidate by ID."""
    return _manual_candidates.get(cid)


def build_workbench_handler(
    *,
    project_version: str,
    git_commit: str,
) -> type[BaseHTTPRequestHandler]:
    """Build a BaseHTTPRequestHandler subclass bound to a fixed version/commit."""

    class WorkbenchHandler(BaseHTTPRequestHandler):
        # Quieter logs
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            sys.stderr.write(
                f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}\n"
            )

        # ---------------- routing helpers ----------------
        def _send_html(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionAbortedError, BrokenPipeError, OSError):
                pass

        def _send_text(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionAbortedError, BrokenPipeError, OSError):
                pass

        def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
            import json as _json_module
            data = _json_module.dumps(obj, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionAbortedError, BrokenPipeError, OSError):
                # Client disconnected before response could be sent
                pass

        def _send_file(self, path: Path) -> None:
            # Defence-in-depth: path must be under RUNS_DIR
            try:
                resolved = path.resolve(strict=True)
                runs_root = RUNS_DIR.resolve()
                if not str(resolved).startswith(str(runs_root) + str(Path("/"))) and resolved != runs_root:
                    # If resolved is exactly runs_root, it's fine; otherwise it must be inside.
                    if not str(resolved).startswith(str(runs_root) + str(Path("/"))):
                        self._send_text("forbidden", status=403)
                        return
            except (OSError, RuntimeError):
                self._send_text("not found", status=404)
                return
            if not resolved.is_file():
                self._send_text("not found", status=404)
                return
            data = resolved.read_bytes()
            # Very small allowlist of content types; default to octet-stream
            if resolved.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            elif resolved.suffix == ".json":
                ctype = "application/json; charset=utf-8"
            elif resolved.suffix == ".txt":
                ctype = "text/plain; charset=utf-8"
            else:
                ctype = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---------------- routes ----------------
        def do_GET(self) -> None:  # noqa: N802 -- stdlib name
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "":
                self._send_redirect("/workbench")
                return
            if path == "/workbench":
                self._send_html(_render_workbench_form())
                return
            if path == "/latest-review":
                latest = _find_latest_review()
                if latest is None:
                    self._send_html(
                        _HTML_HEAD.format(title="尚無 review")
                        + "<h1>尚無 review.html</h1>"
                        + "<p>請先到 <a href=\"/workbench\">貼上牌單</a> 建立審核批次.</p>"
                        + _HTML_FOOTER
                    )
                    return
                rel = _runs_url(latest.relative_to(RUNS_DIR))
                self._send_redirect(f"/runs/{urllib.parse.quote(rel)}")
                return
            if path == "/sop":
                if not SOP_PATH.exists():
                    self._send_text("SOP not found", status=404)
                    return
                self._send_html(_render_doc("SOP", _html_escape(SOP_PATH.read_text(encoding="utf-8"))))
                return
            if path == "/cli":
                if not CLI_REF_PATH.exists():
                    self._send_text("CLI reference not found", status=404)
                    return
                self._send_html(
                    _render_doc("CLI reference", _html_escape(CLI_REF_PATH.read_text(encoding="utf-8")))
                )
                return
            if path == "/history":
                qs = urllib.parse.parse_qs(parsed.query)
                q = (qs.get("q", [""])[0] or "").strip()
                number = (qs.get("number", [""])[0] or "").strip()
                game = (qs.get("game", [""])[0] or "").strip()
                date = (qs.get("date", [""])[0] or "").strip()
                # Parse number filter as int when possible
                number_val: int | str | None
                if number:
                    try:
                        number_val = int(number)
                    except ValueError:
                        number_val = number
                else:
                    number_val = None
                self._send_html(
                    _render_history(
                        q=q,
                        number=number_val,
                        game=game,
                        date=date,
                    )
                )
                return
            if path.startswith("/runs/"):
                rel = urllib.parse.unquote(path[len("/runs/"):])
                target = (RUNS_DIR / rel).resolve()
                self._send_file(target)
                return
            if path == "/assist-panel":
                self._handle_assist_panel()
                return
            self._send_text("not found", status=404)

        def do_POST(self) -> None:  # noqa: N802 -- stdlib name
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            # POST /workbench — create review batch
            if path == "/workbench":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0:
                    self._send_html(_render_workbench_form("空白輸入會被拒絕"), status=400)
                    return
                body_raw = self.rfile.read(length)
                try:
                    form = urllib.parse.parse_qs(body_raw.decode("utf-8"))
                except UnicodeDecodeError:
                    self._send_html(_render_workbench_form("無法解碼輸入 (需要 UTF-8)"), status=400)
                    return
                text = (form.get("text", [""])[0] or "").strip()
                game = (form.get("game", ["auto"])[0] or "auto").strip()
                redirect_review = (form.get("redirect", ["0"])[0] or "0") == "1"
                if not text:
                    self._send_html(_render_workbench_form("空白輸入會被拒絕"), status=400)
                    return
                try:
                    input_path, queue_path, review_path, summary, unrecognized_html, _unrec_json = _create_batch(text, game)
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    self._send_html(
                        _render_workbench_form(f"建立審核批次失敗: {exc}"),
                        status=500,
                    )
                    return
                # Always redirect to the new review page
                rel = _runs_url(review_path.relative_to(RUNS_DIR))
                self._send_redirect(f"/runs/{urllib.parse.quote(rel)}")
                return

            # GET /assist-panel — slim panel for right-side assist workspace (Phase 1: read-only)
            if path == "/assist-panel":
                self._handle_assist_panel()
                return

            # POST /assist-panel/create-batch — create queue from pasted text, return JSON
            if path == "/assist-panel/create-batch":
                self._handle_assist_panel_create_batch()
                return

            # POST /assist-fill — validate candidate and return preview data (read-only)
            if path == "/assist-fill":
                self._handle_assist_fill_validate()
                return

            # POST /assist-fill/start — open browser, create staged session
            if path == "/assist-fill/start":
                self._handle_assist_fill_start()
                return

            # POST /assist-fill/ready — check page danger elements
            if path == "/assist-fill/ready":
                self._handle_assist_fill_ready()
                return

            # POST /assist-fill/execute — fill numbers and amounts
            if path == "/assist-fill/execute":
                self._handle_assist_fill_execute()
                return

            # POST /assist-fill/cancel — close browser, clean up
            if path == "/assist-fill/cancel":
                self._handle_assist_fill_cancel()
                return

            # POST /assist-fill/open-site — open or reuse betting site browser
            if path == "/assist-fill/open-site":
                self._handle_assist_fill_open_site()
                return

            # POST /assist-fill/manual-done — mark item as manually done (no site op)
            if path == "/assist-fill/manual-done":
                self._handle_manual_done()
                return

            # POST /manual-reparse — re-parse corrected text (no file writes)
            if path == "/manual-reparse":
                self._handle_manual_reparse()
                return

            self._send_text("not found", status=404)

        # ----------------------------------------------------------------
        # Assist-fill staged handlers
        # ----------------------------------------------------------------

        def _read_json_body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                self._send_json({"ok": False, "error": "empty body"})
                return None
            body_raw = self.rfile.read(length)
            try:
                return json.loads(body_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"ok": False, "error": "invalid JSON"})
                return None

        def _validate_candidate(
            self, data: dict[str, Any] | None = None
        ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            """Parse body, resolve queue, validate item. Returns (error_response, validation).

            ``data`` is the already-parsed JSON body when the caller has read it;
            the request body can only be read from the socket once, so a second
            ``_read_json_body()`` would block forever waiting for bytes that
            never arrive.
            """
            if data is None:
                data = self._read_json_body()
                if data is None:
                    return (None, None)  # error already sent
            queue_path = (data.get("queue_path") or "").strip()
            item_index = data.get("item_index")
            if not queue_path or item_index is None:
                self._send_json({"ok": False, "error": "missing queue_path or item_index"})
                return (None, None)
            resolved = _resolve_queue_path(queue_path)
            if resolved is None:
                self._send_json({"ok": False, "error": f"queue not found: {queue_path}"})
                return (None, None)
            try:
                validation = _validate_assist_fill_item(resolved, int(item_index))
            except (TypeError, ValueError) as exc:
                self._send_json({"ok": False, "error": f"invalid item_index: {exc}"})
                return (None, None)
            if not validation["ok"]:
                self._send_json(validation)
                return (None, None)
            return (None, validation)

        def _handle_assist_fill_validate(self) -> None:
            """Read-only: validate candidate and return parsed data for preview."""
            _err, validation = self._validate_candidate()
            if validation is None:
                return  # error already sent
            self._send_json({
                "ok": True,
                "numbers": validation["numbers"],
                "stars": validation["stars"],
                "amounts": validation["amounts"],
                "game": validation.get("game", "539"),
            })

        def _handle_assist_fill_start(self) -> None:
            """Validate candidate, start fill, auto-execute if browser is open.

            Supports both queue-based candidates (queue_path + item_index) and
            manually corrected candidates (manual_candidate_id).
            """
            try:
                self._assist_fill_start_inner()
            except Exception as exc:
                self._send_json({
                    "ok": False,
                    "error": f"assist_fill_start error: {exc}",
                    "diagnostic": {
                        "stage": "assist_fill_start",
                        "exception_type": type(exc).__name__,
                    },
                })
                try:
                    import traceback
                    traceback.print_exc()
                except Exception:
                    pass

        def _assist_fill_start_inner(self) -> None:
            """Inner implementation — wrapped by try/except in caller."""
            data = self._read_json_body()
            if data is None:
                return

            manual_id = (data.get("manual_candidate_id") or "").strip()

            # ── Initialize bet_type early (must be set before any use) ──
            bet_type = (data.get("bet_type") or "").strip()
            if not bet_type:
                # Try to derive from manual candidate if available
                if manual_id:
                    candidate = _lookup_manual_candidate(manual_id)
                    if candidate:
                        bet_type = candidate.get("bet_type") or candidate.get("type") or ""
                if not bet_type:
                    bet_type = "normal"  # safe default

            if manual_id:
                # Manual correction path: look up registered candidate
                candidate = _lookup_manual_candidate(manual_id)
                if candidate is None:
                    self._send_json({"ok": False, "error": f"manual candidate not found: {manual_id}"})
                    return
                validation = {
                    "ok": True,
                    "numbers": candidate["numbers"],
                    "stars": candidate["stars"],
                    "amounts": candidate["amounts"],
                    "game": candidate.get("game", "539"),
                }
            else:
                # Queue path: use previously initialized bet_type
                if bet_type == "column":
                    # Column bets skip normal _validate_candidate (which requires flat "numbers")
                    validation = {"ok": True, "bet_type": "column",
                                  "url": "https://www.gts362.com",
                                  "game": data.get("game", "539")}
                else:
                    _err, validation = self._validate_candidate(data)
                    if validation is None:
                        return

            if not validation.get("ok"):
                self._send_json(validation)
                return

            from betguard.webfill.web_assist_session import (
                CMD_CHECK_READY, CMD_EXECUTE_FILL, CMD_START, CMD_ZHU_PENG_EXECUTE,
                get_assist_session,
            )

            worker = get_assist_session()

            # ── Column/zhu-peng path ──
            if bet_type == "column":
                # Manual candidate column → use candidate data directly
                if manual_id and candidate:
                    from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
                    from datetime import datetime, timezone
                    # Build zhu_peng item from candidate data
                    columns = candidate.get("columns") or []
                    stars = candidate.get("stars") or []
                    money = candidate.get("money") or 0
                    amounts = candidate.get("amounts") or {}
                    if columns:
                        _item = {
                            "bet_type": "column",
                            "numbers": columns,
                            "columns": columns,
                            "stars": stars,
                            "money": money,
                            "amounts": amounts,
                            "star_amounts": {str(s): {"unit": 1, "money": money} for s in stars},
                            "accepted_by_human": True,
                            "approved_source": {"from_webui_assist_button": True, "source": "webui_column_assist_manual"},
                        }
                        pre = zhu_peng_preflight(_item)
                        if pre["status"] != "READY_FOR_HUMAN_REVIEW":
                            pre["auto_submit"] = False
                            pre["auto_confirm"] = False
                            self._send_json(pre)
                            return
                        start_payload = {
                            "numbers": columns,
                            "stars": stars,
                            "amounts": amounts,
                            "url": "https://www.gts362.com",
                            "game": candidate.get("game", "539"),
                        }
                        start_result = worker.dispatch(CMD_START, start_payload)
                        if not start_result.get("ok"):
                            self._send_json(start_result)
                            return
                        ready_result = worker.dispatch(CMD_CHECK_READY, None)
                        if not ready_result.get("ok"):
                            self._send_json(ready_result)
                            return
                        exec_result = worker.dispatch(CMD_ZHU_PENG_EXECUTE, {"item": _item})
                        exec_result.setdefault("auto_submit", False)
                        exec_result.setdefault("auto_confirm", False)
                        exec_result.setdefault("danger_buttons_clicked", [])
                        self._send_json(exec_result)
                        return
                    else:
                        self._send_json({"ok": False, "error": "manual column candidate has no columns data"})
                        return

                queue_path_str = (data.get("queue_path") or "").strip()
                item_index = data.get("item_index")
                if not queue_path_str or item_index is None:
                    self._send_json({"ok": False, "error": "missing queue_path or item_index"})
                    return
                resolved = _resolve_queue_path(queue_path_str)
                if resolved is None:
                    self._send_json({"ok": False, "error": f"queue not found: {queue_path_str}"})
                    return
                import json as _json_module
                from datetime import datetime, timezone
                try:
                    queue = _json_module.loads(resolved.read_text(encoding="utf-8"))
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"cannot read queue: {exc}"})
                    return

                # 1) Verify item is a Valid Candidate (not Needs Review/Invalid/Watchlist)
                valid_candidates = queue.get("preprocessing", {}).get("valid_candidates", [])
                vc_item = None
                for vc in valid_candidates:
                    if vc.get("index") == item_index:
                        vc_item = vc
                        break
                if vc_item is None:
                    self._send_json({"ok": False, "error": f"item #{item_index} is not a Valid Candidate (may be Needs Review/Invalid/Watchlist); BLOCKED"})
                    return
                vc_result = vc_item.get("result", {})
                if vc_result.get("status") != "ok":
                    self._send_json({"ok": False, "error": f"item #{item_index} status is not 'ok'; BLOCKED"})
                    return
                if vc_result.get("type") != "column":
                    self._send_json({"ok": False, "error": f"item #{item_index} type is not 'column'; BLOCKED"})
                    return

                # 2) Auto-create approved_fill_queue entry if missing (Web UI button = human confirm)
                afq = queue.get("approved_fill_queue") or []
                if not isinstance(afq, list):
                    afq = []
                zhu_item = None
                for afi in afq:
                    if afi.get("index") == item_index and afi.get("accepted_by_human"):
                        zhu_item = afi
                        break

                if zhu_item is None:
                    # Build AFQ entry from valid_candidates data
                    accepted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    stars = vc_result.get("stars") or []
                    money = vc_result.get("money")
                    unit = vc_result.get("unit")
                    star_amounts = {}
                    if money is not None and stars:
                        star_amounts = {str(s): {"unit": unit, "money": money} for s in stars}
                    zhu_item = {
                        "index": vc_item.get("index"),
                        "original_fragment": vc_item.get("original_fragment") or vc_item.get("raw", ""),
                        "original_line": vc_item.get("original_line") or vc_item.get("raw", ""),
                        "original_lines": list(vc_item.get("original_lines") or [vc_item.get("raw", "")]),
                        "review_result": dict(vc_result),
                        "bet_type": vc_result.get("type"),
                        "numbers": list(vc_result.get("numbers") or []),
                        "columns": list(vc_result.get("columns") or []),
                        "stars": list(stars),
                        "money": money,
                        "unit": unit,
                        "star_amounts": star_amounts,
                        "accepted_at": accepted_at,
                        "accepted_by_human": True,
                        "approved_source": {
                            "from_webui_assist_button": True,
                            "source": "webui_column_assist",
                            "original_fragment": vc_item.get("original_fragment") or vc_item.get("raw", ""),
                            "bet_type": "column",
                        },
                        "audit_snapshot": {
                            "batch_id": queue.get("audit", {}).get("batch_id"),
                            "queue_status": queue.get("status"),
                            "review_action": "webui_column_assist",
                        },
                    }
                    # Save to queue
                    afq.append(zhu_item)
                    queue["approved_fill_queue"] = afq
                    try:
                        resolved.write_text(_json_module.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
                    except OSError:
                        pass  # best-effort save; fill proceeds anyway

                # 3) Normalize + preflight + execute (same as CLI)
                from betguard.webfill.zhu_peng_pipeline import zhu_peng_preflight
                from betguard.webfill.cli import _normalize_zhu_peng_item as _norm_zhu
                zhu_item = _norm_zhu(queue, zhu_item)
                pre = zhu_peng_preflight(zhu_item)
                if pre["status"] != "READY_FOR_HUMAN_REVIEW":
                    pre["auto_submit"] = False
                    pre["auto_confirm"] = False
                    self._send_json(pre)
                    return

                start_payload = {
                    "numbers": zhu_item.get("numbers", []),
                    "stars": zhu_item.get("stars", []),
                    "amounts": zhu_item.get("amounts", {}),
                    "url": validation.get("url", "https://www.gts362.com"),
                    "game": zhu_item.get("game", "539"),
                }
                start_result = worker.dispatch(CMD_START, start_payload)
                if not start_result.get("ok"):
                    self._send_json(start_result)
                    return
                ready_result = worker.dispatch(CMD_CHECK_READY, None)
                if not ready_result.get("ok"):
                    self._send_json(ready_result)
                    return
                exec_result = worker.dispatch(CMD_ZHU_PENG_EXECUTE, {"item": zhu_item})
                exec_result.setdefault("auto_submit", False)
                exec_result.setdefault("auto_confirm", False)
                exec_result.setdefault("danger_buttons_clicked", [])
                self._send_json(exec_result)
                return

            # ── Normal bet path ──
            # Auto-create approved_fill_queue entry (Web UI button = human confirm)
            if bet_type == "normal":
                queue_path_str = (data.get("queue_path") or "").strip()
                item_index = data.get("item_index")
                if queue_path_str and item_index is not None:
                    resolved = _resolve_queue_path(queue_path_str)
                    if resolved is not None:
                        import json as _json_module
                        from datetime import datetime, timezone
                        try:
                            queue = _json_module.loads(resolved.read_text(encoding="utf-8"))
                        except Exception:
                            queue = {}
                        afq = queue.get("approved_fill_queue") or []
                        if not isinstance(afq, list):
                            afq = []
                        exists = any(afi.get("index") == item_index and afi.get("accepted_by_human") for afi in afq)
                        if not exists:
                            accepted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                            entry = {
                                "index": item_index,
                                "bet_type": "normal",
                                "numbers": list(validation.get("numbers", [])),
                                "stars": list(validation.get("stars", [])),
                                "amounts": dict(validation.get("amounts", {})),
                                "accepted_at": accepted_at,
                                "accepted_by_human": True,
                                "approved_source": {
                                    "from_webui_assist_button": True,
                                    "source": "webui_normal_assist",
                                },
                            }
                            afq.append(entry)
                            queue["approved_fill_queue"] = afq
                            try:
                                resolved.write_text(_json_module.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
                            except OSError:
                                pass
            start_result = worker.dispatch(CMD_START, {
                "numbers": validation["numbers"],
                "stars": validation["stars"],
                "amounts": validation["amounts"],
                "url": "https://www.gts362.com",
                "game": validation.get("game", "539"),
            })
            print(f"[assist] start: ok={start_result.get('ok')} state={start_result.get('state')}", flush=True)
            if not start_result.get("ok"):
                self._send_json(start_result)
                return

            ready_result = worker.dispatch(CMD_CHECK_READY, None)
            print(f"[assist] ready: ok={ready_result.get('ok')} danger={len(ready_result.get('danger_detected', []))}", flush=True)
            if not ready_result.get("ok"):
                self._send_json(ready_result)
                return

            exec_result = worker.dispatch(CMD_EXECUTE_FILL, None)
            print(f"[assist] execute: ok={exec_result.get('ok')} selected={exec_result.get('numbers_selected')}/{exec_result.get('numbers_expected')}", flush=True)
            exec_result.setdefault("auto_submit", False)
            exec_result.setdefault("auto_confirm", False)
            exec_result.setdefault("danger_buttons_clicked", [])
            self._send_json(exec_result)

        def _handle_assist_fill_ready(self) -> None:
            """Check page danger elements."""
            from betguard.webfill.web_assist_session import CMD_CHECK_READY, get_assist_session
            worker = get_assist_session()
            result = worker.dispatch(CMD_CHECK_READY, None)
            self._send_json(result)

        def _handle_assist_fill_execute(self) -> None:
            """Fill numbers and amounts."""
            from betguard.webfill.web_assist_session import CMD_EXECUTE_FILL, get_assist_session
            worker = get_assist_session()
            result = worker.dispatch(CMD_EXECUTE_FILL, None)
            # Enforce safety invariants
            result.setdefault("auto_submit", False)
            result.setdefault("auto_confirm", False)
            result.setdefault("danger_buttons_clicked", [])
            self._send_json(result)

        def _handle_assist_fill_cancel(self) -> None:
            """Close browser, clean up."""
            from betguard.webfill.web_assist_session import CMD_CLOSE, get_assist_session
            worker = get_assist_session()
            result = worker.dispatch(CMD_CLOSE, None)
            self._send_json(result)

        def _handle_assist_panel(self) -> None:
            """Return the slim assist panel HTML (Phase 1: read-only for fill)."""
            html = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betguard 輔助面板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b;font-size:13px;padding:12px}
h2{font-size:15px;margin-bottom:8px;color:#0f172a}
textarea{width:100%;min-height:100px;font-size:12px;font-family:monospace;padding:8px;border:1px solid #cbd5e1;border-radius:6px;resize:vertical}
button{font-size:12px;padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-weight:600}
.btn-primary{background:#2563eb;color:#fff}
.btn-primary:disabled{background:#94a3b8;cursor:not-allowed}
.section{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px;margin-bottom:10px}
.badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;font-weight:600}
.badge-valid{background:#dbeafe;color:#1e40af}
.badge-review{background:#fef3c7;color:#92400e}
.item{padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:11px}
.item:last-child{border-bottom:none}
.status{font-size:11px;color:#64748b;margin-top:4px}
.footer{font-size:10px;color:#94a3b8;text-align:center;margin-top:12px}
</style>
</head>
<body>
<h2>Betguard 輔助面板</h2>
<textarea id="batch-text" placeholder="貼上牌單..."></textarea>
<button class="btn-primary" onclick="createBatch()">建立審核</button>
<div class="status" id="status-msg"></div>
<div class="section">
 <h2>可輔助填入 <span class="badge badge-valid" id="valid-count">0</span></h2>
 <div id="valid-items"></div>
</div>
<div class="section">
 <h2>Needs Review / Invalid <span class="badge badge-review" id="review-count">0</span></h2>
 <div id="review-items"></div>
</div>
<div class="footer">⚠️ 真站填入後仍需人工確認與送出</div>
<script>
function createBatch(){
 var t=document.getElementById("batch-text").value.trim();
 if(!t){document.getElementById("status-msg").textContent="請先貼上牌單";return;}
 document.getElementById("status-msg").textContent="⏳ 建立審核中...";
 fetch("/assist-panel/create-batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:t})})
  .then(function(r){return r.json();}).then(function(d){
   document.getElementById("status-msg").textContent=d.ok?"✅ 已建立審核":("❌ "+(d.error||"失敗"));
   if(d.ok)displayResults(d);
 }).catch(function(e){document.getElementById("status-msg").textContent="❌ 連線錯誤: "+e;});
}
function displayResults(d){
 var v=d.valid_candidates||[],iv=d.invalid_fragments||[];
 document.getElementById("valid-count").textContent=v.length;
 document.getElementById("review-count").textContent=iv.length;
 var vh="";
 v.forEach(function(c){
  vh+="<div class=item><span class='badge badge-valid'>"+(c.bet_type||"normal")+"</span>";
  vh+="<strong>"+(c.summary||c.raw||"")+"</strong>";
  vh+="<button disabled class='btn-primary' style='margin-left:8px;font-size:10px;padding:2px 8px'>下一階段開放</button></div>";
 });
 document.getElementById("valid-items").innerHTML=vh||"<div class=item style=color:#94a3b8>無</div>";
 var ih="";
 iv.forEach(function(c){
  ih+="<div class=item><span class='badge badge-review'>"+(c.label||"Needs Review")+"</span> <span>"+(c.raw||c.fragment||"")+"</span></div>";
 });
 document.getElementById("review-items").innerHTML=ih||"<div class=item style=color:#94a3b8>無</div>";
}
</script>
</body>
</html>"""
            self._send_html(html)

        def _handle_assist_panel_create_batch(self) -> None:
            """Create a queue batch from pasted text and return JSON summary."""
            import json as _json_module

            data = self._read_json_body()
            if data is None:
                return
            text = (data.get("text") or "").strip()
            if not text:
                self._send_json({"ok": False, "error": "empty text"})
                return

            try:
                from betguard.webfill.batch_mock_queue import build_batch_mock_queue
                queue = build_batch_mock_queue(text.split("\n") if "\n" in text else text)
            except Exception as exc:
                self._send_json({"ok": False, "error": f"batch create error: {exc}"})
                return

            valid_candidates = queue.get("preprocessing", {}).get("valid_candidates", [])
            invalid_fragments = queue.get("preprocessing", {}).get("invalid_fragments", [])

            # Simplify valid candidates for panel display
            vc_out = []
            for vc in valid_candidates:
                result = vc.get("result", {})
                vc_out.append({
                    "index": vc.get("index"),
                    "raw": vc.get("raw") or vc.get("original_fragment", ""),
                    "summary": vc.get("summary", ""),
                    "bet_type": result.get("type", "normal"),
                    "numbers": result.get("numbers") or [],
                    "stars": result.get("stars") or [],
                    "money": result.get("money"),
                })
            iv_out = []
            for iv in invalid_fragments:
                iv_out.append({
                    "raw": iv.get("raw") or iv.get("original_fragment", ""),
                    "label": iv.get("label") or iv.get("review_label", "Needs Review"),
                    "reason": iv.get("reason", ""),
                })

            # Save queue to a temp file (Phase 1: no full review page integration yet)
            # The queue ID is the batch_id for later reference
            batch_id = queue.get("audit", {}).get("batch_id", "unknown")

            self._send_json({
                "ok": True,
                "batch_id": batch_id,
                "valid_candidates": vc_out,
                "invalid_fragments": iv_out,
            })

        def _handle_manual_done(self) -> None:
            """Mark a pending item as manually done (no real site operation)."""
            import json as _json_module
            from datetime import datetime, timezone
            data = self._read_json_body()
            if data is None:
                return
            manual_id = (data.get("manual_candidate_id") or "").strip()
            queue_path_str = (data.get("queue_path") or "").strip()
            item_index = data.get("item_index")

            # Manual candidate path
            if manual_id:
                candidate = _lookup_manual_candidate(manual_id)
                if candidate is None:
                    self._send_json({"ok": False, "error": f"manual candidate not found: {manual_id}"})
                    return
                # Just acknowledge — no queue update needed for manual candidates
                self._send_json({
                    "ok": True,
                    "status": "MANUAL_DONE",
                    "message": "已標記為手動下牌",
                    "manual_candidate_id": manual_id,
                    "real_site_operation": False,
                    "auto_submit": False,
                    "danger_buttons_clicked": [],
                })
                return

            # Queue path
            if not queue_path_str or item_index is None:
                self._send_json({"ok": False, "error": "missing queue_path or item_index"})
                return
            resolved = _resolve_queue_path(queue_path_str)
            if resolved is None:
                self._send_json({"ok": False, "error": f"queue not found: {queue_path_str}"})
                return
            try:
                queue = _json_module.loads(resolved.read_text(encoding="utf-8"))
            except Exception as exc:
                self._send_json({"ok": False, "error": f"cannot read queue: {exc}"})
                return
            # Find and mark the item
            items = queue.get("items", [])
            matched = None
            for item in items:
                if item.get("index") == item_index:
                    matched = item
                    break
            if matched is None:
                self._send_json({"ok": False, "error": f"item #{item_index} not found in queue"})
                return
            # Only allow marking if item is in valid/assistable state
            status = matched.get("status", "")
            if status in ("INVALID", "NEEDS_REVIEW", "WATCHLIST", "BLOCKED"):
                self._send_json({"ok": False, "error": f"item #{item_index} is not assistable (status={status})"})
                return
            matched["status"] = "MANUAL_DONE"
            matched["handled_by"] = "webui_manual_done_button"
            matched["done_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            matched["accepted_by_human"] = True
            matched["real_site_operation"] = False
            queue["status"] = queue.get("status", "")
            try:
                resolved.write_text(_json_module.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass  # best-effort
            self._send_json({
                "ok": True,
                "status": "MANUAL_DONE",
                "message": "已標記為手動下牌",
                "item_index": item_index,
                "real_site_operation": False,
                "auto_submit": False,
                "danger_buttons_clicked": [],
            })

        def _handle_assist_fill_open_site(self) -> None:
            """Open or reuse the betting site browser (no fill data)."""
            from betguard.webfill.web_assist_session import (
                CMD_START, get_assist_session,
            )

            worker = get_assist_session()
            # Start with empty fill data — just open browser or confirm reuse
            result = worker.dispatch(CMD_START, {
                "numbers": [],
                "stars": [],
                "amounts": {},
                "url": "https://www.gts362.com",
                "open_site_only": True,
            })
            self._send_json(result)

        def _handle_manual_reparse(self) -> None:
            """Re-parse manually corrected text.  No file writes, no queue changes."""
            data = self._read_json_body()
            if data is None:
                return
            text = (data.get("text") or "").strip()
            game = (data.get("game") or "auto").strip()
            if not text:
                self._send_json({"ok": False, "error": "empty text"})
                return
            from betguard.webfill.manual_reparse import reparse_text

            result = reparse_text(text, game=game)
            result.setdefault("auto_submit", False)
            result.setdefault("auto_confirm", False)

            # Register valid manual corrections so they can be assisted later
            if result.get("ok"):
                cid = _register_manual_candidate({
                    "original_text": text,
                    "numbers": result["numbers"],
                    "stars": result["stars"],
                    "amounts": result["amounts"],
                    "summary": result.get("summary", ""),
                    "game": game,
                    "source": "manual_correction",
                    "bet_type": result.get("bet_type") or result.get("type") or "normal",
                    "columns": result.get("columns") or None,
                    "type": result.get("type") or "normal",
                })
                result["manual_candidate_id"] = cid

            self._send_json(result)

    return WorkbenchHandler


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Assist fill (validation + preview only; execution is blocked by safety guard)
# ---------------------------------------------------------------------------


def _derive_star_amounts(result: dict[str, Any], stars: list[int]) -> dict[str, int]:
    """Derive per-star amounts from a parsed result.

    Handles three parser output shapes:
      1. ``result.amounts`` — explicit per-star amounts dict  (already keyed)
      2. ``result.bets``   — per-star BetAmount dicts         (nested ``money``)
      3. ``result.money``  — single money expanded to all stars (most common)

    Returns ``{str(star): int(amount), ...}`` or empty dict when no money found.
    """
    # Shape 1: explicit amounts dict (may come from preprocessor)
    amounts = result.get("amounts", {})
    if amounts and isinstance(amounts, dict):
        out: dict[str, int] = {}
        for k, v in amounts.items():
            try:
                key = str(int(k))
            except (ValueError, TypeError):
                key = str(k)
            try:
                val = v.get("money", v) if isinstance(v, dict) else int(v)
            except (ValueError, TypeError):
                continue
            out[key] = int(val)
        if out:
            return out

    # Shape 2: per-star bets (e.g. 二星200 三星200 四星100)
    bets = result.get("bets", {})
    if bets and isinstance(bets, dict):
        out = {}
        for k, v in bets.items():
            try:
                key = str(int(k))
            except (ValueError, TypeError):
                key = str(k)
            try:
                val = int(v.get("money", 0)) if isinstance(v, dict) else int(v)
            except (ValueError, TypeError):
                val = 0
            if val > 0:
                out[key] = val
        if out:
            return out

    # Shape 3: single money expanded to each star (e.g. 17.20.29.33.440)
    money = result.get("money")
    if money is not None:
        try:
            m = int(money)
        except (ValueError, TypeError):
            m = 0
        if m > 0 and stars:
            return {str(int(s)): m for s in stars}

    return {}


def _resolve_queue_path(queue_path: str) -> Path | None:
    """Resolve a queue path relative to PROJECT_ROOT.

    Supports:
      - Absolute paths (must exist)
      - Relative paths from CWD or PROJECT_ROOT
      - Queue JSON inside runs/ subdirectory
    """
    candidates: list[Path] = []
    p = Path(queue_path)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(PROJECT_ROOT / p)
        candidates.append(Path(p))
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def _validate_assist_fill_item(
    queue_path: Path,
    item_index: int,
) -> dict[str, Any]:
    """Read queue JSON, find item by index, validate it can be assist-filled.

    Returns:
        {"ok": True, "numbers": [...], "stars": [...], "amounts": {...}}
        or {"ok": False, "error": "reason"}
    """
    import json as _json_module

    try:
        raw = queue_path.read_text(encoding="utf-8")
        queue = _json_module.loads(raw)
    except (OSError, _json_module.JSONDecodeError) as exc:
        return {"ok": False, "error": f"cannot read queue: {exc}"}

    # Find item in preprocessing.valid_candidates (the review page data)
    valid_candidates = queue.get("preprocessing", {}).get("valid_candidates", [])
    if not valid_candidates:
        return {"ok": False, "error": "queue has no valid_candidates; Needs Review items cannot be assist-filled"}

    matched = None
    for item in valid_candidates:
        if item.get("index") == item_index:
            matched = item
            break

    if matched is None:
        return {"ok": False, "error": f"item #{item_index} not found in valid_candidates; may be Needs Review/Invalid/Watchlist"}

    result = matched.get("result", {})
    if not isinstance(result, dict):
        return {"ok": False, "error": f"item #{item_index} has no parsed result"}

    if result.get("status") != "ok":
        return {
            "ok": False,
            "error": f"item #{item_index} status is '{result.get('status')}', not a valid candidate; BLOCKED",
        }

    numbers = result.get("numbers", [])
    stars = result.get("stars", [])

    if not numbers:
        return {"ok": False, "error": f"item #{item_index} parsed result has no numbers; BLOCKED"}
    if not stars:
        return {"ok": False, "error": f"item #{item_index} parsed result has no stars; BLOCKED"}

    # Derive per-star amounts from money / bets / amounts in parsed result
    star_amounts = _derive_star_amounts(result, [int(s) for s in stars])
    if not star_amounts:
        return {
            "ok": False,
            "error": (
                f"item #{item_index} has no money/bets/amounts in parsed result; "
                "cannot determine fill amounts; BLOCKED"
            ),
        }

    return {
        "ok": True,
        "numbers": [int(n) for n in numbers],
        "stars": [int(s) for s in stars],
        "amounts": star_amounts,
        "game": result.get("game") or "539",
    }


def _assist_fill_item(
    *,
    numbers: list[int],
    stars: list[int],
    amounts: dict[str, int],
    game: str = "539",
) -> dict[str, Any]:
    """Run the allowlisted web assisted fill executor.

    Opens a visible browser, waits for the user to log in and navigate
    (via ``input()`` in the terminal where the workbench server runs),
    fills numbers via knockout and amounts via Playwright, then stops.
    Never submits, confirms, or clicks danger buttons.

    The HTTP request blocks while the browser session is active -- this
    is intentional so the web UI can show "已輔助填入" after completion.
    """
    try:
        from betguard.webfill.web_assisted_fill_executor import (
            execute_web_assisted_fill_one_item,
        )
    except ImportError:
        return {
            "ok": False,
            "error": "web assisted fill executor not available (import error)",
        }

    try:
        result = execute_web_assisted_fill_one_item(
            numbers=numbers,
            stars=stars,
            amounts=amounts,
            url="https://www.gts362.com",
            game=game,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"web assisted fill executor error: {exc}",
        }

    # Enforce safety invariants regardless of what the executor returns
    result.setdefault("auto_submit", False)
    result.setdefault("auto_confirm", False)
    result.setdefault("danger_buttons_clicked", [])
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="betguard.webui.app",
        description="Betguard local web workbench (stdlib HTTP server).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    args = parser.parse_args(argv)

    if args.host == "0.0.0.0":
        print("WARNING: binding to 0.0.0.0 exposes this workbench to your LAN.", file=sys.stderr)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    version = _project_version()
    commit = _git_short_head()
    handler = build_workbench_handler(project_version=version, git_commit=commit)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Betguard workbench listening on http://{args.host}:{args.port}/  "
        f"(version={version}, commit={commit})",
        file=sys.stderr,
    )
    print("Press Ctrl-C to stop.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping workbench...", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
