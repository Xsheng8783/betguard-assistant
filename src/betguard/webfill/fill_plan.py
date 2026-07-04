from __future__ import annotations

from copy import deepcopy
from typing import Any


FORBIDDEN_STEPS = [
    "submit",
    "confirm",
    "send_bet",
    "click_danger_button",
]
STAR_LABELS = {
    2: "二星",
    3: "三星",
    4: "四星",
}

NUMBERS_ONLY_MODE = "assisted_fill_plan_numbers_only_v0"


def build_fill_plan(review_result: dict[str, Any]) -> dict[str, Any]:
    if "items" in review_result:
        return _build_batch_fill_plan(review_result)
    return _build_single_fill_plan(review_result)


def _base_plan(*, game: str | None = None, bet_type: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "assisted_fill_plan_v0",
        "executable": False,
        "requires_human_review": True,
        "forbidden_steps": list(FORBIDDEN_STEPS),
        "warnings": [],
        "errors": [],
    }
    if game is not None:
        data["game"] = game
    if bet_type is not None:
        data["bet_type"] = bet_type
    return data


def _build_batch_fill_plan(summary: dict[str, Any]) -> dict[str, Any]:
    plan = _base_plan()
    plan.update(
        {
            "total": summary.get("total", 0),
            "can_continue": bool(summary.get("can_continue")),
            "plans": [],
            "skipped": [],
        }
    )

    if not summary.get("can_continue"):
        plan["errors"].append("review result is not ok")
        return plan

    for item in summary.get("items", []):
        result = item.get("result", {})
        if result.get("status") == "ok" and result.get("type") == "normal":
            item_plan = _build_single_fill_plan(result)
            item_plan["line_no"] = item.get("line_no")
            item_plan["raw"] = item.get("raw")
            plan["plans"].append(item_plan)
        elif result.get("status") == "ok":
            plan["skipped"].append(
                {
                    "line_no": item.get("line_no"),
                    "raw": item.get("raw"),
                    "type": result.get("type"),
                    "reason": _unsupported_reason(result.get("type")),
                }
            )
    return plan


def _build_single_fill_plan(result: dict[str, Any]) -> dict[str, Any]:
    bet_type = str(result.get("type", ""))
    plan = _base_plan(game=result.get("game"), bet_type=bet_type or None)

    if result.get("status") != "ok":
        plan["errors"].append("review result is not ok")
        return plan

    if bet_type == "column":
        plan["errors"].append("unsupported in v0: column fill plan requires selector verification")
        return plan

    if bet_type == "car":
        plan["errors"].append("unsupported in v0: car page has no confirmation, fill plan disabled")
        return plan

    if bet_type != "normal":
        plan["errors"].append(f"unsupported in v0: {bet_type or 'unknown'}")
        return plan

    numbers = [_format_number(number) for number in result.get("numbers", [])]
    stars = [_star_label(star) for star in result.get("stars", [])]
    amounts = _amounts_by_star(result)

    if not numbers:
        plan["errors"].append("missing numbers")
        return plan
    if not stars:
        plan["errors"].append("missing stars")
        return plan
    if any(amount is None for amount in amounts.values()):
        plan["errors"].append("missing amount")
        return plan

    planned_steps: list[dict[str, Any]] = [
        {"type": "select_number", "label": number}
        for number in numbers
    ]
    planned_steps.extend(
        {"type": "set_amount", "star": star, "amount": amount}
        for star, amount in amounts.items()
    )

    plan.update(
        {
            "numbers": numbers,
            "stars": stars,
            "amounts": amounts,
            "planned_steps": planned_steps,
        }
    )
    return plan


def _amounts_by_star(result: dict[str, Any]) -> dict[str, int | None]:
    bets = result.get("bets") or {}
    amounts: dict[str, int | None] = {}
    for raw_star in result.get("stars", []):
        star = int(raw_star)
        label = _star_label(star)
        per_star = bets.get(str(star), {})
        money = per_star.get("money") if isinstance(per_star, dict) else None
        if money is None:
            money = result.get("money")
        amounts[label] = int(money) if money is not None else None
    return amounts


def _format_number(number: Any) -> str:
    return f"{int(number):02d}"


def _star_label(star: Any) -> str:
    return STAR_LABELS.get(int(star), f"{star}星")


def _unsupported_reason(bet_type: Any) -> str:
    if bet_type == "column":
        return "unsupported in v0: column fill plan requires selector verification"
    if bet_type == "car":
        return "unsupported in v0: car page has no confirmation, fill plan disabled"
    return f"unsupported in v0: {bet_type or 'unknown'}"


def to_numbers_only_plan(fill_plan: dict[str, Any]) -> dict[str, Any]:
    """Pure transform: assisted_fill_plan_v0 -> assisted_fill_plan_numbers_only_v0.

    Only ``select_number`` steps stay in ``planned_steps``; any ``set_amount``
    step is moved into ``amount_steps_removed`` and can never re-enter
    ``planned_steps``. Amounts are for human reference only — the caller must
    display them for manual entry, never fill them. Idempotent: applying this
    to an already-numbers-only plan returns an equivalent plan unchanged.
    """
    plan = deepcopy(fill_plan)
    planned_steps = list(plan.get("planned_steps") or [])
    already_removed = list(plan.get("amount_steps_removed") or [])

    kept_steps = [step for step in planned_steps if step.get("type") != "set_amount"]
    newly_removed = [step for step in planned_steps if step.get("type") == "set_amount"]

    plan["mode"] = NUMBERS_ONLY_MODE
    plan["executable"] = False
    plan["planned_steps"] = kept_steps
    plan["amount_steps_removed"] = already_removed + newly_removed
    plan["amount_manual_required"] = True
    return plan
