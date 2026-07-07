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
        "valid_candidates": [_candidate_with_labels(item) for item in preprocessing.get("valid_candidates", [])],
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

    valid_rows = "".join(_candidate_row(item) for item in model["valid_candidates"])
    if not valid_rows:
        valid_rows = '<div class="empty-block">目前沒有正確候選</div>'

    metadata_rows = "".join(
        f"<li>{_e(str(item.get('raw', item)))}</li>" for item in model["ignored_metadata_lines"][:8]
    ) or "<li>無</li>"
    current = model["queue_view"]["current_item"]
    current_html = _current_item_html(current)
    last_mock_html = _last_mock_html(model["queue_view"].get("last_mock_result"))
    actions_html = "".join(f"<li><code>{_e(action)}</code></li>" for action in model["actions"]) or "<li>無可用指令</li>"
    audit = model["audit"]
    source_json = _e(json.dumps(model, ensure_ascii=False, indent=2))

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
    .bottom-row {{ display: grid; grid-template-columns: 25fr 75fr; gap: 20px; margin-bottom: 20px; min-height: 400px; }}
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

    .stats-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }}
    .stat-card {{
      background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
      padding: 14px 10px; text-align: center; font-size: 12px; color: var(--slate);
      transition: transform 0.1s;
    }}
    .stat-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.06); }}
    .stat-card strong {{ display: block; font-size: 32px; font-weight: 700; color: #0f172a; margin-top: 4px; }}
    .stat-card.valid {{ border-color: var(--green-border); }} .stat-card.valid strong {{ color: var(--green); }}
    .stat-card.watch {{ border-color: var(--yellow-border); }} .stat-card.watch strong {{ color: var(--yellow); }}
    .stat-card.invalid {{ border-color: var(--red-border); }} .stat-card.invalid strong {{ color: var(--red); }}

    .safety-card {{
      background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
      border: 1px solid #86efac; border-radius: var(--radius); padding: 18px 22px;
    }}
    .safety-card h2 {{ color: #166534; }}
    .safety-card ul {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; list-style: none; }}
    .safety-card li {{ color: #166534; font-size: 12px; font-weight: 500; }}

    /* Review cards - WIDE horizontal */
    .card-list {{ display: flex; flex-direction: column; gap: 16px; }}
    .review-card {{
      display: grid; grid-template-columns: 6px 1fr auto;
      background: var(--white); border-radius: 12px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      transition: box-shadow 0.15s;
      min-height: 90px;
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
      padding: 18px 24px; display: flex; flex-direction: column; gap: 10px;
    }}
    .card-meta {{ display: flex; align-items: center; gap: 10px; }}
    .card-meta .card-idx {{ font-size: 12px; font-weight: 600; color: var(--slate); }}
    .card-meta .card-line {{ font-size: 11px; color: var(--slate); background: #f1f5f9; padding: 2px 8px; border-radius: 8px; }}
    .review-card .card-fragment {{
      font-size: 28px; font-weight: 800; color: #0f172a; line-height: 1.3;
      word-break: break-word; letter-spacing: 0.5px;
    }}
    .review-card .card-labels {{ display: flex; flex-wrap: wrap; gap: 4px; }}
    .review-card .card-en-label {{
      font-size: 11px; color: #94a3b8; margin-top: 4px;
    }}

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
      .top-row {{ grid-template-columns: 1fr; }}
      .safety-card ul {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (max-width: 700px) {{
      body {{ padding: 12px; }}
      .stats-grid {{ grid-template-columns: repeat(3, 1fr); }}
      .safety-card ul {{ grid-template-columns: 1fr; }}
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
    <span class="mode-tag">僅本機模式</span>
  </div>

  <details class="paste-block">
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
    <section class="card">
      <h2>📊 審核狀態</h2>
      <div class="stats-grid">
        <div class="stat-card">總筆數<strong>{model["preprocessing"]["candidate_count"]}</strong></div>
        <div class="stat-card valid">正確<strong>{model["preprocessing"]["valid_count"]}</strong></div>
        <div class="stat-card invalid">需人工確認<strong>{model["preprocessing"]["needs_review_count"]}</strong></div>
        <div class="stat-card">警告<strong>{model["preprocessing"]["warnings_count"]}</strong></div>
        <div class="stat-card">已忽略<strong>{model["preprocessing"]["ignored_metadata_count"]}</strong></div>
      </div>
    </section>
    <section class="safety-card">
      <h2>🔒 安全狀態</h2>
      <ul>
        <li>✅ 未連真網站</li>
        <li>✅ 未點擊</li>
        <li>✅ 未填寫</li>
        <li>✅ 未送出</li>
        <li>✅ 不合格項目不進 approved_fill_queue</li>
        <li>✅ 每筆仍需人工確認</li>
      </ul>
    </section>
  </div>

  <div class="bottom-row">
    <section class="card" style="border-left: 4px solid var(--green);">
      <h2><span class="badge valid">✅ 正確</span> 正確候選</h2>
      <div style="overflow-x:auto"><table><thead><tr><th>#</th><th>原始片段</th><th>摘要</th><th>類型</th></tr></thead><tbody>{valid_rows}</tbody></table></div>
    </section>
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
        <button onclick="showDismissed()">顯示已處理</button>
        <button onclick="hideDismissed()">隱藏已處理</button>
        <button onclick="restoreAll()">全部復原</button>
        <span id="dismissed-count" style="font-size:11px;color:var(--slate);margin-left:8px"></span>
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
      <h2>⚡ 指令 / 摘要</h2>
      <p style="font-size:12px;color:var(--slate);margin-bottom:8px">手動執行，本報告不執行任何操作。</p>
      <ul style="font-size:11px">{actions_html}</ul>
      <hr style="margin:12px 0;border-color:#e2e8f0">
      <ul style="font-size:11px">
        <li>批次：{_e(str(audit.get("batch_id")))}</li>
        <li>時間：{_e(str(audit.get("created_at")))}</li>
        <li>錯誤：{_e(str(audit.get("invalid_fragments_count")))}</li>
      </ul>
    </section>
  </div>

  <section class="card full-row">
    <h2>📄 原始資料 (JSON)</h2>
    <pre>{source_json}</pre>
  </section>
<script>
  var currentFilter = 'all';

  // ---- localStorage + dismiss logic ----
  var batchId = (window.location.href.match(/queue_([^/.]+)\.json/) || [])[1] || 'default';
  var storageKey = 'betguard-dismissed-' + batchId;
  function loadDismissed() {{
    try {{ return JSON.parse(localStorage.getItem(storageKey) || '[]'); }} catch(e) {{ return []; }}
  }}
  function saveDismissed(list) {{
    try {{ localStorage.setItem(storageKey, JSON.stringify(list)); }} catch(e) {{}}
  }}
  function dismissCard(btn) {{
    var card = btn.closest('.review-card');
    if (!card) return;
    var idx = card.dataset.batchId;
    card.classList.add('dismissed');
    // Toggle buttons: hide ✓已處理, show ↩取消已處理
    var doneBtn = card.querySelector('.dismiss-btn:not(.undo-btn)');
    var undoBtn = card.querySelector('.undo-btn');
    if (doneBtn) doneBtn.style.display = 'none';
    if (undoBtn) undoBtn.style.display = '';
    var dismissed = loadDismissed();
    if (dismissed.indexOf(idx) < 0) {{ dismissed.push(idx); }}
    saveDismissed(dismissed);
    updateCounts();
  }}
  function undoDismiss(btn) {{
    var card = btn.closest('.review-card');
    if (!card) return;
    var idx = card.dataset.batchId;
    card.classList.remove('dismissed');
    var doneBtn = card.querySelector('.dismiss-btn:not(.undo-btn)');
    var undoBtn = card.querySelector('.undo-btn');
    if (doneBtn) doneBtn.style.display = '';
    if (undoBtn) undoBtn.style.display = 'none';
    var dismissed = loadDismissed().filter(function(d) {{ return d !== idx; }});
    saveDismissed(dismissed);
    updateCounts();
  }}
  function showDismissed() {{
    document.querySelectorAll('#review-cards .review-card:not(.dismissed)').forEach(function(c) {{ c.classList.add('hidden'); }});
    document.querySelectorAll('#review-cards .review-card.dismissed').forEach(function(c) {{ c.classList.remove('hidden'); }});
  }}
  function hideDismissed() {{
    document.querySelectorAll('#review-cards .review-card.dismissed').forEach(function(c) {{ c.classList.add('hidden'); }});
    document.querySelectorAll('#review-cards .review-card:not(.dismissed)').forEach(function(c) {{ c.classList.remove('hidden'); }});
  }}
  function restoreAll() {{
    document.querySelectorAll('#review-cards .review-card.dismissed').forEach(function(c) {{
      c.classList.remove('dismissed', 'hidden');
      var doneBtn = c.querySelector('.dismiss-btn:not(.undo-btn)');
      var undoBtn = c.querySelector('.undo-btn');
      if (doneBtn) doneBtn.style.display = '';
      if (undoBtn) undoBtn.style.display = 'none';
    }});
    localStorage.removeItem(storageKey);
    updateCounts();
  }}
  (function() {{
    var dismissed = loadDismissed();
    document.querySelectorAll('#review-cards .review-card').forEach(function(card) {{
      if (dismissed.indexOf(card.dataset.batchId) >= 0) {{
        card.classList.add('dismissed');
        var doneBtn = card.querySelector('.dismiss-btn:not(.undo-btn)');
        var undoBtn = card.querySelector('.undo-btn');
        if (doneBtn) doneBtn.style.display = 'none';
        if (undoBtn) undoBtn.style.display = '';
      }}
    }});
    updateCounts();
  }})();
  function updateCounts() {{
    var el = document.getElementById('dismissed-count');
    var total = document.querySelectorAll('#review-cards .review-card').length;
    var dismissed = document.querySelectorAll('#review-cards .review-card.dismissed').length;
    if (el) el.textContent = '已處理: ' + dismissed + ' / 剩餘: ' + (total - dismissed);
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
    renderHistoryPanel(); updateToggleBadge();
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
  function updateToggleBadge() {{
    var toggle = document.getElementById('history-toggle-btn');
    var badge = document.getElementById('history-count-badge');
    if (!toggle || !badge) return;
    var count = loadHistory().length;
    badge.textContent = count;
    badge.style.display = count > 0 ? 'flex' : 'none';
    toggle.classList.toggle('has-items', count > 0);
  }}
  (function() {{
    renderHistoryPanel(); updateToggleBadge();
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

  // ---- assist fill ----
  var assistItem = null;
  var assistInProgress = false;
  var queuePath = document.body.getAttribute('data-queue-path') || '';
  function previewAssist(idx, fragment, summary, numbers, stars, amounts) {{
    assistItem = {{idx: idx, fragment: fragment, summary: summary, numbers: numbers, stars: stars, amounts: amounts}};
    var starNames = {{2: '二星', 3: '三星', 4: '四星'}};
    var preview = '<p><strong>原始：</strong>' + fragment + '</p><p><strong>號碼：</strong>' + numbers.join(',') + '</p>';
    var hasAmounts = false;
    for (var s in amounts) {{
      preview += '<p><strong>' + (starNames[parseInt(s)] || s) + '金額：</strong>' + amounts[s] + ' 元</p>';
      hasAmounts = true;
    }}
    if (!hasAmounts) {{
      preview += '<p><strong style=\\"color:var(--red)\\">⚠️ 無金額資料</strong></p>';
    }}
    preview += '<p style=\\"color:var(--slate);font-size:11px\\">⚠️ 僅填入號碼與金額，不送出、不確認。需人工核對後手動送出。</p>';
    document.getElementById('assist-preview').innerHTML = preview;
    document.getElementById('assist-status').textContent = '';
    document.getElementById('assist-confirm-btn').disabled = false;
    document.getElementById('assist-confirm-btn').textContent = '確認輔助填入';
    document.getElementById('assist-modal-overlay').classList.add('show');
  }}
  function closeAssistModal() {{
    document.getElementById('assist-modal-overlay').classList.remove('show');
    assistItem = null;
  }}
  function confirmAssist() {{
    if (!assistItem || assistInProgress) return;
    assistInProgress = true;
    var btn = document.getElementById('assist-confirm-btn');
    btn.disabled = true;
    btn.textContent = '執行中...';
    var statusEl = document.getElementById('assist-status');
    statusEl.textContent = '處理中... 瀏覽器將開啟，請手動登入後按 Enter。';
    fetch('/assist-fill', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{queue_path: queuePath, item_index: parseInt(assistItem.idx)}})
    }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
      if (data.ok) {{
        statusEl.textContent = '✅ 已輔助填入，待人工送出';
        btn.textContent = '已完成';
        // Add to history
        var history = loadHistory();
        if (!history.some(function(h) {{ return h.idx === assistItem.idx && h.type === '已輔助填入，待人工送出'; }})) {{
          history.unshift({{
            idx: assistItem.idx, time: new Date().toLocaleTimeString(),
            fragment: assistItem.fragment, summary: assistItem.summary,
            type: '已輔助填入，待人工送出'
          }});
          saveHistory(history);
          renderHistoryPanel(); updateToggleBadge();
        }}
        // Update button state
        var rowBtn = document.querySelector('.assist-btn[onclick*=\\"' + assistItem.idx + '\\"]');
        if (rowBtn) {{ rowBtn.textContent = '已輔助填入'; rowBtn.classList.add('done'); }}
      }} else {{
        statusEl.textContent = '❌ 失敗: ' + (data.error || 'unknown');
        btn.disabled = false;
        btn.textContent = '重試';
        assistInProgress = false;
      }}
    }}).catch(function(err) {{
      statusEl.textContent = '❌ 錯誤: ' + err.message;
      btn.disabled = false;
      btn.textContent = '重試';
      assistInProgress = false;
    }});
  }}
</script>

  <button id="history-toggle-btn" class="history-toggle" onclick="toggleHistoryPanel()" title="剛剛下注紀錄">
    📋<span id="history-count-badge" class="count-badge" style="display:none">0</span>
  </button>
  <div id="history-side-panel" class="history-panel">
    <div class="history-panel-header">
      <h3>📋 剛剛下注紀錄</h3>
      <button onclick="clearHistory()">清空全部</button>
    </div>
    <div id="history-panel-body" class="history-panel-body"></div>
  </div>

  <div id="assist-modal-overlay" class="assist-modal-overlay">
    <div class="assist-modal">
      <h3>🔧 輔助填入預覽</h3>
      <div class="am-preview" id="assist-preview"></div>
      <div class="am-status" id="assist-status"></div>
      <div class="am-actions">
        <button class="btn-cancel" onclick="closeAssistModal()">取消</button>
        <button class="btn-confirm" id="assist-confirm-btn" onclick="confirmAssist()">確認輔助填入</button>
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
        f"<div class='card-en-label'>{en_label_text}</div>"
        f"</div>"
        f"<div class='card-right'>"
        f"<span class='card-status {kind}'>{status_cn}</span>"
        f"<details class='card-details'><summary>技術原因</summary>"
        f"<div style='text-align:left'><strong>解析：</strong>{parsed}</div>"
        f"<div style='text-align:left'><strong>原因：</strong>{reason}</div>"
        f"</details>"
        f"<button class='dismiss-btn' onclick='dismissCard(this)' title='標記為已處理 (不影響 queue)'>✓ 已處理</button>"
        f"<button class='dismiss-btn undo-btn' onclick='undoDismiss(this)' style='display:none' title='取消已處理'>↩ 取消已處理</button>"
        f"<button class='history-btn' onclick='addToHistory(this)' title='加入剛剛下注紀錄 (僅存瀏覽器)'>已手動下注</button>"
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


def _candidate_with_labels(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    review_labels = _extract_review_labels(item)
    if _is_car_related(item, result):
        review_labels.append("car_bet")
        review_labels = list(dict.fromkeys(review_labels))
    return {
        "index": item.get("index"),
        "original_fragment": item.get("original_fragment") or item.get("raw"),
        "parsed_summary": item.get("summary", ""),
        "bet_type": result.get("type"),
        "review_labels": review_labels,
        "result": result,
    }


def _candidate_row(item: dict[str, Any]) -> str:
    idx = _e(str(item.get('index')))
    fragment = _e(str(item.get('original_fragment')))
    summary = _e(str(item.get('parsed_summary')))
    bet_type = _e(str(item.get('bet_type')))
    # Build assist-fill data attributes from the parsed result
    parsed = item.get("result", {}) or item.get("review_result", {}) or {}
    numbers = json.dumps(parsed.get("numbers", []))
    stars_raw = parsed.get("stars", []) or []
    stars = json.dumps(stars_raw)

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
    amounts_json = json.dumps(star_amounts) if star_amounts else "{}"
    return (
        "<tr>"
        f"<td>{idx}</td>"
        f"<td>{fragment}</td>"
        f"<td>{summary}</td>"
        f"<td>{bet_type}</td>"
        f"<td><button class='assist-btn' onclick='previewAssist(\"{idx}\",\"{fragment}\",\"{summary}\",{numbers},{stars},{amounts_json})'>輔助填入</button></td>"
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
