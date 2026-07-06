from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def build_review_console_model(queue: dict[str, Any], *, queue_path: str | None = None) -> dict[str, Any]:
    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    audit = queue.get("audit", {})
    safety = _safety_view(queue)
    current = _current_item(queue)
    all_not_ok = list(preprocessing.get("invalid_fragments", []))
    watchlist_source = [item for item in all_not_ok if _fragment_status(item) == "warning"]
    invalid_source = [item for item in all_not_ok if _fragment_status(item) != "warning"]
    model = {
        "mode": "local_review_console",
        "status": queue.get("status"),
        "queue_path": queue_path,
        "preprocessing": {
            "status": preprocessing.get("status") or queue.get("preprocessing_status"),
            "candidate_count": int(preprocessing_summary.get("candidate_count", queue.get("summary", {}).get("total", 0))),
            "valid_count": int(preprocessing_summary.get("valid_count", queue.get("summary", {}).get("ok", 0))),
            "invalid_count": int(preprocessing_summary.get("invalid_unsupported_count", 0)),
            "watchlist_count": len(watchlist_source),
            "needs_review_count": len(invalid_source),
            "warnings_count": int(preprocessing_summary.get("warnings_count", 0)),
            "ignored_metadata_count": int(preprocessing_summary.get("ignored_metadata_count", 0)),
        },
        "valid_candidates": [
            {
                "index": item.get("index"),
                "original_fragment": item.get("original_fragment") or item.get("raw"),
                "parsed_summary": item.get("summary", ""),
                "bet_type": item.get("result", {}).get("type"),
            }
            for item in preprocessing.get("valid_candidates", [])
        ],
        "watchlist": [_watchlist_entry(item) for item in watchlist_source],
        "invalid_fragments": [_invalid_entry(item) for item in invalid_source],
        "ignored_metadata_lines": list(preprocessing.get("ignored_metadata_lines", [])),
        "queue_view": {
            "status": queue.get("status"),
            "current_index": queue.get("summary", {}).get("current_index", 0),
            "item_count": len(queue.get("items", [])),
            "current_item": _item_view(current) if current else None,
            "last_mock_result": _last_mock_result(queue),
        },
        "actions": _actions(queue, queue_path),
        "audit": {
            "batch_id": audit.get("batch_id"),
            "created_at": audit.get("created_at"),
            "review_action": audit.get("review", {}).get("action") or "none",
            "invalid_fragments_count": audit.get("preprocessing", {}).get("invalid_count", 0),
            "export_available": True,
        },
        "safety": safety,
    }
    return model


def render_review_console_html(queue: dict[str, Any], *, queue_path: str | None = None) -> str:
    model = build_review_console_model(queue, queue_path=queue_path)
    status_class = _status_class(str(model.get("status") or ""))
    valid_rows = "".join(_candidate_row(item) for item in model["valid_candidates"]) or _empty_row(4, "No valid candidates")
    watchlist_rows = "".join(_watchlist_row(item) for item in model["watchlist"]) or _empty_row(4, "No watchlist items")
    invalid_rows = "".join(_invalid_row(item) for item in model["invalid_fragments"]) or _empty_row(4, "No invalid or error fragments")
    metadata_rows = "".join(
        f"<li>{_e(str(item.get('raw', item)))}</li>" for item in model["ignored_metadata_lines"][:8]
    ) or "<li>None</li>"
    current = model["queue_view"]["current_item"]
    current_html = _current_item_html(current)
    last_mock_html = _last_mock_html(model["queue_view"].get("last_mock_result"))
    actions_html = "".join(f"<li><code>{_e(action)}</code></li>" for action in model["actions"]) or "<li>No action available</li>"
    safety = model["safety"]
    audit = model["audit"]
    source_json = _e(json.dumps(model, ensure_ascii=False, indent=2))

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>Betguard Local Review Console</title>
  <style>
    body {{ font-family: Arial, "Microsoft JhengHei", sans-serif; margin: 24px; color: #17202a; background: #f6f7f9; }}
    h1 {{ margin-bottom: 4px; }}
    h2 {{ margin-top: 0; }}
    .grid {{ display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; align-items: start; }}
    .card {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
    .status {{ border-left: 8px solid #98a2b3; }}
    .status.ready {{ border-left-color: #198754; }}
    .status.review {{ border-left-color: #f59f00; }}
    .status.blocked {{ border-left-color: #dc3545; }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(90px, 1fr)); gap: 8px; }}
    .metric {{ background: #f1f4f8; border-radius: 6px; padding: 10px; }}
    .metric strong {{ display: block; font-size: 22px; }}
    .card.watch {{ border-left: 8px solid #f59f00; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e7ebf0; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f7f9fb; }}
    code, pre {{ background: #eef2f6; border-radius: 4px; padding: 2px 4px; }}
    pre {{ padding: 12px; overflow: auto; max-height: 360px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; color: #fff; }}
    .badge.valid {{ background: #198754; }}
    .badge.watch {{ background: #f59f00; }}
    .badge.invalid {{ background: #dc3545; }}
    .safe {{ color: #087f5b; font-weight: bold; }}
    .danger {{ color: #c92a2a; font-weight: bold; }}
  </style>
</head>
<body>
  <h1>Betguard Local Review Console</h1>
  <p>Paste review, accept/reject, mock-next planning, and audit export. <strong>No live site operation.</strong></p>

  <section class="card status {status_class}">
    <h2>Queue Status: {_e(str(model.get("status")))}</h2>
    <div class="metrics">
      <div class="metric">Candidates<strong>{model["preprocessing"]["candidate_count"]}</strong></div>
      <div class="metric">Valid<strong>{model["preprocessing"]["valid_count"]}</strong></div>
      <div class="metric">待觀察 Watchlist<strong>{model["preprocessing"]["watchlist_count"]}</strong></div>
      <div class="metric">Needs Review / Invalid<strong>{model["preprocessing"]["needs_review_count"]}</strong></div>
      <div class="metric">Warnings<strong>{model["preprocessing"]["warnings_count"]}</strong></div>
      <div class="metric">Ignored Metadata<strong>{model["preprocessing"]["ignored_metadata_count"]}</strong></div>
    </div>
  </section>

  <div class="grid">
    <section class="card">
      <h2><span class="badge valid">Valid</span> Valid Candidates</h2>
      <table><thead><tr><th>#</th><th>Original Fragment</th><th>Summary</th><th>Type</th></tr></thead><tbody>{valid_rows}</tbody></table>
    </section>
    <section class="card">
      <h2><span class="badge invalid">Invalid</span> Needs Review / Invalid</h2>
      <table><thead><tr><th>#</th><th>Original Fragment</th><th>Parsed Summary</th><th>Reason</th></tr></thead><tbody>{invalid_rows}</tbody></table>
    </section>
  </div>

  <section class="card watch">
    <h2><span class="badge watch">待觀察</span> 待觀察 / Watchlist</h2>
    <p>需人工判斷的不確定項目。<strong>display-only；不會自動接受，不會進入 approved_fill_queue、fill-preview 或 mock-fill。</strong></p>
    <table><thead><tr><th>#</th><th>Original Fragment</th><th>Parsed Summary</th><th>Reason</th></tr></thead><tbody>{watchlist_rows}</tbody></table>
  </section>

  <div class="grid">
    <section class="card">
      <h2>Queue View</h2>
      <p><strong>Status:</strong> {_e(str(model["queue_view"]["status"]))}</p>
      <p><strong>Current index:</strong> {_e(str(model["queue_view"]["current_index"]))} / {_e(str(model["queue_view"]["item_count"]))}</p>
      {current_html}
      {last_mock_html}
    </section>
    <section class="card">
      <h2>Actions</h2>
      <p>Run these commands manually. This report does not execute actions.</p>
      <ul>{actions_html}</ul>
    </section>
  </div>

  <div class="grid">
    <section class="card">
      <h2>Ignored Metadata</h2>
      <ul>{metadata_rows}</ul>
    </section>
    <section class="card">
      <h2>Safety</h2>
      <p class="safe">No live site operation</p>
      <ul>
        <li>real_site_operation={str(safety["real_site_operation"]).lower()}</li>
        <li>auto_submit={str(safety["auto_submit"]).lower()}</li>
        <li>danger_buttons_clicked={_e(str(safety["danger_buttons_clicked"]))}</li>
      </ul>
    </section>
  </div>

  <section class="card">
    <h2>Audit Summary</h2>
    <ul>
      <li>batch_id: {_e(str(audit.get("batch_id")))}</li>
      <li>created_at: {_e(str(audit.get("created_at")))}</li>
      <li>review_action: {_e(str(audit.get("review_action")))}</li>
      <li>invalid_fragments_count: {_e(str(audit.get("invalid_fragments_count")))}</li>
    </ul>
  </section>

  <section class="card">
    <h2>Console Data</h2>
    <pre>{source_json}</pre>
  </section>
</body>
</html>
"""


def write_review_console_html(queue: dict[str, Any], path: str | Path, *, queue_path: str | None = None) -> dict[str, Any]:
    html_text = render_review_console_html(queue, queue_path=queue_path)
    Path(path).write_text(html_text, encoding="utf-8")
    return build_review_console_model(queue, queue_path=queue_path)


def format_pretty_review_console(model: dict[str, Any]) -> str:
    preprocessing = model.get("preprocessing", {})
    queue_view = model.get("queue_view", {})
    safety = model.get("safety", {})
    lines = [
        "Local Review Console",
        "",
        f"Status: {model.get('status')}",
        "",
        "Preprocessing:",
        f"- candidates: {preprocessing.get('candidate_count', 0)}",
        f"- valid: {preprocessing.get('valid_count', 0)}",
        f"- watchlist: {preprocessing.get('watchlist_count', 0)}",
        f"- invalid/review: {preprocessing.get('invalid_count', 0)}",
        f"- ignored metadata: {preprocessing.get('ignored_metadata_count', 0)}",
        "",
        "Queue:",
        f"- status: {queue_view.get('status')}",
        f"- current index: {queue_view.get('current_index')} / {queue_view.get('item_count')}",
        "",
        "Safety:",
        f"- real_site_operation: {str(safety.get('real_site_operation')).lower()}",
        f"- auto_submit: {str(safety.get('auto_submit')).lower()}",
        f"- danger_buttons_clicked: {safety.get('danger_buttons_clicked')}",
        "- No live site operation",
    ]
    return "\n".join(lines)


def _actions(queue: dict[str, Any], queue_path: str | None) -> list[str]:
    path = queue_path or "queue_state.json"
    status = queue.get("status")
    actions = []
    if status == "NEEDS_REVIEW":
        actions.append(f"python -m betguard.webfill.cli --batch-review-accept-valid --queue {path} --pretty")
        actions.append(f"python -m betguard.webfill.cli --batch-review-reject --queue {path} --pretty")
    elif status == "READY_FOR_QUEUE":
        actions.append(f"python -m betguard.webfill.cli --batch-mock-run --queue {path} --pretty")
    elif status == "WAITING_FOR_HUMAN_CONFIRM":
        actions.append(f"python -m betguard.webfill.cli --batch-mock-next --queue {path} --pretty")
    actions.append(f"python -m betguard.webfill.cli --batch-audit-export --queue {path} --out audit.json --pretty")
    return actions


def _candidate_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{_e(str(item.get('index')))}</td>"
        f"<td>{_e(str(item.get('original_fragment')))}</td>"
        f"<td>{_e(str(item.get('parsed_summary')))}</td>"
        f"<td>{_e(str(item.get('bet_type')))}</td>"
        "</tr>"
    )


def _invalid_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{_e(str(item.get('index')))}</td>"
        f"<td>{_e(str(item.get('original_fragment')))}</td>"
        f"<td>{_e(str(item.get('parsed_summary')))}</td>"
        f"<td>{_e(str(item.get('reason')))}</td>"
        "</tr>"
    )


def _watchlist_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{_e(str(item.get('index')))}</td>"
        f"<td>{_e(str(item.get('original_fragment')))}</td>"
        f"<td>{_e(str(item.get('parsed_summary')))}</td>"
        f"<td>{_e(str(item.get('reason')))}</td>"
        "</tr>"
    )


def _empty_row(colspan: int, text: str) -> str:
    return f"<tr><td colspan=\"{colspan}\">{_e(text)}</td></tr>"


def _current_item_html(item: dict[str, Any] | None) -> str:
    if not item:
        return "<p>No current item.</p>"
    blocks = [
        "<h3>Current Item</h3>",
        f"<p><strong>[{_e(str(item.get('index')))}] {_e(str(item.get('status')))}</strong></p>",
        f"<p>{_e(str(item.get('original_fragment')))}</p>",
        f"<p>{_e(str(item.get('parsed_summary')))}</p>",
    ]
    if item.get("status") == "WAITING_FOR_HUMAN_CONFIRM":
        blocks.append("<p class=\"danger\">WAITING_FOR_HUMAN_CONFIRM: stop here until human confirms.</p>")
    return "\n".join(blocks)


def _last_mock_html(mock: dict[str, Any] | None) -> str:
    if not mock:
        return "<h3>Last Mock Result</h3><p>None</p>"
    return (
        "<h3>Last Mock Result</h3>"
        f"<p>selected_numbers: {_e(str(mock.get('selected_numbers', [])))}</p>"
        f"<p>selected_columns: {_e(str(mock.get('selected_columns', [])))}</p>"
        f"<p>selected_car_number: {_e(str(mock.get('selected_car_number')))}</p>"
        f"<p>filled_amounts: {_e(str(mock.get('filled_amounts', {})))}</p>"
    )


def _item_view(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "index": item.get("index"),
        "status": item.get("status"),
        "original_fragment": item.get("original_fragment") or item.get("original"),
        "parsed_summary": item.get("parsed_summary", ""),
        "bet_type": item.get("bet_type") or item.get("review_result", {}).get("type"),
        "danger_buttons_clicked": list(item.get("danger_buttons_clicked", [])),
    }


def _current_item(queue: dict[str, Any]) -> dict[str, Any] | None:
    for status in ("WAITING_FOR_HUMAN_CONFIRM", "MOCK_FILL_FAILED", "PENDING"):
        for item in queue.get("items", []):
            if item.get("status") == status:
                return item
    for item in reversed(queue.get("items", [])):
        if item.get("status") == "DONE":
            return item
    return None


def _last_mock_result(queue: dict[str, Any]) -> dict[str, Any] | None:
    for item in reversed(queue.get("items", [])):
        if item.get("status") in {"WAITING_FOR_HUMAN_CONFIRM", "DONE"}:
            return {
                "selected_numbers": list(item.get("selected_numbers", [])),
                "selected_columns": list(item.get("selected_columns", [])),
                "selected_car_number": item.get("selected_car_number"),
                "filled_amount": item.get("filled_amount"),
                "filled_amounts": dict(item.get("filled_amounts", {})),
                "danger_buttons_clicked": list(item.get("danger_buttons_clicked", [])),
            }
    return None


def _safety_view(queue: dict[str, Any]) -> dict[str, Any]:
    clicked = [
        click
        for item in queue.get("items", [])
        for click in item.get("danger_buttons_clicked", [])
    ]
    return {
        "real_site_operation": False,
        "auto_submit": False,
        "danger_buttons_clicked": clicked,
        "no_live_site_operation": True,
    }


def _reason_text(item: dict[str, Any]) -> str:
    if _is_missing_money_only(item):
        return "Needs Review: missing money (缺金額，需人工補)"
    reasons = list(item.get("errors", [])) + list(item.get("warnings", []))
    return "; ".join(str(reason) for reason in reasons) or "review required"


def _is_missing_money_only(item: dict[str, Any]) -> bool:
    return not item.get("errors") and list(item.get("warnings", [])) == ["missing money"]


def _fragment_status(item: dict[str, Any]) -> str:
    return str(item.get("status") or item.get("result", {}).get("status") or "")


def _parsed_amount_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "money": result.get("money"),
        "unit": result.get("unit"),
        "car_units": result.get("car_units"),
        "number": result.get("number"),
    }


def _extract_review_labels(item: dict[str, Any]) -> list[str]:
    """Extract review_label: prefixed classification labels from preprocessing_notes."""
    notes = item.get("preprocessing_notes", [])
    if not isinstance(notes, list):
        notes = []
    labels: list[str] = []
    for note in notes:
        if isinstance(note, str) and note.startswith("review_label:"):
            labels.append(note[len("review_label:"):])
    return labels


def _watchlist_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    entry = {
        "index": item.get("index"),
        "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": item.get("summary", ""),
        "bet_type": result.get("type"),
        "numbers": list(result.get("numbers", [])),
        "stars": list(result.get("stars", [])),
        "reason": _watchlist_reason(item),
        "warnings": list(item.get("warnings", [])),
        "is_missing_money": _is_missing_money_only(item),
        "accepted_automatically": False,
        "review_labels": _extract_review_labels(item),
    }
    entry.update(_parsed_amount_view(result))
    return entry


def _invalid_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    return {
        "index": item.get("index"),
        "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": item.get("summary", ""),
        "numbers": list(result.get("numbers", [])),
        "stars": list(result.get("stars", [])),
        "reason": _reason_text(item),
        "warnings": list(item.get("warnings", [])),
        "errors": list(item.get("errors", [])),
        "is_missing_money": _is_missing_money_only(item),
        "review_labels": _extract_review_labels(item),
    }


def _watchlist_reason(item: dict[str, Any]) -> str:
    if _is_missing_money_only(item):
        return "待觀察 / Watchlist: 缺金額，需人工補 (missing money)"
    reasons = list(item.get("warnings", []))
    joined = "; ".join(str(reason) for reason in reasons)
    return f"待觀察 / Watchlist: {joined}" if joined else "待觀察 / Watchlist: 需人工判斷"


def _status_class(status: str) -> str:
    if status in {"READY_FOR_QUEUE", "WAITING_FOR_HUMAN_CONFIRM", "COMPLETED"}:
        return "ready"
    if status == "NEEDS_REVIEW":
        return "review"
    return "blocked"


def _e(value: str) -> str:
    return html.escape(value, quote=True)
