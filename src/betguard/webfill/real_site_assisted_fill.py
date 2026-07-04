from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from betguard.webfill.batch_queue import WAITING_FOR_HUMAN_CONFIRM, mark_item_waiting_for_human
from betguard.webfill.fill_mapping import (
    AMOUNT_INPUT_DATA_BIND_MARKER,
    AMOUNT_ROW_TOP_TOLERANCE,
    B03_FRAME_MARKER,
    REQUIRED_AMOUNT_FIELD_COUNT,
)
from betguard.webfill.real_site_fill_plan import build_real_site_assisted_fill_plan
from betguard.webfill.real_site_fill_preflight import (
    build_execution_actions_from_preflight,
    build_real_site_fill_preflight_report,
)
from betguard.webfill.safety import is_dangerous_action


RISK_LOCK_MESSAGE = (
    "本功能會在真實網站協助帶入號碼與金額。必須加上 "
    "--i-understand-real-site-fill-risk 才能執行；系統不會自動送出或確認。"
)
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
READY_TO_FILL = "READY_TO_FILL"
BLOCKED = "BLOCKED"

ENGLISH_DANGER_WORDS = (
    "submit",
    "confirm",
    "sendbet",
    "send_bet",
    "data-danger",
    "delete",
    "clearall",
)
FINAL_DECISION = {
    "real_site_auto_submit": False,
    "human_required": True,
    "next_step": "Human must inspect and manually confirm/submit. After that, user may run next item.",
}


def build_real_site_assisted_fill_preflight(
    queue: dict[str, Any],
    selector_report: dict[str, Any],
    *,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    report = _base_preflight_report()
    if not risk_acknowledged:
        report["errors"].append(RISK_LOCK_MESSAGE)
        return report

    fill_plan_report = build_real_site_assisted_fill_plan(queue, selector_report)
    report["fill_plan_report"] = fill_plan_report
    report["item"] = fill_plan_report.get("item")
    report["danger_buttons_detected"] = _danger_buttons(fill_plan_report)

    if fill_plan_report.get("status") != READY_FOR_HUMAN_REVIEW:
        report["errors"].append("real_site_fill_plan is not READY_FOR_HUMAN_REVIEW")
        report["errors"].extend(str(error) for error in fill_plan_report.get("errors", []))
        report["missing"].extend(str(item) for item in fill_plan_report.get("missing", []))
        return report

    if not report["danger_buttons_detected"]:
        report["errors"].append("danger candidates not found")
        report["missing"].append("danger candidates missing")
        return report

    for planned_action in fill_plan_report.get("planned_actions", []):
        action = _execution_action(planned_action, selector_report)
        error = validate_real_site_action(action)
        if error:
            report["errors"].append(error)
            return report
        report["execution_actions"].append(action)

    report["status"] = READY_TO_FILL
    return report


def run_real_site_assisted_fill_with_page(
    queue: dict[str, Any],
    profile: dict[str, Any],
    page: Any,
    *,
    item_index: int,
    risk_acknowledged: bool,
) -> dict[str, Any]:
    """Execute one ``approved_fill_queue`` item's actions on ``page``.

    Hard gate (v1b): this never builds or runs an execution action unless a
    fresh ``real_site_fill_preflight`` v1 report for this exact
    ``(queue, profile, item_index)`` is ``READY_FOR_HUMAN_REVIEW``. Selector
    truth always comes from ``build_execution_actions_from_preflight`` --
    the legacy first-candidate path (``build_real_site_assisted_fill_preflight``
    / ``_execution_action`` / ``_first_candidate``) is never called here.
    """
    if not risk_acknowledged:
        return _risk_locked_runtime_report()

    v1_report = build_real_site_fill_preflight_report(queue, profile, item_index=item_index)
    if v1_report.get("status") != READY_FOR_HUMAN_REVIEW:
        return _blocked_runtime_report(v1_report)

    try:
        execution_actions = build_execution_actions_from_preflight(v1_report)
    except ValueError as exc:
        blocked = _blocked_runtime_report(v1_report)
        blocked["errors"].append(str(exc))
        return blocked

    try:
        executed_actions = execute_actions_on_page(page, execution_actions)
    except Exception as exc:
        blocked = _blocked_runtime_report(v1_report)
        blocked["errors"].append(str(exc))
        return blocked

    return build_real_site_assisted_fill_report(queue, v1_report, executed_actions)


def run_real_site_assisted_fill(
    queue: dict[str, Any],
    profile: dict[str, Any],
    *,
    item_index: int,
    url: str,
    risk_acknowledged: bool,
    headless: bool = False,
    input_func: Any = input,
) -> dict[str, Any]:
    """Same v1 preflight gate as ``run_real_site_assisted_fill_with_page``,
    but opens a real browser afterward. The preflight + execution-action
    build happens before any browser is touched, so a BLOCKED preflight never
    reaches Playwright at all.
    """
    if not risk_acknowledged:
        return _risk_locked_runtime_report()

    v1_report = build_real_site_fill_preflight_report(queue, profile, item_index=item_index)
    if v1_report.get("status") != READY_FOR_HUMAN_REVIEW:
        return _blocked_runtime_report(v1_report)

    try:
        execution_actions = build_execution_actions_from_preflight(v1_report)
    except ValueError as exc:
        blocked = _blocked_runtime_report(v1_report)
        blocked["errors"].append(str(exc))
        return blocked

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        blocked = _blocked_runtime_report(v1_report)
        blocked["errors"].append(f"Playwright is not installed: {exc}")
        return blocked

    with sync_playwright() as playwright:  # pragma: no cover - browser dependent
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            input_func("請在瀏覽器中手動登入並進入正確頁面，完成後回到終端機按 Enter 繼續。")
            executed_actions = execute_actions_on_page(page, execution_actions)
            report = build_real_site_assisted_fill_report(queue, v1_report, executed_actions)
            input_func("已完成安全帶入，請人工檢查畫面。按 Enter 結束此工具。")
            return report
        except Exception as exc:
            blocked = _blocked_runtime_report(v1_report)
            blocked["errors"].append(str(exc))
            return blocked
        finally:
            browser.close()


def execute_actions_on_page(page: Any, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for action in actions:
        error = validate_real_site_action(action)
        if error:
            raise RuntimeError(error)

        try:
            locator = _locator_for_action(page, action)
        except Exception as exc:
            raise RuntimeError(f"locator lookup failed for {action_label(action)}: {exc}") from exc

        element_info = _read_element_safety_info(locator)
        if not _metadata_is_safe(element_info):
            raise RuntimeError(f"danger text detected on target element for {action_label(action)}")

        if action["type"] == "SELECT_NUMBER":
            try:
                locator.click()
            except Exception as exc:
                raise RuntimeError(f"click failed for {action_label(action)}: {exc}") from exc
            executed.append(
                {
                    "type": "SELECT_NUMBER",
                    "number": action["number"],
                    "executed": True,
                }
            )
        elif action["type"] == "SET_AMOUNT":
            try:
                locator.fill(str(action["amount"]))
            except Exception as exc:
                raise RuntimeError(f"fill failed for {action_label(action)}: {exc}") from exc
            executed.append(
                {
                    "type": "SET_AMOUNT",
                    "star": action["star"],
                    "amount": action["amount"],
                    "executed": True,
                }
            )
        else:
            raise RuntimeError(f"unsupported action type: {action.get('type')}")
    return executed


def build_real_site_assisted_fill_report(
    queue: dict[str, Any],
    v1_report: dict[str, Any],
    executed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    item = v1_report.get("item") or {}
    updated_queue = mark_item_waiting_for_human(deepcopy(queue), int(item.get("index")))
    danger_check = v1_report.get("danger_check") or {}
    return {
        "mode": "real_site_assisted_fill",
        "status": WAITING_FOR_HUMAN_CONFIRM,
        "item": item,
        "actions_executed": executed_actions,
        "danger_buttons_detected": list(danger_check.get("dangerous_buttons_detected", [])),
        "danger_buttons_clicked": [],
        "queue_status": updated_queue.get("status"),
        "queue": updated_queue,
        "final_decision": dict(FINAL_DECISION),
        "warnings": [],
        "errors": [],
    }


def validate_real_site_action(action: dict[str, Any]) -> str | None:
    action_type = action.get("type")
    selector = str(action.get("selector") or "")
    if action_type not in {"SELECT_NUMBER", "SET_AMOUNT"}:
        return f"unsupported action type: {action_type}"
    if not selector:
        return f"selector missing for {action_label(action)}"
    if _contains_danger_text(selector):
        return f"unsafe selector blocked for {action_label(action)}"
    if _targets_groupset_value(selector, action.get("candidate") or {}):
        return (
            f"unsafe selector blocked for {action_label(action)}: "
            "GroupSet_Value must never be used as an amount target"
        )
    if not _metadata_is_safe(action.get("candidate") or {}):
        return f"unsafe selector candidate blocked for {action_label(action)}"

    if action_type == "SELECT_NUMBER":
        number = str(action.get("number") or "")
        if re.fullmatch(r"0[1-9]|[12]\d|3[0-9]", number) is None:
            return f"invalid number action: {number}"
        return None

    try:
        amount = float(action.get("amount"))
    except (TypeError, ValueError):
        return f"invalid amount action: {action.get('amount')}"
    if amount <= 0:
        return f"invalid amount action: {action.get('amount')}"
    if not action.get("star"):
        return "missing amount star"
    return None


def format_pretty_real_site_assisted_fill(report: dict[str, Any]) -> str:
    item = report.get("item") or {}
    lines = [
        "Real-site Assisted Fill Report",
        "",
        f"Status: {report.get('status')}",
        "",
        "Current Item:",
        f"[{item.get('index', '')}] {item.get('original_text') or item.get('original', '')}".rstrip(),
    ]
    if item.get("parsed_summary"):
        lines.append(str(item["parsed_summary"]))

    lines.extend(["", "Executed:"])
    for action in report.get("actions_executed", []):
        if action.get("type") == "SELECT_NUMBER":
            lines.append(f"- SELECT_NUMBER {action.get('number')}")
        elif action.get("type") == "SET_AMOUNT":
            lines.append(f"- SET_AMOUNT {action.get('star')} {action.get('amount')}")

    lines.extend(["", "Danger:"])
    for label in report.get("danger_buttons_detected", []):
        lines.append(f"- {label} detected, not clicked")
    if not report.get("danger_buttons_detected"):
        lines.append("- danger candidates not verified")

    queue = report.get("queue") or {}
    items = list(queue.get("items", []))
    next_locked = any(item.get("status") == WAITING_FOR_HUMAN_CONFIRM for item in items)
    lines.extend(
        [
            "",
            "Queue:",
            f"- current item: {report.get('queue_status') or queue.get('status')}",
            f"- next item: {'locked until human confirms current item' if next_locked else 'not locked'}",
        ]
    )

    if report.get("missing"):
        lines.append("")
        lines.append("Missing:")
        for item_missing in report["missing"]:
            lines.append(f"- {item_missing}")

    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for error in report["errors"]:
            lines.append(f"- {error}")

    decision = report.get("final_decision", FINAL_DECISION)
    lines.extend(
        [
            "",
            "Final:",
            f"- real_site_auto_submit: {str(decision.get('real_site_auto_submit')).lower()}",
            f"- human_required: {str(decision.get('human_required')).lower()}",
        ]
    )
    return "\n".join(lines)


def action_label(action: dict[str, Any]) -> str:
    if action.get("type") == "SELECT_NUMBER":
        return f"SELECT_NUMBER {action.get('number')}"
    if action.get("type") == "SET_AMOUNT":
        return f"SET_AMOUNT {action.get('star')} {action.get('amount')}"
    return str(action.get("type") or "UNKNOWN")


def _base_preflight_report() -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill_preflight",
        "status": BLOCKED,
        "item": None,
        "execution_actions": [],
        "danger_buttons_detected": [],
        "danger_buttons_clicked": [],
        "missing": [],
        "warnings": [],
        "errors": [],
        "final_decision": dict(FINAL_DECISION),
    }


def _blocked_runtime_report(v1_report: dict[str, Any]) -> dict[str, Any]:
    danger_check = v1_report.get("danger_check") or {}
    return {
        "mode": "real_site_assisted_fill",
        "status": BLOCKED,
        "item": v1_report.get("item"),
        "actions_executed": [],
        "danger_buttons_detected": list(danger_check.get("dangerous_buttons_detected", [])),
        "danger_buttons_clicked": [],
        "queue_status": None,
        "final_decision": dict(FINAL_DECISION),
        "missing": list(v1_report.get("missing", [])),
        "warnings": list(v1_report.get("warnings", [])),
        "errors": list(v1_report.get("errors", [])),
    }


def _risk_locked_runtime_report() -> dict[str, Any]:
    return {
        "mode": "real_site_assisted_fill",
        "status": BLOCKED,
        "item": None,
        "actions_executed": [],
        "danger_buttons_detected": [],
        "danger_buttons_clicked": [],
        "queue_status": None,
        "final_decision": dict(FINAL_DECISION),
        "missing": [],
        "warnings": [],
        "errors": [RISK_LOCK_MESSAGE],
    }


def _execution_action(planned_action: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    if planned_action.get("type") == "SELECT_NUMBER":
        number = str(planned_action.get("number") or "")
        candidate = _first_candidate(_number_candidates(selector_report).get(number))
        return {
            "type": "SELECT_NUMBER",
            "number": number,
            "selector": planned_action.get("selector") or _best_selector(candidate),
            "frame": planned_action.get("frame") or _frame(candidate),
            "candidate": candidate,
        }

    if planned_action.get("type") == "SET_AMOUNT":
        star = str(planned_action.get("star") or "")
        candidate = _first_candidate(_amount_candidates(selector_report).get(star))
        return {
            "type": "SET_AMOUNT",
            "star": star,
            "amount": planned_action.get("amount"),
            "selector": planned_action.get("selector") or _best_selector(candidate),
            "frame": planned_action.get("frame") or _frame(candidate),
            "candidate": candidate,
        }

    return {"type": planned_action.get("type"), "candidate": {}}


def _number_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("number_candidates") or selector_report.get("number_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _amount_candidates(selector_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidates = selector_report.get("amount_field_candidates") or selector_report.get("amount_field_selectors") or {}
    return candidates if isinstance(candidates, dict) else {}


def _first_candidate(candidates: Any) -> dict[str, Any]:
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        return first if isinstance(first, dict) else {}
    return {}


def _danger_buttons(plan_report: dict[str, Any]) -> list[str]:
    danger_check = plan_report.get("danger_check") or {}
    buttons = danger_check.get("danger_buttons") or []
    return [str(button) for button in buttons]


def _best_selector(candidate: dict[str, Any]) -> str:
    selectors = candidate.get("candidate_selectors")
    if isinstance(selectors, list) and selectors:
        return str(selectors[0])
    for key in ("selector", "text", "value", "id", "name"):
        if candidate.get(key):
            return str(candidate[key])
    return ""


def _frame(candidate: dict[str, Any]) -> str:
    return str(candidate.get("frame_name") or candidate.get("frame_url") or "")


def _metadata_is_safe(metadata: dict[str, Any]) -> bool:
    fields = (
        "text",
        "innerText",
        "textContent",
        "value",
        "id",
        "name",
        "className",
        "onclick",
        "title",
        "alt",
        "aria-label",
        "ariaLabel",
        "outerHTML",
    )
    values = [str(metadata.get(field) or "") for field in fields]
    selectors = metadata.get("candidate_selectors") or []
    values.extend(str(selector) for selector in selectors)
    return not any(_contains_danger_text(value) for value in values)


def _targets_groupset_value(selector: str, candidate: dict[str, Any]) -> bool:
    """Explicit, independent reject for GroupSet_Value (a 分組序號 field, not an
    amount field). Defense-in-depth: does not rely on upstream mapping having
    already excluded it -- checks the literal selector text, candidate id, and
    any candidate_selectors regardless of danger-word content.
    """
    if "GroupSet_Value" in str(selector or ""):
        return True
    if str(candidate.get("id") or "") == "GroupSet_Value":
        return True
    selectors = candidate.get("candidate_selectors") or []
    return any("GroupSet_Value" in str(item) for item in selectors)


def _contains_danger_text(text: Any) -> bool:
    value = str(text or "")
    compact = "".join(value.split())
    lowered = compact.lower()
    return is_dangerous_action(value) or any(word in lowered for word in ENGLISH_DANGER_WORDS)


def _locator_for_action(page: Any, action: dict[str, Any]) -> Any:
    context = _resolve_frame(page, action)
    return _resolve_first_locator(context.locator(str(action["selector"])))


def _resolve_first_locator(locator: Any) -> Any:
    """Return the first-match locator across Playwright versions and fakes.

    Real Playwright exposes ``Locator.first`` as a property, while some fakes
    implement it as a callable method. Handle both without crashing.
    """
    first = getattr(locator, "first", None)
    if first is None:
        return locator
    if callable(first):
        return first()
    return first


# --- Frame resolution v3: exact name -> URL/path -> rendered-content markers
# -> Shared/Index DOM signature ---
#
# Frame Fix v1 threaded a discovered ``frame`` name into every execution
# action, but a real Playwright page's live frame ``name`` is not guaranteed
# to match what was recorded during selector discovery, or to stay stable
# across page loads. Frame Fix v2 added URL/path and rendered-marker
# fallbacks. Live Tiantianle trials then showed a runtime shape neither of
# those cover: the site can expose the bet page through a single, generically
# named ``/Front/Shared/Index`` frame instead of the recorded ``/Front/B/B03``
# route. ``/Front/Shared/Index`` is used by many pages on this site, so its
# URL alone is never trusted -- it is only accepted when the frame
# independently proves it is the real bet page via rendered text markers
# *and* a live re-verification of the exact 3-input PengBet.Value row the
# offline mapping already required. This resolver never blindly falls back
# to the top-level page and never picks a frame merely because it happens to
# contain the target selector. Any ambiguity (more than one candidate frame
# at a given step) is BLOCKED, not guessed.

BETTING_FRAME_GAME_TEXT_MARKERS = ("539 - 下注資訊", "天天樂 - 下注資訊")
BETTING_FRAME_STAR_MARKERS = ("二星", "三星", "四星")
BETTING_FRAME_COMBO_MARKER = "連碰"
NUMBER_BOARD_TOKEN_PATTERN = re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[0-9])(?!\d)")
MIN_NUMBER_BOARD_TOKENS = 10

SHARED_INDEX_URL_MARKER = "/Front/Shared/Index"
AMOUNT_FIELD_QUERY_SELECTOR = f'input[data-bind*="{AMOUNT_INPUT_DATA_BIND_MARKER}"]'


def _resolve_frame(page: Any, action: dict[str, Any]) -> Any:
    frame_name_ref = str(action.get("frame") or "")
    frame_url_ref = str(action.get("frame_url") or "")

    if not frame_name_ref and not frame_url_ref:
        return page

    frames = list(getattr(page, "frames", []) or [])

    # 1) Exact frame-name match, tried first.
    name_matches = [frame for frame in frames if frame_name_ref and _frame_attr(frame, "name") == frame_name_ref]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise RuntimeError(f"ambiguous frame match for name '{frame_name_ref}': {len(name_matches)} frames matched")

    # 2) Frame URL / path metadata (e.g. the B03 route), if the exact name
    # wasn't found or the runtime name is unstable.
    if frame_url_ref:
        url_matches = [frame for frame in frames if _frame_url_matches(frame, frame_url_ref)]
        if len(url_matches) == 1:
            return url_matches[0]
        if len(url_matches) > 1:
            raise RuntimeError(
                f"ambiguous frame match for url '{frame_url_ref}': {len(url_matches)} frames matched"
            )

    # 3) Rendered-content markers unique to the real betting page (539 -
    # 下注資訊 or 天天樂 - 下注資訊 / 二三四星 / 連碰 / a genuine 01~39 number
    # board -- both games share the same page template). A frame that merely
    # happens to contain the target selector text is never enough on its own
    # -- the frame must independently look like the real betting page before
    # we trust it. Frames whose URL contains the generic /Front/Shared/Index
    # path are excluded here on purpose -- that path is used by many pages on
    # this site, so text markers alone are not enough for it; those frames
    # are only trusted via step 4's stricter combined check below.
    content_matches = [
        frame
        for frame in frames
        if SHARED_INDEX_URL_MARKER not in _frame_attr(frame, "url")
        and _frame_looks_like_betting_frame(_frame_rendered_text(frame))
    ]
    if len(content_matches) == 1:
        return content_matches[0]
    if len(content_matches) > 1:
        raise RuntimeError("ambiguous frame match: multiple frames render betting-page markers")

    # 4) Shared/Index DOM-signature fallback: some live sessions only expose
    # the bet page through a generically-named /Front/Shared/Index frame,
    # never recorded as the mapping's frame_url (which points at
    # /Front/B/B03). Because /Front/Shared/Index is not unique to the bet
    # page, URL containment alone is never enough here -- the frame must
    # also independently prove it via rendered text markers *and* a live
    # re-check of the exact 3-input PengBet.Value row structure.
    shared_index_matches = [
        frame
        for frame in frames
        if SHARED_INDEX_URL_MARKER in _frame_attr(frame, "url")
        and _frame_has_strong_shared_index_signature(frame)
    ]
    if len(shared_index_matches) == 1:
        return shared_index_matches[0]
    if len(shared_index_matches) > 1:
        raise RuntimeError(
            "ambiguous frame match: multiple Shared/Index frames show betting-page DOM signature"
        )

    raise RuntimeError(_frame_not_found_message(frames, frame_name_ref, frame_url_ref))


def _frame_has_strong_shared_index_signature(frame: Any) -> bool:
    """True only when a /Front/Shared/Index frame independently proves it is
    the real bet page: genuine rendered text markers (game name, all three
    star labels, 連碰, a real 01~39 number board) *and* a live DOM
    re-verification of exactly three visible PengBet.Value amount inputs in
    one row -- the same structural fact the offline mapping already
    required. Read-only: only counts/measures already-rendered elements,
    never clicks or fills anything.
    """
    if not _frame_looks_like_betting_frame(_frame_rendered_text(frame)):
        return False
    return _frame_has_verified_amount_triple(frame)


def _frame_has_verified_amount_triple(frame: Any) -> bool:
    try:
        locator = frame.locator(AMOUNT_FIELD_QUERY_SELECTOR)
        count = locator.count()
    except Exception:
        return False
    if count != REQUIRED_AMOUNT_FIELD_COUNT:
        return False

    boxes: list[dict[str, Any]] = []
    for index in range(count):
        element = locator.nth(index)
        try:
            element_id = element.evaluate("el => el.id || ''")
        except Exception:
            return False
        # Defense-in-depth: even though #GroupSet_Value has no PengBet.Value
        # data-bind attribute and should never match this query, an explicit
        # id-based reject keeps this consistent with every other layer that
        # excludes it (mapping, preflight, execution-action validation).
        if str(element_id or "") == "GroupSet_Value":
            return False
        try:
            box = element.bounding_box()
        except Exception:
            return False
        if not box:
            return False
        boxes.append(box)

    tops = [box.get("y") for box in boxes]
    if any(top is None for top in tops):
        return False
    reference_top = tops[0]
    if any(abs(top - reference_top) > AMOUNT_ROW_TOP_TOLERANCE for top in tops):
        return False

    lefts = [box.get("x") for box in boxes]
    if any(left is None for left in lefts):
        return False
    if len(set(lefts)) != REQUIRED_AMOUNT_FIELD_COUNT:
        return False

    return True


def _frame_not_found_message(frames: list[Any], frame_name_ref: str, frame_url_ref: str) -> str:
    """A diagnostic message that names exactly which fallback stages ran.

    A bare "frame not found: mainFrame" cannot tell a future investigation
    whether frame_url was even present, whether the URL fallback matched
    zero frames, or whether rendered-content markers were tried at all.
    Bounded (frame count + short name/url list only) -- never dumps page
    text or outerHTML.
    """
    seen = [f"{_frame_attr(frame, 'name') or '(unnamed)'}|{_frame_attr(frame, 'url')}" for frame in frames][:10]
    return (
        f"frame not found: name_ref={frame_name_ref or '(none)'}, "
        f"url_ref={frame_url_ref or '(none)'}, "
        f"url_fallback_attempted={bool(frame_url_ref)}, "
        f"frames_seen={len(frames)} {seen}"
    )


def _frame_url_matches(frame: Any, frame_url_ref: str) -> bool:
    frame_url = _frame_attr(frame, "url")
    if not frame_url:
        return False
    frame_url_ref_lower = frame_url_ref.lower()
    frame_url_lower = frame_url.lower()
    if B03_FRAME_MARKER.lower() in frame_url_ref_lower and B03_FRAME_MARKER.lower() in frame_url_lower:
        return True
    return frame_url_ref_lower in frame_url_lower or frame_url_lower in frame_url_ref_lower


def _frame_looks_like_betting_frame(text: str) -> bool:
    """True only for a frame whose rendered text is a genuine 二三四星 bet
    page -- for *any* supported game (539 or 天天樂 share the same page
    template). Requires one game-name marker, all three star labels, the
    連碰 marker, and a real 01~39 number board, never just one weak signal.
    """
    if not text:
        return False
    if not any(marker in text for marker in BETTING_FRAME_GAME_TEXT_MARKERS):
        return False
    if not all(marker in text for marker in BETTING_FRAME_STAR_MARKERS):
        return False
    if BETTING_FRAME_COMBO_MARKER not in text:
        return False
    number_tokens = set(NUMBER_BOARD_TOKEN_PATTERN.findall(text))
    return len(number_tokens) >= MIN_NUMBER_BOARD_TOKENS


def _frame_rendered_text(frame: Any) -> str:
    """Read-only rendered text for a frame, never clicking/filling anything.

    Prefers an explicit ``rendered_text`` attribute (used by fakes in tests);
    falls back to evaluating ``document.body`` the same read-only way
    ``_read_element_safety_info`` already does for elements. Any failure
    yields an empty string, which never matches ``_frame_looks_like_betting_frame``.
    """
    rendered_text = getattr(frame, "rendered_text", None)
    if rendered_text is not None:
        return str(rendered_text() if callable(rendered_text) else rendered_text)
    try:
        text = frame.locator("body").evaluate("el => el.innerText || el.textContent || ''")
    except Exception:
        return ""
    return str(text or "")


def _frame_attr(frame: Any, attr: str) -> str:
    value = getattr(frame, attr, "")
    if callable(value):
        value = value()
    return str(value or "")


def _read_element_safety_info(locator: Any) -> dict[str, Any]:
    try:
        data = locator.evaluate(
            """el => ({
                text: el.textContent || "",
                innerText: el.innerText || "",
                value: el.value || "",
                id: el.id || "",
                name: el.name || "",
                className: el.className || "",
                title: el.title || "",
                alt: el.alt || "",
                ariaLabel: el.getAttribute("aria-label") || "",
                onclick: el.getAttribute("onclick") || "",
                outerHTML: (el.outerHTML || "").slice(0, 500)
            })"""
        )
    except Exception as exc:  # pragma: no cover - browser dependent
        raise RuntimeError(f"unable to verify element safety before operation: {exc}") from exc
    return data if isinstance(data, dict) else {}
