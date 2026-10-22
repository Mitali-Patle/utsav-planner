"""Indian rupee formatting and exact proportional splitting."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import format_inr, largest_remainder_split


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0, "₹0"),
        (5, "₹5"),
        (999, "₹999"),
        (1_000, "₹1,000"),
        (12_345, "₹12,345"),
        (1_00_000, "₹1,00,000"),
        (12_34_567, "₹12,34,567"),
        (10_00_00_000, "₹10,00,00,000"),
        (-999, "-₹999"),
        (-1_500, "-₹1,500"),
    ],
)
def test_format_inr_uses_indian_grouping(amount: int, expected: str) -> None:
    assert format_inr(amount) == expected


@pytest.mark.parametrize(
    ("total", "weights", "expected"),
    [
        (100, [1, 1, 1], [34, 33, 33]),
        (15_000, [5, 3, 2, 5], [5_000, 3_000, 2_000, 5_000]),
        (0, [3, 1], [0, 0]),
        (7, [0, 1], [0, 7]),
        (1, [1, 1, 1], [1, 0, 0]),
    ],
)
def test_split_examples(total: int, weights: list[int], expected: list[int]) -> None:
    assert largest_remainder_split(total, weights) == expected


@pytest.mark.parametrize(
    ("total", "weights", "message"),
    [
        (-1, [1], "total"),
        (10, [1, -1], "non-negative"),
        (10, [0, 0], "positive sum"),
        (10, [], "positive sum"),
    ],
)
def test_split_rejects_invalid_arguments(total: int, weights: list[int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        largest_remainder_split(total, weights)


@given(
    total=st.integers(min_value=0, max_value=10**9),
    weights=st.lists(st.integers(min_value=0, max_value=10**6), min_size=1, max_size=12).filter(
        lambda ws: sum(ws) > 0
    ),
)
def test_split_is_exact_fair_and_deterministic(total: int, weights: list[int]) -> None:
    parts = largest_remainder_split(total, weights)
    assert sum(parts) == total
    for part, weight in zip(parts, weights, strict=True):
        share = Fraction(total * weight, sum(weights))
        assert share - 1 < part < share + 1
        assert part >= 0
    assert largest_remainder_split(total, weights) == parts
