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


def _build_dashboard_links() -> str:
    """Return the dashboard button bar HTML, with optional unrecognized link."""
    lines = [
        '<div class="links">',
        '  <a href="/workbench">貼上牌單建立審核</a>',
        '  <a class="danger" href="/latest-review">開啟最新 review.html</a>',
    ]
    latest_unrec = _find_latest_unrecognized()
    if latest_unrec:
        rel = latest_unrec.relative_to(RUNS_DIR)
        lines.append(
            f'  <a class="danger" href="/runs/{urllib.parse.quote(str(rel))}">'
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
        rel = unrecognized_html.relative_to(RUNS_DIR)
        unrecognized_block_html = (
            f'<p>未辨識格式: <a href="/runs/{urllib.parse.quote(str(rel))}">'
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
    new_batch_args = [
        "--new-batch-from-file", str(input_path),
        "--queue", str(queue_path),
        "--overwrite",
        "--pretty",
    ]
    if game != "auto":
        new_batch_args += ["--game", game]
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
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
            import json as _json_module
            data = _json_module.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
                self._send_html(_render_dashboard(project_version, git_commit))
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
                rel = latest.relative_to(RUNS_DIR)
                self._send_redirect(f"/runs/{urllib.parse.quote(str(rel))}")
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
                if redirect_review:
                    rel = review_path.relative_to(RUNS_DIR)
                    self._send_redirect(f"/runs/{urllib.parse.quote(str(rel))}")
                    return
                self._send_html(
                    _render_result(
                        input_path, queue_path, review_path, summary,
                        unrecognized_html=unrecognized_html,
                    )
                )
                return

            # POST /assist-fill — trigger single-item assisted fill
            if path == "/assist-fill":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0:
                    self._send_json({"ok": False, "error": "empty body"})
                    return
                body_raw = self.rfile.read(length)
                try:
                    data = json.loads(body_raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json({"ok": False, "error": "invalid JSON"})
                    return
                queue_path = (data.get("queue_path") or "").strip()
                item_index = data.get("item_index")
                if not queue_path or item_index is None:
                    self._send_json({"ok": False, "error": "missing queue_path or item_index"})
                    return

                # Resolve queue path relative to PROJECT_ROOT
                resolved = _resolve_queue_path(queue_path)
                if resolved is None:
                    self._send_json({"ok": False, "error": f"queue not found: {queue_path}"})
                    return

                # Read queue, find item, validate, extract parsed data
                validation = _validate_assist_fill_item(resolved, int(item_index))
                if not validation["ok"]:
                    self._send_json(validation)
                    return

                result = _assist_fill_item(
                    numbers=validation["numbers"],
                    stars=validation["stars"],
                    amounts=validation["amounts"],
                )
                self._send_json(result)
                return

            self._send_text("not found", status=404)

    return WorkbenchHandler


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Assist fill (direct call, bypasses CLI FORBIDDEN_FLAGS)
# ---------------------------------------------------------------------------


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
    amounts = result.get("amounts", {}) or result.get("bets", {})

    if not numbers:
        return {"ok": False, "error": f"item #{item_index} parsed result has no numbers; BLOCKED"}
    if not stars:
        return {"ok": False, "error": f"item #{item_index} parsed result has no stars; BLOCKED"}

    # Normalize amounts: convert keys to int and values to int
    normalized_amounts: dict[str, int] = {}
    if isinstance(amounts, dict):
        for k, v in amounts.items():
            try:
                key = str(int(k))
            except (ValueError, TypeError):
                key = str(k)
            try:
                if isinstance(v, dict):
                    val = int(v.get("money", 0))
                else:
                    val = int(v)
            except (ValueError, TypeError):
                val = 0
            normalized_amounts[key] = val

    return {
        "ok": True,
        "numbers": [int(n) for n in numbers],
        "stars": [int(s) for s in stars],
        "amounts": normalized_amounts,
    }


def _assist_fill_item(
    *,
    numbers: list[int],
    stars: list[int],
    amounts: dict[str, int],
) -> dict[str, Any]:
    """Trigger single-item real-site assisted fill.

    Creates a temporary accepted queue and runs the fill CLI.
    The fill opens its own browser window — user must manually login
    and press Enter.  Never auto-submits or auto-confirms.
    """
    import json as _json_module
    import tempfile
    from betguard.formatter import format_bet_summary

    # Build a minimal queue with one accepted item
    parsed = {
        "status": "ok",
        "numbers": numbers,
        "stars": stars,
        "amounts": amounts,
        "game": "539",
    }
    item = {
        "index": 0,
        "status": "CURRENT",
        "original": "",
        "summary": format_bet_summary(parsed),
        "parsed": parsed,
        "parsed_summary": format_bet_summary(parsed),
        "review_result": parsed,
        "fill_plan": {},
        "accepted_by_human": True,
        "warnings": [],
        "errors": [],
    }
    queue = {
        "mode": "batch_assisted_fill_queue",
        "status": "READY",
        "current_index": 0,
        "total": 1,
        "done_count": 0,
        "items": [item],
        "summary": {"total": 1, "ok": 1, "blocked": 0, "current_index": 1, "remaining": 1},
        "final_decision": {"real_site_auto_submit": False, "human_required_each_item": True},
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(_json_module.dumps(queue, ensure_ascii=False))
        queue_path = f.name

    try:
        proc = _run_cli([
            "--real-site-assisted-fill",
            "--queue", queue_path,
            "--url", "https://www.gts362.com",
            "--i-understand-real-site-fill-risk",
            "--pretty",
        ])
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip()[:500] or f"rc={proc.returncode}"}
        return {"ok": True, "numbers": numbers, "stars": stars, "amounts": amounts}
    finally:
        try:
            Path(queue_path).unlink(missing_ok=True)
        except OSError:
            pass


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
