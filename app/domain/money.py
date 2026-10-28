"""Money helpers. Amounts are whole rupees (`int`); floating point is never used."""

from collections.abc import Sequence
from fractions import Fraction

LAST_GROUP = 3
"""Indian grouping: the last three digits together, then pairs (1,20,000)."""


def format_inr(amount: int) -> str:
    """Format rupees with Indian digit grouping (lakh and crore).

    Args:
        amount: Whole rupees; may be negative.

    Returns:
        The formatted amount.

    Examples:
        >>> format_inr(120000)
        '₹1,20,000'
        >>> format_inr(100000000)
        '₹10,00,00,000'
        >>> format_inr(999)
        '₹999'
        >>> format_inr(-1500)
        '-₹1,500'
    """
    sign = "-" if amount < 0 else ""
    digits = str(abs(amount))
    if len(digits) <= LAST_GROUP:
        return f"{sign}₹{digits}"
    head, tail = digits[:-LAST_GROUP], digits[-LAST_GROUP:]
    groups: list[str] = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return f"{sign}₹{','.join([*groups, tail])}"


def largest_remainder_split(total: int, weights: Sequence[int]) -> list[int]:
    """Split a whole-rupee total in proportion to weights, summing exactly to the total.

    Each part gets the floor of its exact share; the leftover rupees go to the parts with
    the largest remainders, ties broken by position, so results are deterministic.
    Runs in O(n log n) and uses exact fractions, never floats.

    Args:
        total: Non-negative amount to split.
        weights: Non-negative relative weights with a positive sum.

    Returns:
        Parts in the same order as `weights`.

    Raises:
        ValueError: If the total is negative, a weight is negative or all weights are zero.

    Examples:
        >>> largest_remainder_split(100, [1, 1, 1])
        [34, 33, 33]
        >>> largest_remainder_split(12000, [5, 3, 2])
        [6000, 3600, 2400]
    """
    if total < 0:
        raise ValueError("total must be non-negative")
    if any(weight < 0 for weight in weights):
        raise ValueError("weights must be non-negative")
    weight_sum = sum(weights)
    if weight_sum == 0:
        raise ValueError("weights must have a positive sum")

    shares = [Fraction(total * weight, weight_sum) for weight in weights]
    parts = [share.numerator // share.denominator for share in shares]
    leftover = total - sum(parts)
    by_remainder = sorted(range(len(parts)), key=lambda i: (parts[i] - shares[i], i))
    for index in by_remainder[:leftover]:
        parts[index] += 1
    return parts
