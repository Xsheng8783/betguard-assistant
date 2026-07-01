from __future__ import annotations

from typing import Any


CAR_PAGE_MARKERS = ("全車", "車", "車號", "金額")
DANGER_WORDS = ("送出", "確認", "確定", "下注", "送出注單", "清除", "刪除")


def build_car_readonly_snapshot_placeholder(text: str = "", elements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    elements = elements or []
    page_text = "\n".join([text, _elements_text(elements)])
    return {
        "mode": "car_readonly_dom_snapshot_placeholder",
        "page_kind": "car",
        "readonly": True,
        "real_site_operation": False,
        "auto_submit": False,
        "car_page_detected": _looks_like_car_page(page_text),
        "selector_confidence": "none",
        "selector_candidates": {},
        "danger_texts_detected": _danger_texts(page_text),
        "warnings": [
            "car live DOM selectors are not verified yet; do not use for real-site assisted fill",
            "placeholder only; no live selector is generated",
        ],
        "errors": [],
    }


def _looks_like_car_page(text: str) -> bool:
    return any(marker in text for marker in CAR_PAGE_MARKERS)


def _danger_texts(text: str) -> list[str]:
    return [word for word in DANGER_WORDS if word in text]


def _elements_text(elements: list[dict[str, Any]]) -> str:
    fields = ("text", "textContent", "value", "title", "outerHTML", "dataBind")
    return "\n".join(
        str(element.get(field) or "")
        for element in elements
        if isinstance(element, dict)
        for field in fields
    )
