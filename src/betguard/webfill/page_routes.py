from __future__ import annotations

import json
import re
from typing import Any


PAGE_ROUTE_LABELS = (
    "全車",
    "二三四星",
    "快速輸入",
    "台號",
    "特尾三",
)


def parse_page_routes_from_html(source: str) -> dict[str, dict[str, str]]:
    menu_raw = _extract_global_assignment(str(source or ""), "Menu")
    routes = extract_page_routes(menu_raw, str(source or ""))
    return {"routes": routes}


def extract_page_routes(menu_raw: str | None, source: str = "") -> dict[str, str]:
    routes: dict[str, str] = {}
    parsed_menu = _parse_js_value(menu_raw)
    if parsed_menu is not None:
        _collect_routes_from_object(parsed_menu, routes)

    fallback_source = "\n".join([str(menu_raw or ""), source])
    for label in PAGE_ROUTE_LABELS:
        if label not in routes:
            route = _find_route_near_label(fallback_source, label)
            if route:
                routes[label] = route
    return routes


def assisted_fill_route_error(selector_report: dict[str, Any], page_name: str | None = None) -> str | None:
    route_probe = selector_report.get("route_probe")
    if isinstance(route_probe, dict):
        label = str(route_probe.get("label") or route_probe.get("route_name") or page_name or "")
        if route_probe.get("status") == "route_not_found":
            return f"route not found for page: {label or 'unknown'}"
        if route_probe.get("route") or route_probe.get("built_url") or route_probe.get("url") or route_probe.get("actual_url"):
            return None
        return f"route not found for page: {label or 'unknown'}"

    market_state = selector_report.get("market_state") or {}
    if isinstance(market_state, dict) and market_state.get("selected_route_url"):
        return None

    routes = (selector_report.get("global_config") or {}).get("routes") or {}
    if page_name:
        if isinstance(routes, dict) and routes.get(page_name):
            return None
        return f"route not found for page: {page_name}"

    if isinstance(routes, dict) and any(routes.get(label) for label in PAGE_ROUTE_LABELS):
        return None
    return "route not found for assisted fill"


def _collect_routes_from_object(value: Any, routes: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in PAGE_ROUTE_LABELS and isinstance(item, str) and "/Front/" in item:
                routes.setdefault(key_text, item)

        string_values = [str(item) for item in value.values() if isinstance(item, (str, int, float))]
        joined = " ".join(string_values)
        route_values = [item for item in string_values if "/Front/" in item]
        for label in PAGE_ROUTE_LABELS:
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

