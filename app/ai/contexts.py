"""Inputs for each AI task."""

from dataclasses import dataclass
from datetime import date

from app.domain.models import EventPlan

DEFAULT_TONE = "warm, practical and concise, like an experienced family event planner"


@dataclass(frozen=True)
class PersonalizeContext:
    """A freshly drafted plan to personalize."""

    plan: EventPlan
    today: date
    tone: str = DEFAULT_TONE


@dataclass(frozen=True)
class ChangeContext:
    """A natural-language change request against a saved plan."""

    plan: EventPlan
    request: str
    today: date


@dataclass(frozen=True)
class AlternativesContext:
    """A plan that is short of budget by `shortfall_inr`."""

    plan: EventPlan
    shortfall_inr: int
