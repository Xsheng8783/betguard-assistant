from __future__ import annotations

import re
import json
from typing import Any
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit

from betguard.webfill.inspector import guess_current_game, scan_text_for_labels
from betguard.webfill.market_state import build_market_state_summary
from betguard.webfill.page_routes import PAGE_ROUTE_LABELS
from betguard.webfill.safety import is_dangerous_action


SCAN_SELECTOR = "*"
NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))
LOGIN_MARKERS = (
    "\u5e33\u865f",
    "\u5bc6\u78bc",
    "\u767b\u5165",
    "\u4e0b\u8f09Chrome",
)
ROUTE_LABELS = PAGE_ROUTE_LABELS
KNOWN_GAME_NAMES = {
    "11": "\u516d\u5408",
    "12": "\u5927\u6a02",
    "13": "539",
    "22": "\u5929\u5929\u6a02",
}
CLOSED_MARKERS = (
    "\u5df2\u95dc\u76e4",
    "\u505c\u6b62\u6536\u55ae",
    "\u4e0d\u5f97\u4e0b\u6ce8",
    "\u6b64\u73a9\u6cd5\u672a\u958b\u653e",
)
AMOUNT_MARKERS = ("二星", "三星", "四星")
INPUT_TAGS = {"input", "select", "textarea"}
DANGER_TAGS = {"button", "a", "input", "select", "textarea", "div", "span", "td", "th", "label"}
AMOUNT_ALIASES = {
    "二星": ("二星", "2星", "twostar", "two_star", "two-star", "star2", "star_2", "2star"),
    "三星": ("三星", "3星", "threestar", "three_star", "three-star", "star3", "star_3", "3star"),
    "四星": ("四星", "4星", "fourstar", "four_star", "four-star", "star4", "star_4", "4star"),
}
MAX_FRAME_SRC_DEPTH = 3
ELEMENT_SAMPLE_PER_FRAME = 20
MATCH_FIELDS = (
    "text",
    "textContent",
    "innerText",
    "value",
    "id",
    "name",
    "className",
    "onclick",
    "alt",
    "title",
    "ariaLabel",
    "outerHTML",
)


def run_selector_discovery(url: str, *, probe_route: str | None = None) -> dict[str, Any]:
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
        warnings: list[str] = []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:  # pragma: no cover - depends on live browser state
            warnings.append(f"initial page load warning: {exc}")

        input("請在瀏覽器中手動登入，並手動進到 539 / 二三四星 / 連碰頁，完成後回到終端機按 Enter 繼續。")
        report = discover_selectors(
            context.pages,
            url=url,
            context=context,
            initial_warnings=warnings,
            probe_route=probe_route,
        )
        browser.close()
        return report


def discover_selectors(
    pages: list[Any],
    *,
    url: str,
    context: Any | None = None,
    initial_warnings: list[str] | None = None,
    initial_errors: list[str] | None = None,
    max_frame_src_depth: int = MAX_FRAME_SRC_DEPTH,
    probe_route: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = list(initial_warnings or [])
    errors: list[str] = list(initial_errors or [])
    open_pages = [page for page in pages if not _is_closed(page)]
    visited_urls: set[str] = set()
    state = _empty_scan_state()
    active_page_url = url

    if not open_pages:
        errors.append("active page not found")

    for page_index, page in enumerate(open_pages, start=1):
        _wait_for_page(page, warnings, page_index)
        active_url = _safe_page_url(page) or url
        active_page_url = active_url
        mark_visited_url(visited_urls, active_url)
        _merge_scan_state(
            state,
            _scan_page(
                page,
                page_index=page_index,
                source_url=active_url,
                source_kind="active_page",
                frame_name="",
                warnings=warnings,
            ),
        )

    _note_frame_src_targets_without_scanning(
        state["frame_elements"] + state["iframe_elements"],
        visited_urls,
    )

    global_config = extract_global_routes_from_html(_global_config_source(state))
    route_probe_report: dict[str, Any] | None = None
    if probe_route:
        if context is None:
            warnings.append("route probe requires browser context")
        else:
            route_base_url = _select_route_base_url(active_page_url, state["frame_urls"], url)
            route_state, route_probe_report = _probe_route(
                context,
                active_url=route_base_url,
                route_name=probe_route,
                global_config=global_config,
                warnings=warnings,
                page_index=len(open_pages) + 1,
            )
            if route_state is not None:
                _merge_scan_state(state, route_state)

    return build_selector_discovery_report(
        url=url,
        elements=state["elements"],
        frame_urls=state["frame_urls"],
        frame_elements=state["frame_elements"],
        iframe_elements=state["iframe_elements"],
        pages_count=len(open_pages),
        frames_count=state["frames_scanned"],
        frame_src_pages_scanned=state["frame_src_pages_scanned"],
        visited_urls=visited_urls,
        live_frames=state["live_frames"],
        elements_sample_by_frame=state["elements_sample_by_frame"],
        global_config=global_config,
        route_probe=route_probe_report,
        warnings=warnings,
        errors=errors,
    )


def build_selector_discovery_report(
    *,
    url: str,
    elements: list[dict[str, Any]],
    frame_urls: list[str] | None = None,
    frame_elements: list[dict[str, Any]] | None = None,
    iframe_elements: list[dict[str, Any]] | None = None,
    pages_count: int = 0,
    frames_count: int = 0,
    frame_src_pages_scanned: list[str] | None = None,
    visited_urls: set[str] | None = None,
    live_frames: list[dict[str, Any]] | None = None,
    elements_sample_by_frame: dict[str, list[dict[str, Any]]] | None = None,
    global_config: dict[str, Any] | None = None,
    route_probe: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    report_warnings: list[str] = list(warnings or [])
    report_errors: list[str] = list(errors or [])
    frame_elements = list(frame_elements or [])
    iframe_elements = list(iframe_elements or [])
    live_frames = list(live_frames or [])
    detection_elements, ignored_login_groups = _filter_login_page_elements(elements)

    if any(group["source_kind"] == "frame_src_page" for group in ignored_login_groups):
        _append_warning_once(report_warnings, "frame src page appears to be login page; ignored")
    if live_frames and all(bool(frame.get("appears_login_page")) for frame in live_frames):
        _append_warning_once(report_warnings, "not logged in or redirected to login page")
    elif elements and not detection_elements and ignored_login_groups:
        _append_warning_once(report_warnings, "not logged in or redirected to login page")

    number_candidates = detect_number_candidates(detection_elements)
    amount_field_candidates = detect_amount_field_candidates(detection_elements)
    danger_candidates = detect_danger_candidates(detection_elements)
    number_selectors = number_candidates
    amount_field_selectors = amount_field_candidates
    danger_selectors = danger_candidates
    available_labels = discover_available_labels(detection_elements, frame_elements + iframe_elements)
    current_game_guess = guess_current_game("\n".join(available_labels), detection_elements)
    resolved_global_config = global_config or _empty_global_config()
    market_state = build_market_state(
        global_config=resolved_global_config,
        route_probe=route_probe,
        detection_elements=detection_elements,
    )
    can_probe_bet_page = bool(market_state["can_probe_bet_page"])

    if not current_game_guess:
        report_warnings.append("supported game label not found")
    if can_probe_bet_page:
        if len(number_selectors) != 39:
            report_warnings.append(f"expected 39 number selectors, found {len(number_selectors)}")
        if len(number_candidates) == 0:
            report_warnings.append("number candidates not found; inspect live_frames sample_text and elements_sample")
        if not any(amount_field_selectors.values()):
            report_warnings.append("amount field selectors not found")
        if not danger_selectors:
            report_warnings.append("danger buttons not found; do not proceed to assisted fill until verified")
    elif market_state["reason"]:
        _append_warning_once(
            report_warnings,
            "bet page unavailable because market appears closed; selector discovery postponed",
        )
    if not elements and not frame_elements and not iframe_elements:
        report_errors.append("unable to read page content or elements")

    safe_to_continue = can_probe_bet_page and not report_errors
    return {
        "url": url,
        "mode": "selector_discovery",
        "current_game_guess": current_game_guess,
        "global_config": resolved_global_config,
        "market_state": market_state,
        "safe_to_continue": safe_to_continue,
        "next_action": (
            "review selector candidates"
            if safe_to_continue
            else "wait until market opens, then rerun selector discovery"
        ),
        "route_probe": route_probe,
        "available_labels": available_labels,
        "number_selectors_count": len(number_selectors),
        "number_selectors": number_selectors,
        "number_candidates_count": len(number_candidates),
        "number_candidates": number_candidates,
        "amount_field_selectors": amount_field_selectors,
        "amount_field_candidates": amount_field_candidates,
        "danger_selectors": danger_selectors,
        "danger_candidates": danger_candidates,
        "diagnostics": {
            "pages_count": pages_count,
            "frames_count": frames_count,
            "frame_urls": _dedupe(frame_urls or []),
            "frame_elements": frame_elements,
            "iframe_elements": iframe_elements,
            "frame_src_pages_scanned": _dedupe(frame_src_pages_scanned or []),
            "visited_urls_count": len(visited_urls or set()),
            "elements_scanned": len(detection_elements),
            "live_frames": live_frames,
            "elements_sample_by_frame": elements_sample_by_frame or {},
        },
        "warnings": report_warnings,
        "errors": report_errors,
    }


def should_scan_frame_src(frame_src: str) -> bool:
    src = str(frame_src or "").strip()
    if not src:
        return False
    lower = src.lower()
    return not (
        lower == "about:blank"
        or lower.startswith("javascript:")
        or src.startswith("#")
    )


def resolve_frame_src(active_url: str, frame_src: str) -> str | None:
    if not should_scan_frame_src(frame_src):
        return None
    base = active_origin_url(active_url)
    if not base:
        return None
    resolved = urljoin(base, str(frame_src).strip())
    return urldefrag(resolved)[0]


def active_origin_url(active_url: str) -> str:
    parsed = urlsplit(str(active_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def build_route_url(active_url: str, route: str) -> str | None:
    route = str(route or "").strip()
    active = urlsplit(str(active_url or "").strip())
    if not route or not active.scheme or not active.netloc:
        return None

    route_parts = urlsplit(route)
    if route_parts.scheme and route_parts.netloc:
        return urldefrag(route)[0]

    active_path = active.path or "/"
    front_index = active_path.find("/Front/")
    token_prefix = active_path[:front_index].rstrip("/") if front_index >= 0 else ""
    route_path = route_parts.path
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    if route_path.startswith("/Front/"):
        full_path = f"{token_prefix}{route_path}" if token_prefix else route_path
    else:
        full_path = route_path
    return urlunsplit((active.scheme, active.netloc, full_path, route_parts.query, route_parts.fragment))


def detect_login_page(text: str, html: str = "", elements: list[dict[str, Any]] | None = None) -> bool:
    return _appears_login_page(text, html, elements)


def detect_market_closed(text: str, html: str = "", elements: list[dict[str, Any]] | None = None) -> bool:
    content = "\n".join([text or "", html or "", _elements_text(elements or [])])
    return any(marker in content for marker in CLOSED_MARKERS)


def build_market_state(
    *,
    global_config: dict[str, Any],
    route_probe: dict[str, Any] | None,
    detection_elements: list[dict[str, Any]],
) -> dict[str, Any]:
    current_game_id = global_config.get("game_id")
    current_game_name = _game_name_for_id(global_config, current_game_id)
    selected_route = route_probe.get("label") if route_probe else None
    selected_route_url = route_probe.get("route") if route_probe else None
    game_state = _game_state_by_name(global_config)

    can_probe_bet_page = bool(detection_elements)
    reason = "" if can_probe_bet_page else "market closed or bet page unavailable"

    if route_probe:
        status = str(route_probe.get("status") or "")
        unavailable = (
            bool(route_probe.get("appears_404"))
            or bool(route_probe.get("appears_login_page"))
            or bool(route_probe.get("appears_market_closed"))
            or status in {"http_404", "login_page", "market_closed", "bet_page_unavailable"}
            or route_probe.get("has_bet_page_body") is False
        )
        can_probe_bet_page = not unavailable
        reason = "" if can_probe_bet_page else "market closed or bet page unavailable"

    return {
        "current_game_id": current_game_id,
        "current_game_name": current_game_name,
        "all_bet_state": global_config.get("all_bet_state"),
        "games": build_market_state_summary(global_config)["games"],
        "game_state": game_state,
        "selected_route": selected_route,
        "selected_route_url": selected_route_url,
        "can_probe_bet_page": can_probe_bet_page,
        "reason": reason,
    }


def _select_route_base_url(active_url: str, frame_urls: list[str], fallback_url: str) -> str:
    candidates = _dedupe([active_url, *reversed(frame_urls or []), fallback_url])
    if not candidates:
        return fallback_url

    def score(candidate: str) -> tuple[int, int, int]:
        parsed = urlsplit(candidate)
        host = parsed.netloc.lower()
        path = parsed.path or ""
        front_index = path.find("/Front/")
        has_token_front = front_index > 0
        is_entry_host = host == "www.gts362.com"
        has_front = front_index >= 0
        return (
            1 if has_token_front else 0,
            1 if not is_entry_host else 0,
            1 if has_front else 0,
        )

    return max(candidates, key=score)


def _route_url_missing_session_token(built_url: str | None, route: str) -> bool:
    if not built_url:
        return False
    parsed = urlsplit(built_url)
    route_path = urlsplit(route).path
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host == "www.gts362.com" and route_path.startswith("/Front/"):
        return True
    return path.startswith("/Front/")


def extract_global_routes_from_html(html: str) -> dict[str, Any]:
    source = str(html or "")
    menu_value = _extract_global_assignment(source, "Menu")
    game_list_value = _extract_global_assignment(source, "GameList")
    all_game_value = _extract_global_assignment(source, "AllGame")
    config = _empty_global_config()
    config["game_id"] = _parse_int(_extract_global_assignment(source, "GameID"))
    config["all_bet_state"] = _parse_int(_extract_global_assignment(source, "AllBetState"))
    config["game_state"] = _parse_js_value(_extract_global_assignment(source, "GameState")) or {}
    config["default_page"] = _parse_string_value(_extract_global_assignment(source, "DefaultPage"))
    config["game_list"] = _parse_js_value(game_list_value) if game_list_value else None
    config["all_game"] = _parse_js_value(all_game_value) if all_game_value else None
    config["routes"] = _extract_routes_from_menu(menu_value, source)
    return config


def mark_visited_url(visited_urls: set[str], url: str) -> bool:
    normalized = urldefrag(str(url or "").strip())[0]
    if not normalized or normalized in visited_urls:
        return False
    visited_urls.add(normalized)
    return True


def build_candidate_selectors(element_info: dict[str, Any]) -> list[str]:
    element = _normalize_element(element_info)
    tag = element["tag"] or "*"
    selectors: list[str] = []

    if element["id"]:
        if _is_simple_css_identifier(element["id"]):
            selectors.append(f"#{element['id']}")
        selectors.append(f'{tag}[id="{_attr_value(element["id"])}"]')
    if element["name"]:
        selectors.append(f'{tag}[name="{_attr_value(element["name"])}"]')
    if element["type"]:
        selectors.append(f'{tag}[type="{_attr_value(element["type"])}"]')
    if element["value"]:
        selectors.append(f'{tag}[value="{_attr_value(element["value"])}"]')
    if element["href"]:
        selectors.append(f'{tag}[href="{_attr_value(element["href"])}"]')
    if element["src"]:
        selectors.append(f'{tag}[src="{_attr_value(element["src"])}"]')
    if element["title"]:
        selectors.append(f'{tag}[title="{_attr_value(element["title"])}"]')
    if element["alt"]:
        selectors.append(f'{tag}[alt="{_attr_value(element["alt"])}"]')
    if element["ariaLabel"]:
        selectors.append(f'{tag}[aria-label="{_attr_value(element["ariaLabel"])}"]')
    first_class = _first_css_class(element["className"])
    if first_class:
        selectors.append(f"{tag}.{first_class}")
    if element["role"]:
        selectors.append(f'{tag}[role="{_attr_value(element["role"])}"]')
    if element["text"]:
        short_text = _short_text(element["text"])
        selectors.append(f"text={short_text}")
        selectors.append(f'{tag}:has-text("{_attr_value(short_text)}")')

    return _dedupe(selectors)


def detect_number_candidates(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {label: [] for label in NUMBER_LABELS}
    seen: set[tuple[str, str, str, str]] = set()
    for element in elements:
        normalized = _normalize_element(element)
        for label, field_name, field_value in _number_matches_for_element(normalized):
            record = _selector_record(normalized)
            record["matched_field"] = field_name
            record["matched_value"] = _short_text(field_value, 500)
            key = (label, record["frame_url"], str(record["index"]), field_name)
            if key in seen:
                continue
            found[label].append(record)
            seen.add(key)
    return {label: records for label, records in found.items() if records}


def detect_number_elements(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return detect_number_candidates(elements)


def detect_amount_field_candidates(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {marker: [] for marker in AMOUNT_MARKERS}
    normalized = [_normalize_element(element) for element in elements]
    seen: set[tuple[str, str, str, str]] = set()

    for index, element in enumerate(normalized):
        if element["tag"] not in INPUT_TAGS:
            continue

        search_sources = {
            "direct": _input_amount_search_text(element),
            "parent": "\n".join([element["parentText"], element["parentHTML"]]),
            "grandparent": "\n".join([element["grandparentText"], element["grandparentHTML"]]),
            "nearby": "\n".join(
                _element_search_text(item) for item in normalized[max(0, index - 3) : index + 4]
            ),
        }

        for marker in AMOUNT_MARKERS:
            for source_name, source_text in search_sources.items():
                if not _contains_amount_marker(source_text, marker):
                    continue
                record = _selector_record(element)
                record["matched_field"] = source_name
                record["matched_value"] = _short_text(source_text, 500)
                key = (marker, record["frame_url"], str(record["index"]), source_name)
                if key in seen:
                    continue
                found[marker].append(record)
                seen.add(key)
                break

    return found


def detect_amount_fields(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return detect_amount_field_candidates(elements)


def detect_danger_candidates(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for element in elements:
        normalized = _normalize_element(element)
        if normalized["tag"] not in DANGER_TAGS:
            continue
        for field_name, field_value in _candidate_match_fields(normalized):
            if not field_value or not is_dangerous_action(field_value):
                continue
            record = _selector_record(normalized)
            record["text"] = field_value
            record["matched_field"] = field_name
            record["matched_value"] = _short_text(field_value, 500)
            key = (field_value, record["frame_url"], str(record["index"]))
            if key in seen:
                continue
            found.append(record)
            seen.add(key)
            break
    return found


def detect_danger_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return detect_danger_candidates(elements)


def _empty_global_config() -> dict[str, Any]:
    return {
        "game_id": None,
        "all_bet_state": None,
        "game_state": {},
        "default_page": None,
        "game_list": None,
        "all_game": None,
        "routes": {},
    }


def _extract_global_assignment(source: str, name: str) -> str | None:
    match = re.search(rf"\$Global\.{re.escape(name)}\s*=\s*", source)
    if not match:
        return None
    index = match.end()
    while index < len(source) and source[index].isspace():
        index += 1
    if index >= len(source):
        return None

    first = source[index]
    if first in "{[":
        return _read_balanced_js(source, index)
    if first in "\"'":
        return _read_quoted_js(source, index)

    end = index
    while end < len(source) and source[end] not in ";\r\n<":
        end += 1
    return source[index:end].strip()


def _read_balanced_js(source: str, start: int) -> str | None:
    opening = source[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return None


def _read_quoted_js(source: str, start: int) -> str | None:
    quote = source[start]
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return source[start : index + 1]
    return None


def _parse_js_value(raw: str | None) -> Any:
    if raw is None:
        return None
    raw = raw.strip().rstrip(";")
    if not raw:
        return None
    for candidate in (raw, _js_to_jsonish(raw)):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    if raw[0:1] in "\"'" and raw[-1:] == raw[0]:
        return raw[1:-1]
    return raw


def _js_to_jsonish(raw: str) -> str:
    text = raw.replace("'", '"')
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', text)
    return text


def _parse_string_value(raw: str | None) -> str | None:
    value = _parse_js_value(raw)
    if value is None:
        return None
    return str(value)


def _parse_int(raw: str | None) -> int | None:
    value = _parse_js_value(raw)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _game_state_by_name(global_config: dict[str, Any]) -> dict[str, Any]:
    raw_state = global_config.get("game_state") or {}
    if not isinstance(raw_state, dict):
        return {}
    result: dict[str, Any] = {}
    for game_id, state in raw_state.items():
        name = _game_name_for_id(global_config, game_id)
        result[name or str(game_id)] = state
    return result


def _game_name_for_id(global_config: dict[str, Any], game_id: Any) -> str | None:
    if game_id is None:
        return None
    game_id_text = str(game_id)
    for source_name in ("all_game", "game_list"):
        name = _find_game_name_in_source(global_config.get(source_name), game_id_text)
        if name:
            return name
    return KNOWN_GAME_NAMES.get(game_id_text)


def _find_game_name_in_source(source: Any, game_id: str) -> str | None:
    if isinstance(source, dict):
        if game_id in source:
            return _game_name_from_value(source[game_id])
        for key, value in source.items():
            if str(key) == game_id:
                return _game_name_from_value(value)
            if isinstance(value, dict) and _dict_game_id(value) == game_id:
                return _game_name_from_value(value)
    if isinstance(source, list):
        for item in source:
            if isinstance(item, dict) and _dict_game_id(item) == game_id:
                return _game_name_from_value(item)
    return None


def _dict_game_id(item: dict[str, Any]) -> str | None:
    for key in ("ID", "Id", "id", "GameID", "game_id", "GameId"):
        if key in item:
            return str(item[key])
    return None


def _game_name_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("Name", "name", "GameName", "game_name", "Title", "title"):
            if key in value and value[key]:
                return str(value[key])
    return None


def _extract_routes_from_menu(menu_raw: str | None, source: str) -> dict[str, str]:
    routes: dict[str, str] = {}
    parsed_menu = _parse_js_value(menu_raw)
    if parsed_menu is not None:
        _collect_routes_from_object(parsed_menu, routes)
    fallback_source = "\n".join([str(menu_raw or ""), source])
    for label in ROUTE_LABELS:
        if label not in routes:
            route = _find_route_near_label(fallback_source, label)
            if route:
                routes[label] = route
    return routes


def _collect_routes_from_object(value: Any, routes: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in ROUTE_LABELS and isinstance(item, str) and "/Front/" in item:
                routes.setdefault(key_text, item)

        string_values = [str(item) for item in value.values() if isinstance(item, (str, int, float))]
        joined = " ".join(string_values)
        route_values = [item for item in string_values if "/Front/" in item]
        for label in ROUTE_LABELS:
            if label in joined and route_values:
                routes.setdefault(label, route_values[0])

        for item in value.values():
            _collect_routes_from_object(item, routes)
    elif isinstance(value, list):
        for item in value:
            _collect_routes_from_object(item, routes)


def _find_route_near_label(source: str, label: str) -> str | None:
    for match in re.finditer(re.escape(label), source):
        start = max(0, match.start() - 500)
        end = min(len(source), match.end() + 500)
        chunk = source[start:end]
        route_match = re.search(r"/Front/[A-Za-z0-9_./?=&%+-]+", chunk)
        if route_match:
            return route_match.group(0).rstrip("',\";)")
    return None


def _filter_login_page_elements(
    elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for element in elements:
        normalized = _normalize_element(element)
        key = (
            normalized["source_kind"],
            normalized["source_url"],
            normalized["frame_url"],
            normalized["frame_name"],
        )
        groups.setdefault(key, []).append(element)

    kept: list[dict[str, Any]] = []
    ignored: list[dict[str, str]] = []
    for (source_kind, source_url, frame_url, frame_name), group in groups.items():
        if _appears_login_page("", "", group):
            ignored.append(
                {
                    "source_kind": source_kind,
                    "source_url": source_url,
                    "frame_url": frame_url,
                    "frame_name": frame_name,
                }
            )
            continue
        kept.extend(group)
    return kept, ignored


def _appears_login_page(
    text: str,
    html: str = "",
    elements: list[dict[str, Any]] | None = None,
) -> bool:
    content = "\n".join([text or "", html or "", _elements_text(elements or [])])
    if not content.strip():
        return False
    marker_count = sum(1 for marker in LOGIN_MARKERS if marker in content)
    has_account_password = LOGIN_MARKERS[0] in content and LOGIN_MARKERS[1] in content
    has_chrome_download = LOGIN_MARKERS[3] in content
    return has_chrome_download or has_account_password or marker_count >= 3


def _append_warning_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def discover_available_labels(elements: list[dict[str, Any]], frame_items: list[dict[str, Any]] | None = None) -> list[str]:
    text = "\n".join([_elements_text(elements), _frame_items_text(frame_items or [])])
    labels = list(scan_text_for_labels(text)["available_labels"])
    for item in frame_items or []:
        for key in ["name", "id"]:
            value = str(item.get(key, "")).strip()
            if value:
                labels.append(value)
        src = str(item.get("src", "")).strip()
        if src:
            labels.extend(_src_labels(src))
    return _dedupe(labels)


def _note_frame_src_targets_without_scanning(
    frame_items: list[dict[str, Any]],
    visited_urls: set[str],
) -> None:
    for item in frame_items:
        src = str(item.get("src", ""))
        base_url = active_origin_url(str(item.get("source_url") or item.get("frame_url") or ""))
        if not base_url:
            continue
        resolved = resolve_frame_src(base_url, src)
        if resolved:
            mark_visited_url(visited_urls, resolved)


def _global_config_source(state: dict[str, Any]) -> str:
    return "\n".join(
        [
            "\n".join(state.get("text_sources", [])),
            "\n".join(state.get("html_sources", [])),
            _elements_text(state.get("elements", [])),
        ]
    )


def _probe_route(
    context: Any,
    *,
    active_url: str,
    route_name: str,
    global_config: dict[str, Any],
    warnings: list[str],
    page_index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    route = str((global_config.get("routes") or {}).get(route_name, ""))
    route_url = build_route_url(active_url, route) if route else None
    route_warnings: list[str] = []
    if _route_url_missing_session_token(route_url, route):
        route_warnings.append("route url missing session token path")
        warnings.append("route url missing session token path")
    if not route_url:
        warnings.append(f"route not found for probe: {route_name}")
        return None, {
            "label": route_name,
            "route_name": route_name,
            "route": route or None,
            "built_url": None,
            "url": None,
            "actual_url": None,
            "status": "route_not_found",
            "appears_404": False,
            "appears_login_page": False,
            "title": "",
            "text_length": 0,
            "html_length": 0,
            "html_sample": "",
            "elements_sample": [],
            "number_candidates": {},
            "amount_field_candidates": {},
            "danger_candidates": [],
            "warnings": [f"route not found for probe: {route_name}"],
        }

    page = context.new_page()
    probe_warnings: list[str] = list(route_warnings)
    status_code: int | None = None
    try:
        try:
            response = page.goto(route_url, wait_until="domcontentloaded", timeout=15000)
            status_code = int(response.status) if response is not None else None
        except Exception as exc:  # pragma: no cover - depends on live browser state
            probe_warnings.append(f"route probe page load warning {route_url}: {exc}")
        _wait_for_page(page, probe_warnings, page_index)
        scan = _scan_page(
            page,
            page_index=page_index,
            source_url=route_url,
            source_kind="route_probe",
            frame_name=route_name,
            warnings=probe_warnings,
        )
        appears_login = bool(scan["live_frames"]) and all(
            bool(frame.get("appears_login_page")) for frame in scan["live_frames"]
        )
        html = "\n".join(scan["html_sources"])
        text = "\n".join(scan["text_sources"])
        title = _safe_page_title(page)
        appears_404 = _appears_404(status_code, title, text, html)
        appears_market_closed = detect_market_closed(text, html, scan["elements"])
        number_candidates = detect_number_candidates(scan["elements"])
        amount_field_candidates = detect_amount_field_candidates(scan["elements"])
        danger_candidates = detect_danger_candidates(scan["elements"])
        has_bet_page_body = _has_bet_page_body(number_candidates, amount_field_candidates)
        if appears_login:
            probe_warnings.append("route probe redirected to login page")
            warnings.append("route probe redirected to login page")
            merge_state: dict[str, Any] | None = None
        elif appears_404 or appears_market_closed or not has_bet_page_body:
            merge_state = None
        else:
            merge_state = scan

        elements = scan["elements"] if merge_state is not None else []
        report = {
            "label": route_name,
            "route_name": route_name,
            "route": route,
            "built_url": route_url,
            "url": route_url,
            "actual_url": _safe_page_url(page),
            "status": _route_probe_status(
                appears_login,
                appears_404,
                appears_market_closed,
                not has_bet_page_body,
            ),
            "appears_404": appears_404,
            "appears_login_page": appears_login,
            "appears_market_closed": appears_market_closed,
            "has_bet_page_body": has_bet_page_body,
            "title": title,
            "text_length": sum(len(text) for text in scan["text_sources"]),
            "html_length": sum(len(html) for html in scan["html_sources"]),
            "html_sample": _short_text(html, 500) if appears_404 else "",
            "elements_sample": elements[:ELEMENT_SAMPLE_PER_FRAME],
            "number_candidates": detect_number_candidates(elements),
            "amount_field_candidates": detect_amount_field_candidates(elements),
            "danger_candidates": detect_danger_candidates(elements),
            "live_frames": scan["live_frames"],
            "warnings": probe_warnings,
        }
        return merge_state, report
    finally:
        try:
            page.close()
        except Exception:  # pragma: no cover - depends on live browser state
            pass


def _appears_404(status_code: int | None, title: str, text: str, html: str) -> bool:
    if status_code == 404:
        return True
    content = "\n".join([title or "", text or "", html or ""]).lower()
    return any(marker in content for marker in ("http 404", "404 not found", "404 -", ">404<"))


def _has_bet_page_body(
    number_candidates: dict[str, list[dict[str, Any]]],
    amount_field_candidates: dict[str, list[dict[str, Any]]],
) -> bool:
    return bool(number_candidates) or any(amount_field_candidates.values())


def _route_probe_status(
    appears_login: bool,
    appears_404: bool,
    appears_market_closed: bool,
    missing_bet_page_body: bool,
) -> str:
    if appears_login:
        return "login_page"
    if appears_404:
        return "http_404"
    if appears_market_closed:
        return "market_closed"
    if missing_bet_page_body:
        return "bet_page_unavailable"
    return "ok"


def _frame_src_targets(
    frame_items: list[dict[str, Any]],
    visited_urls: set[str],
    *,
    depth: int = 1,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for item in frame_items:
        src = str(item.get("src", ""))
        base_url = active_origin_url(str(item.get("source_url") or item.get("frame_url") or ""))
        if not base_url:
            continue
        resolved = resolve_frame_src(base_url, src)
        if not resolved or not mark_visited_url(visited_urls, resolved):
            continue
        targets.append(
            {
                "url": resolved,
                "frame_name": str(item.get("name") or item.get("id") or item.get("tag") or ""),
                "depth": depth,
            }
        )
    return targets


def _scan_page(
    page: Any,
    *,
    page_index: int,
    source_url: str,
    source_kind: str,
    frame_name: str,
    warnings: list[str],
) -> dict[str, Any]:
    state = _empty_scan_state()
    for frame_index, frame in enumerate(_collect_all_frames(page, warnings), start=1):
        state["frames_scanned"] += 1
        frame_url = _safe_frame_url(frame) or source_url
        frame_base_url = active_origin_url(source_url)
        if frame_url:
            state["frame_urls"].append(frame_url)
        frame_text, frame_html = _read_frame_texts(frame, page_index, frame_index, warnings)
        if frame_text:
            state["text_sources"].append(frame_text)
        if frame_html:
            state["html_sources"].append(frame_html)
        frame_elements_raw = _collect_selector_elements(
            frame,
            frame_url,
            page_index,
            frame_index,
            warnings,
            source_url=source_url,
            source_kind=source_kind,
            frame_name=frame_name,
        )
        appears_login_page = _appears_login_page(frame_text, frame_html, frame_elements_raw)
        state["live_frames"].append(
            _live_frame_info(
                frame,
                frame_url,
                frame_text,
                frame_html,
                len(frame_elements_raw),
                appears_login_page,
            )
        )
        frame_sample_key = _frame_sample_key(frame, frame_url, frame_name)
        state["elements_sample_by_frame"][frame_sample_key] = frame_elements_raw[:ELEMENT_SAMPLE_PER_FRAME]
        frame_elements = _collect_embedded_frame_elements(
            frame,
            page_index,
            frame_index,
            "frame",
            warnings,
            source_url=frame_base_url,
            source_kind=source_kind,
            frame_name=frame_name,
        )
        iframe_elements = _collect_embedded_frame_elements(
            frame,
            page_index,
            frame_index,
            "iframe",
            warnings,
            source_url=frame_base_url,
            source_kind=source_kind,
            frame_name=frame_name,
        )
        state["frame_elements"].extend(frame_elements)
        state["iframe_elements"].extend(iframe_elements)
        if not appears_login_page:
            state["elements"].extend(frame_elements_raw)
    return state


def _empty_scan_state() -> dict[str, Any]:
    return {
        "elements": [],
        "frame_urls": [],
        "frame_elements": [],
        "iframe_elements": [],
        "frame_src_pages_scanned": [],
        "frames_scanned": 0,
        "live_frames": [],
        "elements_sample_by_frame": {},
        "text_sources": [],
        "html_sources": [],
    }


def _merge_scan_state(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["elements"].extend(source["elements"])
    target["frame_urls"].extend(source["frame_urls"])
    target["frame_elements"].extend(source["frame_elements"])
    target["iframe_elements"].extend(source["iframe_elements"])
    target["frame_src_pages_scanned"].extend(source["frame_src_pages_scanned"])
    target["frames_scanned"] += source["frames_scanned"]
    target["live_frames"].extend(source["live_frames"])
    target["text_sources"].extend(source["text_sources"])
    target["html_sources"].extend(source["html_sources"])
    for frame_key, samples in source["elements_sample_by_frame"].items():
        target["elements_sample_by_frame"].setdefault(frame_key, []).extend(samples)


def _collect_selector_elements(
    frame: Any,
    frame_url: str,
    page_index: int,
    frame_index: int,
    warnings: list[str],
    *,
    source_url: str,
    source_kind: str,
    frame_name: str,
) -> list[dict[str, Any]]:
    script = f"""
    () => Array.from(document.querySelectorAll('{SCAN_SELECTOR}')).map((el, index) => {{
      const attr = (name) => el.getAttribute(name) || '';
      const className = typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '');
      const parent = el.parentElement;
      const grandparent = parent ? parent.parentElement : null;
      return {{
        tag: (el.tagName || '').toLowerCase(),
        textContent: el.textContent || '',
        innerText: el.innerText || '',
        text: el.innerText || el.textContent || '',
        value: 'value' in el ? el.value || '' : '',
        id: el.id || '',
        name: attr('name'),
        className,
        type: attr('type'),
        role: attr('role'),
        href: attr('href'),
        src: attr('src'),
        alt: attr('alt'),
        placeholder: attr('placeholder'),
        title: attr('title'),
        onclick: attr('onclick'),
        ariaLabel: attr('aria-label'),
        outerHTML: (el.outerHTML || '').slice(0, 500),
        parentText: parent ? (parent.innerText || parent.textContent || '') : '',
        parentHTML: parent ? (parent.outerHTML || '').slice(0, 500) : '',
        grandparentText: grandparent ? (grandparent.innerText || grandparent.textContent || '') : '',
        grandparentHTML: grandparent ? (grandparent.outerHTML || '').slice(0, 500) : '',
        index,
      }};
    }})
    """
    try:
        raw_items = frame.evaluate(script)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"page {page_index} frame {frame_index} element scan failed: {exc}")
        return []

    elements: list[dict[str, Any]] = []
    actual_frame_name = _safe_frame_name(frame) or frame_name
    for item in raw_items:
        element = _normalize_element(item)
        element["frame_url"] = frame_url
        element["source_url"] = source_url
        element["source_kind"] = source_kind
        element["frame_name"] = actual_frame_name
        element["page_index"] = page_index
        element["frame_index"] = frame_index
        element["index"] = item.get("index")
        elements.append(element)
    return elements


def _collect_embedded_frame_elements(
    frame: Any,
    page_index: int,
    frame_index: int,
    tag: str,
    warnings: list[str],
    *,
    source_url: str,
    source_kind: str,
    frame_name: str,
) -> list[dict[str, str]]:
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
        warnings.append(f"page {page_index} frame {frame_index} {tag} scan failed: {exc}")
        return []
    actual_frame_name = _safe_frame_name(frame) or frame_name
    return [
        {
            "tag": str(item.get("tag", tag)),
            "name": str(item.get("name", "")),
            "id": str(item.get("id", "")),
            "src": str(item.get("src", "")),
            "frame_url": _safe_frame_url(frame),
            "source_url": source_url,
            "source_kind": source_kind,
            "frame_name": actual_frame_name,
        }
        for item in raw_items
    ]


def _collect_all_frames(page: Any, warnings: list[str]) -> list[Any]:
    try:
        roots = list(page.frames)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"unable to list page frames: {exc}")
        return []

    frames: list[Any] = []
    seen: set[int] = set()

    def add_frame(frame: Any) -> None:
        identity = id(frame)
        if identity in seen:
            return
        seen.add(identity)
        frames.append(frame)
        try:
            child_frames = getattr(frame, "child_frames", [])
            children = list(child_frames() if callable(child_frames) else child_frames)
        except Exception:  # pragma: no cover - depends on live browser state
            children = []
        for child in children:
            add_frame(child)

    for frame in roots:
        add_frame(frame)
    return frames


def _read_frame_texts(frame: Any, page_index: int, frame_index: int, warnings: list[str]) -> tuple[str, str]:
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
            warnings.append(f"page {page_index} frame {frame_index} {label} unavailable: {exc}")
            continue
        if not value:
            continue
        if is_html:
            html_parts.append(value)
        else:
            text_parts.append(value)
    return "\n".join(text_parts), "\n".join(html_parts)


def _live_frame_info(
    frame: Any,
    frame_url: str,
    frame_text: str,
    frame_html: str,
    element_count: int,
    appears_login_page: bool,
) -> dict[str, Any]:
    parent = _safe_parent_frame(frame)
    return {
        "name": _safe_frame_name(frame),
        "url": frame_url,
        "parent": _safe_frame_name(parent) if parent is not None else "",
        "has_parent": parent is not None,
        "element_count": element_count,
        "text_length": len(frame_text),
        "html_length": len(frame_html),
        "sample_text": _short_text(frame_text, 300),
        "appears_login_page": appears_login_page,
    }


def _frame_sample_key(frame: Any, frame_url: str, fallback_name: str) -> str:
    name = _safe_frame_name(frame) or fallback_name or "frame"
    if frame_url:
        return f"{name} | {frame_url}"
    return name


def _selector_record(element: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_element(element)
    return {
        "tag": normalized["tag"],
        "text": normalized["text"],
        "textContent": normalized["textContent"],
        "innerText": normalized["innerText"],
        "value": normalized["value"],
        "id": normalized["id"],
        "name": normalized["name"],
        "className": normalized["className"],
        "type": normalized["type"],
        "role": normalized["role"],
        "href": normalized["href"],
        "src": normalized["src"],
        "alt": normalized["alt"],
        "title": normalized["title"],
        "onclick": normalized["onclick"],
        "ariaLabel": normalized["ariaLabel"],
        "frame_url": normalized["frame_url"],
        "source_url": normalized["source_url"],
        "source_kind": normalized["source_kind"],
        "frame_name": normalized["frame_name"],
        "index": normalized["index"],
        "candidate_selectors": build_candidate_selectors(normalized),
    }


def _normalize_element(element: dict[str, Any]) -> dict[str, Any]:
    text_content = str(element.get("textContent", element.get("text", ""))).strip()
    inner_text = str(element.get("innerText", element.get("text", ""))).strip()
    text = str(element.get("text", inner_text or text_content)).strip()
    return {
        "tag": str(element.get("tag", "")).lower(),
        "text": text,
        "textContent": text_content,
        "innerText": inner_text,
        "value": str(element.get("value", "")).strip(),
        "id": str(element.get("id", "")).strip(),
        "name": str(element.get("name", "")).strip(),
        "className": str(element.get("className", element.get("class_name", ""))).strip(),
        "type": str(element.get("type", "")).strip(),
        "role": str(element.get("role", "")).strip(),
        "href": str(element.get("href", "")).strip(),
        "src": str(element.get("src", "")).strip(),
        "alt": str(element.get("alt", "")).strip(),
        "onclick": str(element.get("onclick", "")).strip(),
        "outerHTML": str(element.get("outerHTML", "")).strip(),
        "parentText": str(element.get("parentText", "")).strip(),
        "parentHTML": str(element.get("parentHTML", "")).strip(),
        "grandparentText": str(element.get("grandparentText", "")).strip(),
        "grandparentHTML": str(element.get("grandparentHTML", "")).strip(),
        "frame_url": str(element.get("frame_url", "")).strip(),
        "source_url": str(element.get("source_url", "")).strip(),
        "source_kind": str(element.get("source_kind", "")).strip(),
        "frame_name": str(element.get("frame_name", "")).strip(),
        "placeholder": str(element.get("placeholder", "")).strip(),
        "title": str(element.get("title", "")).strip(),
        "ariaLabel": str(element.get("ariaLabel", element.get("aria_label", ""))).strip(),
        "index": element.get("index"),
        "page_index": element.get("page_index"),
        "frame_index": element.get("frame_index"),
    }


def _element_search_text(element: dict[str, Any]) -> str:
    normalized = _normalize_element(element)
    return "\n".join(
        [
            normalized["text"],
            normalized["textContent"],
            normalized["innerText"],
            normalized["value"],
            normalized["id"],
            normalized["name"],
            normalized["className"],
            normalized["type"],
            normalized["role"],
            normalized["href"],
            normalized["src"],
            normalized["placeholder"],
            normalized["title"],
            normalized["alt"],
            normalized["onclick"],
            normalized["ariaLabel"],
            normalized["outerHTML"],
            normalized["parentText"],
            normalized["parentHTML"],
            normalized["grandparentText"],
            normalized["grandparentHTML"],
        ]
    )


def _candidate_match_fields(element: dict[str, Any]) -> list[tuple[str, str]]:
    normalized = _normalize_element(element)
    return [(field, normalized[field]) for field in MATCH_FIELDS if normalized.get(field)]


def _number_matches_for_element(element: dict[str, Any]) -> list[tuple[str, str, str]]:
    matches: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field_name, field_value in _candidate_match_fields(element):
        for match in re.findall(r"(?<!\d)(0[1-9]|[12]\d|3[0-9])(?!\d)", field_value):
            key = (match, field_name)
            if key in seen:
                continue
            matches.append((match, field_name, field_value))
            seen.add(key)
    return matches


def _input_amount_search_text(element: dict[str, Any]) -> str:
    normalized = _normalize_element(element)
    return "\n".join(
        [
            normalized["value"],
            normalized["id"],
            normalized["name"],
            normalized["className"],
            normalized["type"],
            normalized["role"],
            normalized["placeholder"],
            normalized["title"],
            normalized["ariaLabel"],
            normalized["outerHTML"],
        ]
    )


def _number_labels_for_element(element: dict[str, Any]) -> list[str]:
    normalized = _normalize_element(element)
    labels: set[str] = set()
    for label, _, _ in _number_matches_for_element(normalized):
        labels.add(label)
    return sorted(labels, key=lambda value: int(value))


def _contains_amount_marker(text: str, marker: str) -> bool:
    normalized = _normalize_search_text(text)
    if not normalized:
        return False
    aliases = list(AMOUNT_ALIASES.get(marker, (marker,)))
    try:
        star_number = str(AMOUNT_MARKERS.index(marker) + 2)
    except ValueError:
        star_number = ""
    if star_number:
        english = {"2": "two", "3": "three", "4": "four"}[star_number]
        aliases.extend(
            [
                star_number,
                f"{star_number}star",
                f"star{star_number}",
                f"star_{star_number}",
                f"star-{star_number}",
                f"amount{star_number}",
                f"amount_{star_number}",
                f"amount-{star_number}",
                english,
                f"{english}star",
                f"{english}_star",
                f"{english}-star",
            ]
        )
    return any(_normalize_search_text(alias) in normalized for alias in aliases)


def _danger_text(element: dict[str, Any]) -> str:
    for _, field in _candidate_match_fields(element):
        if field and is_dangerous_action(field):
            return field
    return ""


def _wait_for_page(page: Any, warnings: list[str], page_index: int) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"page {page_index} domcontentloaded wait failed: {exc}")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as exc:  # pragma: no cover - depends on live browser state
        warnings.append(f"page {page_index} networkidle wait failed: {exc}")


def _safe_frame_url(frame: Any) -> str:
    try:
        return str(frame.url)
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _safe_frame_name(frame: Any | None) -> str:
    if frame is None:
        return ""
    try:
        name = getattr(frame, "name", "")
        return str(name() if callable(name) else name or "")
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _safe_parent_frame(frame: Any) -> Any | None:
    try:
        parent = getattr(frame, "parent_frame", None)
        return parent() if callable(parent) else parent
    except Exception:  # pragma: no cover - depends on live browser state
        return None


def _safe_page_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _safe_page_title(page: Any) -> str:
    try:
        return str(page.title())
    except Exception:  # pragma: no cover - depends on live browser state
        return ""


def _is_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:  # pragma: no cover - depends on live browser state
        return True


def _elements_text(elements: list[dict[str, Any]]) -> str:
    return "\n".join(_element_search_text(element) for element in elements)


def _frame_items_text(items: list[dict[str, Any]]) -> str:
    return "\n".join(
        " ".join(
            [
                str(item.get("tag", "")),
                str(item.get("name", "")),
                str(item.get("id", "")),
                str(item.get("src", "")),
            ]
        )
        for item in items
    )


def _src_labels(src: str) -> list[str]:
    labels: list[str] = []
    for item in re.split(r"[/?.=&_-]+", src):
        if item and len(item) <= 30 and not item.isdigit():
            labels.append(item)
    return labels


def _short_text(text: str, limit: int = 80) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:limit]


def _attr_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _is_simple_css_identifier(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value or "") is not None


def _first_css_class(class_name: str) -> str:
    for item in str(class_name or "").split():
        if _is_simple_css_identifier(item):
            return item
    return ""


def _normalize_search_text(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "").lower())


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


__all__ = [
    "build_candidate_selectors",
    "build_market_state",
    "build_selector_discovery_report",
    "build_route_url",
    "detect_amount_fields",
    "detect_danger_elements",
    "detect_login_page",
    "detect_market_closed",
    "detect_number_elements",
    "discover_selectors",
    "discover_available_labels",
    "extract_global_routes_from_html",
    "mark_visited_url",
    "resolve_frame_src",
    "run_selector_discovery",
    "should_scan_frame_src",
]
