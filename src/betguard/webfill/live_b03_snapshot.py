from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import Any


B03_NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))
B03_AMOUNT_STARS = ("二星", "三星", "四星")
B03_TEXT_MARKERS = (
    "539 - 下注資訊",
    "天天樂 - 下注資訊",
    "大樂 - 下注資訊",
    "六合 - 下注資訊",
    "快速輸入",
    "送出注單",
    "二三星碰法",
)
B03_DANGER_WORDS = (
    "送出注單",
    "加入常用牌組",
    "送出",
    "確認",
    "確定",
    "下注",
    "清除",
    "清",
    "刪除",
    "close",
)
DANGER_PRETTY_ORDER = (
    "送出注單",
    "送出",
    "確認",
    "確定",
    "清除",
    "清",
    "刪除",
    "加入常用牌組",
    "close",
    "下注資訊",
)
LOGIN_MARKERS = ("帳號", "密碼", "登入")
NOT_FOUND_MARKERS = ("HTTP 404", "找不到資源", "要求的 URL")
B03_FRAME_ERROR = (
    "no live B03 mainFrame detected; please manually navigate to 539 or 天天樂 / "
    "二三四星 / 連碰 before pressing Enter"
)
FRAME_TAGS_SCRIPT = """
() => Array.from(document.querySelectorAll("frame, iframe")).map((el) => ({
  name: el.getAttribute("name") || "",
  id: el.getAttribute("id") || "",
  src: el.getAttribute("src") || "",
  outerHTML: (el.outerHTML || "").slice(0, 1000)
}))
"""
ELEMENTS_SCRIPT = """
() => Array.from(document.querySelectorAll("*")).map((el, index) => ({
  index,
  tag: (el.tagName || "").toLowerCase(),
  text: el.innerText || "",
  textContent: el.textContent || "",
  value: el.value || "",
  id: el.id || "",
  name: el.getAttribute("name") || "",
  className: el.getAttribute("class") || "",
  type: el.getAttribute("type") || "",
  role: el.getAttribute("role") || "",
  href: el.getAttribute("href") || "",
  src: el.getAttribute("src") || "",
  alt: el.getAttribute("alt") || "",
  placeholder: el.getAttribute("placeholder") || "",
  title: el.getAttribute("title") || "",
  ariaLabel: el.getAttribute("aria-label") || "",
  dataBind: el.getAttribute("data-bind") || "",
  onclick: el.getAttribute("onclick") || "",
  outerHTML: (el.outerHTML || "").slice(0, 2000)
}))
"""


def collect_live_b03_snapshot(page: Any) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    frame_snapshots: list[dict[str, Any]] = []
    active_page_url = _safe_page_url(page)
    page_candidates = _collect_page_candidates(page, warnings)
    selected_page_info = _select_page_candidate(page_candidates)
    selected_page = selected_page_info.get("page") if selected_page_info else page
    selected_page_url = str(selected_page_info.get("url") or "") if selected_page_info else active_page_url
    frame_tags = list(selected_page_info.get("frame_tags") or []) if selected_page_info else []
    main_frame_found = bool(selected_page_info.get("main_frame_found")) if selected_page_info else False

    try:
        frames = list(selected_page.frames)
    except Exception as exc:  # pragma: no cover - browser state dependent
        frames = []
        errors.append(f"unable to list live frames: {exc}")

    for named_frame in _named_frames(selected_page):
        if named_frame is not None and all(named_frame is not existing for existing in frames):
            frames.append(named_frame)

    if len(frames) <= 1 and frame_tags:
        warnings.append("Playwright page.frames did not expose child frames; frameset tags detected")

    for frame in frames:
        frame_snapshot = _read_live_frame(frame, warnings)
        _score_live_frame_snapshot(frame_snapshot)
        frame_snapshots.append(frame_snapshot)

    selected = _select_b03_frame_snapshot(frame_snapshots)
    if selected is None:
        errors.append(B03_FRAME_ERROR)
        selected = {
            "frame_name": "",
            "frame_url": "",
            "body_text": "",
            "html": "",
            "html_length": 0,
            "elements": [],
            "b03_detected": False,
            "score": 0,
        }

    return {
        "mode": "live_b03_snapshot",
        "frame_name": selected["frame_name"],
        "frame_url": selected["frame_url"],
        "body_text": selected["body_text"],
        "html_length": selected["html_length"],
        "elements": selected["elements"],
        "b03_detected": _is_b03_text(selected["body_text"]),
        "selected_frame_score": selected.get("score", 0),
        "frame_candidates": [_frame_candidate_summary(frame) for frame in frame_snapshots],
        "live_frames_scanned": len(frame_snapshots),
        "browser_pages_count": len(page_candidates),
        "active_page_url": active_page_url,
        "selected_page_url": selected_page_url,
        "page_urls": [str(item.get("url") or "") for item in page_candidates],
        "page_frames_count": len(frames),
        "frame_tags": frame_tags,
        "frameset_tags_count": len(frame_tags),
        "main_frame_found": main_frame_found,
        "warnings": warnings,
        "errors": errors,
    }


def build_b03_selector_mapping_from_live_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    body_text = str(snapshot.get("body_text") or "")
    elements = [item for item in snapshot.get("elements", []) if isinstance(item, dict)]
    page_text = "\n".join([body_text, _elements_text(elements)])
    number_selectors = _live_number_selectors(body_text)
    quick_input = _live_quick_input(elements)
    amount_field_candidates = _live_amount_fields(page_text)
    danger_candidates = _live_danger_candidates(page_text, elements)
    unique_danger_texts = _unique_danger_texts(danger_candidates)
    warnings: list[str] = list(snapshot.get("warnings") or [])
    errors: list[str] = list(snapshot.get("errors") or [])
    number_count = sum(1 for records in number_selectors.values() if records)
    amount_fields_confident = False
    b03_detected = bool(snapshot.get("b03_detected")) and bool(body_text.strip())

    if number_count != 39 and not errors:
        warnings.append(f"expected 39 B03 number selectors, found {number_count}")
    if not quick_input["number_input_candidates"] and not errors:
        warnings.append("quick input number field not confidently detected")
    warnings.append("amount input selectors not confidently detected")

    return {
        "mode": "b03_selector_mapping",
        "page": {
            "game": _guess_game(page_text),
            "route": "/Front/B/B03",
            "frame_name": snapshot.get("frame_name") or "",
            "url": snapshot.get("frame_url") or "",
            "source": "live DOM snapshot",
        },
        "number_selectors": number_selectors,
        "quick_input": quick_input,
        "amount_field_candidates": amount_field_candidates,
        "danger_candidates": danger_candidates,
        "unique_danger_texts": unique_danger_texts,
        "safe_to_assisted_fill": False,
        "number_selectors_count": number_count,
        "quick_input_found": bool(quick_input["number_input_candidates"]),
        "amount_fields_confident": amount_fields_confident,
        "danger_candidates_count": len(danger_candidates),
        "debug": {
            "live_frames_scanned": snapshot.get("live_frames_scanned", 0),
            "browser_pages_count": snapshot.get("browser_pages_count", 0),
            "active_page_url": snapshot.get("active_page_url") or "",
            "selected_page_url": snapshot.get("selected_page_url") or "",
            "page_urls": snapshot.get("page_urls", []),
            "page_frames_count": snapshot.get("page_frames_count", 0),
            "frameset_tags_count": snapshot.get("frameset_tags_count", 0),
            "main_frame_found": bool(snapshot.get("main_frame_found")),
            "selected_frame": snapshot.get("frame_name") or "",
            "selected_frame_url": snapshot.get("frame_url") or "",
            "selected_frame_score": snapshot.get("selected_frame_score", 0),
            "live_elements": len(elements),
            "body_text_length": len(body_text),
            "b03_text_detected": b03_detected,
            "frame_candidates": snapshot.get("frame_candidates", []),
            "frame_tags": snapshot.get("frame_tags", []),
        },
        "warnings": _dedupe(warnings),
        "errors": errors,
    }


def run_live_b03_selector_mapping(url: str) -> dict[str, Any]:  # pragma: no cover - browser dependent
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
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        input("請在瀏覽器中手動登入，並手動進到 539 / 二三四星 / 連碰頁，完成後回到終端機按 Enter 繼續。")
        snapshot = collect_live_b03_snapshot(page)
        return build_b03_selector_mapping_from_live_snapshot(snapshot)


def format_pretty_live_b03_selector_mapping(mapping: dict[str, Any]) -> str:
    page = mapping.get("page") if isinstance(mapping.get("page"), dict) else {}
    debug = mapping.get("debug") if isinstance(mapping.get("debug"), dict) else {}
    quick_input = mapping.get("quick_input") if isinstance(mapping.get("quick_input"), dict) else {}
    amount_fields = mapping.get("amount_field_candidates") if isinstance(mapping.get("amount_field_candidates"), dict) else {}
    danger_candidates = mapping.get("danger_candidates") if isinstance(mapping.get("danger_candidates"), list) else []
    unique_danger_texts = mapping.get("unique_danger_texts")
    if not isinstance(unique_danger_texts, list):
        unique_danger_texts = _unique_danger_texts(danger_candidates)
    lines = [
        "B03 Selector Mapping",
        "",
        "Page:",
        f"- game: {page.get('game') or ''}",
        f"- route: {page.get('route') or ''}",
        f"- frame: {page.get('frame_name') or ''}",
        f"- source: {page.get('source') or ''}",
        "",
        "Debug:",
        f"- browser pages: {debug.get('browser_pages_count', 0)}",
        f"- active page url: {debug.get('active_page_url') or ''}",
        f"- selected page url: {debug.get('selected_page_url') or ''}",
        f"- page frames count: {debug.get('page_frames_count', 0)}",
        f"- frameset tags count: {debug.get('frameset_tags_count', 0)}",
        f"- page.frame(mainFrame) found: {'yes' if debug.get('main_frame_found') else 'no'}",
        f"- live frames scanned: {debug.get('live_frames_scanned', 0)}",
        f"- selected frame: {debug.get('selected_frame') or ''}",
        f"- selected frame url: {debug.get('selected_frame_url') or ''}",
        f"- selected frame score: {debug.get('selected_frame_score', 0)}",
        f"- live elements: {debug.get('live_elements', 0)}",
        f"- body text length: {debug.get('body_text_length', 0)}",
        f"- B03 text detected: {'yes' if debug.get('b03_text_detected') else 'no'}",
        "",
        "Frame Tags:",
    ]
    frame_tags = debug.get("frame_tags") if isinstance(debug.get("frame_tags"), list) else []
    if frame_tags:
        for tag in frame_tags:
            if isinstance(tag, dict):
                label = tag.get("name") or tag.get("id") or "(unnamed)"
                lines.append(f"- {label}: {tag.get('src') or ''}")
    else:
        lines.append("- none detected")
    lines.extend(
        [
        "",
        "Numbers:",
        f"- found: {mapping.get('number_selectors_count', 0)} / 39",
        "- confidence: medium",
        "",
        "Quick Input:",
        f"- number input: {'found' if quick_input.get('number_input_candidates') else 'not found'}",
        f"- send button: {'restricted' if quick_input.get('send_button_candidates') else 'not found'}",
        "",
        "Amount Fields:",
        ]
    )
    for star in B03_AMOUNT_STARS:
        candidates = amount_fields.get(star) or []
        lines.append(f"- {star}: {'label found / selector unsure' if candidates else 'unsure'}")
    lines.extend(["", "Danger:"])
    if unique_danger_texts:
        for text in unique_danger_texts:
            suffix = " text only" if text == "下注資訊" else ""
            lines.append(f"- {text}{suffix}")
    else:
        lines.append("- none detected")
    if mapping.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in mapping["errors"])
    lines.extend(
        [
            "",
            "Final:",
            f"- safe_to_assisted_fill: {str(mapping.get('safe_to_assisted_fill')).lower()}",
            "- real_site_operation: disabled",
        ]
    )
    return "\n".join(lines)


def _read_live_frame(frame: Any, warnings: list[str]) -> dict[str, Any]:
    frame_name = _safe_frame_name(frame)
    frame_url = _safe_frame_url(frame)
    body_text = _safe_frame_eval(frame, "document.body ? document.body.innerText : ''", warnings)
    html = _safe_frame_eval(frame, "document.documentElement ? document.documentElement.outerHTML : ''", warnings)
    elements = _safe_frame_eval(frame, ELEMENTS_SCRIPT, warnings, default=[])
    if not isinstance(elements, list):
        elements = []
    normalized = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        element = dict(item)
        element["frame_name"] = frame_name
        element["frame_url"] = frame_url
        element["source_kind"] = "live_b03_snapshot"
        normalized.append(element)
    return {
        "frame_name": frame_name,
        "frame_url": frame_url,
        "body_text": str(body_text or ""),
        "html": str(html or ""),
        "html_length": len(str(html or "")),
        "elements": normalized,
        "b03_detected": _is_b03_text(str(body_text or "")),
        "score": 0,
        "reasons": [],
        "banned_reasons": [],
    }


def _collect_page_candidates(page: Any, warnings: list[str]) -> list[dict[str, Any]]:
    pages = [page]
    try:
        context = getattr(page, "context", None)
        context_pages = list(getattr(context, "pages", []) or []) if context is not None else []
    except Exception as exc:  # pragma: no cover - browser state dependent
        context_pages = []
        warnings.append(f"unable to list browser context pages: {exc}")
    for candidate in context_pages:
        if all(candidate is not existing for existing in pages):
            pages.append(candidate)

    candidates: list[dict[str, Any]] = []
    for candidate_page in pages:
        body_text = str(_safe_page_eval(candidate_page, "document.body ? document.body.innerText : ''", warnings) or "")
        html = str(
            _safe_page_eval(candidate_page, "document.documentElement ? document.documentElement.outerHTML : ''", warnings)
            or ""
        )
        frame_tags = _read_frame_tags(candidate_page, warnings)
        main_frame = _page_frame(candidate_page, "mainFrame")
        info = {
            "page": candidate_page,
            "url": _safe_page_url(candidate_page),
            "body_text": body_text,
            "html": html,
            "frame_tags": frame_tags,
            "main_frame_found": main_frame is not None,
            "score": 0,
            "reasons": [],
        }
        _score_page_candidate(info)
        candidates.append(info)
    return candidates


def _score_page_candidate(candidate: dict[str, Any]) -> None:
    url = str(candidate.get("url") or "")
    body_text = str(candidate.get("body_text") or "")
    html = str(candidate.get("html") or "")
    frame_tags = [item for item in candidate.get("frame_tags", []) if isinstance(item, dict)]
    combined = "\n".join([body_text, html])
    score = 0
    reasons: list[str] = []
    if _is_authenticated_host(url):
        score += 40
        reasons.append("authenticated w0/w1 host")
    if "/Front/Shared/Index" in url:
        score += 30
        reasons.append("shared index page")
    if "frameset" in html.lower() or frame_tags:
        score += 25
        reasons.append("frameset tags detected")
    if any(str(tag.get("name") or "") == "mainFrame" for tag in frame_tags):
        score += 25
        reasons.append("mainFrame tag detected")
    if candidate.get("main_frame_found"):
        score += 30
        reasons.append("page.frame(mainFrame) found")
    if any(marker in combined for marker in ("下注資訊", "遊戲選單", "下註資料")):
        score += 20
        reasons.append("betting page text marker")
    candidate["score"] = score
    candidate["reasons"] = reasons


def _select_page_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: int(candidate.get("score") or 0))


def _read_frame_tags(page: Any, warnings: list[str]) -> list[dict[str, Any]]:
    tags = _safe_page_eval(page, FRAME_TAGS_SCRIPT, warnings, default=[])
    if not isinstance(tags, list):
        return []
    result: list[dict[str, Any]] = []
    for tag in tags:
        if isinstance(tag, dict):
            result.append(
                {
                    "name": str(tag.get("name") or ""),
                    "id": str(tag.get("id") or ""),
                    "src": str(tag.get("src") or ""),
                    "outerHTML": str(tag.get("outerHTML") or ""),
                }
            )
    return result


def _named_frames(page: Any) -> list[Any]:
    return [_page_frame(page, name) for name in ("mainFrame", "gmenu", "gprint")]


def _page_frame(page: Any, name: str) -> Any:
    try:
        frame_getter = getattr(page, "frame")
        return frame_getter(name=name)
    except Exception:
        return None


def _select_b03_frame_snapshot(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [
        frame
        for frame in frames
        if frame.get("score", 0) > 0
        and not frame.get("banned_reasons")
        and str(frame.get("body_text") or "").strip()
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda frame: int(frame.get("score", 0)))


def _score_live_frame_snapshot(frame: dict[str, Any]) -> None:
    url = str(frame.get("frame_url") or "")
    name = str(frame.get("frame_name") or "")
    body_text = str(frame.get("body_text") or "")
    html = str(frame.get("html") or "")
    reasons: list[str] = []
    banned_reasons: list[str] = []
    score = 0

    if _is_www_entry_url(url):
        banned_reasons.append("www entry page")
    if not body_text.strip():
        banned_reasons.append("empty body text")
    if _is_login_text(body_text):
        banned_reasons.append("login page")
    if _is_404_text(body_text, html):
        banned_reasons.append("404 page")

    if name == "mainFrame":
        score += 40
        reasons.append("frame name mainFrame")
    if "/Front/B/B03" in url:
        score += 40
        reasons.append("url contains /Front/B/B03")
    if _is_authenticated_host(url):
        score += 20
        reasons.append("authenticated w0/w1 host")
    for marker in B03_TEXT_MARKERS:
        if marker in body_text:
            score += 20
            reasons.append(f"body contains {marker}")
    if _body_has_all_numbers(body_text):
        score += 40
        reasons.append("body contains 01-39")
    if "/Front/B/B03" in html or any(marker in html for marker in B03_TEXT_MARKERS):
        score += 3
        reasons.append("html contains B03 marker")

    frame["score"] = score
    frame["reasons"] = reasons
    frame["banned_reasons"] = banned_reasons


def _frame_candidate_summary(frame: dict[str, Any]) -> dict[str, Any]:
    reasons = list(frame.get("reasons") or [])
    banned = [f"banned: {reason}" for reason in frame.get("banned_reasons") or []]
    return {
        "name": frame.get("frame_name") or "",
        "url": frame.get("frame_url") or "",
        "body_text_length": len(str(frame.get("body_text") or "")),
        "html_length": int(frame.get("html_length") or 0),
        "score": int(frame.get("score") or 0),
        "reasons": reasons + banned,
    }


def _live_number_selectors(page_text: str) -> dict[str, list[dict[str, Any]]]:
    labels = set(re.findall(r"(?<!\d)(0[1-9]|[1-3][0-9])(?!\d)", page_text))
    found: dict[str, list[dict[str, Any]]] = {label: [] for label in B03_NUMBER_LABELS}
    for label in B03_NUMBER_LABELS:
        if label in labels:
            found[label].append({"source": "live_body_text", "confidence": "medium", "selector_hint": None})
    return found


def _live_quick_input(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    number_inputs: list[dict[str, Any]] = []
    send_buttons: list[dict[str, Any]] = []
    for element in elements:
        tag = str(element.get("tag") or "").lower()
        context = _element_context(element)
        if tag == "input" and ("QkNums" in context or "Mo.OnChkNo" in context):
            number_inputs.append(
                {
                    "selector_hint": 'input[data-bind*="QkNums"]',
                    "confidence": "high",
                    "restricted": False,
                    "frame_name": element.get("frame_name", ""),
                    "frame_url": element.get("frame_url", ""),
                }
            )
        text = _clean_text(element.get("text") or element.get("textContent"))
        if tag == "button" and "送出" in text and "Mo.OnAddSel" in context:
            send_buttons.append(
                {
                    "selector_hint": 'button[data-bind*="Mo.OnAddSel"]',
                    "confidence": "high",
                    "restricted": True,
                    "reason": "quick input send button must not be auto-clicked",
                    "frame_name": element.get("frame_name", ""),
                    "frame_url": element.get("frame_url", ""),
                }
            )
    return {"number_input_candidates": _dedupe_dicts(number_inputs), "send_button_candidates": _dedupe_dicts(send_buttons)}


def _live_amount_fields(page_text: str) -> dict[str, list[dict[str, Any]]]:
    has_money_context = any(word in page_text for word in ("本金", "每碰金額", "下注金額"))
    result: dict[str, list[dict[str, Any]]] = {star: [] for star in B03_AMOUNT_STARS}
    if not has_money_context:
        return result
    for star in B03_AMOUNT_STARS:
        if star in page_text:
            result[star].append(
                {
                    "source": "live_body_text",
                    "labels_detected": True,
                    "selector_confidence": "low",
                    "selector_hint": None,
                }
            )
    return result


def _live_danger_candidates(page_text: str, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for word in B03_DANGER_WORDS:
        if word in page_text:
            found.append({"text": word if word != "下注" else "下注資訊", "type": "text_only", "restricted": True})
    for element in elements:
        context = _element_context(element)
        tag = str(element.get("tag") or "").lower()
        is_interactive = tag in {"button", "input", "a", "td"} and ("click" in context or "送出" in context)
        for word in B03_DANGER_WORDS:
            if word not in context:
                continue
            found.append(
                {
                    "text": word if word != "下注" else "下注資訊",
                    "type": "interactive" if is_interactive else "text_only",
                    "restricted": True,
                    "selector_hint": _selector_hint_for_danger(element),
                }
            )
    return _dedupe_dicts(found)


def _unique_danger_texts(danger_candidates: list[dict[str, Any]]) -> list[str]:
    found = {str(item.get("text") or "") for item in danger_candidates if isinstance(item, dict)}
    result = [text for text in DANGER_PRETTY_ORDER if text in found]
    extras = sorted(text for text in found if text and text not in DANGER_PRETTY_ORDER)
    return result + extras


def _selector_hint_for_danger(element: dict[str, Any]) -> str:
    data_bind = str(element.get("dataBind") or "")
    if "Mo.OnAddSel" in data_bind:
        return 'button[data-bind*="Mo.OnAddSel"]'
    tag = str(element.get("tag") or "").lower()
    text = _clean_text(element.get("text") or element.get("textContent"))
    return f"{tag}:has-text(\"{text}\")" if tag and text else ""


def _is_b03_text(*parts: str) -> bool:
    text = "\n".join(str(part or "") for part in parts)
    return any(marker in text for marker in B03_TEXT_MARKERS)


def _guess_game(text: str) -> str | None:
    for game in ("539", "天天樂", "大樂", "六合"):
        if game in text:
            return game
    return None


def _body_has_all_numbers(text: str) -> bool:
    labels = set(re.findall(r"(?<!\d)(0[1-9]|[1-3][0-9])(?!\d)", text))
    return all(label in labels for label in B03_NUMBER_LABELS)


def _is_www_entry_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() == "www.gts362.com" and parsed.path.rstrip("/") in {"", "/"}


def _is_authenticated_host(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"w0.gts362.com", "w1.gts362.com"}


def _is_login_text(text: str) -> bool:
    return all(marker in text for marker in LOGIN_MARKERS)


def _is_404_text(*parts: str) -> bool:
    text = "\n".join(str(part or "") for part in parts)
    return any(marker in text for marker in NOT_FOUND_MARKERS)


def _elements_text(elements: list[dict[str, Any]]) -> str:
    return "\n".join(_element_context(element) for element in elements)


def _element_context(element: dict[str, Any]) -> str:
    fields = (
        "text",
        "textContent",
        "value",
        "dataBind",
        "onclick",
        "outerHTML",
        "title",
        "ariaLabel",
    )
    return "\n".join(str(element.get(field) or "") for field in fields)


def _safe_frame_eval(frame: Any, script: str, warnings: list[str], default: Any = "") -> Any:
    try:
        return frame.evaluate(script)
    except Exception as exc:  # pragma: no cover - browser state dependent
        warnings.append(f"live frame read warning: {exc}")
        return default


def _safe_page_eval(page: Any, script: str, warnings: list[str], default: Any = "") -> Any:
    try:
        return page.evaluate(script)
    except Exception as exc:  # pragma: no cover - browser state dependent
        warnings.append(f"live page read warning: {exc}")
        return default


def _safe_frame_name(frame: Any) -> str:
    try:
        name = frame.name
        return str(name() if callable(name) else name)
    except Exception:  # pragma: no cover - browser state dependent
        return ""


def _safe_frame_url(frame: Any) -> str:
    try:
        return str(frame.url)
    except Exception:  # pragma: no cover - browser state dependent
        return ""


def _safe_page_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:  # pragma: no cover - browser state dependent
        return ""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in items:
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key in seen:
            continue
        result.append(item)
        seen.add(key)
    return result
