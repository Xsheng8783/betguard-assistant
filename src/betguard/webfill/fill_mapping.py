from __future__ import annotations

import re
from typing import Any

from betguard.webfill.fill_plan import FORBIDDEN_STEPS
from betguard.webfill.selector_discovery import build_candidate_selectors


B03_NUMBER_LABELS = tuple(f"{number:02d}" for number in range(1, 40))
B03_AMOUNT_STARS = ("二星", "三星", "四星")
B03_DANGER_WORDS = (
    "送出注單",
    "加入注單",
    "加入常用牌組",
    "加入",
    "送出",
    "確認",
    "確定",
    "下注",
    "清除",
    "清",
    "刪除",
    "close",
    "加入{0}星單碰注單",
    "刪除全部{0}星注單",
)


def build_b03_selector_mapping(route_probe_report: dict[str, Any]) -> dict[str, Any]:
    input_debug = _b03_input_debug(route_probe_report)
    elements = normalize_b03_route_elements(route_probe_report)
    page = _b03_page_info(route_probe_report, elements)
    number_selectors = _b03_number_selectors(route_probe_report, elements)
    quick_input = _b03_quick_input(elements)
    amount_field_candidates = _b03_amount_fields(route_probe_report, elements)
    danger_candidates = _b03_danger_candidates(route_probe_report, elements)
    warnings: list[str] = []
    errors: list[str] = []
    main_frame_count = sum(1 for element in elements if _frame_name(element) == "mainFrame")
    route_page_text_detected = bool(_combined_text(elements).strip())
    b03_text_detected = _b03_text_detected(elements)

    if _route_probe_login_page(route_probe_report):
        warnings.append("route probe redirected to login page; selector mapping disabled")
        elements = []
        number_selectors = {label: [] for label in B03_NUMBER_LABELS}
        quick_input = {"number_input_candidates": [], "send_button_candidates": []}
        amount_field_candidates = {star: [] for star in B03_AMOUNT_STARS}
        danger_candidates = []
        main_frame_count = 0
        route_page_text_detected = False
        b03_text_detected = False

    if not elements:
        errors.append("no route_probe mainFrame elements available for B03 mapping")
        errors.append("no route_probe B03 elements were passed into mapping")
        errors.append("check CLI pipeline: selector_report may be replaced before mapping")

    for candidate in quick_input.get("send_button_candidates", []):
        danger = {
            "text": candidate.get("text") or "送出",
            "tag": candidate.get("tag") or "button",
            "selector_hint": candidate.get("selector_hint") or "",
            "candidate_selectors": candidate.get("candidate_selectors") or [],
            "frame_name": candidate.get("frame_name") or "",
            "frame_url": candidate.get("frame_url") or "",
            "reason": "quick input send is restricted",
            "type": "restricted_action",
        }
        _append_unique_candidate(danger_candidates, danger)

    amount_fields_confident = all(_has_confident_amount_selector(amount_field_candidates.get(star) or []) for star in B03_AMOUNT_STARS)
    quick_input_found = bool(quick_input["number_input_candidates"])
    number_count = sum(1 for records in number_selectors.values() if records)

    if number_count != 39:
        warnings.append(f"expected 39 B03 number selectors, found {number_count}")
    if not quick_input_found:
        warnings.append("quick input number field not confidently detected")
    if not amount_fields_confident:
        warnings.append("amount input selectors not confidently detected")

    return {
        "mode": "b03_selector_mapping",
        "page": page,
        "number_selectors": number_selectors,
        "quick_input": quick_input,
        "amount_field_candidates": amount_field_candidates,
        "danger_candidates": danger_candidates,
        "safe_to_assisted_fill": False,
        "number_selectors_count": number_count,
        "quick_input_found": quick_input_found,
        "amount_fields_confident": amount_fields_confident,
        "danger_candidates_count": len(danger_candidates),
        "debug": {
            **input_debug,
            "route_elements": len(elements),
            "B03_candidate_elements": len(elements),
            "mainFrame_elements": main_frame_count,
            "route_page_text_detected": route_page_text_detected,
            "b03_text_detected": b03_text_detected,
            "mapping_source": "route_probe mainFrame" if main_frame_count else "route_probe",
            "first_b03_frame_url": _first_value(elements, "frame_url"),
            "first_b03_source_url": _first_value(elements, "source_url"),
            "first_b03_text_preview": _short_preview(_b03_page_text(elements), 200),
        },
        "warnings": warnings,
        "errors": errors,
    }


def _b03_input_debug(report: dict[str, Any]) -> dict[str, Any]:
    route_probe = report.get("route_probe") if isinstance(report.get("route_probe"), dict) else {}
    sample = _elements_sample_by_frame(report)
    raw_element_like = _collect_b03_raw_element_like_dicts(report)
    return {
        "input_keys": list(report.keys()),
        "has_elements_sample_by_frame": sample is not None,
        "elements_sample_by_frame_type": type(sample).__name__ if sample is not None else "NoneType",
        "raw_route_probe_keys": list(route_probe.keys()),
        "raw_element_like_dicts_found": len(raw_element_like),
    }


def format_pretty_b03_selector_mapping(mapping: dict[str, Any]) -> str:
    page = mapping.get("page") if isinstance(mapping.get("page"), dict) else {}
    debug = mapping.get("debug") if isinstance(mapping.get("debug"), dict) else {}
    quick_input = mapping.get("quick_input") if isinstance(mapping.get("quick_input"), dict) else {}
    amount_fields = mapping.get("amount_field_candidates") if isinstance(mapping.get("amount_field_candidates"), dict) else {}
    danger_candidates = mapping.get("danger_candidates") if isinstance(mapping.get("danger_candidates"), list) else []
    confidence = _number_mapping_confidence(mapping)
    lines = [
        "B03 Selector Mapping",
        "",
        "Page:",
        f"- game: {page.get('game') or ''}",
        f"- route: {page.get('route') or ''}",
        f"- frame: {page.get('frame_name') or ''}",
        "",
        "Debug:",
        f"- input keys: {', '.join(str(key) for key in debug.get('input_keys', []))}",
        f"- has elements_sample_by_frame: {'yes' if debug.get('has_elements_sample_by_frame') else 'no'}",
        f"- elements_sample_by_frame type: {debug.get('elements_sample_by_frame_type') or ''}",
        f"- raw element-like dicts found: {debug.get('raw_element_like_dicts_found', 0)}",
        f"- B03 candidate elements: {debug.get('B03_candidate_elements', 0)}",
        f"- route elements: {debug.get('route_elements', 0)}",
        f"- mainFrame elements: {debug.get('mainFrame_elements', 0)}",
        f"- route page text detected: {'yes' if debug.get('route_page_text_detected') else 'no'}",
        f"- B03 text detected: {'yes' if debug.get('b03_text_detected') else 'no'}",
        f"- first B03 frame_url: {debug.get('first_b03_frame_url') or ''}",
        f"- first B03 source_url: {debug.get('first_b03_source_url') or ''}",
        f"- first B03 text preview: {debug.get('first_b03_text_preview') or ''}",
        f"- mapping source: {debug.get('mapping_source') or ''}",
        "",
        "Numbers:",
        f"- found: {mapping.get('number_selectors_count', 0)} / 39",
        f"- confidence: {confidence}",
        "",
        "Quick Input:",
        f"- number input: {'found' if quick_input.get('number_input_candidates') else 'not found'}",
        f"- send button: {'restricted' if quick_input.get('send_button_candidates') else 'not found'}",
        "",
        "Amount Fields:",
    ]
    for star in B03_AMOUNT_STARS:
        candidates = amount_fields.get(star) or []
        if any(candidate.get("labels_detected") for candidate in candidates if isinstance(candidate, dict)):
            status = "label found / selector unsure"
        else:
            status = "found" if candidates else "unsure"
        lines.append(f"- {star}: {status}")
    lines.extend(["", "Danger:"])
    if danger_candidates:
        for item in danger_candidates:
            lines.append(f"- {item.get('text') or item.get('matched_value') or ''}")
    else:
        lines.append("- none detected")
    if mapping.get("errors"):
        lines.extend(["", "Errors:"])
        for error in mapping["errors"]:
            lines.append(f"- {error}")
    lines.extend(
        [
            "",
            "Final:",
            f"- safe_to_assisted_fill: {str(mapping.get('safe_to_assisted_fill')).lower()}",
            "- real_site_operation: disabled",
        ]
    )
    return "\n".join(lines)


def _number_mapping_confidence(mapping: dict[str, Any]) -> str:
    number_selectors = mapping.get("number_selectors")
    if not isinstance(number_selectors, dict):
        return "low"
    records = [record for items in number_selectors.values() for record in (items or []) if isinstance(record, dict)]
    if not records:
        return "low"
    if all(record.get("source") == "page_text_fallback" for record in records):
        return "medium"
    return "high"


def normalize_b03_route_elements(route_probe_report: dict[str, Any]) -> list[dict[str, Any]]:
    if _route_probe_login_page(route_probe_report):
        return []

    raw_elements: list[dict[str, Any]] = []
    raw_elements.extend(collect_element_like_dicts(_elements_sample_by_frame(route_probe_report)))
    diagnostics = route_probe_report.get("diagnostics")
    if isinstance(diagnostics, dict):
        raw_elements.extend(collect_element_like_dicts(diagnostics))
    raw_elements.extend(_collect_b03_raw_element_like_dicts(route_probe_report))

    route_probe = route_probe_report.get("route_probe")
    if isinstance(route_probe, dict):
        route_elements = route_probe.get("elements")
        if isinstance(route_elements, list):
            raw_elements.extend(item for item in route_elements if isinstance(item, dict))
        route_sample = route_probe.get("elements_sample")
        if isinstance(route_sample, list):
            raw_elements.extend(item for item in route_sample if isinstance(item, dict))

    elements = route_probe_report.get("route_probe_elements")
    if isinstance(elements, list):
        raw_elements.extend(item for item in elements if isinstance(item, dict))

    _extend_elements_from_sample(raw_elements, _elements_sample_by_frame(route_probe_report))

    pages = route_probe_report.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            frames = page.get("frames")
            if not isinstance(frames, list):
                continue
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                frame_elements = frame.get("elements")
                if isinstance(frame_elements, list):
                    for item in frame_elements:
                        if not isinstance(item, dict):
                            continue
                        item = dict(item)
                        item.setdefault("frame_name", frame.get("name", ""))
                        item.setdefault("frame_url", frame.get("url", ""))
                        raw_elements.append(item)

    normalized = [
        element
        for element in (_normalize_b03_element(element) for element in raw_elements)
        if not _is_container_only_element(element)
    ]
    route_main = [
        element for element in normalized if element.get("source_kind") == "route_probe" and element.get("frame_name") == "mainFrame"
    ]
    b03_candidates = [element for element in normalized if _is_b03_candidate_element(element)]
    route_b03 = [element for element in normalized if _is_route_probe_b03_element(element)]
    route_any = [element for element in normalized if element.get("source_kind") == "route_probe"]
    selected = route_main or b03_candidates or route_b03 or route_any or normalized
    return _dedupe_b03_elements(selected)


def collect_element_like_dicts(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if _is_element_like_dict(value):
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return found


def _collect_b03_raw_element_like_dicts(report: dict[str, Any]) -> list[dict[str, Any]]:
    return collect_element_like_dicts(report)


def _is_element_like_dict(value: dict[str, Any]) -> bool:
    element_keys = {
        "tag",
        "outerHTML",
        "text",
        "textContent",
        "innerText",
        "frame_url",
        "source_url",
        "source_kind",
        "frame_name",
        "parentText",
        "grandparentText",
        "grandparentHTML",
    }
    return any(key in value for key in element_keys)


def _is_container_only_element(element: dict[str, Any]) -> bool:
    if "elements" not in element:
        return False
    return not any(
        str(element.get(field) or "").strip()
        for field in (
            "tag",
            "text",
            "textContent",
            "innerText",
            "value",
            "outerHTML",
            "parentText",
            "parentHTML",
            "grandparentText",
            "grandparentHTML",
        )
    )


def _elements_sample_by_frame(report: dict[str, Any]) -> Any:
    if "elements_sample_by_frame" in report:
        return report.get("elements_sample_by_frame")
    diagnostics = report.get("diagnostics")
    if isinstance(diagnostics, dict) and "elements_sample_by_frame" in diagnostics:
        return diagnostics.get("elements_sample_by_frame")
    return None


def _extend_elements_from_sample(raw_elements: list[dict[str, Any]], sample: Any) -> None:
    if isinstance(sample, list):
        for item in sample:
            _append_sample_item(raw_elements, item, {})
        return
    if not isinstance(sample, dict):
        return
    frames = sample.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            _append_sample_item(raw_elements, frame, {})
        return
    for key, value in sample.items():
        if key == "frames":
            continue
        defaults = _frame_defaults_from_key(str(key))
        _append_sample_item(raw_elements, value, defaults)


def _append_sample_item(raw_elements: list[dict[str, Any]], item: Any, defaults: dict[str, str]) -> None:
    if isinstance(item, list):
        for child in item:
            _append_sample_item(raw_elements, child, defaults)
        return
    if not isinstance(item, dict):
        return
    if isinstance(item.get("elements"), list):
        frame_defaults = {
            **defaults,
            "frame_name": str(item.get("frame_name") or item.get("name") or defaults.get("frame_name", "")),
            "frame_url": str(item.get("frame_url") or item.get("url") or defaults.get("frame_url", "")),
            "source_url": str(item.get("source_url") or item.get("url") or defaults.get("source_url", "")),
            "source_kind": str(item.get("source_kind") or defaults.get("source_kind", "route_probe")),
        }
        for element in item["elements"]:
            _append_sample_item(raw_elements, element, frame_defaults)
        return
    element = dict(item)
    for key, value in defaults.items():
        element.setdefault(key, value)
    if not element.get("source_kind"):
        element["source_kind"] = "route_probe"
    raw_elements.append(element)


def _frame_defaults_from_key(key: str) -> dict[str, str]:
    frame_name = ""
    frame_url = ""
    if "|" in key:
        left, right = key.split("|", 1)
        frame_name = left.strip()
        frame_url = right.strip()
    else:
        frame_name = key.strip()
    return {
        "frame_name": frame_name,
        "frame_url": frame_url,
        "source_url": frame_url,
        "source_kind": "route_probe",
    }


def _normalize_b03_element(element: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(element)
    for key in (
        "tag",
        "text",
        "textContent",
        "innerText",
        "value",
        "outerHTML",
        "parentText",
        "parentHTML",
        "grandparentText",
        "grandparentHTML",
        "frame_name",
        "source_kind",
        "frame_url",
        "source_url",
        "id",
        "name",
        "className",
        "onclick",
        "alt",
        "title",
        "ariaLabel",
        "page_index",
        "frame_index",
        "index",
    ):
        normalized[key] = str(normalized.get(key) or "")
    if not normalized["source_kind"] and _is_b03_route_url(normalized):
        normalized["source_kind"] = "route_probe"
    return normalized


def _is_route_probe_b03_element(element: dict[str, Any]) -> bool:
    if element.get("source_kind") != "route_probe":
        return False
    return _frame_name(element) == "mainFrame" or _is_b03_route_url(element)


def _is_b03_candidate_element(element: dict[str, Any]) -> bool:
    if element.get("source_kind") == "route_probe" and _frame_name(element) == "mainFrame":
        return True
    if _is_b03_route_url(element):
        return True
    text = _b03_element_text_fields(element)
    return any(marker in text for marker in ("539 - 下注資訊", "快速輸入", "送出注單", "連碰", "二三星碰法"))


def _is_b03_route_url(element: dict[str, Any]) -> bool:
    return "/Front/B/B03" in "\n".join(
        [
            str(element.get("frame_url") or ""),
            str(element.get("source_url") or ""),
        ]
    )


def _b03_element_text_fields(element: dict[str, Any]) -> str:
    return "\n".join(
        str(element.get(field) or "")
        for field in (
            "text",
            "textContent",
            "innerText",
            "outerHTML",
            "parentText",
            "parentHTML",
            "grandparentText",
            "grandparentHTML",
        )
    )


def _dedupe_b03_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for element in elements:
        key = (
            str(element.get("tag") or ""),
            str(element.get("text") or element.get("innerText") or element.get("textContent") or ""),
            str(element.get("outerHTML") or ""),
            str(element.get("parentText") or ""),
            str(element.get("grandparentText") or ""),
        )
        if key in seen:
            continue
        result.append(element)
        seen.add(key)
    return result


def _route_probe_login_page(route_probe_report: dict[str, Any]) -> bool:
    route_probe = route_probe_report.get("route_probe")
    if isinstance(route_probe, dict) and route_probe.get("appears_login_page") is True:
        return True
    if route_probe_report.get("appears_login_page") is True:
        return True
    return False


def _b03_page_info(route_probe_report: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any]:
    route_probe = route_probe_report.get("route_probe") if isinstance(route_probe_report.get("route_probe"), dict) else {}
    global_config = (
        route_probe_report.get("global_config") if isinstance(route_probe_report.get("global_config"), dict) else {}
    )
    route = (
        route_probe.get("route_path")
        or route_probe.get("route")
        or (global_config.get("routes") or {}).get("二三四星")
        or "/Front/B/B03"
    )
    return {
        "game": _guess_b03_game(route_probe_report, elements),
        "route": route,
        "frame_name": _preferred_frame_name(elements),
        "url": route_probe.get("actual_url") or route_probe.get("resolved_url") or route_probe.get("built_url"),
    }


def _guess_b03_game(route_probe_report: dict[str, Any], elements: list[dict[str, Any]]) -> str | None:
    global_config = route_probe_report.get("global_config")
    if isinstance(global_config, dict) and str(global_config.get("game_id")) == "13":
        return "539"
    text = _combined_text(elements)
    return "539" if "539" in text else None


def _preferred_frame_name(elements: list[dict[str, Any]]) -> str:
    for element in elements:
        if str(element.get("frame_name") or "") == "mainFrame":
            return "mainFrame"
    for element in elements:
        frame_name = str(element.get("frame_name") or "").strip()
        if frame_name:
            return frame_name
    return ""


def _b03_number_selectors(route_probe_report: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {label: [] for label in B03_NUMBER_LABELS}

    existing = route_probe_report.get("number_candidates")
    if isinstance(existing, dict):
        for label in B03_NUMBER_LABELS:
            for candidate in existing.get(label, []) or []:
                if isinstance(candidate, dict):
                    _append_unique_candidate(found[label], _b03_selector_candidate(candidate, matched_label=label))

    for element in elements:
        for label in B03_NUMBER_LABELS:
            if _is_b03_number_element(element, label):
                _append_unique_candidate(found[label], _b03_selector_candidate(element, matched_label=label))
    _apply_page_text_number_fallback(found, elements)
    return found


def _is_b03_number_element(element: dict[str, Any], label: str) -> bool:
    if _source_kind(element) not in {"", "route_probe"}:
        return False
    if _frame_name(element) and _frame_name(element) != "mainFrame":
        return False

    exact_fields = ("text", "innerText", "textContent", "value")
    if not any(_clean_text(element.get(field)) == label for field in exact_fields):
        return False

    context = _element_context(element)
    if "OnSwitchSel" in context or "html: NO" in context or "data-bind" in context:
        return True
    tag = str(element.get("tag") or "").lower()
    return tag in {"button", "a", "span", "div", "td", "th", "label"}


def _apply_page_text_number_fallback(found: dict[str, list[dict[str, Any]]], elements: list[dict[str, Any]]) -> None:
    text = _b03_page_text(elements)
    if not _looks_like_number_selection_area(text):
        return
    labels = set(re.findall(r"(?<!\d)(0[1-9]|[1-3][0-9])(?!\d)", text))
    for label in B03_NUMBER_LABELS:
        if found[label] or label not in labels:
            continue
        found[label].append(
            {
                "source": "page_text_fallback",
                "confidence": "medium",
                "matched_label": label,
                "frame_name": "mainFrame",
                "source_kind": "route_probe",
                "selector_hint": "",
                "candidate_selectors": [],
            }
        )


def _looks_like_number_selection_area(text: str) -> bool:
    return any(marker in text for marker in ("連碰", "請點選號碼", "號碼", "OnSwitchSel", "快速輸入"))


def _b03_quick_input(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    number_inputs: list[dict[str, Any]] = []
    send_buttons: list[dict[str, Any]] = []
    for element in elements:
        tag = str(element.get("tag") or "").lower()
        context = _element_context(element)
        if _is_quick_number_input_element(tag, context):
            candidate = _b03_selector_candidate(element)
            if "QkNums" in context and not candidate["selector_hint"]:
                candidate["selector_hint"] = "input[data-bind*='QkNums']"
                candidate["candidate_selectors"] = ["input[data-bind*='QkNums']"]
            _append_unique_candidate(number_inputs, candidate)
        if _is_quick_send_button_element(tag, element, context):
            candidate = _b03_selector_candidate(element)
            if not candidate["text"]:
                candidate["text"] = "送出"
            if not candidate["selector_hint"]:
                candidate["selector_hint"] = "button[data-bind*='Mo.OnAddSel']"
                candidate["candidate_selectors"] = ["button[data-bind*='Mo.OnAddSel']"]
            candidate["restricted"] = True
            candidate["reason"] = "quick input send button must not be auto-clicked"
            _append_unique_candidate(send_buttons, candidate)
    return {
        "number_input_candidates": number_inputs,
        "send_button_candidates": send_buttons,
    }


def _is_quick_number_input_element(tag: str, context: str) -> bool:
    if "QkNums" not in context and "Mo.OnChkNo" not in context:
        return tag == "input" and "快速輸入" in context and "號碼" in context
    return tag == "input" or "<input" in context


def _is_quick_send_button_element(tag: str, element: dict[str, Any], context: str) -> bool:
    text = _clean_text(element.get("text") or element.get("value"))
    if tag in {"button", "input", "a"} and text == "送出":
        return True
    return "<button" in context and "送出" in context and ("Mo.OnAddSel" in context or "快速輸入" in context)


def _b03_amount_fields(route_probe_report: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {star: [] for star in B03_AMOUNT_STARS}
    existing = route_probe_report.get("amount_field_candidates")
    if isinstance(existing, dict):
        for star in B03_AMOUNT_STARS:
            for candidate in existing.get(star, []) or []:
                if isinstance(candidate, dict):
                    _append_unique_candidate(found[star], _b03_selector_candidate(candidate))

    for index, element in enumerate(elements):
        if str(element.get("tag") or "").lower() not in {"input", "select", "textarea"}:
            continue
        context = _element_context(element)
        for star in B03_AMOUNT_STARS:
            if star in context and any(word in context for word in ("本金", "每碰金額", "下注金額", "金額")):
                _append_unique_candidate(
                    found[star],
                    _b03_amount_diagnostic_candidate(element, star=star, index=index),
                )
    page_text = _b03_page_text(elements)
    for star in B03_AMOUNT_STARS:
        if found[star]:
            continue
        if star in page_text and any(word in page_text for word in ("本金", "每碰金額", "下注金額", "金額")):
            found[star].append(
                {
                    "source": "page_text_label",
                    "labels_detected": True,
                    "selector_confidence": "low",
                    "matched_label": star,
                    "selector_hint": "",
                    "candidate_selectors": [],
                    "frame_name": "mainFrame",
                    "source_kind": "route_probe",
                }
            )
    return found


def _b03_danger_candidates(route_probe_report: dict[str, Any], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    existing = route_probe_report.get("danger_candidates")
    if isinstance(existing, list):
        for candidate in existing:
            if isinstance(candidate, dict):
                text = _danger_text(candidate)
                if text:
                    _append_unique_candidate(found, _danger_candidate(candidate, text))

    for element in elements:
        context = _element_context(element)
        for keyword in B03_DANGER_WORDS:
            if keyword not in context:
                continue
            _append_unique_candidate(found, _danger_candidate(element, keyword))
    return found


def _b03_selector_candidate(element: dict[str, Any], *, matched_label: str | None = None) -> dict[str, Any]:
    selectors = list(element.get("candidate_selectors") or [])
    if not selectors:
        selectors = build_candidate_selectors(element)
    return {
        "tag": str(element.get("tag") or "").lower(),
        "text": _clean_text(element.get("text") or element.get("innerText") or element.get("textContent")),
        "value": _clean_text(element.get("value")),
        "frame_name": str(element.get("frame_name") or ""),
        "frame_url": str(element.get("frame_url") or ""),
        "source_kind": str(element.get("source_kind") or ""),
        "selector_hint": selectors[0] if selectors else "",
        "candidate_selectors": selectors,
        "matched_label": matched_label,
    }


AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT = 800
AMOUNT_DIAGNOSTIC_TEXT_LIMIT = 200


def _b03_amount_diagnostic_candidate(
    element: dict[str, Any], *, star: str, index: int
) -> dict[str, Any]:
    """Amount-only wrapper that adds bounded diagnostic context to a candidate.

    Reporting-only. Deliberately separate from the shared
    ``_b03_selector_candidate`` (used by number/amount/danger paths) so adding
    diagnostic keys here cannot alter number or danger behavior. Every excerpt is
    truncated via ``_short_preview`` so the report never emits huge blobs.
    """

    candidate = _b03_selector_candidate(element, matched_label=star)
    candidate["amount_diagnostic"] = {
        "matched_label": star,
        "source_index": index,
        "id": str(element.get("id") or ""),
        "name": str(element.get("name") or ""),
        "className": str(element.get("className") or ""),
        "parentText": _short_preview(element.get("parentText"), AMOUNT_DIAGNOSTIC_TEXT_LIMIT),
        "grandparentText": _short_preview(
            element.get("grandparentText"), AMOUNT_DIAGNOSTIC_TEXT_LIMIT
        ),
        "text": _short_preview(
            element.get("text") or element.get("innerText") or element.get("textContent"),
            AMOUNT_DIAGNOSTIC_TEXT_LIMIT,
        ),
        "value": _short_preview(element.get("value"), AMOUNT_DIAGNOSTIC_TEXT_LIMIT),
        "outerHTML": _short_preview(element.get("outerHTML"), AMOUNT_DIAGNOSTIC_OUTER_HTML_LIMIT),
        "diagnostic_source": "automatic_mapping",
    }
    return candidate


def _danger_candidate(element: dict[str, Any], text: str) -> dict[str, Any]:
    base = _b03_selector_candidate(element)
    tag = base["tag"]
    danger_type = "action_candidate" if tag in {"button", "input", "a"} else "text_only"
    return {
        "text": text,
        "tag": tag,
        "selector_hint": base["selector_hint"],
        "candidate_selectors": base["candidate_selectors"],
        "frame_name": base["frame_name"],
        "frame_url": base["frame_url"],
        "reason": "danger keyword",
        "type": danger_type,
    }


def _danger_text(element: dict[str, Any]) -> str:
    for field in ("text", "value", "innerText", "textContent", "matched_value"):
        value = _clean_text(element.get(field))
        if any(keyword in value for keyword in B03_DANGER_WORDS):
            return value
    return ""


def _append_unique_candidate(target: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    key = (
        str(candidate.get("selector_hint") or ""),
        str(candidate.get("text") or ""),
        str(candidate.get("value") or ""),
        str(candidate.get("frame_url") or ""),
    )
    for existing in target:
        existing_key = (
            str(existing.get("selector_hint") or ""),
            str(existing.get("text") or ""),
            str(existing.get("value") or ""),
            str(existing.get("frame_url") or ""),
        )
        if existing_key == key:
            return
    target.append(candidate)


def _combined_text(elements: list[dict[str, Any]]) -> str:
    return "\n".join(_element_context(element) for element in elements)


def _first_value(elements: list[dict[str, Any]], key: str) -> str:
    for element in elements:
        value = str(element.get(key) or "").strip()
        if value:
            return value
    return ""


def _short_preview(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:limit]


def _b03_page_text(elements: list[dict[str, Any]]) -> str:
    fields = (
        "text",
        "innerText",
        "textContent",
        "outerHTML",
        "parentText",
        "parentHTML",
        "grandparentText",
        "grandparentHTML",
    )
    return "\n".join(str(element.get(field) or "") for element in elements for field in fields)


def _b03_text_detected(elements: list[dict[str, Any]]) -> bool:
    text = _b03_page_text(elements)
    return "539" in text and any(marker in text for marker in ("二星", "三星", "四星", "快速輸入", "送出注單"))


def _has_confident_amount_selector(candidates: list[dict[str, Any]]) -> bool:
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("labels_detected") and candidate.get("selector_confidence") == "low":
            continue
        if candidate.get("selector_hint") or candidate.get("candidate_selectors"):
            return True
    return False


def _element_context(element: dict[str, Any]) -> str:
    fields = (
        "text",
        "innerText",
        "textContent",
        "value",
        "id",
        "name",
        "className",
        "onclick",
        "alt",
        "title",
        "ariaLabel",
        "outerHTML",
        "parentText",
        "parentHTML",
        "grandparentText",
        "grandparentHTML",
    )
    return "\n".join(str(element.get(field) or "") for field in fields)


def _source_kind(element: dict[str, Any]) -> str:
    return str(element.get("source_kind") or "")


def _frame_name(element: dict[str, Any]) -> str:
    return str(element.get("frame_name") or "")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def build_dry_run_mapping(fill_plan: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    mapping = _base_mapping()

    if fill_plan.get("executable") is not False:
        mapping["errors"].append("fill plan executable must be false in v0")
        mapping["can_map_all_required_fields"] = False
        return mapping

    if fill_plan.get("errors"):
        mapping["errors"].extend(fill_plan.get("errors", []))
        mapping["can_map_all_required_fields"] = False
        return mapping

    market_state = selector_report.get("market_state")
    if isinstance(market_state, dict) and market_state.get("can_probe_bet_page") is False:
        mapping["errors"].append("bet page unavailable; cannot map selectors")
        mapping["can_map_all_required_fields"] = False
        return mapping

    if not selector_report.get("danger_candidates"):
        mapping["warnings"].append("danger candidates not found; assisted fill must remain disabled")

    steps = list(fill_plan.get("planned_steps", []))
    for step in steps:
        mapped = _map_step(step, selector_report)
        mapping["mapped_steps"].append(mapped)
        if not mapped["selector_candidates"]:
            missing = _missing_for_step(step)
            if missing:
                mapping["missing"].append(missing)

    mapping["can_map_all_required_fields"] = not mapping["missing"] and not mapping["errors"]
    return mapping


def _base_mapping() -> dict[str, Any]:
    return {
        "mode": "assisted_fill_dry_run_mapping_v0",
        "executable": False,
        "requires_human_review": True,
        "can_map_all_required_fields": False,
        "mapped_steps": [],
        "missing": [],
        "forbidden_steps": list(FORBIDDEN_STEPS),
        "warnings": [],
        "errors": [],
    }


def _map_step(step: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    step_type = step.get("type")
    if step_type == "select_number":
        raw = _number_candidates(selector_report).get(str(step.get("label")), [])
        candidates = _rank_actionable_candidates(raw, step_type, str(step.get("label")))
    elif step_type == "set_amount":
        star = str(step.get("star"))
        verified = _position_verified_amount_map(selector_report).get(star)
        if verified is not None:
            raw = [verified]
            candidates = raw
        else:
            raw = _amount_candidates(selector_report).get(star, [])
            candidates = _rank_actionable_candidates(raw, step_type, star)
    else:
        raw = []
        candidates = []
    return {
        "plan_step": step,
        "selector_candidates": candidates,
        "rejected_candidate_count": len(raw) - len(candidates),
    }


# --- Amount Field Discovery Precision v1 ---
#
# The site offers no id/name/data attribute that is unique per star for its
# three 每碰金額 inputs -- every one of them shares the exact same class
# (input.BDAll). The only site-provided marker that is unique to just these
# three elements is the knockout data-bind ``PengBet.Value``, and the only way
# to tell them apart from each other is their pixel position in the same row.
# This block turns that structural fact into a verified, read-only mapping.
# #GroupSet_Value (a grouping/sequence-number field, not an amount field) and
# hidden #ta_*/#tb_* table cells are explicitly excluded regardless of any
# other signal.

AMOUNT_INPUT_DATA_BIND_MARKER = "PengBet.Value"
AMOUNT_ROW_TOP_TOLERANCE = 3
REQUIRED_AMOUNT_FIELD_COUNT = 3
AMOUNT_STAR_ORDER = ("二星", "三星", "四星")


def _position_verified_amount_map(selector_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map 二星/三星/四星 to distinct amount inputs by verified row position.

    Returns ``{}`` (never partially SAFE) unless exactly three visible,
    eligible ``PengBet.Value`` inputs exist in the same row with distinct
    left coordinates. Read-only: never clicks/fills, only classifies
    already-discovered candidates.
    """
    eligible = [
        candidate
        for candidate in _collect_amount_candidate_pool(selector_report)
        if _is_eligible_pengbet_amount_input(candidate)
    ]
    if len(eligible) != REQUIRED_AMOUNT_FIELD_COUNT:
        return {}

    tops = [_box_top(candidate) for candidate in eligible]
    if any(top is None for top in tops):
        return {}
    reference_top = tops[0]
    if any(abs(top - reference_top) > AMOUNT_ROW_TOP_TOLERANCE for top in tops):
        return {}

    lefts = [_box_left(candidate) for candidate in eligible]
    if any(left is None for left in lefts):
        return {}
    if len(set(lefts)) != REQUIRED_AMOUNT_FIELD_COUNT:
        return {}

    ordered = [candidate for _, candidate in sorted(zip(lefts, eligible), key=lambda pair: pair[0])]

    result: dict[str, dict[str, Any]] = {}
    for index, (star, candidate) in enumerate(zip(AMOUNT_STAR_ORDER, ordered)):
        verified = dict(candidate)
        verified["candidate_selectors"] = [
            f'input[data-bind*="{AMOUNT_INPUT_DATA_BIND_MARKER}"] >> nth={index}'
        ]
        verified["position_verified"] = True
        result[star] = verified
    return result


def _collect_amount_candidate_pool(selector_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Union of every star's raw amount candidates, de-duplicated.

    Discovery sometimes files an element under only one star's list even
    though the element could equally be any of the three; position
    verification must consider the full pool, not just one star's list.
    """
    seen: set[tuple[Any, ...]] = set()
    pool: list[dict[str, Any]] = []
    for candidates in _amount_candidates(selector_report).values():
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = _candidate_identity_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            pool.append(candidate)
    return pool


def _candidate_identity_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    box = candidate.get("box") if isinstance(candidate.get("box"), dict) else {}
    return (
        str(candidate.get("tag") or ""),
        str(candidate.get("id") or ""),
        str(candidate.get("outerHTML") or ""),
        box.get("top"),
        box.get("left"),
        box.get("w"),
        box.get("h"),
        str(candidate.get("frame_url") or ""),
    )


def _is_eligible_pengbet_amount_input(candidate: dict[str, Any]) -> bool:
    """True only for a visible amount input that cannot be anything else.

    Explicitly rejects #GroupSet_Value (grouping/sequence field, never an
    amount target), hidden #ta_*/#tb_* table cells, and quick-input /
    number-submit controls, regardless of any other matching signal.
    """
    if str(candidate.get("tag", "")).lower() != "input":
        return False
    if candidate.get("visible") is not True:
        return False
    if candidate.get("hidden") is True:
        return False

    element_id = str(candidate.get("id") or "")
    if element_id == "GroupSet_Value":
        return False
    if element_id.startswith("ta_") or element_id.startswith("tb_"):
        return False

    if AMOUNT_INPUT_DATA_BIND_MARKER not in str(candidate.get("outerHTML") or ""):
        return False

    context_text = " ".join(
        str(candidate.get(field) or "") for field in ("parentText", "grandparentText")
    )
    if "送出" in context_text or "號碼" in context_text:
        return False

    return True


def _box_top(candidate: dict[str, Any]) -> float | None:
    return _box_coordinate(candidate, "top")


def _box_left(candidate: dict[str, Any]) -> float | None:
    return _box_coordinate(candidate, "left")


def _box_coordinate(candidate: dict[str, Any], key: str) -> float | None:
    box = candidate.get("box")
    if not isinstance(box, dict):
        return None
    value = box.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


BROAD_CONTAINER_TAGS = {"html", "head", "body", "script", "style"}
AMOUNT_INPUT_TAGS = {"input", "select", "textarea"}
BROAD_AMOUNT_TAGS = {"html", "head", "body", "script", "style", "table", "tbody", "thead", "tr", "div"}
B03_FRAME_MARKER = "/Front/B/B03"
BROAD_SELECTOR_PREFIXES = ("text=+++", "text=var $Global", "html:has-text", "head:has-text", "body:has-text")
MAX_ACTIONABLE_TEXT_LEN = 40


def _rank_actionable_candidates(
    candidates: list[dict[str, Any]],
    step_type: str,
    target: str,
) -> list[dict[str, Any]]:
    """Drop broad/false-positive candidates and rank precise ones first.

    Read-only: never clicks/fills. Only decides which selectors are precise
    enough to be considered actionable for a later human-confirmed step.
    """

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        if _candidate_is_broad(candidate, step_type):
            continue
        score = _candidate_precision_score(candidate, step_type, target)
        if score <= 0:
            continue
        scored.append((score, -index, candidate))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [candidate for _, _, candidate in scored]


def _candidate_is_broad(candidate: dict[str, Any], step_type: str) -> bool:
    tag = str(candidate.get("tag", "")).lower()
    broad_tags = BROAD_AMOUNT_TAGS if step_type == "set_amount" else BROAD_CONTAINER_TAGS
    if tag in broad_tags:
        return True
    selector = _first_selector(candidate)
    lowered = selector.lower()
    if any(lowered.startswith(prefix.lower()) for prefix in BROAD_SELECTOR_PREFIXES):
        return True
    if selector.startswith("text=") and len(selector) > MAX_ACTIONABLE_TEXT_LEN + 5:
        return True
    return False


def _candidate_precision_score(candidate: dict[str, Any], step_type: str, target: str) -> int:
    score = 0
    tag = str(candidate.get("tag", "")).lower()
    in_b03 = B03_FRAME_MARKER in str(candidate.get("frame_url", ""))
    if in_b03:
        score += 3

    if step_type == "select_number":
        exact = any(
            str(candidate.get(field, "")).strip() == target
            for field in ("text", "innerText", "value")
        )
        if exact:
            score += 5
        else:
            matched = str(candidate.get("matched_value", "")).strip()
            if matched == target:
                score += 2
        if tag in BROAD_CONTAINER_TAGS:
            return 0
        if _first_selector(candidate):
            score += 1
        return score if (exact or in_b03) else 0

    if step_type == "set_amount":
        # Broad containers already dropped in _candidate_is_broad. Input-like
        # fields are strongly preferred; other actionable tags stay usable.
        score += 5 if tag in AMOUNT_INPUT_TAGS else 1
        if any(marker in _candidate_text(candidate) for marker in (target, target.replace("星", ""))):
            score += 2
        # Prefer a candidate that already carries a star-specific, provably
        # unique selector (id/name/star-scoped) over one that only offers a
        # generic shared class such as input.BDAll, so the specific field wins
        # ranking regardless of discovery order.
        if _candidate_has_specific_amount_selector(candidate, target):
            score += 4
        return score

    return 0


def _candidate_has_specific_amount_selector(candidate: dict[str, Any], star: str) -> bool:
    """True when the candidate offers a star-specific, unique amount selector.

    Read-only: only inspects already-discovered selectors. Generic class-only
    selectors (input.BDAll) and broad page selectors never qualify.
    """

    for selector in _candidate_selector_list(candidate):
        if _selector_string_is_broad(selector):
            continue
        if _amount_selector_is_specific(selector, star):
            return True
    return False


def _first_selector(candidate: dict[str, Any]) -> str:
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    for key in ("text", "value", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    return ""


def _candidate_text(candidate: dict[str, Any]) -> str:
    return "\n".join(
        str(candidate.get(field, ""))
        for field in ("text", "innerText", "value", "name", "id", "matched_value")
    )


def _candidate_selector_list(candidate: dict[str, Any]) -> list[str]:
    """Ordered list of concrete selector strings for a candidate.

    Falls back to identity fields when no explicit ``candidate_selectors`` are
    present, matching how the report used to pick a "best" selector.
    """

    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list):
        cleaned = [str(item) for item in selectors if str(item).strip()]
        if cleaned:
            return cleaned
    fallback: list[str] = []
    for key in ("text", "value", "id", "name"):
        value = candidate.get(key)
        if value:
            fallback.append(str(value))
    return fallback


def _selector_string_is_broad(selector: str) -> bool:
    text = str(selector or "")
    lowered = text.lower()
    if any(lowered.startswith(prefix.lower()) for prefix in BROAD_SELECTOR_PREFIXES):
        return True
    if text.startswith("text=") and len(text) > MAX_ACTIONABLE_TEXT_LEN + 5:
        return True
    return False


def _selector_targets_label(selector: str, target: str) -> bool:
    """True when ``selector`` provably targets the exact ``target`` label.

    Generic class-only selectors (``td.selectline2``) return False because they
    could match any sibling cell; only exact-text selectors, ``:has-text``
    scoping, or id/attribute selectors carrying the label token qualify.
    """

    text = str(selector or "").strip()
    label = str(target or "").strip()
    if not text or not label:
        return False
    if text in {f"text={label}", f'text="{label}"'}:
        return True
    if f'has-text("{label}")' in text or f"has-text('{label}')" in text:
        return True
    if label in text and (text.startswith("#") or "[" in text):
        return True
    return False


def _amount_selector_is_specific(selector: str, star: str) -> bool:
    """True when an amount selector is unique enough to trust for a star.

    Plain class selectors such as ``input.BDAll`` are shared across every star
    field, so only id/name/attribute selectors or star-scoped text selectors
    count as specific.
    """

    text = str(selector or "").strip()
    if not text:
        return False
    if text.startswith("#"):
        return True
    if "[" in text and ("id=" in text or "name=" in text):
        return True
    if "data-bind*=" in text and ">> nth=" in text:
        # Synthesized by _position_verified_amount_map: a data-bind attribute
        # that is unique to exactly the 3 amount inputs on this page, paired
        # with a verified left-to-right row position. Never synthesized for a
        # candidate that failed the exactly-3/same-row/distinct-left checks.
        return True
    return _selector_targets_label(text, star)


def resolve_final_selector(candidate: dict[str, Any], step: dict[str, Any]) -> tuple[str, bool]:
    """Pick the final selector for a step, preferring label/star-specific forms.

    Returns ``(selector, unique)`` where ``unique`` reports whether the chosen
    selector is provably specific to the requested number/star. Read-only: it
    never synthesizes selectors that were not discovered, so a candidate that
    only carries a generic class selector stays generic (and ``unique=False``).
    """

    selectors = _candidate_selector_list(candidate)
    non_broad = [selector for selector in selectors if not _selector_string_is_broad(selector)]
    step_type = step.get("type")

    if step_type == "select_number":
        target = str(step.get("label") or "")
        for selector in non_broad:
            if _selector_targets_label(selector, target):
                return selector, True
        if non_broad:
            return non_broad[0], False
        return (selectors[0] if selectors else ""), False

    if step_type == "set_amount":
        star = str(step.get("star") or "")
        for selector in non_broad:
            if _amount_selector_is_specific(selector, star):
                return selector, True
        if non_broad:
            return non_broad[0], False
        return (selectors[0] if selectors else ""), False

    return (selectors[0] if selectors else ""), False


def _missing_for_step(step: dict[str, Any]) -> dict[str, Any] | None:
    step_type = step.get("type")
    if step_type == "select_number":
        return {"type": "number", "label": step.get("label")}
    if step_type == "set_amount":
        return {"type": "amount", "star": step.get("star")}
    return None


def _number_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("number_candidates") or selector_report.get("number_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _amount_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("amount_field_candidates") or selector_report.get("amount_field_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}
