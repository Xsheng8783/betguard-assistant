import pytest

from betguard.expander import expand_tail


def test_expand_tail_zero_for_539_default_range() -> None:
    assert expand_tail(0) == [10, 20, 30]


def test_expand_tail_each_539_tail() -> None:
    assert expand_tail(1) == [1, 11, 21, 31]
    assert expand_tail(2) == [2, 12, 22, 32]
    assert expand_tail(3) == [3, 13, 23, 33]
    assert expand_tail(4) == [4, 14, 24, 34]
    assert expand_tail(5) == [5, 15, 25, 35]
    assert expand_tail(6) == [6, 16, 26, 36]
    assert expand_tail(7) == [7, 17, 27, 37]
    assert expand_tail(8) == [8, 18, 28, 38]
    assert expand_tail(9) == [9, 19, 29, 39]


def test_expand_tail_rejects_invalid_tail() -> None:
    with pytest.raises(ValueError):
        expand_tail(12)
