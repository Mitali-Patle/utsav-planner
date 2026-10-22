"""Opening budget for a new plan, built from the catalog and the user's context.

The split depends on the event type, the chosen sub-events and the guest count, so two
users with different events get different budgets before any AI is involved.
"""

from collections import Counter
from collections.abc import Sequence

from app.domain.budget import allocate, validate_total_budget
from app.domain.catalog import spec_for, validate_sub_events
from app.domain.models import MAX_GUESTS, BudgetCategory, EventType

NEUTRAL_PERCENT = 100

# (largest guest count in the band, weight percent for guest-scaled categories)
GUEST_BANDS: tuple[tuple[int, int], ...] = (
    (50, 90),
    (200, 100),
    (500, 110),
    (MAX_GUESTS, 120),
)


def guest_multiplier_percent(guest_count: int) -> int:
    """Weight percent for guest-scaled categories, by guest-count band.

    Bigger events spend a larger share on per-guest costs such as catering and seating.

    Args:
        guest_count: Number of guests, from 1 to the maximum.

    Returns:
        The weight percent for the guest count's band.

    Raises:
        ValueError: If the guest count is outside 1 to the maximum.

    Examples:
        >>> [guest_multiplier_percent(n) for n in (50, 51, 200, 201, 500, 501)]
        [90, 100, 100, 110, 110, 120]
    """
    if not 1 <= guest_count <= MAX_GUESTS:
        raise ValueError("guest_count is out of range")
    return next(percent for limit, percent in GUEST_BANDS if guest_count <= limit)


def baseline_categories(
    event_type: EventType,
    sub_events: Sequence[str],
    guest_count: int,
    total_inr: int,
) -> tuple[BudgetCategory, ...]:
    """Build and allocate the opening budget categories.

    Weight = catalog weight + bonuses from chosen sub-events, then multiplied by the
    guest-band percent for guest-scaled categories (100 % for the rest).

    Args:
        event_type: The event type.
        sub_events: Chosen sub-event ids.
        guest_count: Number of guests.
        total_inr: Total budget.

    Returns:
        Categories whose allocations sum exactly to the total.

    Raises:
        InvalidInputError: If the total is out of range or a sub-event is unknown.
    """
    validate_total_budget(total_inr)
    spec = spec_for(event_type)
    selected = validate_sub_events(event_type, sub_events)
    bonus: Counter[str] = Counter()
    for sub_event in spec.sub_events:
        if sub_event.id in selected:
            bonus.update({b.category_id: b.weight for b in sub_event.weight_bonus})

    guest_percent = guest_multiplier_percent(guest_count)
    categories = tuple(
        BudgetCategory(
            id=category.id,
            name=category.name,
            weight=(category.weight + bonus[category.id])
            * (guest_percent if category.scales_with_guests else NEUTRAL_PERCENT),
            allocated_inr=0,
            scales_with_guests=category.scales_with_guests,
        )
        for category in spec.categories
    )
    return allocate(categories, total_inr)
