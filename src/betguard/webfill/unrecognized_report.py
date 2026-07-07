"""Unrecognized format report (v1).

Classifies queue items that are BLOCKED / Needs Review by error/warning
reason and produces a read-only HTML + JSON report.

The report is **optional**: it is generated automatically during batch
creation, but it never blocks the user's workflow and never promotes
any item to Valid.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

CATEGORY_RULES: list[tuple[str, str, str, list[str]]] = [
    # (category_key, display_name, suggestion, [trigger substring list])
    # Ordered most-specific first to avoid false matches.
    (
        "four_star_insufficient",
        "四星號碼不足",
        "人工 review：四星需要至少 4 個號碼",
        ["四星 requires at least 4 numbers"],
    ),
    (
        "hk_prefix",
        "港 / HK 前綴",
        "人工 review：確認是否為六合彩 / 港彩；非 539",
        [
            "hk",
            "港 prefix",
            "game prefix",
        ],
    ),
    (
        "ambiguous_amount",
        "金額格式不明",
        "人工 review：確認金額後補正",
        [
            "unclear amount",
            "ambiguous amount",
            "missing or unclear",
            "ambiguous money",
        ],
    ),
    (
        "missing_money",
        "缺少金額",
        "人工 review：確認是否有金額，若無則補金額後重新貼",
        [
            "missing money",
            "missing amount",
        ],
    ),
    (
        "insufficient_numbers",
        "號碼數量不足",
        "人工 review：確認號碼數量後補齊",
        [
            "requires at least",
            "too few numbers",
        ],
    ),
    (
        "customer_shorthand",
        "兩碼 600 / 1000 shorthand",
        "人工 review：確認是 shorthand 還是手誤；若常用可考慮補 parser",
        [
            "customer-specific shorthand",
        ],
    ),
    (
        "slash_group_zhu_peng",
        "slash-group 注碰 / 住碰",
        "人工 review：確認是住碰還是 539 連碰；未來可補 parser",
        [
            "slash-group",
            "zhu peng",
            "zhupeng",
        ],
    ),
    (
        "suspected_concatenated",
        "疑似串接兩筆",
        "人工 review：確認是否是兩筆黏在一起，手動分開後重新貼",
        [
            "concatenated",
            "suspicious",
            "suspected pasted",
        ],
    ),
    (
        "unsupported_star_format",
        "星別格式不支援",
        "人工 review：確認星別語法；可手動改成標準格式",
        [
            "star format",
            "未支援的星別",
            "unsupported star",
        ],
    ),
    (
        "invalid_number",
        "號碼超出範圍",
        "人工 review：確認是否為非 539 彩種（如六合彩 / 大樂透 1-49）",
        [
            "number out of range",
            "valid range",
        ],
    ),
    (
        "unsupported_characters",
        "特殊字元",
        "人工 review：確認內含字元是否為人名 / 備註；整理後重新貼",
        [
            "unsupported characters",
            "invalid character",
        ],
    ),
]


def classify_item(item: dict[str, Any]) -> str:
    """Return the category key for a BLOCKED/review item.

    Matches the first rule whose trigger appears in any error or warning.
    Falls back to ``"other"`` when nothing matches.
    """
    errors: list[str] = item.get("errors") or []
    warnings: list[str] = item.get("warnings") or []
    combined_text = " ".join(errors + warnings).lower()
    for key, _name, _suggestion, triggers in CATEGORY_RULES:
        for t in triggers:
            if t in combined_text:
                return key
    return "other"


def extract_items(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return items that are BLOCKED / Needs Review (not VALID / DONE)."""
    blocked: list[dict[str, Any]] = []
    for item in queue.get("items") or []:
        status = (item.get("status") or "").lower()
        errors: list[str] = item.get("errors") or []
        warnings: list[str] = item.get("warnings") or []
        if status in ("blocked", "needs_review") or errors or warnings:
            blocked.append(item)
    return blocked


def build_report_data(
    queue: dict[str, Any],
    *,
    queue_path: str | Path | None = None,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the report data structure from a queue dict.

    Returns::
        {
            "created_at": "2026-07-07T14:00:00+08:00",
            "total_blocked": N,
            "total_valid": M,
            "categories": [
                {
                    "key": "missing_money",
                    "name": "缺少金額",
                    "count": 1,
                    "suggestion": "人工 review：...",
                    "samples": [
                        {"text": "...", "errors": [...], "warnings": [...]},
                    ],
                },
                ...
            ],
            "queue_path": "...",
            "input_path": "...",
        }
    """
    items = extract_items(queue)
    total_blocked = len(items)
    total_valid = (
        queue.get("preprocessing", {})
        .get("summary", {})
        .get("valid_count", 0)
    )

    # Group blocked items by category
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = classify_item(item)
        by_category.setdefault(key, []).append(item)

    # Build category groups
    MAX_SAMPLES = 5
    categories: list[dict[str, Any]] = []
    for key, display_name, suggestion, _triggers in CATEGORY_RULES:
        matched = by_category.pop(key, None)
        if not matched:
            continue
        samples = [
            {
                "original_text": it.get("original_text") or it.get("text") or "",
                "errors": list(it.get("errors") or []),
                "warnings": list(it.get("warnings") or []),
            }
            for it in matched[:MAX_SAMPLES]
        ]
        categories.append(
            {
                "key": key,
                "name": display_name,
                "count": len(matched),
                "suggestion": suggestion,
                "samples": samples,
            }
        )

    # Add "other" (items that matched no rule)
    other_items = by_category.pop("other", None) or list(
        by_category.values()
    )
    if other_items:
        # Flatten if we have multiple unmatched keys
        flat: list[dict[str, Any]] = []
        for val in other_items:
            if isinstance(val, list):
                flat.extend(val)
        samples = [
            {
                "original_text": it.get("original_text") or it.get("text") or "",
                "errors": list(it.get("errors") or []),
                "warnings": list(it.get("warnings") or []),
            }
            for it in flat[:MAX_SAMPLES]
        ]
        categories.append(
            {
                "key": "other",
                "name": "其他未分類",
                "count": len(flat),
                "suggestion": "人工 review：確認原始文字後判斷",
                "samples": samples,
            }
        )

    total_categorized = sum(c["count"] for c in categories)
    # Safety: should equal total_blocked (any remaining are lost)
    _remainder = total_blocked - total_categorized  # noqa: F841 -- safety check

    return {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "total_blocked": total_blocked,
        "total_valid": total_valid,
        "total_categorized": total_categorized,
        "categories": categories,
        "queue_path": str(queue_path) if queue_path else None,
        "input_path": str(input_path) if input_path else None,
    }


def render_report_html(data: dict[str, Any]) -> str:
    """Render the report data as a standalone HTML page."""
    if data["total_blocked"] == 0:
        return _html_page(
            "未辨識格式報告",
            "<h1>未辨識格式報告</h1>\n"
            f"<p>所有 {data['total_valid']} 筆皆正常, 無未辨識格式.</p>",
        )

    categories_html = "".join(
        _render_category(c) for c in data["categories"]
    )

    body = f"""
<h1>未辨識格式報告</h1>
<p>valid {data['total_valid']} / blocked {data['total_blocked']} / 已分類 {data['total_categorized']}</p>
<p style="color:#6b7280;">這份報告是自動統計, 僅供參考. 它不會自動修正任何項目, 也不會影響 review.html 的操作.</p>
{categories_html}
<div class="links">
  <a class="danger" href="/">回首頁</a>
</div>
"""
    return _html_page("未辨識格式報告", body)


def _render_category(cat: dict[str, Any]) -> str:
    name = _html_escape(cat["name"])
    count = cat["count"]
    suggestion = _html_escape(cat["suggestion"])

    samples_html = ""
    for s in cat.get("samples") or []:
        text = _html_escape(s.get("original_text", ""))
        errors = _html_escape("; ".join(s.get("errors", [])))
        warnings = _html_escape("; ".join(s.get("warnings", [])))
        samples_html += f"""
<div class="sample">
  <code>{text[:80]}</code>
  <div class="sample-detail">
    {'<span class="err">errors: ' + errors + '</span>' if errors else ''}
    {'<span class="warn">warnings: ' + warnings + '</span>' if warnings else ''}
  </div>
</div>"""

    return f"""
<div class="category">
  <h2>{name} ({count})</h2>
  <p class="suggestion">建議: {suggestion}</p>
  <div class="samples">
    {samples_html}
    <p class="note">僅顯示前 {samples_html.count('<div class="sample"')} 筆</p>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_HTML_STYLE = """
<style>
  body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
         margin: 2em auto; max-width: 880px; padding: 0 1em; color: #222; }
  h1 { font-size: 1.4em; border-bottom: 2px solid #444; padding-bottom: 0.2em; }
  h2 { font-size: 1.1em; margin-top: 1.2em; }
  .category { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px;
              padding: 0.5em 1em; margin: 0.8em 0; }
  .sample { background: #fff; border: 1px solid #e5e7eb; border-radius: 4px;
            padding: 0.4em 0.8em; margin: 0.3em 0; }
  .sample-detail { font-size: 0.85em; color: #6b7280; margin-top: 0.2em; }
  .err { color: #c33; }
  .warn { color: #b8860b; }
  .suggestion { font-size: 0.9em; color: #2563eb; }
  .note { font-size: 0.8em; color: #9ca3af; }
  .links a { display: inline-block; margin: 0.3em 0.6em 0.3em 0;
             padding: 0.4em 1em; background: #2563eb; color: #fff;
             border-radius: 4px; text-decoration: none; }
  .links a.danger { background: #6b7280; }
  .links a:hover { background: #1d4ed8; }
  .safety { background: #fff5f5; border: 1px solid #c33; border-radius: 6px;
            padding: 0.8em 1.2em; margin: 1em 0; }
</style>
"""


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{_html_escape(title)}</title>
{_HTML_STYLE}
</head>
<body>
{body}
<hr>
<div class="links">
  <a class="danger" href="/">回到首頁</a>
</div>
</body>
</html>"""


def _html_escape(s: str) -> str:
    import html as _html

    return _html.escape(s)


def build_report_from_queue_path(
    queue_path: str | Path,
    *,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load queue.json and build report data.  Safe: never mutates the queue."""
    try:
        queue = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "total_blocked": 0,
            "total_valid": 0,
            "total_categorized": 0,
            "categories": [],
            "queue_path": str(queue_path),
            "input_path": str(input_path) if input_path else None,
            "error": "unable to read queue",
        }
    return build_report_data(
        queue, queue_path=queue_path, input_path=input_path
    )


def has_unrecognized(queue: dict[str, Any]) -> bool:
    """Return True if the queue has any unrecognized items."""
    summary = (
        (queue.get("preprocessing") or {}).get("summary") or {}
    )
    needs_review = int(summary.get("needs_review_count", 0))
    invalid = int(summary.get("invalid_unsupported_count", 0))
    watchlist = int(queue.get("preprocessing", {}).get("watchlist_count", 0) or 0)
    return (needs_review + invalid + watchlist) > 0


# ---------------------------------------------------------------------------
# Convenience: generate report files given a queue path
# ---------------------------------------------------------------------------


def write_report_files(
    queue_path: str | Path,
    *,
    input_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    stamp: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write ``unrecognized_STAMP.(html|json)`` and return the paths + data.

    If ``out_dir`` is None, the parent of ``queue_path`` is used.
    If ``stamp`` is None, the current time (HHMMSS) is used.
    """
    qp = Path(queue_path)
    base_dir = Path(out_dir) if out_dir else qp.parent
    base_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp or datetime.now(timezone.utc).astimezone().strftime("%H%M%S")

    data = build_report_from_queue_path(queue_path, input_path=input_path)

    html_path = base_dir / f"unrecognized_{ts}.html"
    html_path.write_text(render_report_html(data), encoding="utf-8")

    json_path = base_dir / f"unrecognized_{ts}.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    return html_path, json_path, data


__all__ = [
    "CATEGORY_RULES",
    "classify_item",
    "extract_items",
    "build_report_data",
    "build_report_from_queue_path",
    "render_report_html",
    "has_unrecognized",
    "write_report_files",
]