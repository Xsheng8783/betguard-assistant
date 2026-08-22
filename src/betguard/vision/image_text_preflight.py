"""Read-only existing-parser preview for editable image transcription text."""

from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = "betguard-image-text-parser-preflight-v1"


def _simple_parser_reason(messages: list[str], *, ignored: bool = False) -> str:
    if ignored:
        return "這一行未被辨識為投注文字"
    joined = "; ".join(message for message in messages if message).strip()
    lowered = joined.lower()
    translations = (
        ("missing numbers", "無法辨識投注號碼"),
        ("unsupported or unclear column format", "柱碰格式無法完整解析"),
        ("unsupported or unclear betting format", "投注格式無法完整解析"),
        ("missing or unclear amount", "倍率或金額無法完整解析"),
        ("column ", "柱碰欄位不完整"),
        ("missing money", "缺少可解析的金額"),
        ("unsupported characters", "包含 parser 不支援的文字"),
        ("ambiguous long number", "連續數字無法安全拆分"),
        ("duplicate number", "投注號碼重複"),
        ("number out of range", "投注號碼超出範圍"),
        ("missing star", "缺少可解析的星數"),
        ("missing amount", "缺少可解析的倍率或金額"),
        ("requires manual review", "格式仍需要人工確認"),
    )
    for source, translated in translations:
        if source in lowered:
            return translated
    return joined or "existing parser 無法完整解析"


def _issue_from_fragment(fragment: dict[str, Any]) -> dict[str, Any]:
    messages = [
        str(message)
        for message in list(fragment.get("errors", []))
        + list(fragment.get("warnings", []))
        if str(message).strip()
    ]
    return {
        "line_no": int(fragment.get("line_no") or 0),
        "raw": str(fragment.get("raw") or fragment.get("original_fragment") or ""),
        "reason": _simple_parser_reason(messages),
        "parser_errors": messages,
    }


def preview_image_text_with_existing_parser(text: str) -> dict[str, Any]:
    """Preview the exact text without mutating it or creating queue artifacts."""
    from betguard.webfill.batch_mock_queue import READY_FOR_QUEUE, build_batch_mock_queue

    original_text = str(text or "")
    stripped = original_text.strip()
    digest = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    if not stripped:
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "all_parseable": False,
            "parsed_bet_count": 0,
            "unresolved_count": 0,
            "issues": [],
            "input_text_sha256": digest,
            "preview_only": True,
            "text_mutated": False,
            "auto_apply": False,
            "auto_submit": False,
        }

    try:
        queue = build_batch_mock_queue(original_text, game="六合")
    except Exception as exc:
        return {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "all_parseable": False,
            "parsed_bet_count": 0,
            "unresolved_count": 1,
            "issues": [
                {
                    "line_no": 0,
                    "raw": stripped,
                    "reason": "existing parser preview 失敗",
                    "parser_errors": [str(exc)],
                }
            ],
            "input_text_sha256": digest,
            "preview_only": True,
            "text_mutated": False,
            "auto_apply": False,
            "auto_submit": False,
        }

    preprocessing = queue.get("preprocessing", {})
    valid = list(preprocessing.get("valid_candidates", []))
    invalid = list(preprocessing.get("invalid_fragments", []))
    ignored = list(preprocessing.get("ignored_metadata_lines", []))
    issues = [_issue_from_fragment(fragment) for fragment in invalid]
    for line in ignored:
        issues.append(
            {
                "line_no": int(line.get("line_no") or 0),
                "raw": str(line.get("raw") or ""),
                "reason": _simple_parser_reason([], ignored=True),
                "parser_errors": [],
            }
        )
    all_parseable = (
        bool(valid)
        and not issues
        and preprocessing.get("status") == "READY"
        and queue.get("status") == READY_FOR_QUEUE
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "all_parseable": all_parseable,
        "parsed_bet_count": len(valid),
        "unresolved_count": len(issues),
        "issues": issues,
        "input_text_sha256": digest,
        "preview_only": True,
        "text_mutated": False,
        "auto_apply": False,
        "auto_submit": False,
    }
