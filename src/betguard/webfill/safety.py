from __future__ import annotations


DANGER_WORDS = (
    "送出注單",
    "加入注單",
    "清除全部",
    "送出",
    "確認",
    "確定",
    "下注",
    "刪除",
)


def normalize_action_text(text: str) -> str:
    return "".join(str(text or "").split())


def is_dangerous_action(text: str) -> bool:
    normalized = normalize_action_text(text)
    return any(word in normalized for word in DANGER_WORDS)
