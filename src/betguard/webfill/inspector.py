from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from betguard.webfill.safety import DANGER_WORDS, is_dangerous_action


GAME_539_MARKERS = ("今彩539", "今彩", "539")
GAME_TIANTIAN_MARKERS = ("天天樂",)
GAME_DALETOU_MARKERS = ("大樂",)
GAME_LIUHE_MARKERS = ("六合",)
GAME_HK_MARKERS = ("港",)
GAME_GUESSES = (
    ("539", GAME_539_MARKERS),
    ("天天樂", GAME_TIANTIAN_MARKERS),
    ("大樂", GAME_DALETOU_MARKERS),
    ("六合", GAME_LIUHE_MARKERS),
    ("港", GAME_HK_MARKERS),
)
STAR_MARKERS = ("二三四星", "234", "二星", "三星", "四星")
NORMAL_TAB_MARKERS = ("單碰", "一般", "二三四星")
LINKED_TAB_MARKERS = ("連碰",)
COLUMN_TAB_MARKERS = ("柱碰",)
AMOUNT_FIELD_MARKERS = ("二星", "三星", "四星")
NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))
AVAILABLE_LABEL_MARKERS = (
    "539",
    "今彩",
    "今彩539",
    "天天樂",
    "大樂",
    "六合",
    "港",
    "二三四星",
    "單碰",
    "一般",
    "連碰",
    "柱碰",
    "二星",
    "三星",
    "四星",
)
ELEMENT_SAMPLE_LIMIT = 200


@dataclass(frozen=True)
class ElementInfo:
    tag: str
    text: str
    value: str
    id: str
    name: str
    class_name: str
    href: str
    type: str
    visible: bool


@dataclass(frozen=True)
class FrameElementInfo:
    tag: str
    name: str
    id: str
    src: str


@dataclass(frozen=True)
class FrameScan:
    url: str
    body_text: str
    html_text: str
    elements: list[ElementInfo]
    frame_elements: list[FrameElementInfo]
    iframe_elements: list[FrameElementInfo]


def run_dry_run_inspector(url: str) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        open_warnings: list[str] = []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:  # pragma: no cover - depends on live browser state
            open_warnings.append(f"initial page load warning: {exc}")

        input("請在瀏覽器中手動登入，登入完成後回到終端機按 Enter 繼續。")
        pages = _open_pages(context.pages)
        active_page = _select_active_page(pages)
        if active_page is None:
            report = _empty_report(url, errors=["active page not found"], pages_count=len(pages))
            report["warnings"].extend(open_warnings)
        else:
            warnings: list[str] = list(open_warnings)
            _wait_for_page(active_page, warnings)
            report = inspect_page(active_page, url=url, pages_count=len(pages), initial_warnings=warnings)
        browser.close()
        return report


def inspect_page(
    page: Any,
    *,
    url: str,
    pages_count: int | None = None,
    initial_warnings: list[str] | None = None,
    initial_errors: list[str] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = list(initial_warnings or [])
    errors: list[str] = list(initial_errors or [])

    frames = _page_frames(page, warnings)
    scans = [_scan_frame(frame, index, warnings) for index, frame in enumerate(frames, start=1)]
    body_text = "\n".join(scan.body_text for scan in scans if scan.body_text)
    html_text = "\n".join(scan.html_text for scan in scans if scan.html_text)
    elements = [element for scan in scans for element in scan.elements]
    frame_elements = [item for scan in scans for item in scan.frame_elements]
    iframe_elements = [item for scan in scans for item in scan.iframe_elements]
    frame_urls = [scan.url for scan in scans]

    element_labels = extract_labels_from_elements([_element_to_dict(element) for element in elements])
    frame_label_text = _frame_elements_text(frame_elements + iframe_elements)
    readable_text = "\n".join(
        [body_text, html_text, frame_label_text, _elements_text([_element_to_dict(element) for element in elements])]
    )
    text_found = scan_text_for_labels(readable_text)
    current_game_guess = guess_current_game(readable_text, [_element_to_dict(element) for element in elements])
    matching_number_labels = _merge_number_labels(text_found["matching_number_labels"], element_labels["matching_number_labels"])
    amount_fields = _merge_unique(text_found["amount_fields"], element_labels["amount_fields"])
    danger_buttons = _merge_unique(_find_danger_buttons(elements), element_labels["danger_buttons"])
    available_labels = _merge_unique(text_found["available_labels"], element_labels["available_labels"], amount_fields, danger_buttons)

    found = {
        "game_539": text_found["game_539"],
        "game_tiantian": text_found["game_tiantian"],
        "game_daletou": text_found["game_daletou"],
        "game_liuhe": text_found["game_liuhe"],
        "game_hk": text_found["game_hk"],
        "star_tab": text_found["star_tab"],
        "normal_tab": text_found["normal_tab"],
        "linked_tab": text_found["linked_tab"],
        "column_tab": text_found["column_tab"],
        "number_buttons_count": len(matching_number_labels),
        "matching_number_labels": matching_number_labels,
        "amount_fields": amount_fields,
        "danger_buttons": danger_buttons,
    }

    if not current_game_guess:
        warnings.append("supported game label not found")
    elif current_game_guess != "539" and not found["game_539"]:
        warnings.append(f"current page appears to be {current_game_guess}, 539 not detected")
    if found["number_buttons_count"] != 39:
        warnings.append(f"expected 39 number buttons, found {found['number_buttons_count']}")
    if not amount_fields:
        warnings.append("amount fields not found")
    if danger_buttons:
        warnings.append("danger buttons detected; dry-run inspector will not operate them")
    if not readable_text.strip() and not elements and not frame_elements and not iframe_elements:
        errors.append("unable to read page content or elements")

    elements_sample = [_element_to_dict(element) for element in elements[:ELEMENT_SAMPLE_LIMIT]]

    return {
        "url": url,
        "mode": "dry_run_inspector",
        "current_game_guess": current_game_guess,
        "found": found,
        "available_labels": available_labels,
        "diagnostics": {
            "pages_count": pages_count if pages_count is not None else None,
            "active_url": _safe_page_url(page),
            "active_title": _safe_page_title(page, warnings),
            "frames_count": len(frames),
            "frame_urls": frame_urls,
            "body_text_length": len(body_text),
            "html_length": len(html_text),
            "frame_elements": [_frame_element_to_dict(item) for item in frame_elements],
            "iframe_elements": [_frame_element_to_dict(item) for item in iframe_elements],
            "elements_sample_count": min(len(elements), ELEMENT_SAMPLE_LIMIT),
            "elements_sample": elements_sample,
        },
        "warnings": warnings,
        "errors": errors,
    }


def scan_text_for_labels(text: str) -> dict[str, Any]:
    content = text or ""
    matching_number_labels = [label for label in NUMBER_LABELS if _label_in_text(content, label)]
    return {
        "current_game_guess": _guess_current_game_from_text(content),
        "game_539": _contains_any(content, GAME_539_MARKERS),
        "game_tiantian": _contains_any(content, GAME_TIANTIAN_MARKERS),
        "game_daletou": _contains_any(content, GAME_DALETOU_MARKERS),
        "game_liuhe": _contains_any(content, GAME_LIUHE_MARKERS),
        "game_hk": _contains_any(content, GAME_HK_MARKERS),
        "star_tab": _contains_any(content, STAR_MARKERS),
        "normal_tab": _contains_any(content, NORMAL_TAB_MARKERS),
        "linked_tab": _contains_any(content, LINKED_TAB_MARKERS),
        "column_tab": _contains_any(content, COLUMN_TAB_MARKERS),
        "amount_fields": [marker for marker in AMOUNT_FIELD_MARKERS if marker in content],
        "danger_buttons": _danger_words_in_text(content),
        "available_labels": _available_labels_in_text(content),
        "matching_number_labels": matching_number_labels,
        "number_buttons_count": len(matching_number_labels),
    }


def extract_labels_from_elements(elements: list[dict[str, Any]]) -> dict[str, Any]:
    text = _elements_text(elements)
    found = scan_text_for_labels(text)
    exact_number_labels = _number_labels_from_elements(elements)
    danger_buttons = _danger_buttons_from_element_dicts(elements)
    return {
        "available_labels": found["available_labels"],
        "matching_number_labels": _merge_number_labels(found["matching_number_labels"], exact_number_labels),
        "amount_fields": found["amount_fields"],
        "danger_buttons": _merge_unique(found["danger_buttons"], danger_buttons),
    }


def guess_current_game(text: str, elements: list[dict[str, Any]] | None = None) -> str | None:
    combined = "\n".join([text or "", _elements_text(elements or [])])
    return _guess_current_game_from_text(combined)


def _empty_report(url: str, *, errors: list[str], pages_count: int) -> dict[str, Any]:
    return {
        "url": url,
        "mode": "dry_run_inspector",
        "current_game_guess": None,
        "found": {
            "game_539": False,
            "game_tiantian": False,
            "game_daletou": False,
            "game_liuhe": False,
            "game_hk": False,
            "star_tab": False,
            "normal_tab": False,
            "linked_tab": False,
            "column_tab": False,
            "number_buttons_count": 0,
            "matching_number_labels": [],
            "amount_fields": [],
            "danger_buttons": [],
        },
        "available_labels": [],
        "diagnostics": {
            "pages_count": pages_count,
            "active_url": "",
            "active_title": "",
            "frames_count": 0,
            "frame_urls": [],
            "body_text_length": 0,
            "html_length": 0,
            "frame_elements": [],
            "iframe_elements": [],
            "elements_sample_count": 0,
            "elements_sample": [],
        },
        "warnings": [],
        "errors": errors,
    }


def _open_pages(pages: list[Any]) -> list[Any]:
    return [page for page in pages if not _is_closed(page)]


def _select_active_page(pages: list[Any]) -> Any | None:
    return pages[-1] if pages else None


def _is_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:  # pragma: no cover - depends on live browser state
        return True


def _wait_for_page(page: Any, warnings: list[str]) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"domcontentloaded wait failed: {exc}")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"networkidle wait failed: {exc}")


def _page_frames(page: Any, warnings: list[str]) -> list[Any]:
    try:
        return list(page.frames)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"unable to list frames: {exc}")
        return []


def _scan_frame(frame: Any, index: int, warnings: list[str]) -> FrameScan:
    url = _safe_frame_url(frame)
    body_text, html_text = _read_frame_texts(frame, index, warnings)
    elements = _collect_frame_elements(frame, index, warnings)
    frame_elements = _collect_embedded_frame_elements(frame, index, "frame", warnings)
    iframe_elements = _collect_embedded_frame_elements(frame, index, "iframe", warnings)
    return FrameScan(
        url=url,
        body_text=body_text,
        html_text=html_text,
        elements=elements,
        frame_elements=frame_elements,
        iframe_elements=iframe_elements,
    )


def _safe_frame_url(frame: Any) -> str:
    try:
        return str(frame.url)
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _safe_page_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _safe_page_title(page: Any, warnings: list[str]) -> str:
    try:
        return str(page.title())
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"unable to read active page title: {exc}")
        return ""


def _read_frame_texts(frame: Any, index: int, warnings: list[str]) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    readers = (
        ("body.inner_text", lambda: frame.locator("body").inner_text(timeout=3000), False),
        ("document.body.innerText", lambda: frame.evaluate("document.body ? document.body.innerText : ''"), False),
        (
            "document.documentElement.innerText",
            lambda: frame.evaluate("document.documentElement ? document.documentElement.innerText : ''"),
            False,
        ),
        (
            "document.documentElement.outerHTML",
            lambda: frame.evaluate("document.documentElement ? document.documentElement.outerHTML : ''"),
            True,
        ),
    )
    for label, reader, is_html in readers:
        try:
            value = str(reader() or "")
        except Exception as exc:  # pragma: no cover - depends on live browser state
            warnings.append(f"frame {index} {label} unavailable: {exc}")
            continue
        if value:
            if is_html:
                html_parts.append(value)
            else:
                text_parts.append(value)
    return "\n".join(text_parts), "\n".join(html_parts)


def _collect_embedded_frame_elements(frame: Any, index: int, tag: str, warnings: list[str]) -> list[FrameElementInfo]:
    script = f"""
    () => Array.from(document.querySelectorAll('{tag}')).map((el) => ({{
      tag: (el.tagName || '').toLowerCase(),
      name: el.getAttribute('name') || '',
      id: el.getAttribute('id') || '',
      src: el.getAttribute('src') || '',
    }}))
    """
    try:
        raw_items = frame.evaluate(script)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"frame {index} {tag} element scan failed: {exc}")
        return []
    return [
        FrameElementInfo(
            tag=str(item.get("tag", tag)),
            name=str(item.get("name", "")),
            id=str(item.get("id", "")),
            src=str(item.get("src", "")),
        )
        for item in raw_items
    ]


def _collect_frame_elements(frame: Any, index: int, warnings: list[str]) -> list[ElementInfo]:
    script = """
    () => {
      const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width >= 0 && rect.height >= 0;
      };
      const className = (el) => typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '');
      return Array.from(document.querySelectorAll('button,a,input,select,textarea,div,span,td,th,label')).map((el) => ({
        tag: (el.tagName || '').toLowerCase(),
        text: el.innerText || el.textContent || '',
        value: 'value' in el ? el.value || '' : '',
        id: el.id || '',
        name: el.getAttribute('name') || '',
        className: className(el),
        href: el.getAttribute('href') || '',
        type: el.getAttribute('type') || '',
        visible: visible(el),
      }));
    }
    """
    try:
        raw_items = frame.evaluate(script)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"frame {index} element scan failed: {exc}")
        return []

    return [
        ElementInfo(
            tag=str(item.get("tag", "")),
            text=str(item.get("text", "")).strip(),
            value=str(item.get("value", "")).strip(),
            id=str(item.get("id", "")).strip(),
            name=str(item.get("name", "")).strip(),
            class_name=str(item.get("className", "")).strip(),
            href=str(item.get("href", "")).strip(),
            type=str(item.get("type", "")).strip(),
            visible=bool(item.get("visible", False)),
        )
        for item in raw_items
    ]


def _find_danger_buttons(elements: list[ElementInfo]) -> list[str]:
    danger: list[str] = []
    seen: set[str] = set()
    for element in elements:
        if not element.visible or element.tag not in {"button", "input", "a"}:
            continue
        text = _element_text(_element_to_dict(element))
        if text and text not in seen and is_dangerous_action(text):
            danger.append(_best_element_label(element))
            seen.add(text)
    return danger


def _danger_buttons_from_element_dicts(elements: list[dict[str, Any]]) -> list[str]:
    danger: list[str] = []
    seen: set[str] = set()
    for element in elements:
        text = _element_text(element)
        if text and text not in seen and is_dangerous_action(text):
            danger.append(_best_element_dict_label(element))
            seen.add(text)
    return danger


def _number_labels_from_elements(elements: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for element in elements:
        for field in _element_detection_fields(element):
            label = _compact(field)
            if label in NUMBER_LABELS:
                found.add(label)
            elif label.isdigit() and 1 <= int(label) <= 39:
                found.add(f"{int(label):02d}")
    return sorted(found, key=lambda value: int(value))


def _element_detection_fields(element: dict[str, Any]) -> list[str]:
    return [
        str(element.get("text", "")),
        str(element.get("value", "")),
        str(element.get("id", "")),
        str(element.get("name", "")),
        str(element.get("className", element.get("class_name", ""))),
    ]


def _element_text(element: dict[str, Any]) -> str:
    return " ".join(field for field in _element_detection_fields(element) if field)


def _elements_text(elements: list[dict[str, Any]]) -> str:
    return "\n".join(_element_text(element) for element in elements)


def _frame_elements_text(items: list[FrameElementInfo]) -> str:
    return "\n".join(" ".join([item.tag, item.name, item.id, item.src]) for item in items)


def _element_to_dict(element: ElementInfo) -> dict[str, Any]:
    return {
        "tag": element.tag,
        "text": element.text,
        "value": element.value,
        "id": element.id,
        "name": element.name,
        "className": element.class_name,
        "href": element.href,
        "type": element.type,
    }


def _frame_element_to_dict(item: FrameElementInfo) -> dict[str, str]:
    return {
        "tag": item.tag,
        "name": item.name,
        "id": item.id,
        "src": item.src,
    }


def _best_element_label(element: ElementInfo) -> str:
    return element.text or element.value or element.id or element.name or element.class_name


def _best_element_dict_label(element: dict[str, Any]) -> str:
    return str(
        element.get("text")
        or element.get("value")
        or element.get("id")
        or element.get("name")
        or element.get("className")
        or element.get("class_name")
        or ""
    )


def _guess_current_game_from_text(text: str) -> str | None:
    for game, markers in GAME_GUESSES:
        if _contains_any(text, markers):
            return game
    return None


def _available_labels_in_text(text: str) -> list[str]:
    return [label for label in AVAILABLE_LABEL_MARKERS if label in text]


def _danger_words_in_text(text: str) -> list[str]:
    found: list[str] = []
    for word in DANGER_WORDS:
        if word in text and not any(word in existing for existing in found):
            found.append(word)
    return found


def _merge_number_labels(*groups: list[str]) -> list[str]:
    values = {label for group in groups for label in group}
    return sorted(values, key=lambda value: int(value))


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _label_in_text(text: str, label: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(label)}(?!\d)", text or "") is not None


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


__all__ = [
    "extract_labels_from_elements",
    "guess_current_game",
    "inspect_page",
    "run_dry_run_inspector",
    "scan_text_for_labels",
]
