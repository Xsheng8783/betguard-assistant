from __future__ import annotations


def expand_tail(tail: int, *, min_number: int = 1, max_number: int = 39) -> list[int]:
    if tail < 0 or tail > 9:
        raise ValueError("tail must be between 0 and 9")
    return [
        number
        for number in range(min_number, max_number + 1)
        if number % 10 == tail
    ]
