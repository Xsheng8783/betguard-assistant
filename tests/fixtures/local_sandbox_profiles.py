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
    # Full canonical sample-007 value shape: 24 active bets plus the separate
    # cancelled audit record below.  It is test-only and never enters runtime
    # inference or mapping rules.
    "sample-007": [
        _operation(1, "H-001", "column", [["05"], ["08", "09", "23"], ["10", "20", "29"]], rules=["2/3X2"]),
        _operation(2, "H-002", "normal", [["11", "20", "14", "36", "38", "26"]], rules=["2/3/4X0.5"]),
        _operation(3, "H-003", "normal", [["12", "24", "38", "08", "18"]], rules=["2/3/4X0.5"]),
        _operation(4, "H-004", "normal", [["02", "16", "20", "28", "35"]], rules=["2/3/4X0.5"]),
        _operation(5, "H-005", "column", [["36", "38"], ["07", "17"], ["04", "05", "03"]], rules=["2/3X0.5"]),
        _operation(6, "H-006", "column", [["36", "38"], ["07", "17"], ["20", "21", "22"]], rules=["2/3X0.5"]),
        _operation(7, "H-007", "column", [["36", "38"], ["02", "12"], ["08", "18"]], rules=["2/3X0.5"]),
        _operation(8, "H-008", "column", [["38", "36"], ["06", "13"], ["27", "31"]], rules=["2/3X0.5"]),
        _operation(9, "H-009", "column", [["36", "38"], ["06", "13"], ["09", "24"]], rules=["2/3X0.5"]),
        _operation(10, "H-010", "column", [["36", "38"], ["22", "32"], ["27", "11"]], rules=["2/3X0.5"]),
        _operation(11, "H-011", "column", [["36", "38"], ["14", "24"], ["06", "13"]], rules=["2/3X0.5"]),
        _operation(12, "H-012", "normal", [["11", "14", "15", "06", "38"]], rules=["2/3/4X0.5"]),
        _operation(13, "H-013", "column", [["36", "38"], ["07", "17"], ["08", "18"], ["06", "13"]], rules=["2/3/4X0.5"]),
        _operation(14, "H-014", "column", [["04", "09"], ["19", "22"], ["39", "36", "38"]], rules=["2/3X0.5"]),
        _operation(15, "H-016", "column", [["06", "08"], ["04", "15"], ["36", "38"]], rules=["2/3X0.5"]),
        _operation(16, "H-017", "column", [["12", "03"], ["28", "22"], ["39", "31"]], rules=["2/3X0.5"]),
        _operation(17, "H-018", "column", [["12", "08"], ["24", "14"], ["36", "38"]], rules=["2/3X0.5"]),
        _operation(18, "H-019", "column", [["03"], ["16"]], rules=["2/3X1"], continuation=True,
                   special={"kind": "tail", "raw_text": "7尾", "scope": "bet", "resolved": True}),
        _operation(19, "H-020", "column", [["03"], ["16"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "5尾", "scope": "bet", "resolved": True}),
        _operation(20, "H-021", "column", [["03"], ["16"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "9尾", "scope": "bet", "resolved": True}),
        _operation(21, "H-022", "column", [["03"], ["38"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "6尾", "scope": "bet", "resolved": True}),
        _operation(22, "H-023", "column", [["03"], ["16"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "2尾", "scope": "bet", "resolved": True}),
        _operation(23, "H-024", "column", [["03"], ["16"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "4尾", "scope": "bet", "resolved": True}),
        _operation(24, "H-025", "column", [["03"], ["16"]], rules=["2/3X1"],
                   special={"kind": "tail", "raw_text": "8尾", "scope": "bet", "resolved": True}),
    ],
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
    "sample-007": [{"human_bet_id": "H-015", "excluded_reason": "cancelled_non_executable"}],
    "sample-008": [{"human_bet_id": "sample-008-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-010": [{"human_bet_id": "sample-010-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-011": [{"human_bet_id": "sample-011-C1", "excluded_reason": "cancelled_non_executable"}],
    "sample-014": [{"human_bet_id": "sample-014-C1", "excluded_reason": "cancelled_non_executable"}],
}


def sample_case(name: str) -> tuple[list[dict], list[dict]]:
    return deepcopy(SAMPLE_OPERATIONS[name]), deepcopy(SAMPLE_CANCELLED[name])
