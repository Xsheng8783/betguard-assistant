"""Representative immutable Human Confirmed operations for Task B."""

from __future__ import annotations

from copy import deepcopy


def _operation(
    index: int,
    human_id: str,
    bet_type: str,
    groups: list[list[str]],
    *,
    rules: list[str] | None = None,
    continuation: bool = False,
    special: dict | None = None,
) -> dict:
    return {
        "operation_index": index,
        "human_bet_id": human_id,
        "bet_type": bet_type,
        "number_groups": deepcopy(groups),
        "multiplier": {
            "ordered_rules": deepcopy(rules or ["2X1"]),
            "scope": "bet",
            "resolved": True,
        },
        "continuation": {
            "present": continuation,
            "resolved": True,
            "binding": "within_human_bet",
        },
        "special_play": deepcopy(special or {
            "kind": "none", "raw_text": None, "scope": None, "resolved": True,
        }),
        "cancelled": False,
        "active": True,
        "value_authority": "validated_candidate",
    }


SAMPLE_OPERATIONS = {
    "sample-007": [_operation(
        1, "sample-007-H1", "column", [["03"], ["16"]], rules=["2/3X1"],
        special={"kind": "tail", "raw_text": "9tail", "scope": "bet", "resolved": True},
    )],
    "sample-008": [_operation(1, "sample-008-H1", "normal", [["08", "04", "26", "09"]])],
    "sample-010": [_operation(
        1, "sample-010-H1", "normal", [["01", "02"]], rules=["2X2", "3X5"],
        special={"kind": "each", "raw_text": "each", "scope": "bet", "resolved": True},
    )],
    "sample-011": [_operation(
        1, "sample-011-H1", "column", [["30"], ["35", "36", "38"]],
        rules=["3/4X1"], continuation=True,
    )],
    "sample-014": [_operation(
        1, "sample-014-H1", "column", [["03"], ["08", "09"]],
        rules=["2X1", "3X1"], continuation=True,
    )],
}


SAMPLE_CANCELLED = {
    "sample-007": [{"human_bet_id": "sample-007-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-008": [{"human_bet_id": "sample-008-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-010": [{"human_bet_id": "sample-010-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-011": [{"human_bet_id": "sample-011-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-014": [{"human_bet_id": "sample-014-C1", "excluded_reason": "cancelled_non_executable"}],
}


def sample_case(name: str) -> tuple[list[dict], list[dict]]:
    return deepcopy(SAMPLE_OPERATIONS[name]), deepcopy(SAMPLE_CANCELLED[name])
