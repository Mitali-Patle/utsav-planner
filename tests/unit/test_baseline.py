"""Opening budget: exact totals, and the split changes with the user's context."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.baseline import baseline_categories, guest_multiplier_percent
from app.domain.catalog import spec_for
from app.domain.errors import InvalidInputError
from app.domain.models import MAX_BUDGET_INR, MAX_GUESTS, MIN_BUDGET_INR, EventType


def share(event_type: EventType, sub_events: list[str], guests: int, category: str) -> float:
    categories = baseline_categories(event_type, sub_events, guests, 1_000_000)
    return next(c.allocated_inr for c in categories if c.id == category) / 1_000_000


def test_wedding_split_is_within_published_ranges() -> None:
    venue = share(EventType.WEDDING, [], 150, "venue")
    catering = share(EventType.WEDDING, [], 150, "catering")
    decor = share(EventType.WEDDING, [], 150, "decor")
    assert 0.20 <= venue <= 0.30
    assert 0.20 <= catering <= 0.30
    assert 0.10 <= decor <= 0.16


def test_sub_events_shift_the_budget() -> None:
    assert share(EventType.WEDDING, ["sangeet"], 150, "music") > share(
        EventType.WEDDING, [], 150, "music"
    )
    assert share(EventType.COMMUNITY, ["community_meal"], 150, "prasad_catering") > share(
        EventType.COMMUNITY, [], 150, "prasad_catering"
    )


def test_more_guests_put_more_into_per_guest_costs() -> None:
    small = share(EventType.BIRTHDAY, [], 20, "catering")
    large = share(EventType.BIRTHDAY, [], 800, "catering")
    assert large > small


def test_event_types_get_different_categories() -> None:
    ids = {t: {c.id for c in baseline_categories(t, [], 50, 100_000)} for t in EventType}
    assert "cake" in ids[EventType.BIRTHDAY]
    assert "permits" in ids[EventType.COMMUNITY]
    assert "rituals" in ids[EventType.WEDDING]
    assert len({frozenset(v) for v in ids.values()}) == 3


@pytest.mark.parametrize(
    ("guests", "percent"),
    [
        (1, 90),
        (50, 90),
        (51, 100),
        (200, 100),
        (201, 110),
        (500, 110),
        (501, 120),
        (MAX_GUESTS, 120),
    ],
)
def test_guest_bands(guests: int, percent: int) -> None:
    assert guest_multiplier_percent(guests) == percent


@pytest.mark.parametrize("guests", [0, MAX_GUESTS + 1])
def test_guest_band_rejects_out_of_range(guests: int) -> None:
    with pytest.raises(ValueError, match="guest_count"):
        guest_multiplier_percent(guests)


def test_baseline_rejects_bad_input() -> None:
    with pytest.raises(InvalidInputError):
        baseline_categories(EventType.BIRTHDAY, [], 30, MIN_BUDGET_INR - 1)
    with pytest.raises(InvalidInputError):
        baseline_categories(EventType.BIRTHDAY, ["sangeet"], 30, 20_000)


@given(
    event_type=st.sampled_from(list(EventType)),
    data=st.data(),
    guests=st.integers(1, MAX_GUESTS),
    total=st.integers(MIN_BUDGET_INR, MAX_BUDGET_INR),
)
def test_baseline_always_sums_exactly(
    event_type: EventType, data: st.DataObject, guests: int, total: int
) -> None:
    spec = spec_for(event_type)
    sub_events = data.draw(st.lists(st.sampled_from([s.id for s in spec.sub_events]), max_size=5))
    categories = baseline_categories(event_type, sub_events, guests, total)
    assert sum(c.allocated_inr for c in categories) == total
    assert [c.id for c in categories] == [c.id for c in spec.categories]
    assert all(c.allocated_inr >= 0 for c in categories)
