"""Review console — dashboard layout with wide review cards."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


_LABEL_CN: dict[str, str] = {
    "non_539_candidate": "疑似六合彩 / 港彩",
    "suspected_non_539_due_to_range": "號碼超出 539，疑似六合彩 / 大樂透",
    "suspected_tiantianle": "疑似天天樂",
    "person_name_suffix": "疑似人名或備註",
    "ambiguous_long_token": "疑似客人唸牌黏住",
    "per_star_amount_split": "疑似星別金額拆分",
    "car_bet": "疑似車 / 車號",
    "write_shorthand": "寫法簡寫",
    "tail_write_shorthand": "尾數寫法",
}

_LABEL_COLOR: dict[str, str] = {
    "non_539_candidate": "blue",
    "suspected_non_539_due_to_range": "red",
    "suspected_tiantianle": "yellow",
    "person_name_suffix": "purple",
    "ambiguous_long_token": "purple",
    "per_star_amount_split": "orange",
    "car_bet": "slate",
    "write_shorthand": "orange",
    "tail_write_shorthand": "orange",
}


def build_review_console_model(queue: dict[str, Any], *, queue_path: str | None = None) -> dict[str, Any]:
    preprocessing = queue.get("preprocessing", {})
    preprocessing_summary = preprocessing.get("summary", {})
    audit = queue.get("audit", {})
    safety = _safety_view(queue)
    current = _current_item(queue)
    all_not_ok = list(preprocessing.get("invalid_fragments", []))
    watchlist_source = [item for item in all_not_ok if _fragment_status(item) == "warning"]
    invalid_source = [item for item in all_not_ok if _fragment_status(item) != "warning"]
    candidate_count = int(preprocessing_summary.get("candidate_count", queue.get("summary", {}).get("total", 0)))
    valid_count = int(preprocessing_summary.get("valid_count", queue.get("summary", {}).get("ok", 0)))
    invalid_count = int(preprocessing_summary.get("invalid_unsupported_count", 0))
    watchlist_count = len(watchlist_source)
    needs_review_count = len(invalid_source)
    locally_handled_count = sum(
        1
        for item in queue.get("items", [])
        if (
            item.get("selected_numbers")
            or item.get("selected_columns")
            or item.get("selected_car_number") is not None
            or item.get("filled_amount") is not None
            or item.get("filled_amounts")
        )
    )
    unprocessed_count = max(valid_count - locally_handled_count, 0)
    model = {
        "mode": "local_review_console",
        "status": queue.get("status"),
        "queue_path": queue_path,
        "preprocessing": {
            "status": preprocessing.get("status") or queue.get("preprocessing_status"),
            "candidate_count": candidate_count,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "watchlist_count": watchlist_count,
            "needs_review_count": needs_review_count,
            "warnings_count": int(preprocessing_summary.get("warnings_count", 0)),
            "ignored_metadata_count": int(preprocessing_summary.get("ignored_metadata_count", 0)),
        },
        "comfort_summary": {
            "total_count": candidate_count,
            "assistable_count": valid_count,
            "locally_handled_count": locally_handled_count,
            "unprocessed_count": unprocessed_count,
            "needs_review_count": needs_review_count,
            "invalid_count": invalid_count,
            "watchlist_count": watchlist_count,
            "safety_reminder": "只輔助填入，不會送出或確認",
        },
        "valid_candidates": [_candidate_with_labels(item) for item in preprocessing.get("valid_candidates", [])],
        # Split valid candidates into pending / done based on queue items status
        "done_candidates": _split_done_candidates(preprocessing.get("valid_candidates", []), queue),
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

    # Build cards
    invalid_cards = "".join(_review_card(item, "invalid") for item in model["invalid_fragments"])
    if not invalid_cards:
        invalid_cards = '<div class="empty-block">目前沒有需要人工確認的項目</div>'

    watchlist_cards = "".join(_review_card(item, "watch") for item in model["watchlist"])
    if not watchlist_cards:
        watchlist_cards = '<div class="empty-block">目前沒有待觀察項目</div>'

    # Exclude done candidates from pending rows
    # Use string coercion for index matching (queue items may have int, candidates may have int)
    done_indices = {str(d.get("index") or d.get("item_index") or "") for d in model.get("done_candidates", [])}
    pending = [c for c in model["valid_candidates"] if str(c.get("index") or c.get("item_index") or "") not in done_indices]
    valid_rows = "".join(_candidate_row(item) for item in pending)
    done_rows = "".join(_candidate_row(item) for item in model.get("done_candidates", []))
    done_count = len(model.get("done_candidates", []))
    if not valid_rows:
        valid_rows = '<div class="empty-block">目前沒有正確候選</div>'

    # Assist-panel sync payload — server-rendered from the SAME source as the
    # pending rows, so the panel can never disagree with the table.
    # "</" is escaped so user text can't terminate the surrounding <script>.
    assist_sync_json = json.dumps(
        [_candidate_sync_entry(item) for item in model["valid_candidates"]],
        ensure_ascii=False,
    ).replace("</", "<\\/")

    metadata_rows = "".join(
        f"<li>{_e(str(item.get('raw', item)))}</li>" for item in model["ignored_metadata_lines"][:8]
    ) or "<li>無</li>"
    current = model["queue_view"]["current_item"]
    current_html = _current_item_html(current)
    last_mock_html = _last_mock_html(model["queue_view"].get("last_mock_result"))
    actions_html = "".join(f"<li><code>{_e(action)}</code></li>" for action in model["actions"]) or "<li>無可用指令</li>"
    audit = model["audit"]
    source_json = _e(json.dumps(model, ensure_ascii=False, indent=2))
    comfort = model["comfort_summary"]
    daily_report_stats = json.dumps(
        {
            "candidateCount": model["preprocessing"]["candidate_count"],
            "validCount": model["preprocessing"]["valid_count"],
            "needsReviewCount": model["preprocessing"]["needs_review_count"],
            "invalidCount": model["preprocessing"]["invalid_count"],
            "watchlistCount": model["preprocessing"]["watchlist_count"],
            "safetyReminder": "系統只輔助填入，不會送出或確認",
        },
        ensure_ascii=False,
    )

    # Compute label category counts
    _all_review_items = model["invalid_fragments"] + model["watchlist"]
    _label_counts: dict[str, int] = {}
    for item in _all_review_items:
        for lb in item.get("review_labels", []):
            _label_counts[lb] = _label_counts.get(lb, 0) + 1

    _label_count_html_parts = []
    for lb_key in ("non_539_candidate", "suspected_non_539_due_to_range", "suspected_tiantianle",
                   "person_name_suffix", "ambiguous_long_token", "per_star_amount_split",
                   "car_bet", "write_shorthand", "tail_write_shorthand"):
        cnt = _label_counts.get(lb_key, 0)
        cn = _LABEL_CN.get(lb_key, lb_key)
        color = _LABEL_COLOR.get(lb_key, "slate")
        short = cn.replace("疑似", "").replace("號碼超出 539，", "")
        _label_count_html_parts.append(
            f'<span class="label-count" data-filter="{lb_key}">'
            f'<span class="label-badge {color}">{cn}</span>'
            f'<strong style="font-size:14px;margin-left:4px">{cnt}</strong>'
            f'</span>'
        )
    _label_count_html = " ".join(_label_count_html_parts)

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Betguard 本地審核台</title>
  <style>
    :root {{
      --green: #059669; --green-bg: #ecfdf5; --green-border: #a7f3d0;
      --yellow: #d97706; --yellow-bg: #fffbeb; --yellow-border: #fde68a;
      --red: #dc2626; --red-bg: #fef2f2; --red-border: #fecaca;
      --blue: #2563eb; --blue-bg: #eff6ff; --blue-border: #bfdbfe;
      --purple: #7c3aed; --purple-bg: #f5f3ff; --purple-border: #c4b5fd;
      --orange: #ea580c; --orange-bg: #fff7ed; --orange-border: #fed7aa;
      --slate: #64748b; --slate-dark: #334155; --slate-light: #f1f5f9;
      --white: #ffffff; --radius: 12px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Microsoft JhengHei", "Segoe UI", Arial, sans-serif;
      background: #f0f4f8; color: #1e293b; line-height: 1.6;
      padding: 28px; max-width: 1500px; margin: 0 auto;
    }}

    .page-header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      margin-bottom: 24px;
    }}
    .page-header h1 {{ font-size: 22px; font-weight: 700; }}
    .page-header .subtitle {{ color: var(--slate); font-size: 12px; margin-top: 2px; }}
    .page-header .mode-tag {{
      font-size: 11px; color: var(--slate); background: #e2e8f0;
      padding: 3px 12px; border-radius: 10px; font-weight: 500;
    }}

    .top-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
    .full-width-row {{ display: flex; gap: 20px; margin-bottom: 20px; }}
    .bottom-row {{ display: grid; grid-template-columns: 1fr; gap: 20px; margin-bottom: 20px; }}
    .full-row {{ margin-bottom: 20px; }}

    .card {{
      background: var(--white); border: 1px solid #e2e8f0; border-radius: var(--radius);
      padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .card h2 {{ font-size: 14px; font-weight: 600; margin-bottom: 14px; }}

    .filter-bar {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }}
    .search-box {{
      width: 100%; padding: 12px 16px; border: 2px solid #e2e8f0; border-radius: 10px;
      font-size: 15px; font-family: inherit; outline: none; background: #f8fafc;
    }}
    .search-box:focus {{ border-color: var(--blue); box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }}
    .filter-chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{
      display: inline-block; padding: 5px 14px; border-radius: 16px;
      font-size: 12px; font-weight: 500; cursor: pointer; background: #f1f5f9;
      color: var(--slate-dark); border: 1px solid #e2e8f0;
      transition: all 0.15s;
    }}
    .chip:hover {{ background: #e2e8f0; }}
    .chip.active {{ background: var(--red); color: #fff; border-color: var(--red); }}
    .review-card.hidden {{ display: none; }}
    .label-count {{ display: inline-flex; align-items: center; gap: 2px; cursor: pointer; opacity: 0.7; transition: opacity 0.15s; }}
    .label-count:hover {{ opacity: 1; }}

    .stats-inline {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 13px; }}
    .stats-inline .stat-chip {{
      display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
      border-radius: 14px; background: #f1f5f9; border: 1px solid #e2e8f0;
      font-size: 12px; white-space: nowrap;
    }}
    .stats-inline .stat-chip strong {{ font-weight: 700; }}
    .stats-inline .stat-chip.green {{ border-color: #bbf7d0; background: #f0fdf4; }}
    .stats-inline .stat-chip.green strong {{ color: #059669; }}
    .stats-inline .stat-chip.red {{ border-color: #fecaca; background: #fef2f2; }}
    .stats-inline .stat-chip.red strong {{ color: #dc2626; }}
    .stats-inline .stat-chip.amber {{ border-color: #fed7aa; background: #fff7ed; }}
    .stats-inline .stat-chip.amber strong {{ color: #c2410c; }}
    .stats-inline .stat-chip.blue {{ border-color: #bfdbfe; background: #eff6ff; }}
    .stats-inline .stat-chip.blue strong {{ color: #2563eb; }}

    .safety-card {{
      background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
      border: 1px solid #86efac; border-radius: var(--radius);
      padding: 10px 16px; margin-bottom: 16px;
      display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    }}
    .safety-card h2 {{ color: #166534; font-size: 13px; font-weight: 600; white-space: nowrap; }}
    .safety-card ul {{ display: flex; flex-wrap: wrap; gap: 8px; list-style: none; padding: 0; margin: 0; }}
    .safety-card li {{ color: #166534; font-size: 12px; font-weight: 500; }}

    /* Review cards - WIDE horizontal */
    .card-list {{ display: flex; flex-direction: column; gap: 16px; }}
    .review-card {{
      display: grid; grid-template-columns: 6px 1fr auto;
      background: var(--white); border-radius: 12px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      transition: box-shadow 0.15s;
      min-height: 120px;
    }}
    .review-card:hover {{ box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
    .review-card .card-bar {{
      width: 6px; min-height: 100%;
    }}
    .review-card .card-bar.blue {{ background: var(--blue); }}
    .review-card .card-bar.red {{ background: var(--red); }}
    .review-card .card-bar.yellow {{ background: var(--yellow); }}
    .review-card .card-bar.purple {{ background: var(--purple); }}
    .review-card .card-bar.orange {{ background: var(--orange); }}
    .review-card .card-bar.watch {{ background: var(--yellow); }}
    .review-card .card-bar.invalid {{ background: var(--red); }}

    .review-card .card-body {{
      padding: 22px 28px; display: flex; flex-direction: column; gap: 12px;
    }}
    .card-meta {{ display: flex; align-items: center; gap: 10px; }}
    .card-meta .card-idx {{ font-size: 12px; font-weight: 600; color: var(--slate); }}
    .card-meta .card-line {{ font-size: 11px; color: var(--slate); background: #f1f5f9; padding: 2px 8px; border-radius: 8px; }}
    .review-card .card-fragment {{
      font-size: 32px; font-weight: 800; color: #0f172a; line-height: 1.3;
      word-break: break-word; letter-spacing: 0.5px;
    }}
    .review-card .card-human-review {{
      font-size: 12px; font-weight: 600; color: #dc2626;
      background: #fef2f2; border: 1px solid #fecaca;
      padding: 3px 8px; border-radius: 6px;
      display: inline-flex; align-items: center; gap: 4px;
    }}
    .review-card .card-labels {{ display: flex; flex-wrap: wrap; gap: 4px; }}
    .review-card .card-en-label {{
      font-size: 11px; color: #94a3b8; margin-top: 4px;
    }}
    .card-state-btns {{
      display: flex; flex-direction: column; gap: 4px; margin-top: 6px;
    }}
    .card-state-btns button {{
      font-size: 11px; padding: 3px 8px; border-radius: 6px; cursor: pointer;
      border: 1px solid #e2e8f0; background: #fff; white-space: nowrap;
    }}
    .btn-copy-text {{ font-size:11px;padding:3px 8px;border:1px solid #cbd5e1;border-radius:4px;background:#f8fafc;color:#475569;cursor:pointer; }}
    .btn-copy-text:hover {{ background:#e2e8f0; }}
    .btn-state-done.active {{ background: #059669; color: #fff; border-color: #059669; }}
    .btn-state-manual.active {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    .btn-edit {{ color: #64748b; }}
    .btn-edit:hover {{ background: #f1f5f9; }}
    .edit-panel {{
      margin-top: 8px; padding: 8px; background: #f8fafc;
      border: 1px solid #e2e8f0; border-radius: 8px;
    }}
    .review-card.state-done {{ opacity: 0.6; }}
    .review-card.state-manual {{ border-left: 4px solid var(--blue); opacity: 0.8; }}

    .review-card .card-right {{
      padding: 18px 20px; display: flex; flex-direction: column;
      align-items: flex-end; justify-content: center; gap: 10px;
      border-left: 1px solid #f1f5f9; min-width: 120px;
    }}
    .review-card .card-status {{
      font-size: 13px; font-weight: 600; padding: 5px 14px; border-radius: 14px;
    }}
    .review-card .card-status.invalid {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }}
    .review-card .card-status.watch {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow-border); }}

    .review-card .card-details {{
      margin-top: 8px; font-size: 12px; grid-column: 2 / -1; padding: 0 24px 14px;
    }}
    .review-card .card-details summary {{
      color: var(--slate); cursor: pointer; font-size: 12px; font-weight: 500;
    }}
    .review-card .card-details div {{
      margin: 4px 0; padding-left: 12px; border-left: 2px solid #e2e8f0;
      font-size: 12px; color: var(--slate);
    }}
    .dismiss-btn {{
      font-size: 11px; font-weight: 500; cursor: pointer;
      padding: 4px 12px; border-radius: 12px;
      background: #f8fafc; color: var(--slate);
      border: 1px solid #e2e8f0; transition: all 0.15s;
    }}
    .dismiss-btn:hover {{ background: var(--green-bg); color: var(--green); border-color: var(--green); }}
    .history-btn {{
      font-size: 11px; font-weight: 500; cursor: pointer;
      padding: 4px 12px; border-radius: 12px;
      background: #eff6ff; color: var(--blue);
      border: 1px solid #bfdbfe; transition: all 0.15s;
    }}
    .history-btn:hover {{ background: var(--blue); color: #fff; }}
    .history-btn.done {{ background: var(--green-bg); color: var(--green); border-color: var(--green); cursor: default; }}
    .review-card.dismissed {{ display: none; }}
    .controls-bar {{
      display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; align-items: center;
    }}
    .controls-bar button {{
      font-size: 11px; padding: 3px 10px; border-radius: 10px; border: 1px solid #e2e8f0;
      background: #fff; color: var(--slate-dark); cursor: pointer;
    }}
    .controls-bar button:hover {{ background: #f1f5f9; }}

    .label-badge {{
      display: inline-block; padding: 6px 18px; border-radius: 16px; font-size: 15px;
      color: #fff; font-weight: 600; letter-spacing: 0.3px;
    }}
    .label-badge.blue {{ background: var(--blue); }}
    .label-badge.red {{ background: var(--red); }}
    .label-badge.yellow {{ background: var(--yellow); }}
    .label-badge.purple {{ background: var(--purple); }}
    .label-badge.orange {{ background: var(--orange); }}

    .badge {{
      display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
      border-radius: 20px; font-size: 11px; font-weight: 600; color: #fff;
    }}
    .badge.valid {{ background: var(--green); }}
    .badge.watch {{ background: var(--yellow); }}
    .badge.invalid {{ background: var(--red); }}

    .empty-block {{
      text-align: center; color: var(--slate); padding: 40px 20px;
      font-size: 14px; border: 1px dashed #e2e8f0; border-radius: var(--radius);
    }}

    .collapsed-section .card-list {{ display: none; }}
    .collapsed-section .filter-bar {{ display: none; }}
    .collapsed-section .controls-bar {{ display: none; }}
    .collapsed-section .label-counts {{ display: none; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{
      background: #f8fafc; font-weight: 600; color: var(--slate-dark); font-size: 11px;
      padding: 10px 8px; border-bottom: 2px solid #e2e8f0; text-align: left;
    }}
    td {{ padding: 8px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
    tr:hover td {{ background: #f8fafc; }}

    pre {{
      background: #1e293b; color: #e2e8f0; border-radius: 8px; padding: 16px;
      overflow: auto; max-height: 400px; font-size: 11px; font-family: "Consolas", monospace;
    }}
    code {{ background: #f1f5f9; border-radius: 3px; padding: 1px 5px; font-size: 11px; }}
    ul {{ padding-left: 20px; }} li {{ margin-bottom: 4px; }}
    .danger {{ color: var(--red); font-weight: 600; }}
    .empty {{ color: var(--slate); font-style: italic; }}

    @media (max-width: 1100px) {{
      .bottom-row {{ grid-template-columns: 1fr; }}
      .full-width-row {{ flex-direction: column; }}
      .top-row {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 700px) {{
      body {{ padding: 12px; }}
      .full-width-row {{ flex-direction: column; }}
    }}

    /* ---- 剛剛下注紀錄 side panel ---- */
    .history-panel {{
      position: fixed; right: 0; top: 0; width: 360px; height: 100vh;
      background: var(--white); box-shadow: -2px 0 12px rgba(0,0,0,0.08);
      z-index: 1000; transform: translateX(100%); transition: transform 0.25s;
      display: flex; flex-direction: column;
    }}
    .history-panel.open {{ transform: translateX(0); }}
    .history-panel-header {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 16px 20px; border-bottom: 1px solid #e2e8f0;
      background: #f8fafc; flex-shrink: 0;
    }}
    .history-panel-header h3 {{ font-size: 14px; font-weight: 600; }}
    .history-panel-header button {{
      font-size: 11px; padding: 3px 10px; border-radius: 8px;
      border: 1px solid #e2e8f0; background: #fff; cursor: pointer; color: var(--slate);
    }}
    .history-panel-header button:hover {{ background: #fee2e2; color: var(--red); border-color: #fecaca; }}
    .history-panel-body {{
      flex: 1; overflow-y: auto; padding: 12px 16px;
    }}
    .history-item {{
      background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
      padding: 10px 12px; margin-bottom: 8px; font-size: 12px;
    }}
    .history-item .hi-time {{ color: var(--slate); font-size: 10px; }}
    .history-item .hi-fragment {{ font-weight: 600; font-size: 14px; margin: 4px 0; }}
    .history-item .hi-summary {{ color: var(--slate); font-size: 11px; }}
    .history-item .hi-type {{ font-size: 10px; margin-top: 4px; }}
    .history-item .hi-del {{
      float: right; cursor: pointer; color: var(--slate); font-size: 10px;
      padding: 2px 6px; border-radius: 4px; border: 1px solid #e2e8f0; background: #fff;
    }}
    .history-item .hi-del:hover {{ color: var(--red); border-color: var(--red); }}

    .history-inline-item {{
      background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px;
      padding: 10px 14px; margin-bottom: 8px; font-size: 12px;
    }}
    .history-inline-item .hi-time {{ color: var(--slate); font-size: 10px; }}
    .history-inline-item .hi-fragment {{ font-weight: 700; font-size: 15px; margin: 2px 0; }}
    .history-inline-item .hi-numbers {{ color: var(--blue); font-size: 13px; font-weight: 600; margin: 2px 0; }}
    .history-inline-item .hi-amounts {{ font-size: 12px; margin: 2px 0; }}
    .history-inline-item .hi-amount {{ display: inline-block; background: #dbeafe; color: #1e40af; padding: 1px 6px; border-radius: 4px; margin-right: 4px; font-size: 11px; }}
    .history-inline-item .hi-type {{ font-size: 11px; color: var(--green); font-weight: 600; margin-top: 4px; }}
    .daily-report-box {{ margin-top: 10px; padding: 10px; background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; color: var(--ink); white-space: pre-line; }}
    .history-toggle {{
      position: fixed; right: 12px; bottom: 20px; z-index: 1001;
      background: var(--blue); color: #fff; border: none; border-radius: 50%;
      width: 44px; height: 44px; font-size: 18px; cursor: pointer;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    .history-toggle.has-items {{ background: var(--green); }}
    .history-toggle .count-badge {{
        position: absolute; top: -4px; right: -4px;
        background: var(--red); color: #fff; font-size: 9px; font-weight: 700;
        width: 18px; height: 18px; border-radius: 50%; display: flex;
        align-items: center; justify-content: center;
      }}

      /* ---- paste-to-review block ---- */
      .paste-block {{
        background: var(--white); border: 2px dashed #e2e8f0; border-radius: var(--radius);
        padding: 16px 20px; margin-bottom: 20px;
      }}
      .paste-block summary {{
        font-size: 14px; font-weight: 600; cursor: pointer; color: var(--slate-dark);
      }}
      .paste-block summary:hover {{ color: var(--blue); }}
      .paste-block .paste-body {{ margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }}
      .paste-block textarea {{
        width: 100%; min-height: 120px; padding: 12px; border: 1px solid #e2e8f0;
        border-radius: 8px; font-family: inherit; font-size: 14px; resize: vertical;
      }}
      .paste-block textarea:focus {{ border-color: var(--blue); outline: none; }}
      .paste-block .paste-buttons {{ display: flex; gap: 8px; }}
      .paste-block .paste-buttons button {{
        font-size: 13px; padding: 6px 16px; border-radius: 8px; cursor: pointer; font-weight: 500;
        border: 1px solid #e2e8f0; background: var(--white);
      }}
      .paste-block .btn-create {{
        background: var(--blue) !important; color: #fff !important; border-color: var(--blue) !important;
      }}
      .paste-block .btn-create:hover {{ background: #1d4ed8 !important; }}
      .paste-block .btn-clear {{ color: var(--slate); }}
      .paste-block .btn-clear:hover {{ background: #fee2e2; color: var(--red); }}
      .paste-block .paste-warn {{ font-size: 11px; color: var(--slate); }}

      /* ---- assist-fill button + modal ---- */
      .assist-btn {{
        font-size: 11px; padding: 2px 10px; border-radius: 10px; cursor: pointer;
        background: var(--blue); color: #fff; border: none; font-weight: 500;
      }}
      .assist-btn:hover {{ background: #1d4ed8; }}
      .assist-btn.done {{ background: var(--green); cursor: default; }}
      .assist-modal-overlay {{
        display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4);
        z-index: 2000; align-items: center; justify-content: center;
      }}
      .assist-modal-overlay.show {{ display: flex; }}
      .assist-modal {{
        background: #fff; border-radius: 12px; padding: 24px; max-width: 440px;
        width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.2);
      }}
      .assist-modal h3 {{ font-size: 15px; margin-bottom: 12px; }}
      .assist-modal .am-preview {{ font-size: 12px; color: var(--slate); margin-bottom: 16px; }}
      .assist-modal .am-actions {{ display: flex; gap: 8px; justify-content: flex-end; }}
      .assist-modal .am-actions button {{
        font-size: 13px; padding: 6px 14px; border-radius: 8px; cursor: pointer;
        border: 1px solid #e2e8f0; background: #fff;
      }}
      .assist-modal .btn-confirm {{ background: var(--blue); color: #fff; border-color: var(--blue); }}
      .assist-modal .btn-confirm:hover {{ background: #1d4ed8; }}
      .assist-modal .btn-cancel {{ color: var(--slate); }}
      .assist-modal .am-status {{ font-size: 12px; margin-top: 8px; }}
    </style>
</head>
<body data-queue-path="{_e(str(model.get('queue_path', '')))}">
  <div class="page-header">
    <div>
      <h1>🛡️ Betguard 本地審核台</h1>
      <p class="subtitle">僅供本地審核 — 不合格項目不會進入 approved_fill_queue</p>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-open-site" id="open-site-btn" onclick="openBettingSite()" style="font-size:14px;padding:8px 16px">🌐 開啟下牌網站</button>
      <span id="open-site-status" style="font-size:12px;color:var(--slate);margin-left:8px"></span>
      <span class="mode-tag">僅本機模式</span>
    </div>
  </div>

  <div class="full-width-row" style="margin-bottom:16px">
    <section class="card" style="border-left: 4px solid var(--blue);flex:3">
      <h2 style="display:flex;align-items:center;gap:12px">
        <span class="badge valid" style="background:#dbeafe;color:#1e40af;font-size:14px">📝 待輔助填入</span>
      </h2>
      <div style="overflow-x:auto"><table id="pending-table"><thead><tr><th>#</th><th>原始片段</th><th>摘要</th><th>類型</th><th></th></tr></thead><tbody id="pending-tbody">{valid_rows}</tbody></table></div>
      <div class="empty-block" id="pending-empty" style="display:none">全部已輔助填入 ✅</div>
    </section>
    <section class="card" style="border-left: 4px solid var(--green);flex:1">
      <h2><span class="badge valid" style="font-size:12px">✅ 已輔助填入</span></h2>
      <div style="overflow-x:auto;max-height:300px;overflow-y:auto"><table id="done-table"><thead><tr><th>#</th><th>原始片段</th><th>摘要</th></tr></thead><tbody id="done-tbody">{done_rows}</tbody></table></div>
      <div class="empty-block" id="done-empty">尚無已輔助填入項目</div>
    </section>
  </div>

  <details class="paste-block" open>
    <summary>📋 貼上牌單建立審核</summary>
    <div class="paste-body">
      <textarea id="paste-input" placeholder="貼上 LINE / 聊天室牌單..."></textarea>
      <div class="paste-buttons">
        <button class="btn-create" onclick="submitPaste()">建立審核</button>
        <button class="btn-clear" onclick="document.getElementById('paste-input').value=''">清空</button>
      </div>
      <div class="paste-warn" id="paste-warn"></div>
    </div>
  </details>

  <div class="top-row">
    <section class="card" style="padding:10px 16px">
      <div class="stats-inline">
        <span class="stat-chip">📊 共 <strong>{comfort["total_count"]}</strong> 筆</span>
        <span class="stat-chip green">✅ 可填入 <strong>{comfort["assistable_count"]}</strong></span>
        <span class="stat-chip blue">📝 未處理 <strong>{comfort["unprocessed_count"]}</strong></span>
        <span class="stat-chip red needs-total">⚠️ 需確認 <strong>{comfort["needs_review_count"]}</strong></span>
        <span class="stat-chip amber">👀 Watchlist <strong>{comfort["watchlist_count"]}</strong></span>
        <span class="stat-chip">❌ Invalid <strong>{comfort["invalid_count"]}</strong></span>
        <span class="stat-chip needs-unprocessed" style="font-size:11px"><strong>0</strong> 筆待手動處理</span>
        <span class="stat-chip needs-processed" style="font-size:11px;color:#64748b"><strong>0</strong> 筆已手動處理</span>
      </div>
    </section>
    <section class="safety-card">
      <h2>🔒 安全狀態</h2>
      <ul>
        <li>✅ 不自動送出</li>
        <li>✅ 不自動確認</li>
        <li>✅ 不自動完成</li>
        <li>✅ 不合格項目不進輔助填入</li>
        <li>✅ 每筆仍需人工核對</li>
      </ul>
    </section>
  </div>

  <div class="bottom-row">
    <section class="card" style="border-left: 4px solid var(--red);">
      <h2><span class="badge invalid">❌ 需確認</span> 需要人工確認</h2>
      <div class="filter-bar">
        <input type="text" class="search-box" placeholder="搜尋原文、號碼、分類..." oninput="filterCards()">
        <div class="filter-chips">
          <span class="chip active" data-filter="all" onclick="setFilter('all')">全部 {model["preprocessing"]["needs_review_count"] + model["preprocessing"]["watchlist_count"]}</span>
          <span class="chip" data-filter="non_539_candidate" onclick="setFilter('non_539_candidate')">疑似六合彩 {_label_counts.get("non_539_candidate", 0)}</span>
          <span class="chip" data-filter="suspected_non_539_due_to_range" onclick="setFilter('suspected_non_539_due_to_range')">超出 539 {_label_counts.get("suspected_non_539_due_to_range", 0)}</span>
          <span class="chip" data-filter="suspected_tiantianle" onclick="setFilter('suspected_tiantianle')">疑似天天樂 {_label_counts.get("suspected_tiantianle", 0)}</span>
          <span class="chip" data-filter="person_name_suffix" onclick="setFilter('person_name_suffix')">人名備註 {_label_counts.get("person_name_suffix", 0)}</span>
          <span class="chip" data-filter="ambiguous_long_token" onclick="setFilter('ambiguous_long_token')">唸牌黏住 {_label_counts.get("ambiguous_long_token", 0)}</span>
          <span class="chip" data-filter="per_star_amount_split" onclick="setFilter('per_star_amount_split')">星別金額 {_label_counts.get("per_star_amount_split", 0)}</span>
          <span class="chip" data-filter="car_bet" onclick="setFilter('car_bet')">疑似車 / 車號 {_label_counts.get("car_bet", 0)}</span>
          <span class="chip" data-filter="write_shorthand" onclick="setFilter('write_shorthand')">寫法簡寫 {_label_counts.get("write_shorthand", 0)}</span>
          <span class="chip" data-filter="tail_write_shorthand" onclick="setFilter('tail_write_shorthand')">尾數寫法 {_label_counts.get("tail_write_shorthand", 0)}</span>
          <span class="chip" data-filter="uncategorized" onclick="setFilter('uncategorized')">未分類</span>
        </div>
      </div>
      <div class="label-counts" style="margin:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        {_label_count_html}
      </div>
      <div class="controls-bar">
        <button onclick="var cards=document.querySelectorAll('.review-card.state-done,.review-card.state-manual');cards.forEach(function(c){{c.classList.toggle('hidden')}})">切換顯示已處理</button>
      </div>
      <div class="card-list" id="review-cards">{invalid_cards}</div>
    </section>
  </div>

  <div class="top-row">
    <section class="card">
      <h2>📋 佇列</h2>
      <p><strong>狀態：</strong> {_e(str(model["queue_view"]["status"]))}</p>
      <p><strong>進度：</strong> {_e(str(model["queue_view"]["current_index"]))} / {_e(str(model["queue_view"]["item_count"]))}</p>
      {current_html}{last_mock_html}
    </section>
    <section class="card">
      <h2>📋 下注紀錄 <button onclick="exportHistory()" style="font-size:11px;padding:2px 8px;margin-left:8px">📥 匯出 CSV</button><button onclick="downloadDailyReport()" style="font-size:11px;padding:2px 8px;margin-left:4px">🧾 今日檢查報告</button></h2>
      <div id="history-inline-body" style="max-height:400px;overflow-y:auto"></div>
      <div id="daily-report-preview" class="daily-report-box" style="display:none"></div>
    </section>
  </div>

  <details class="card full-row" style="margin-bottom:20px">
    <summary>📄 原始資料 (JSON)</summary>
    <pre>{source_json}</pre>
  </details>
<script>
  var currentFilter = 'all';
  var dailyReportStats = {daily_report_stats};
  // Server-rendered assist-panel sync candidates (same source as pending rows)
  var assistSyncCandidates = {assist_sync_json};

  // ---- localStorage + review card state ----
  var batchId = (window.location.href.match(/queue_([^/.]+)\.json/) || [])[1]
    || (window.location.href.match(/review_([^/.]+)\.html/) || [])[1]
    || (document.body.getAttribute('data-queue-path') || '').replace(/[^a-zA-Z0-9_\-]/g, '_').substring(0, 64)
    || 'betguard-' + location.pathname.replace(/[^a-zA-Z0-9_\-]/g, '_').substring(0, 64);
  var cardStateKey = 'betguard-card-state-' + batchId;
  function loadCardStates() {{
    try {{ return JSON.parse(localStorage.getItem(cardStateKey) || '{{}}'); }} catch(e) {{ return {{}}; }}
  }}
  function saveCardStates(states) {{
    try {{ localStorage.setItem(cardStateKey, JSON.stringify(states)); }} catch(e) {{}}
  }}
  function getCardState(idx) {{ return loadCardStates()[String(idx)] || null; }}
  function setCardState(idx, state) {{
    var states = loadCardStates();
    if (state === null) {{ delete states[String(idx)]; }}
    else {{ states[String(idx)] = state; }}
    saveCardStates(states);
    updateCardButtons(idx);
    updateReviewCounts();
  }}
  function updateCardButtons(idx) {{
    var card = document.querySelector('.review-card[data-batch-id="' + idx + '"]');
    if (!card) return;
    var state = getCardState(idx);
    var doneBtn = card.querySelector('.btn-state-done');
    var manualBtn = card.querySelector('.btn-state-manual');
    if (doneBtn) doneBtn.classList.toggle('active', state === 'done');
    if (manualBtn) manualBtn.classList.toggle('active', state === 'manual');
    card.classList.toggle('state-done', state === 'done');
    card.classList.toggle('state-manual', state === 'manual');
  }}
  function toggleCardState(idx, newState) {{
    var cur = getCardState(idx);
    setCardState(idx, cur === newState ? null : newState);
    updateReviewCounts();
  }}
  function copyCardText(idx, evt) {{
    var card = document.querySelector('.review-card[data-batch-id="' + idx + '"]');
    if (!card) return;
    var frag = card.getAttribute('data-fragment') || '';
    if (!frag) return;
    // Use Clipboard API
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(frag).then(function () {{
        var btn = evt.target;
        var orig = btn.textContent;
        btn.textContent = '✅ 已複製';
        btn.style.color = '#059669';
        setTimeout(function () {{ btn.textContent = orig; btn.style.color = ''; }}, 1500);
      }}).catch(function () {{ prompt('請手動複製:', frag); }});
    }} else {{
      prompt('請手動複製:', frag);
    }}
  }}
  function updateReviewCounts() {{
    var total = 0, unprocessed = 0, processed = 0;
    document.querySelectorAll('.review-card[data-batch-id]').forEach(function (c) {{
      var s = getCardState(c.getAttribute('data-batch-id'));
      total++;
      if (s === 'done' || s === 'manual') processed++;
      else unprocessed++;
    }});
    var totalEl = document.querySelector('.stat-chip.needs-total strong');
    var unprocEl = document.querySelector('.stat-chip.needs-unprocessed strong');
    var procEl = document.querySelector('.stat-chip.needs-processed strong');
    if (totalEl) totalEl.textContent = total;
    if (unprocEl) unprocEl.textContent = unprocessed;
    if (procEl) procEl.textContent = processed;
  }}
  (function() {{
    var states = loadCardStates();
    Object.keys(states).forEach(function(idx) {{ updateCardButtons(idx); }});
    updateReviewCounts();
  }})();
  // ---- edit panel ----
  function toggleEditPanel(idx) {{
    var panel = document.getElementById('edit-panel-' + idx);
    if (!panel) return;
    var isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : '';
    if (!isOpen) {{
      var textarea = panel.querySelector('textarea');
      if (textarea && !textarea.value) {{
        var card = document.querySelector('.review-card[data-batch-id="' + idx + '"]');
        if (card) {{
          var frag = card.querySelector('.card-fragment');
          if (frag) textarea.value = frag.textContent || '';
        }}
      }}
    }}
  }}
  function submitReparse(idx) {{
    var panel = document.getElementById('edit-panel-' + idx);
    var textarea = panel ? panel.querySelector('textarea') : null;
    var resultEl = document.getElementById('reparse-result-' + idx);
    var btn = document.getElementById('reparse-btn-' + idx);
    if (!textarea || !textarea.value.trim()) {{
      if (resultEl) resultEl.innerHTML = '<span style="color:#dc2626">請輸入修正文字</span>';
      return;
    }}
    if (btn) {{ btn.disabled = true; btn.textContent = '解析中...'; }}
    if (resultEl) resultEl.innerHTML = '<span style="color:#64748b">⏳ 重新解析中...</span>';
    fetch('/manual-reparse', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{text: textarea.value.trim(), game: 'auto'}})
    }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
      if (btn) {{ btn.disabled = false; btn.textContent = '重新解析'; }}
      if (data.ok) {{
        var nums = (data.numbers || []).map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(', ');
        var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
        var amtHtml = '';
        var amts = data.amounts || {{}};
        for (var s in amts) {{ amtHtml += (starNames[parseInt(s)] || s) + ' ' + amts[s] + '元 '; }}
        // Store for addManualCandidateRow
        _lastReparseResult = data;
        _lastReparseText = textarea.value || '';
        window._lastReparseColumns = data.columns || null;
        // Auto-add manual candidate row to pending section if reparse succeeds
        if (data.manual_candidate_id && data.type) {{
          var betType = data.type || 'normal';
          try {{
            if (typeof window.addManualCandidateRowToPending === 'function') {{
              window.addManualCandidateRowToPending(data.manual_candidate_id, _lastReparseText, data.summary || '', data.numbers || [], data.stars || [], data.amounts || {{}}, betType);
            }} else {{
              // Fallback: reload page so the row appears from server state
              console.warn('addManualCandidateRowToPending not defined, using addManualCandidateRow');
              addManualCandidateRow();
            }}
          }} catch(e) {{ console.log('auto-add pending row error:', e); }}
        }}
        if (resultEl) resultEl.innerHTML = '<div style="color:#059669;font-weight:600">✅ 解析通過 — 已加入可輔助填入區</div>'
          + '<div>號碼: ' + nums + '</div>'
          + '<div>金額: ' + (amtHtml || data.money + '元') + '</div>'
          + '<div style="font-size:11px;color:#64748b">' + (data.summary || '') + '</div>'
          + (data.manual_candidate_id ? '<p style="margin-top:4px;font-size:11px;color:#2563eb">已加入可輔助填入區，請從上方表格點擊「輔助填入」</p>' : '');
      }} else {{
        if (resultEl) resultEl.innerHTML = '<span style="color:#dc2626">❌ ' + (data.error || '解析失敗') + '</span>'
          + (data.reason ? ' <span style="color:#64748b;font-size:10px">(' + data.reason + ')</span>' : '');
      }}
    }}).catch(function(err) {{
      if (btn) {{ btn.disabled = false; btn.textContent = '重新解析'; }}
      if (resultEl) resultEl.innerHTML = '<span style="color:#dc2626">❌ 錯誤: ' + err.message + '</span>';
    }});
  }}
  function setFilter(f) {{
    currentFilter = f;
    document.querySelectorAll('.filter-chips .chip').forEach(function(c) {{ c.classList.toggle('active', c.dataset.filter === f); }});
    filterCards();
  }}
  function filterCards() {{
    var q = (document.querySelector('.search-box') || {{}}).value || '';
    q = q.toLowerCase();
    var cards = document.querySelectorAll('#review-cards .review-card');
    cards.forEach(function(card) {{
      var labels = (card.dataset.labels || '').toLowerCase();
      var frag = (card.dataset.fragment || '').toLowerCase();
      var matchFilter;
      if (currentFilter === 'uncategorized') {{
        matchFilter = (labels.trim() === '');
      }} else {{
        matchFilter = currentFilter === 'all' || labels.indexOf(currentFilter) >= 0;
      }}
      var matchSearch = !q || frag.indexOf(q) >= 0 || labels.indexOf(q) >= 0;
      card.classList.toggle('hidden', !(matchFilter && matchSearch));
    }});
  }}
  document.querySelector('.search-box').addEventListener('input', filterCards);

  // ---- 剛剛下注紀錄 ----
  var historyStorageKey = 'betguard-history-' + batchId;
  function loadHistory() {{
    try {{ return JSON.parse(localStorage.getItem(historyStorageKey) || '[]'); }} catch(e) {{ return []; }}
  }}
  function saveHistory(list) {{
    try {{ localStorage.setItem(historyStorageKey, JSON.stringify(list)); }} catch(e) {{}}
  }}
  function addToHistory(btn) {{
    var card = btn.closest('.review-card');
    if (!card) return;
    var idx = card.dataset.batchId;
    var history = loadHistory();
    if (history.some(function(h) {{ return h.idx === idx; }})) {{
      alert('此筆已記錄');
      return;
    }}
    var frag = card.querySelector('.card-fragment') ? card.querySelector('.card-fragment').textContent : '';
    var summary = '';
    var details = card.querySelector('.card-details div');
    if (details) summary = details.textContent.replace('解析：', '').trim();
    var typeEl = card.querySelector('.card-status');
    var kind = typeEl ? typeEl.className.replace('card-status ', '') : '';
    var kindMap = {{invalid: '需人工確認', watch: '待觀察'}};
    var typeText = kindMap[kind] || '正常';
    if (kind === 'invalid') typeText = typeEl ? typeEl.textContent.trim() : typeText;
    history.unshift({{
      idx: idx, time: new Date().toLocaleTimeString(),
      fragment: frag, summary: summary, type: typeText
    }});
    saveHistory(history);
    btn.textContent = '已記錄'; btn.classList.add('done');
    btn.onclick = function() {{ alert('此筆已記錄'); }};
    renderHistoryPanel();
    updateToggleBadge();
  }}
  function removeFromHistory(idx) {{
    var history = loadHistory().filter(function(h) {{ return h.idx !== idx; }});
    saveHistory(history);
    renderHistoryPanel();
    updateToggleBadge();
    var card = document.querySelector('.review-card[data-batch-id=\"' + idx + '\"]');
    if (card) {{
      var btn = card.querySelector('.history-btn');
      if (btn) {{ btn.textContent = '已手動下注'; btn.classList.remove('done'); btn.onclick = function() {{ addToHistory(btn); }}; }}
    }}
  }}
  function clearHistory() {{
    if (!confirm('確定清除所有紀錄？')) return;
    localStorage.removeItem(historyStorageKey);
    document.querySelectorAll('.review-card .history-btn.done').forEach(function(btn) {{
      btn.textContent = '已手動下注'; btn.classList.remove('done');
      btn.onclick = function() {{ addToHistory(btn); }};
    }});
    renderHistoryPanel(); renderHistoryInline(); updateToggleBadge();
  }}
  function toggleHistoryPanel() {{
    var panel = document.getElementById('history-side-panel');
    panel.classList.toggle('open');
  }}
  function renderHistoryPanel() {{
    var body = document.getElementById('history-panel-body');
    if (!body) return;
    var history = loadHistory();
    if (history.length === 0) {{
      body.innerHTML = '<div class=\"empty-block\">目前沒有紀錄</div>';
      return;
    }}
    var html = '';
    history.forEach(function(h) {{
      html += '<div class=\"history-item\">' +
        '<button class=\"hi-del\" onclick=\"removeFromHistory(\\'' + h.idx + '\\')\">✕</button>' +
        '<div class=\"hi-time\">' + h.time + ' · #' + h.idx + '</div>' +
        '<div class=\"hi-fragment\">' + (h.fragment || '') + '</div>' +
        '<div class=\"hi-summary\">' + (h.summary || '') + '</div>' +
        '<div class=\"hi-type\">' + (h.type || '') + '</div>' +
        '</div>';
    }});
    body.innerHTML = html;
  }}
  function renderHistoryInline() {{
    var body = document.getElementById('history-inline-body');
    if (!body) return;
    var history = loadHistory();
    if (history.length === 0) {{
      body.innerHTML = '<div class="empty-block">尚無輔助填入紀錄</div>';
      return;
    }}
    var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
    var html = '';
    history.forEach(function(h) {{
      var nums = (h.numbers || []).map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(', ');
      var amtHtml = '';
      var amts = h.amounts || {{}};
      for (var s in amts) {{
        amtHtml += '<span class="hi-amount">' + (starNames[parseInt(s)] || s) + ' ' + amts[s] + '</span> ';
      }}
      html += '<div class="history-inline-item">' +
        '<div class="hi-time">' + h.time + ' · #' + h.idx + '</div>' +
        '<div class="hi-fragment">' + (h.fragment || '') + '</div>' +
        '<div class="hi-numbers">號碼: ' + (nums || '') + '</div>' +
        '<div class="hi-amounts">' + (amtHtml || '') + '</div>' +
        '<div class="hi-type">' + (h.type || '') + '</div>' +
        '</div>';
    }});
    body.innerHTML = html;
  }}

  function updateToggleBadge() {{
    var toggle = document.getElementById('history-toggle-btn');
    var badge = document.getElementById('history-count-badge');
    if (!toggle || !badge) return;
    var count = loadHistory().length;
    badge.textContent = count;
    badge.style.display = count > 0 ? 'flex' : 'none';
    toggle.classList.toggle('has-items', count > 0);
  }}
  // ---- auto-collapse empty sections ----
  (function() {{
    // Collapse Needs Review section if empty
    var invalidCards = document.getElementById('review-cards');
    if (invalidCards && invalidCards.querySelectorAll('.review-card:not(.dismissed)').length === 0) {{
      var nrSection = invalidCards.closest('.card');
      if (nrSection) {{
        nrSection.classList.add('collapsed-section');
        var h2 = nrSection.querySelector('h2');
        if (h2) h2.textContent = h2.textContent + '（目前沒有需要人工確認的項目）';
      }}
    }}
    // Hide filter chips with count 0
    document.querySelectorAll('.filter-chips .chip').forEach(function(chip) {{
      var match = chip.textContent.match(/\(\d+\)/) || chip.textContent.match(/\d+$/);
      if (match && parseInt(match[0]) === 0 && !chip.classList.contains('active')) {{
        chip.style.display = 'none';
      }}
    }});
  }})();

  (function() {{
    renderHistoryPanel(); renderHistoryInline(); updateToggleBadge();
    var history = loadHistory();
    var doneIds = history.map(function(h) {{ return h.idx; }});
    document.querySelectorAll('#review-cards .review-card, .card-list .review-card').forEach(function(card) {{
      var idx = card.dataset.batchId;
      if (doneIds.indexOf(idx) >= 0) {{
        var btn = card.querySelector('.history-btn');
        if (btn) {{ btn.textContent = '已記錄'; btn.classList.add('done'); btn.onclick = function() {{ alert('此筆已記錄'); }}; }}
      }}
    }});
  }})();

  // ---- paste-to-review ----
  function submitPaste() {{
    var text = document.getElementById('paste-input').value.trim();
    var warn = document.getElementById('paste-warn');
    if (!text) {{ warn.textContent = '請先貼上牌單內容'; return; }}
    warn.textContent = '處理中...';
    fetch('/workbench', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
      body: 'text=' + encodeURIComponent(text) + '&game=auto&redirect=1',
      redirect: 'follow'
    }}).then(function(resp) {{
      if (resp.redirected) {{
        window.location.href = resp.url;
        return;
      }}
      return resp.text();
    }}).then(function(html) {{
      if (html) {{ document.open(); document.write(html); document.close(); }}
    }}).catch(function(err) {{
      warn.textContent = '無法建立審核: ' + err.message + ' (請確認工作臺已啟動)';
    }});
  }}

    // ---- row splitting: pending → done ----
      function isRowAssisted(idx) {{
        var history = loadHistory();
        return history.some(function(h) {{ return String(h.idx) === String(idx) && h.type === '已輔助填入，待人工送出'; }});
      }}
      function moveRowToDone(idx) {{
        var row = document.querySelector('#pending-tbody .assist-row-' + idx);
        if (!row) {{
          // Row not in pending — may have already been moved, or idx is stale.
          // Clean up stale history entries that don't match any current row.
          return;
        }}
        var doneTbody = document.getElementById('done-tbody');
        if (doneTbody) {{
          doneTbody.appendChild(row);
          var btn = row.querySelector('.assist-btn');
          if (btn) {{ btn.textContent = '已輔助填入'; btn.classList.add('done'); }}
        }}
        updateSectionVisibility();
      }}
    function updateSectionVisibility() {{
      var pendingTbody = document.getElementById('pending-tbody');
      var doneTbody = document.getElementById('done-tbody');
      var pendingEmpty = document.getElementById('pending-empty');
      var doneEmpty = document.getElementById('done-empty');
      if (pendingTbody && pendingEmpty) {{
        var hasPending = pendingTbody.querySelectorAll('tr').length > 0;
        pendingEmpty.style.display = hasPending ? 'none' : '';
        document.getElementById('pending-table').style.display = hasPending ? '' : 'none';
      }}
      if (doneTbody && doneEmpty) {{
        var hasDone = doneTbody.querySelectorAll('tr').length > 0;
        doneEmpty.style.display = hasDone ? 'none' : '';
        document.getElementById('done-table').style.display = hasDone ? '' : 'none';
      }}
    }}
    (function() {{
      // Clean up stale history: remove records whose idx doesn't match any current candidate row
      var allIdx = [];
      document.querySelectorAll('#pending-tbody tr, #done-tbody tr').forEach(function(r) {{
        var m = r.className.match(/assist-row-(\d+)/);
        if (m) allIdx.push(m[1]);
      }});
      var history = loadHistory();
      var clean = history.filter(function(h) {{
        return h.type !== '已輔助填入，待人工送出' || allIdx.indexOf(String(h.idx)) >= 0;
      }});
      if (clean.length !== history.length) {{
        saveHistory(clean);
      }}
      // Move already-done rows on page load (only if they exist in pending table)
      clean.filter(function(h) {{ return h.type === '已輔助填入，待人工送出'; }})
        .forEach(function(h) {{
          var row = document.querySelector('#pending-tbody .assist-row-' + String(h.idx));
          if (row) moveRowToDone(String(h.idx));
        }});
    }})();

    // ---- export history ----
    function csvCell(value) {{
      var s = String(value == null ? '' : value);
      var unsafeCsvChars = new RegExp('[",' + String.fromCharCode(13) + String.fromCharCode(10) + ']');
      if (unsafeCsvChars.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
      return s;
    }}

    function assistedHistoryItems(history) {{
      return history.filter(function(h) {{ return String(h.type || '').indexOf('已輔助填入') >= 0; }});
    }}

    function exportHistory() {{
      var history = loadHistory();
      if (history.length === 0) {{ alert('尚無紀錄可匯出'); return; }}
      var NL = String.fromCharCode(10);
      var csv = '日期,時間,序號,原始片段,號碼,二星金額,三星金額,四星金額,狀態,人工核對提醒' + NL;
      history.forEach(function(h) {{
        var nums = (h.numbers || []).map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(' ');
        var amts = h.amounts || {{}};
        var row = [
          new Date().toISOString().slice(0, 10),
          h.time || '',
          h.idx || '',
          h.fragment || '',
          nums,
          amts['2'] || '',
          amts['3'] || '',
          amts['4'] || '',
          h.type || '',
          dailyReportStats.safetyReminder
        ];
        csv += row.map(csvCell).join(',') + NL;
      }});
      var blob = new Blob([csv], {{type: 'text/csv;charset=utf-8'}});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'betguard_history_' + new Date().toISOString().slice(0,10) + '.csv';
      a.click(); URL.revokeObjectURL(url);
    }}

    function buildDailyReportText() {{
      var history = loadHistory();
      var assistedCount = assistedHistoryItems(history).length;
      var todayTotal = dailyReportStats.candidateCount || 0;
      var unprocessedCount = Math.max(todayTotal - assistedCount, 0);
      var NL = String.fromCharCode(10);
      return [
        'BetGuard 今日檢查報告',
        '日期: ' + new Date().toISOString().slice(0, 10),
        '今日總筆數: ' + todayTotal,
        '已輔助填入數: ' + assistedCount,
        '尚未處理數: ' + unprocessedCount,
        'Needs Review: ' + (dailyReportStats.needsReviewCount || 0),
        'Invalid: ' + (dailyReportStats.invalidCount || 0),
        'Watchlist: ' + (dailyReportStats.watchlistCount || 0),
        '安全提醒: ' + dailyReportStats.safetyReminder
      ].join(NL);
    }}

    function downloadDailyReport() {{
      var text = buildDailyReportText();
      var preview = document.getElementById('daily-report-preview');
      if (preview) {{
        preview.textContent = text;
        preview.style.display = '';
      }}
      var blob = new Blob([text], {{type: 'text/plain;charset=utf-8'}});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'betguard_daily_check_' + new Date().toISOString().slice(0,10) + '.txt';
      a.click(); URL.revokeObjectURL(url);
    }}

    // ---- Chinese error messages ----
    function translateError(err) {{
      var m = (err || '').toLowerCase();
      if (m.indexOf('browser start failed') >= 0 || m.indexOf('browser error') >= 0)
        return '無法開啟瀏覽器，請重新執行 install_or_repair.bat';
      if (m.indexOf('session is in state') >= 0 && m.indexOf('idle') >= 0)
        return '請先按「開啟下牌網站」登入並切到 539 或天天樂二三四星頁面';
      if (m.indexOf('already active') >= 0 || m.indexOf('cancel it first') >= 0)
        return '上一筆仍在處理中，請稍候或按「取消本次輔助填入」';
      if (m.indexOf('page is not available') >= 0)
        return '下牌網站視窗已關閉，請重新按「開啟下牌網站」';
      if (m.indexOf('page check failed') >= 0)
        return '請確認目前在下牌網站的 539 或天天樂二三四星連碰頁面';
      if (m.indexOf('fill execution error') >= 0 || m.indexOf('timeout') >= 0 || m.indexOf('timed out') >= 0)
        return '填入失敗，可能網路不穩或頁面已變更，請重整下牌網站頁面後再試';
      if (m.indexOf('no numbers') >= 0 || m.indexOf('missing numbers') >= 0)
        return '找不到號碼按鈕，請確認目前在正確的二三四星頁面';
      if (m.indexOf('no amounts') >= 0 || m.indexOf('no money') >= 0)
        return '找不到金額欄位，請確認目前在二三四星連碰頁面（不是柱碰）';
      return err;
    }}

    // ---- manual correction reparse ----
    var _lastReparseResult = null;
    var _lastReparseText = '';
    function addManualCandidateRow() {{
      if (!_lastReparseResult || !_lastReparseResult.manual_candidate_id) return;
      var cid = _lastReparseResult.manual_candidate_id;
      var nums = (_lastReparseResult.numbers || []).map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(', ');
      var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
      var amtHtml = '';
      var amts = _lastReparseResult.amounts || {{}};
      for (var s in amts) {{ amtHtml += (starNames[parseInt(s)] || s) + ' ' + amts[s] + '元 '; }}
      var summary = _lastReparseResult.summary || '';
      var fragment = (_lastReparseText || summary).substring(0, 40);

      // Build assist button data (numbers as JSON arrays for previewAssist)
      var numbersJson = JSON.stringify(_lastReparseResult.numbers || []);
      var starsJson = JSON.stringify(_lastReparseResult.stars || []);
      var amountsJson = JSON.stringify(_lastReparseResult.amounts || {{}});

      var tbody = document.getElementById('pending-tbody');
      if (!tbody) return;

      var row = document.createElement('tr');
      row.className = 'assist-row-' + cid;
      row.setAttribute('data-manual-id', cid);

      var cells = ['<td>' + cid + '</td>',
        '<td style="font-size:10px;color:#64748b">🔧 人工修正</td>',
        '<td style="font-weight:600">' + fragment + '</td>',
        '<td style="font-size:11px">' + (nums || '') + '</td>',
        '<td style="font-size:10px">' + (amtHtml || '') + '</td>',
        '<td></td>'];
      row.innerHTML = cells.join('');

      var btn = document.createElement('button');
      btn.className = 'assist-btn';
      btn.setAttribute('data-assist-index', cid);
      btn.textContent = '輔助填入';
      btn.onclick = function() {{
        previewAssist(cid, fragment, summary, _lastReparseResult.numbers || [], _lastReparseResult.stars || [], _lastReparseResult.amounts || {{}}, _lastReparseResult.type || 'normal');
      }};
      row.lastElementChild.appendChild(btn);

      tbody.appendChild(row);
      document.getElementById('pending-empty').style.display = 'none';
      document.getElementById('pending-table').style.display = '';
    }}

    // v0.5.32: mark a pending item as manually done (no real site op)
    window.markManualDone = function(btn) {{
      var idx = btn.getAttribute('data-assist-index');
      var row = btn.closest('tr');
      if (!idx) return;
      btn.disabled = true;
      btn.textContent = '⏳';

      // Determine request body: manual_id or queue_path+item_index
      var reqBody;
      if (idx && String(idx).indexOf('manual-') === 0) {{
        reqBody = {{manual_candidate_id: idx}};
      }} else {{
        var qp = (typeof queuePath !== 'undefined') ? queuePath : (document.body.getAttribute('data-queue-path') || '');
        reqBody = {{queue_path: qp, item_index: parseInt(idx)}};
      }}

      fetch('/assist-fill/manual-done', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(reqBody)
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        if (data.ok) {{
          // Move row to done section
          if (row) {{
            var doneBody = document.getElementById('done-tbody');
            if (doneBody) {{
              // Update row appearance
              row.querySelectorAll('button').forEach(function(b) {{ b.disabled = true; }});
              var cells = row.querySelectorAll('td');
              if (cells.length >= 5) {{
                cells[4].innerHTML = '<span style="color:var(--green);font-size:11px">✅ ' + (data.message || '已手動下牌') + '</span>';
              }}
              doneBody.appendChild(row);
            }}
            document.getElementById('done-empty').style.display = 'none';
          }}
          // Show next-item banner
          refreshPendingQueue();
          var banner = document.getElementById('next-item-banner');
          if (banner) {{
            if (window._pendingQueue && window._pendingQueue.length > 0) {{
              banner.innerHTML = '<span style="color:var(--slate)">完成本筆 ➜ 還有 <strong>' + window._pendingQueue.length + '</strong> 筆可輔助填入</span> '
                + '<button onclick="showNextPending()" style="background:var(--blue);color:#fff;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;font-weight:600">📝 預覽下一筆</button>';
              banner.style.display = 'flex';
            }} else {{
              banner.innerHTML = '✅ 全部已輔助填入完成';
              banner.style.display = 'flex';
            }}
          }}
        }} else {{
          alert(data.error || '標記失敗');
          btn.disabled = false;
          btn.textContent = '✓ 已手動下牌';
        }}
      }}).catch(function(err) {{
        alert('連線錯誤: ' + err);
        btn.disabled = false;
        btn.textContent = '✓ 已手動下牌';
      }});
    }};

    // v0.5.32: add a manual-reparse candidate to pending section by ID
    window.addManualCandidateRowToPending = function(cid, text, summary, numbers, stars, amounts, betType) {{
      try {{
        var tbody = document.getElementById('pending-tbody');
        if (!tbody) return;
        // Avoid duplicates
        if (tbody.querySelector('tr[data-assist-index="' + cid + '"]')) return;
        var numbersDisplay = (numbers || []).map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(', ');
        var label = summary || (numbersDisplay + ' | 人工修正');
        var fragment = (text || summary || '').substring(0, 40);
        var tr = document.createElement('tr');
        tr.setAttribute('data-assist-index', cid);
        tr.innerHTML = '<td>' + cid + '</td>'
          + '<td style="font-size:10px;color:#64748b">🔧 人工修正</td>'
          + '<td style="font-weight:600">' + fragment + '</td>'
          + '<td style="font-size:11px">' + (numbersDisplay || '') + '</td>'
          + '<td></td>';
        var btn = document.createElement('button');
        btn.className = 'assist-btn';
        btn.setAttribute('data-assist-index', cid);
        btn.setAttribute('data-fragment', fragment);
        btn.setAttribute('data-summary', label);
        btn.setAttribute('data-numbers', JSON.stringify((betType === 'column' && window._lastReparseColumns) ? window._lastReparseColumns : (numbers || [])));
        btn.setAttribute('data-stars', JSON.stringify(stars || []));
        btn.setAttribute('data-amounts', JSON.stringify(amounts || {{}}));
        btn.setAttribute('data-bet-type', betType || 'normal');
        btn.textContent = '輔助填入';
        btn.onclick = function() {{ quickAssistBtn(this); }};
        tr.lastElementChild.appendChild(btn);
        // Also add manual-done button
        var mdBtn = document.createElement('button');
        mdBtn.style.cssText = 'font-size:11px;background:#64748b;color:#fff;border:none;padding:3px 8px;border-radius:4px;cursor:pointer;margin-left:4px';
        mdBtn.textContent = '✓ 已手動下牌';
        mdBtn.setAttribute('data-assist-index', cid);
        mdBtn.onclick = function() {{ window.markManualDone(mdBtn); }};
        tr.lastElementChild.appendChild(mdBtn);
        tbody.appendChild(tr);
        document.getElementById('pending-empty').style.display = 'none';
        document.getElementById('pending-table').style.display = '';
      }} catch(e) {{ console.log('addManualCandidateRowToPending error:', e); }}
    }};

    // ---- assist fill (one-button auto flow) ----
    var assistItem = null;
    var assistInProgress = false;
    var queuePath = document.body.getAttribute('data-queue-path') || '';

    function openBettingSite() {{
      var btn = document.getElementById('open-site-btn');
      var statusEl = document.getElementById('open-site-status');
      if (btn) {{ btn.disabled = true; btn.textContent = '⏳ 開啟中...'; }}
      if (statusEl) statusEl.textContent = '⏳ 正在開啟下牌網站...';
      fetch('/assist-fill/open-site', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{}})
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        if (data.ok) {{
          if (btn) {{
            btn.disabled = false;
            btn.textContent = '🌐 重新開啟下牌網站';
            btn.style.background = '';
          }}
          if (data.reused) {{
            if (statusEl) statusEl.textContent = '✅ 下牌網站仍在線上，請確認目前在二三四星連碰頁面';
          }} else {{
            if (statusEl) statusEl.textContent = '✅ 下牌網站已開啟，請手動登入並切到 539 或天天樂二三四星連碰頁面';
          }}
        }} else {{
          if (btn) {{ btn.disabled = false; btn.textContent = '🌐 開啟下牌網站（重試）'; }}
          var errMsg = '❌ 開啟失敗: ' + (data.error || 'unknown');
          if (statusEl) statusEl.textContent = errMsg;
          console.error(errMsg);
        }}
      }}).catch(function(err) {{
        assistInProgress = false;
        setAssistStage('error');
        if (statusEl) {{
          statusEl.textContent = '❌ 連線錯誤: ' + (err.message || 'unknown');
          statusEl.className = 'am-status assist-error';
        }}
        var btnStart = document.getElementById('assist-btn-start');
        if (btnStart) {{ btnStart.style.display = ''; btnStart.disabled = false; btnStart.textContent = '重試輔助填入'; }}
        console.error('[assist]', err);
      }});
    }}

    function previewAssist(idx, fragment, summary, numbers, stars, amounts, betType) {{
      assistItem = {{idx: idx, fragment: fragment, summary: summary, numbers: numbers, stars: stars, amounts: amounts, betType: betType || ''}};
      assistInProgress = false;

      // Column/zhu-peng items → show column preview + enable start
      if (betType === 'column') {{
        var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
        var preview = '<p style="color:#d97706;font-weight:600">🔧 柱碰 / 注碰項目</p>'
          + '<p><strong>原始：</strong>' + fragment + '</p>';
        if (numbers && numbers.length) {{
          preview += '<p><strong>號碼：</strong>';
          for (var ci = 0; ci < numbers.length; ci++) {{
            var colNums = numbers[ci];
            if (colNums && colNums.length) {{
              preview += '第' + (ci+1) + '柱: ' + colNums.map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(',') + (ci < numbers.length-1 ? '｜' : '');
            }}
          }}
          preview += '</p>';
        }}
        var hasAmt = false;
        for (var s in amounts) {{
          preview += '<p><strong>' + (starNames[parseInt(s)] || s) + '金額：</strong>' + amounts[s] + ' 元</p>';
          hasAmt = true;
        }}
        if (!hasAmt) preview += '<p><strong style="color:var(--red)">⚠️ 無金額資料</strong></p>';
        preview += '<p style="color:var(--slate);font-size:11px">⚠️ 請先按「開啟下牌網站」登入並切到天天樂/539 二三四星住碰頁面。系統只會填入號碼與金額，不送出、不確認。</p>';
        document.getElementById('assist-preview').innerHTML = preview;
        document.getElementById('assist-modal-overlay').classList.add('show');
        setAssistStage('start');
        return;
      }}

      var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
      var preview = '<p><strong>原始：</strong>' + fragment + '</p>'
        + '<p><strong>號碼：</strong>' + numbers.map(function(n) {{ return (n < 10 ? '0' : '') + n; }}).join(',') + '</p>';
      var hasAmounts = false;
      for (var s in amounts) {{
        preview += '<p><strong>' + (starNames[parseInt(s)] || s) + '金額：</strong>' + amounts[s] + ' 元</p>';
        hasAmounts = true;
      }}
      if (!hasAmounts) {{
        preview += '<p><strong style=\"color:var(--red)\">⚠️ 無金額資料</strong></p>';
      }}
      preview += '<p style=\"color:var(--slate);font-size:11px\">⚠️ 請先按「開啟下牌網站」登入並切到 539 或天天樂二三四星連碰頁面。系統只會填入號碼與金額，不送出、不確認。</p>';
      document.getElementById('assist-preview').innerHTML = preview;
      setAssistStage('start');
      document.getElementById('assist-modal-overlay').classList.add('show');
    }}

    function setAssistStage(stage) {{
      document.getElementById('assist-status').className = 'am-status assist-' + stage;
      var statusEl = document.getElementById('assist-status');
      var btnStart = document.getElementById('assist-btn-start');
      var btnCancel = document.getElementById('assist-btn-cancel');
      [btnStart, btnCancel].forEach(function(b) {{ if(b) b.style.display = 'none'; }});
      if (stage === 'start') {{
        statusEl.textContent = '';
        if (btnStart) {{ btnStart.style.display = ''; btnStart.disabled = false; btnStart.textContent = '開始輔助填入'; }}
        if (btnCancel) btnCancel.style.display = 'none';
      }} else if (stage === 'executing') {{
        statusEl.textContent = '⏳ 檢查頁面並填入中...';
        if (btnCancel) btnCancel.style.display = '';
      }} else if (stage === 'done') {{
        statusEl.textContent = '✅ 已輔助填入，待人工送出';
      }} else if (stage === 'error') {{
        // statusEl set by caller
      }}
    }}

    function closeAssistModal() {{
      document.getElementById('assist-modal-overlay').classList.remove('show');
      assistItem = null;
    }}

    function startAssist() {{
      if (!assistItem || assistInProgress) return;
      assistInProgress = true;
      var statusEl = document.getElementById('assist-status');
      setAssistStage('executing');
      var btn = document.getElementById('assist-btn-start');
      // Build request body: queue-based or manual candidate
      var reqBody;
      if (assistItem.idx && String(assistItem.idx).indexOf('manual-') === 0) {{
        reqBody = {{manual_candidate_id: assistItem.idx}};
      }} else {{
        reqBody = {{queue_path: queuePath, item_index: parseInt(assistItem.idx), bet_type: assistItem.betType || 'normal'}};
      }}
      // Abort after 20 seconds to avoid infinite loading
      var controller = new AbortController();
      var timeoutId = setTimeout(function() {{ controller.abort(); }}, 20000);
      fetch('/assist-fill/start', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(reqBody),
        signal: controller.signal
      }}).then(function(r) {{ clearTimeout(timeoutId); return r.json(); }}).then(function(data) {{
        if (data.ok) {{
          // Success: update history and UI
          try {{
            var history = loadHistory();
            if (!history.some(function(h) {{ return String(h.idx) === String(assistItem.idx) && h.type === '已輔助填入，待人工送出'; }})) {{
              history.unshift({{
                  idx: assistItem.idx, time: new Date().toLocaleTimeString(),
                  fragment: assistItem.fragment || '', summary: assistItem.summary || '',
                  numbers: assistItem.numbers || [], amounts: assistItem.amounts || {{}},
                  type: '已輔助填入，待人工送入'
                }});
                saveHistory(history);
                renderHistoryPanel(); renderHistoryInline(); updateToggleBadge();
                moveRowToDone(assistItem.idx);
            }}
          }} catch(e) {{ console.error('assist history error:', e); }}
          try {{
            var rowBtn = document.querySelector('.assist-btn[data-assist-index=\"' + assistItem.idx + '\"]');
            if (rowBtn) {{ rowBtn.textContent = '已輔助填入'; rowBtn.classList.add('done'); }}
          }} catch(e) {{ console.log('rowBtn error:', e); }}
          assistInProgress = false;
          setAssistStage('done');
          markCurrentDoneAndNext();
        }} else {{
          var errMsg = data.error || 'unknown';
          if (data.warnings && data.warnings.length) errMsg += ' | ' + data.warnings.join('; ');
          // Append diagnostic info if available
          var diag = data.diagnostic;
          var diagText = '';
          if (diag) {{
            if (diag.page_available === false) {{
              diagText = ' | 瀏覽器頁面已失效，請重新按「開啟下牌網站」';
            }} else {{
              var parts = [];
              if (diag.url) parts.push('網址: ' + diag.url.split('/').pop());
              if (diag.page_has_tiantianle) parts.push('✅ 天天樂');
              else if (diag.page_has_539) parts.push('✅ 539');
              else parts.push('⚠️ 未偵測到遊戲');
              if (diag.page_has_lianpeng) parts.push('連碰模式');
              else if (diag.page_has_danpeng) parts.push('⚠️ 單碰模式（請切到連碰）');
              else if (diag.page_has_zhupeng) parts.push('⚠️ 柱碰模式（請切到連碰）');
              else parts.push('⚠️ 未偵測到頁面模式（請確認在連碰頁）');
              if (diag.numbers_found_in_inputs && diag.numbers_found_in_inputs.length)
                parts.push('號碼已顯示: ' + diag.numbers_found_in_inputs.join(','));
              diagText = ' | ' + parts.join('; ');
            }}
            errMsg += diagText;
          }}
          if (statusEl) {{
            statusEl.textContent = '❌ ' + translateError(errMsg);
            statusEl.className = 'am-status assist-error';
          }}
          if (errMsg.indexOf('already active') >= 0) {{
            var bc = document.getElementById('assist-btn-cancel');
            if (bc) bc.style.display = '';
          }}
          setAssistStage('error');
          assistInProgress = false;
          var btnStart = document.getElementById('assist-btn-start');
          if (btnStart) {{ btnStart.style.display = ''; btnStart.disabled = false; btnStart.textContent = '重試輔助填入'; }}
          var btnCancel = document.getElementById('assist-btn-cancel');
          if (btnCancel && errMsg.indexOf('already active') < 0) btnCancel.style.display = 'none';
        }}
      }}).catch(function(err) {{
        clearTimeout(timeoutId);
        if (statusEl) {{
          var errName = err.name || '';
          if (errName === 'AbortError') {{
            statusEl.textContent = '❌ 操作逾時（超過 20 秒）。請確認已登入、已切到正確遊戲、在二三四星連碰頁面（非柱碰/單碰）。';
          }} else {{
            statusEl.textContent = '❌ 連線錯誤: ' + (err.message || 'unknown');
          }}
          statusEl.className = 'am-status assist-error';
        }}
        setAssistStage('error');
        assistInProgress = false;
        var btnStart = document.getElementById('assist-btn-start');
        if (btnStart) {{ btnStart.style.display = ''; btnStart.disabled = false; btnStart.textContent = '重試輔助填入'; }}
        var btnCancel = document.getElementById('assist-btn-cancel');
        if (btnCancel) btnCancel.style.display = 'none';
      }});
    }}

    function cancelAssist() {{
      var statusEl = document.getElementById('assist-status');
      statusEl.textContent = '⏳ 取消中...';
      fetch('/assist-fill/cancel', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{}})
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        statusEl.textContent = '已取消';
        setAssistStage('start');
        assistInProgress = false;
        if (assistItem) {{
          previewAssist(assistItem.idx, assistItem.fragment, assistItem.summary, assistItem.numbers, assistItem.stars, assistItem.amounts, assistItem.betType || '');
        }}
      }}).catch(function(err) {{
        statusEl.textContent = '已取消（可能有殘留視窗）';
        setAssistStage('start');
        assistInProgress = false;
      }});
    }}

    // ── v0.5.32: auto-expand first pending row ──
    // Auto-expand first pending item preview (preview only, no fill)
    // v0.5.33: one-click assist — open modal + start fill immediately
    function quickAssist(idx, fragment, summary, numbers, stars, amounts, betType) {{
      assistItem = {{idx: idx, fragment: fragment, summary: summary, numbers: numbers, stars: stars, amounts: amounts, betType: betType || 'normal'}};
      assistInProgress = false;
      // Open modal directly in executing state
      document.getElementById('assist-modal-overlay').classList.add('show');
      var statusEl = document.getElementById('assist-status');
      if (statusEl) statusEl.textContent = '⏳ 檢查頁面並填入中...';
      setAssistStage('executing');
      startAssist();
    }}

    function quickAssistBtn(btn) {{
      var idx = btn.getAttribute('data-assist-index');
      var fragment = btn.getAttribute('data-fragment') || '';
      var summary = btn.getAttribute('data-summary') || '';
      try {{ var numbers = JSON.parse(btn.getAttribute('data-numbers') || '[]'); }} catch(e) {{ var numbers = []; }}
      try {{ var stars = JSON.parse(btn.getAttribute('data-stars') || '[]'); }} catch(e) {{ var stars = []; }}
      try {{ var amounts = JSON.parse(btn.getAttribute('data-amounts') || '{{}}'); }} catch(e) {{ var amounts = {{}}; }}
      var betType = btn.getAttribute('data-bet-type') || 'normal';
      // Column/zhu-peng: use existing preview flow (still needs manual tab switch guidance)
      if (betType === 'column') {{
        openAssistPreviewFromBtn(btn);
        return;
      }}
      quickAssist(idx, fragment, summary, numbers, stars, amounts, betType);
    }}

    // Open assist preview from button data attributes (no eval, no click)
    function openAssistPreviewFromBtn(btn) {{
      var idx = btn.getAttribute('data-assist-index');
      var fragment = btn.getAttribute('data-fragment') || '';
      var summary = btn.getAttribute('data-summary') || '';
      try {{ var numbers = JSON.parse(btn.getAttribute('data-numbers') || '[]'); }} catch(e) {{ var numbers = []; }}
      try {{ var stars = JSON.parse(btn.getAttribute('data-stars') || '[]'); }} catch(e) {{ var stars = []; }}
      try {{ var amounts = JSON.parse(btn.getAttribute('data-amounts') || '{{}}'); }} catch(e) {{ var amounts = {{}}; }}
      var betType = btn.getAttribute('data-bet-type') || 'normal';
      previewAssist(idx, fragment, summary, numbers, stars, amounts, betType);
    }}

    function autoExpandFirstPending() {{
      var rows = document.querySelectorAll('#pending-tbody tr[data-assist-index]');
      if (rows.length > 0) {{
        var row = rows[0];
        // Highlight + scroll into view only — never start assist
        row.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        row.style.transition = 'background 0.5s';
        row.style.background = '#fef3c7';
        setTimeout(function() {{ row.style.background = ''; }}, 2000);
      }}
    }}
    document.addEventListener('DOMContentLoaded', function() {{
      setTimeout(autoExpandFirstPending, 300);
    }});

    // ── v0.5.32: batch next-item flow ──
    var _pendingQueue = [];
    var _currentPendingIdx = -1;

    function refreshPendingQueue() {{
      _pendingQueue = [];
      var rows = document.querySelectorAll('#pending-tbody tr[data-assist-index]');
      rows.forEach(function(r) {{
        _pendingQueue.push(r.getAttribute('data-assist-index'));
      }});
      window._pendingQueue = _pendingQueue;
      if (_pendingQueue.length > 0) {{
        document.getElementById('pending-empty').style.display = 'none';
      }}
    }}

    function showNextPending() {{
      refreshPendingQueue();
      window._pendingQueue = _pendingQueue;
      if (_pendingQueue.length === 0) {{
        document.getElementById('pending-empty').style.display = '';
        document.getElementById('next-item-banner').style.display = 'none';
        return;
      }}
      // Highlight first pending row — user clicks manually
      var rows = document.querySelectorAll('#pending-tbody tr[data-assist-index]');
      if (rows.length > 0) {{
        var banner = document.getElementById('next-item-banner');
        if (banner) banner.style.display = 'none';
        var row = rows[0];
        row.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        row.style.transition = 'background 0.5s';
        row.style.background = '#fef3c7';
        setTimeout(function() {{ row.style.background = ''; }}, 2000);
      }}
    }}

    function markCurrentDoneAndNext() {{
      // Move the current assist item from pending to done
      var lastAssistIdx = assistItem ? assistItem.idx : null;
      if (lastAssistIdx) {{
        var row = document.querySelector('#pending-tbody tr[data-assist-index="' + lastAssistIdx + '"]');
        if (row) {{
          // Move to done section
          var doneBody = document.getElementById('done-tbody');
          if (doneBody) {{
            doneBody.appendChild(row);
          }}
          // Update the assist button
          var btn = row.querySelector('.assist-btn');
          if (btn) {{ btn.textContent = '已輔助填入'; btn.classList.add('done'); btn.disabled = true; }}
        }}
        // Show the done section header
        document.getElementById('done-empty').style.display = 'none';
      }}
      // Show next-item banner
      refreshPendingQueue();
      var banner = document.getElementById('next-item-banner');
      if (banner) {{
        if (_pendingQueue.length > 0) {{
          banner.innerHTML = '<span style="color:var(--slate)">完成本筆 ➜ 還有 <strong>' + _pendingQueue.length + '</strong> 筆可輔助填入</span> '
            + '<button onclick="showNextPending()" style="background:var(--blue);color:#fff;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;font-weight:600">📝 預覽下一筆</button>';
          banner.style.display = 'flex';
        }} else {{
          banner.innerHTML = '✅ 全部已輔助填入完成';
          banner.style.display = 'flex';
        }}
      }}
    }}

    // Add next-item banner below pending section
    (function addNextBanner() {{
      var pendingSection = document.getElementById('pending-tbody');
      if (!pendingSection) return;
      var banner = document.createElement('div');
      banner.id = 'next-item-banner';
      banner.style.cssText = 'display:none;align-items:center;justify-content:space-between;padding:12px 16px;margin-top:12px;background:#f0f9ff;border-radius:8px;border:1px solid #bfdbfe;font-size:14px';
      pendingSection.parentNode.insertBefore(banner, pendingSection.parentNode.querySelector('#pending-empty') || pendingSection.nextSibling);
      // Initial queue refresh
      refreshPendingQueue();

      // Publish state to assist-panel via localStorage + BroadcastChannel
      try {{
        // Primary source: server-rendered sync data — the exact same list the
        // pending rows were built from. DOM scraping below is only a fallback
        // for legacy pages that lack assistSyncCandidates.
        var validOut = [];
        if (typeof assistSyncCandidates !== "undefined" && Array.isArray(assistSyncCandidates)) {{
          validOut = assistSyncCandidates;
        }}
        if (!validOut.length) document.querySelectorAll("#pending-tbody .assist-btn").forEach(function(btn) {{
            var nums = []; try {{ nums = JSON.parse(btn.getAttribute("data-numbers") || "[]"); }} catch(_) {{}}
            var stars = []; try {{ stars = JSON.parse(btn.getAttribute("data-stars") || "[]"); }} catch(_) {{}}
            var amts = {{}}; try {{ amts = JSON.parse(btn.getAttribute("data-amounts") || "{{}}"); }} catch(_) {{}}
            var idx = btn.getAttribute("data-assist-index");
            validOut.push({{
              index: parseInt(idx) || 0,
              raw: btn.getAttribute("data-fragment") || "",
              summary: btn.getAttribute("data-summary") || "",
              bet_type: btn.getAttribute("data-bet-type") || "normal",
              numbers: nums,
              stars: stars,
              amounts: amts
            }});
        }});
        var state = {{
          queue_path: queuePath || (document.body.getAttribute("data-queue-path") || ""),
          valid_candidates: validOut,
          timestamp: Date.now()
        }};
        localStorage.setItem("betguard_assist_panel_state", JSON.stringify(state));
        // Also POST to server (cross-browser sync)
        try {{
          fetch("/api/assist-panel/state", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify(state)
          }}).catch(function(e) {{ console.warn("assist-panel state POST failed:", e); }});
        }} catch(e) {{}}
        try {{
          var ch = new BroadcastChannel("betguard_assist_panel");
          ch.postMessage(state);
          ch.close();
        }} catch(e) {{}}
      }} catch(e) {{}}
    }})();
  </script>
  <div id="history-side-panel" class="history-panel">
    <div class="history-panel-header">
      <h3>📋 剛剛下注紀錄</h3>
      <div style="display:flex;gap:4px">
        <button onclick="exportHistory()">📥 匯出 CSV</button>
        <button onclick="downloadDailyReport()">🧾 今日檢查報告</button>
        <button onclick="clearHistory()">清空全部</button>
      </div>
    </div>
    <div id="history-panel-body" class="history-panel-body"></div>
  </div>

  <div id="assist-modal-overlay" class="assist-modal-overlay">
    <div class="assist-modal">
      <h3>🔧 輔助填入預覽</h3>
      <div class="am-preview" id="assist-preview"></div>
      <div class="am-status" id="assist-status"></div>
      <div class="am-actions">
        <button class="btn-confirm" id="assist-btn-start" onclick="startAssist()">開始輔助填入</button>
        <button class="btn-cancel" id="assist-btn-cancel" onclick="cancelAssist()" style="display:none">取消本次輔助填入</button>
        <button class="btn-cancel" onclick="closeAssistModal()">關閉</button>
      </div>
    </div>
  </div>
</body>
</html>
"""



def _review_card(item: dict[str, Any], kind: str) -> str:
    labels = item.get("review_labels", [])
    label_spans = []
    bar_color = kind
    for lb in labels:
        cn = _LABEL_CN.get(lb, lb)
        color = _LABEL_COLOR.get(lb, "slate")
        if kind == "invalid":
            bar_color = color if color != "slate" else kind
        label_spans.append(f'<span class="label-badge {color}">{_e(cn)}</span>')
    label_html = " ".join(label_spans) if label_spans else '<span class="empty">無分類標籤</span>'

    en_label_text = ", ".join(labels) if labels else "無"

    if kind == "invalid":
        errors = item.get("errors", [])
        status_cn = "需要人工確認" if errors else "需人工判斷"
    else:
        status_cn = "待觀察"

    fragment = _e(str(item.get("original_fragment", "")))
    idx = _e(str(item.get("index", "")))
    parsed = _e(str(item.get("parsed_summary", "")))
    reason = _e(str(item.get("reason", "")))

    lb_attr = " ".join(labels) if labels else ""
    line_no = item.get("line_no", "")
    line_tag = f'<span class="card-line">第 {line_no} 行</span>' if line_no else ""
    return (
        f"<div class='review-card' data-labels='{lb_attr}' data-fragment='{fragment}' data-batch-id='{_e(idx)}'>"
        f"<div class='card-bar {bar_color}'></div>"
        f"<div class='card-body'>"
        f"<div class='card-meta'><span class='card-idx'>#{idx}</span>{line_tag}</div>"
        f"<div class='card-fragment'>{fragment}</div>"
        f"<div class='card-labels'>{label_html}</div>"
        f"<div class='card-human-review'>⚠️ 請人工確認</div>"
        f"<div class='card-en-label'>{en_label_text}</div>"
        f"</div>"
        f"<div class='card-right'>"
        f"<span class='card-status {kind}'>{status_cn}</span>"
        f"<details class='card-details'><summary>技術原因</summary>"
        f"<div style='text-align:left'><strong>解析：</strong>{parsed}</div>"
        f"<div style='text-align:left'><strong>原因：</strong>{reason}</div>"
        f"</details>"
        f"<div class='card-state-btns'>"
        f"<button class='btn-copy-text' onclick=\"copyCardText('{idx}',event)\">📋 複製原文</button>"
        f"<button class='btn-state-done' onclick=\"toggleCardState('{idx}','done')\">✓ 已處理</button>"
        f"<button class='btn-state-manual' onclick=\"toggleCardState('{idx}','manual')\">✏️ 已手動下注</button>"
        f"<button class='btn-edit' onclick=\"toggleEditPanel('{idx}')\">[編輯修正]</button>"
        f"</div>"
        f"<div class='edit-panel' id='edit-panel-{idx}' style='display:none'>"
        f"<textarea placeholder='修正後文字...' style='width:100%;min-height:60px;font-size:13px'></textarea>"
        f"<div style='display:flex;gap:4px;margin-top:4px'>"
        f"<button id='reparse-btn-{idx}' onclick=\"submitReparse('{idx}')\" style='font-size:11px'>重新解析</button>"
        f"<button onclick=\"toggleEditPanel('{idx}')\" style='font-size:11px'>取消</button>"
        f"</div>"
        f"<div id='reparse-result-{idx}' style='margin-top:8px;font-size:12px'></div>"
        f"</div>"
        f"</div>"
        f"</div>"
    )


def write_review_console_html(queue: dict[str, Any], path: str | Path, *, queue_path: str | None = None) -> dict[str, Any]:
    html_text = render_review_console_html(queue, queue_path=queue_path)
    Path(path).write_text(html_text, encoding="utf-8")
    return build_review_console_model(queue, queue_path=queue_path)


def format_pretty_review_console(model: dict[str, Any]) -> str:
    p = model.get("preprocessing", {})
    qv = model.get("queue_view", {})
    s = model.get("safety", {})
    return "\n".join([
        "Local Review Console", "", f"Status: {model.get('status')}", "",
        "Preprocessing:",
        f"- candidates: {p.get('candidate_count', 0)}",
        f"- valid: {p.get('valid_count', 0)}",
        f"- watchlist: {p.get('watchlist_count', 0)}",
        f"- invalid/review: {p.get('invalid_count', 0)}",
        f"- ignored: {p.get('ignored_metadata_count', 0)}", "",
        "Queue:", f"- status: {qv.get('status')}",
        f"- current: {qv.get('current_index')} / {qv.get('item_count')}", "",
        "Safety:",
        f"- real_site: {str(s.get('real_site_operation')).lower()}",
        f"- auto_submit: {str(s.get('auto_submit')).lower()}",
        "- No live site operation",
    ])


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


def _split_done_candidates(valid: list[dict[str, Any]], queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return valid candidates whose queue items have DONE or MANUAL_DONE status."""
    items = queue.get("items", [])
    done_statuses = {"DONE", "MANUAL_DONE"}
    done = []
    for vc in valid:
        vc_idx = vc.get("index") or vc.get("item_index")
        if vc_idx is None:
            continue
        for item in items:
            qi = item.get("index")
            if qi is not None and str(qi) == str(vc_idx) and item.get("status") in done_statuses:
                done.append(vc)
                break
    return done


def _candidate_with_labels(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    review_labels = _extract_review_labels(item)
    if _is_car_related(item, result):
        review_labels.append("car_bet")
        review_labels = list(dict.fromkeys(review_labels))

    # ── Normalize X-chain single-number columns → normal ──
    from betguard.webfill.manual_reparse import _normalize_x_chain
    raw_type = result.get("type", "normal")
    raw_columns = result.get("columns")
    original_text = item.get("original_fragment") or item.get("raw", "")
    bet_type, _ = _normalize_x_chain(original_text, raw_type, raw_columns)

    # Regenerate summary if type changed from column to normal
    summary = item.get("summary", "")
    if raw_type == "column" and bet_type == "normal":
        # Clear columns from result to avoid stale column references
        result = dict(result)
        columns = result.pop("columns", None)
        result["type"] = "normal"
        # Build a normal-style summary — use numbers if present, else flatten columns
        numbers = result.get("numbers") or []
        if not numbers and columns:
            numbers = [n for c in columns for n in c]
            result["numbers"] = numbers
        nums_str = ", ".join(str(n) for n in numbers)
        stars = result.get("stars", [])
        stars_str = "".join({2: "二", 3: "三", 4: "四"}.get(s, str(s)) for s in sorted(stars)) + "星" if stars else ""
        money = result.get("money", "")
        parts = [f"一般：{nums_str}"]
        if stars_str:
            parts.append(stars_str)
        if money:
            parts.append(f"{money}元")
        summary = "｜".join(parts)

    return {
        "index": item.get("index"),
        "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": summary,
        "bet_type": bet_type,
        "review_labels": review_labels,
        "result": result,
    }


def _candidate_sync_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Build the assist-panel sync payload entry for one valid candidate.

    Single source of truth shared by the pending-row HTML and the
    localStorage/BroadcastChannel sync payload — both must always describe
    the same item the same way.
    """
    parsed = item.get("result", {}) or item.get("review_result", {}) or {}
    stars_raw = parsed.get("stars", []) or []

    # Derive per-star amounts: bets > money expansion > explicit amounts
    result_amounts_raw = parsed.get("amounts", {}) or {}
    bets_raw = parsed.get("bets", {}) or {}
    money = parsed.get("money")
    star_amounts: dict[str, int] = {}
    if result_amounts_raw and isinstance(result_amounts_raw, dict):
        for k, v in result_amounts_raw.items():
            try:
                val = int(v.get("money", v)) if isinstance(v, dict) else int(v)
            except (ValueError, TypeError):
                continue
            star_amounts[str(k)] = val
    elif bets_raw and isinstance(bets_raw, dict):
        for k, v in bets_raw.items():
            try:
                val = int(v.get("money", 0)) if isinstance(v, dict) else int(v)
            except (ValueError, TypeError):
                continue
            if val > 0:
                star_amounts[str(int(k))] = val
    elif money is not None and stars_raw:
        try:
            m = int(money)
        except (ValueError, TypeError):
            m = 0
        if m > 0:
            star_amounts = {str(int(s)): m for s in stars_raw}

    try:
        index = int(item.get("index"))
    except (TypeError, ValueError):
        index = 0
    return {
        "index": index,
        "raw": str(item.get("original_fragment") or ""),
        "summary": str(item.get("parsed_summary") or ""),
        # Resolve from item first, then parsed result; never default an
        # unknown type to "normal" — the panel only offers fill for normal.
        "bet_type": str(
            item.get("bet_type") or parsed.get("type") or parsed.get("bet_type") or ""
        ),
        "numbers": parsed.get("numbers", []) or [],
        "stars": stars_raw,
        "amounts": star_amounts,
    }


def _candidate_row(item: dict[str, Any]) -> str:
    entry = _candidate_sync_entry(item)
    idx = _e(str(item.get('index')))
    fragment = _e(entry["raw"])
    summary = _e(entry["summary"])
    bet_type = _e(entry["bet_type"])
    numbers = json.dumps(entry["numbers"])
    stars = json.dumps(entry["stars"])
    amounts_json = json.dumps(entry["amounts"]) if entry["amounts"] else "{}"
    return (
        f'<tr class="assist-row-{idx}">'
        f"<td>{idx}</td>"
        f"<td>{fragment}</td>"
        f"<td>{summary}</td>"
        f"<td>{bet_type}</td>"
        f"<td>"
        f"<button class='assist-btn' data-assist-index=\"{idx}\" data-fragment=\"{fragment}\" data-summary=\"{summary}\" data-numbers='{numbers}' data-stars='{stars}' data-amounts='{amounts_json}' data-bet-type=\"{bet_type}\" onclick='quickAssistBtn(this)'>輔助填入</button> "
        f"<button class='manual-done-btn' data-assist-index=\"{idx}\" onclick='markManualDone(this)' style='font-size:11px;background:#64748b;color:#fff;border:none;padding:3px 8px;border-radius:4px;cursor:pointer;margin-left:4px'>✓ 已手動下牌</button>"
        f"</td>"
        "</tr>"
    )


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
        return ""
    return (
        "<h3>Last Mock Result</h3>"
        f"<p>numbers: {_e(str(mock.get('selected_numbers', [])))}</p>"
        f"<p>amounts: {_e(str(mock.get('filled_amounts', {})))}</p>"
    )


def _item_view(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "index": item.get("index"), "status": item.get("status"),
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
        if item.get("status") in {"WAITING_FOR_HUMAN_CONFIRM", "DONE", "MANUAL_DONE"}:
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
    clicked = [click for item in queue.get("items", []) for click in item.get("danger_buttons_clicked", [])]
    return {"real_site_operation": False, "auto_submit": False, "danger_buttons_clicked": clicked, "no_live_site_operation": True}


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
    return {"money": result.get("money"), "unit": result.get("unit"), "car_units": result.get("car_units"), "number": result.get("number")}


def _extract_review_labels(item: dict[str, Any]) -> list[str]:
    notes = item.get("preprocessing_notes", [])
    if not isinstance(notes, list):
        notes = []
    labels: list[str] = []
    for note in notes:
        if isinstance(note, str) and note.startswith("review_label:"):
            labels.append(note[len("review_label:"):])
    return labels


def _is_car_related(item: dict[str, Any], result: dict[str, Any]) -> bool:
    """Return True if this item is car-related."""
    # Check result type
    if str(result.get("type") or "").lower() in ("car", "全車"):
        return True
    # Check parsed summary starts with "車："
    summary = str(item.get("summary") or "")
    if summary.startswith("車："):
        return True
    # Check original fragment contains "車"
    fragment = str(item.get("original_fragment") or item.get("raw") or "")
    if "車" in fragment:
        return True
    return False


def _watchlist_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    review_labels = _extract_review_labels(item)
    if _is_car_related(item, result):
        review_labels.append("car_bet")
        review_labels = list(dict.fromkeys(review_labels))
    entry = {
        "index": item.get("index"), "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": item.get("summary", ""), "bet_type": result.get("type"),
        "numbers": list(result.get("numbers", [])), "stars": list(result.get("stars", [])),
        "reason": _watchlist_reason(item), "warnings": list(item.get("warnings", [])),
        "is_missing_money": _is_missing_money_only(item), "accepted_automatically": False,
        "review_labels": review_labels,
    }
    entry.update(_parsed_amount_view(result))
    return entry


def _invalid_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    review_labels = _extract_review_labels(item)
    # Car detection: add car_bet label if result type is "car", or
    # parsed_summary starts with "車：", or the original fragment contains "車".
    if _is_car_related(item, result):
        review_labels.append("car_bet")
        # Remove duplicates
        review_labels = list(dict.fromkeys(review_labels))
    return {
        "index": item.get("index"), "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": item.get("summary", ""), "numbers": list(result.get("numbers", [])),
        "stars": list(result.get("stars", [])), "reason": _reason_text(item),
        "warnings": list(item.get("warnings", [])), "errors": list(item.get("errors", [])),
        "is_missing_money": _is_missing_money_only(item), "review_labels": review_labels,
    }


def _watchlist_reason(item: dict[str, Any]) -> str:
    if _is_missing_money_only(item):
        return "待觀察: 缺金額，需人工補"
    reasons = list(item.get("warnings", []))
    joined = "; ".join(str(reason) for reason in reasons)
    return f"待觀察: {joined}" if joined else "待觀察: 需人工判斷"


def _status_class(status: str) -> str:
    if status in {"READY_FOR_QUEUE", "WAITING_FOR_HUMAN_CONFIRM", "COMPLETED"}:
        return "ready"
    if status == "NEEDS_REVIEW":
        return "review"
    return "blocked"


def _e(value: str) -> str:
    return html.escape(value, quote=True)
