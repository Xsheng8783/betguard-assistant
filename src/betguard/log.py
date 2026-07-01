from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIR = Path("review_logs")
LOG_SOURCE = "streamlit_review_ui"
SUMMARY_KEYS = ("total", "ok", "warning", "error", "can_continue")


def ensure_log_dir(log_dir: Path | str = DEFAULT_LOG_DIR) -> Path:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_review_log(
    raw_text: str,
    review_data: dict[str, Any],
    *,
    created_at: datetime | None = None,
    source: str = LOG_SOURCE,
) -> dict[str, Any]:
    created = (created_at or datetime.now()).replace(microsecond=0)
    summary = {key: review_data.get(key) for key in SUMMARY_KEYS}
    items = review_data.get("items", [])
    ok_summaries = [
        item.get("summary", "")
        for item in items
        if item.get("result", {}).get("status") == "ok" and item.get("summary")
    ]

    return {
        "created_at": created.isoformat(),
        "source": source,
        "raw_text": raw_text,
        "summary": summary,
        "ok_summaries": ok_summaries,
        "items": items,
    }


def save_review_log(
    raw_text: str,
    review_data: dict[str, Any],
    *,
    log_dir: Path | str = DEFAULT_LOG_DIR,
    created_at: datetime | None = None,
    source: str = LOG_SOURCE,
) -> Path:
    created = (created_at or datetime.now()).replace(microsecond=0)
    directory = ensure_log_dir(log_dir)
    while True:
        path = directory / f"review_{created:%Y%m%d_%H%M%S}.json"
        if not path.exists():
            break
        created += timedelta(seconds=1)

    payload = build_review_log(raw_text, review_data, created_at=created, source=source)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_review_log(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_log_file(filename: str, *, log_dir: Path | str = DEFAULT_LOG_DIR) -> dict[str, Any]:
    safe_name = Path(filename).name
    return read_review_log(Path(log_dir) / safe_name)


def read_recent_logs(limit: int = 10, *, log_dir: Path | str = DEFAULT_LOG_DIR) -> list[dict[str, Any]]:
    directory = ensure_log_dir(log_dir)
    logs: list[dict[str, Any]] = []
    for path in directory.glob("review_*.json"):
        try:
            payload = read_review_log(path)
        except (OSError, json.JSONDecodeError):
            continue
        payload["filename"] = path.name
        logs.append(payload)

    logs.sort(key=lambda payload: (payload.get("created_at") or "", payload.get("filename") or ""), reverse=True)
    return logs[:limit]
