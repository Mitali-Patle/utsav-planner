"""The home page: the new-plan form, its samples and the celebration photos."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.domain.catalog import CATALOG
from app.domain.models import EventType
from app.web.views.formatting import EVENT_TYPE_HINTS, EVENT_TYPE_LABELS
from app.web.views.forms import FormState, error_links


@dataclass(frozen=True)
class Example:
    """A ready-made event that fills the new-event form, so a first visit takes one click."""

    label: str
    days_ahead: int
    values: Mapping[str, Any]


EXAMPLES: Mapping[str, Example] = {
    "wedding": Example(
        "Wedding",
        120,
        {
            "event_type": "wedding",
            "title": "Priya and Arjun's wedding",
            "city": "Jaipur",
            "guest_count": "250",
            "total_budget_inr": "1500000",
            "preferences": "Rajasthani royal theme, vegetarian food, marigold decor and a "
            "sangeet with live dhol.",
            "sub_events": ["haldi", "mehendi", "sangeet", "ceremony", "reception"],
        },
    ),
    "birthday": Example(
        "Birthday",
        30,
        {
            "event_type": "birthday",
            "title": "Aarav's 8th birthday",
            "city": "Pune",
            "guest_count": "30",
            "total_budget_inr": "15000",
            "preferences": "Space theme, he loves rockets. At home, no plastic decorations.",
            "sub_events": ["kids_games"],
        },
    ),
    "community": Example(
        "Community event",
        60,
        {
            "event_type": "community",
            "title": "Shanti Nagar Ganeshotsav",
            "city": "Mumbai",
            "guest_count": "400",
            "total_budget_inr": "150000",
            "preferences": "Eco-friendly clay idol, no plastic, activities for elders and "
            "children. Funded by 120 households.",
            "sub_events": ["puja", "cultural_program", "community_meal", "procession"],
        },
    ),
}


def example_form(name: str | None, today: date) -> FormState:
    """The new-event form filled with an example, or empty for an unknown name.

    Examples:
        >>> example_form("birthday", date(2026, 10, 22)).values["event_date"]
        '2026-11-21'
        >>> example_form("unknown", date(2026, 10, 22)).values
        {}
    """
    example = EXAMPLES.get(name or "")
    if example is None:
        return FormState()
    event_date = (today + timedelta(days=example.days_ahead)).isoformat()
    return FormState(values={**example.values, "event_date": event_date})


@dataclass(frozen=True)
class Slide:
    """One photo in the home page's moving panel, described for screen readers."""

    image: str
    alt: str


CELEBRATION_SLIDES: tuple[Slide, ...] = (
    Slide("diwali-diyas.jpg", "Clay diyas burning in a row at night for Diwali."),
    Slide(
        "birthday-party.jpg",
        "A birthday cake painted in yellow and blue icing, candles lit, among yellow balloons.",
    ),
    Slide(
        "ganeshotsav-pandal.jpg",
        "A Ganesh idol in a saffron and white pandal while a devotee offers aarti.",
    ),
)
"""Photos from Pexels, in public/static/img/celebrations/ (800 x 600 JPEG, under 500 KB)."""


def landing_context(form: FormState, today: date) -> dict[str, Any]:
    """Data for the landing page: the create form and the celebration panel."""
    return {
        "form": form,
        "error_links": error_links(form.errors),
        "today": today.isoformat(),
        "examples": [(name, example.label) for name, example in EXAMPLES.items()],
        "event_types": [(t.value, EVENT_TYPE_LABELS[t], EVENT_TYPE_HINTS[t]) for t in EventType],
        "sub_events": [
            {
                "event_type": t.value,
                "label": EVENT_TYPE_LABELS[t],
                "options": [(s.id, s.name) for s in CATALOG[t].sub_events],
            }
            for t in EventType
        ],
        "slides": CELEBRATION_SLIDES,
    }
