from betguard.review import build_report
from betguard.webfill.fill_plan import NUMBERS_ONLY_MODE, build_fill_plan, to_numbers_only_plan


def normal_fill_plan() -> dict:
    return build_fill_plan(build_report("06-13-23-22/50").to_dict())


def test_numbers_only_transform_removes_set_amount_from_planned_steps() -> None:
    plan = normal_fill_plan()

    numbers_only = to_numbers_only_plan(plan)

    assert all(step.get("type") != "set_amount" for step in numbers_only["planned_steps"])
    assert any(step.get("type") == "select_number" for step in numbers_only["planned_steps"])


def test_numbers_only_transform_preserves_removed_amount_steps() -> None:
    plan = normal_fill_plan()
    original_amount_steps = [step for step in plan["planned_steps"] if step.get("type") == "set_amount"]

    numbers_only = to_numbers_only_plan(plan)

    assert numbers_only["amount_steps_removed"] == original_amount_steps
    assert len(numbers_only["amount_steps_removed"]) == 3  # 二星/三星/四星


def test_numbers_only_transform_sets_amount_manual_required() -> None:
    plan = normal_fill_plan()

    numbers_only = to_numbers_only_plan(plan)

    assert numbers_only["amount_manual_required"] is True


def test_numbers_only_transform_keeps_executable_false() -> None:
    plan = normal_fill_plan()

    numbers_only = to_numbers_only_plan(plan)

    assert numbers_only["executable"] is False


def test_numbers_only_transform_sets_mode() -> None:
    plan = normal_fill_plan()

    numbers_only = to_numbers_only_plan(plan)

    assert numbers_only["mode"] == NUMBERS_ONLY_MODE


def test_numbers_only_transform_is_idempotent() -> None:
    plan = normal_fill_plan()

    once = to_numbers_only_plan(plan)
    twice = to_numbers_only_plan(once)

    assert once == twice


def test_numbers_only_transform_does_not_mutate_input_plan() -> None:
    plan = normal_fill_plan()
    original_planned_steps = [dict(step) for step in plan["planned_steps"]]

    to_numbers_only_plan(plan)

    assert plan["planned_steps"] == original_planned_steps
    assert "amount_manual_required" not in plan


def test_numbers_only_transform_on_plan_with_no_amount_steps() -> None:
    plan = normal_fill_plan()
    plan["planned_steps"] = [step for step in plan["planned_steps"] if step.get("type") != "set_amount"]

    numbers_only = to_numbers_only_plan(plan)

    assert numbers_only["amount_steps_removed"] == []
    assert numbers_only["amount_manual_required"] is True
