"""Prompt for `alternatives`: ways to close a budget shortfall, each with a reason."""

from typing import Any

from app.ai.contexts import AlternativesContext
from app.ai.guardrails import plan_snapshot, quote
from app.ai.prompts import GUARDRAILS
from app.ai.schemas import AIAlternative, Alternatives
from app.domain.catalog import ESSENTIAL_CATEGORIES
from app.domain.models import EventPlan
from app.domain.savings import SavingAction, SavingLevel, freed_inr

VERSION = "alternatives-v6"

SYSTEM = f"""\
A plan needs more money than its budget. Suggest 2 to 4 realistic ways to close the gap,
the kind an experienced Indian event planner would offer.
Each item names one category from frees_inr (category_id), an action (reduce, replace or
remove), a level (small, medium or large), a short title and why it suits this event.
Prefer swaps that keep the event special over simply cutting. Do not state amounts.
frees_inr lists the only categories you may choose: unprotected lines with money not yet
spent. For each it gives the rupees the app would free: small, medium and large are what
reduce or replace frees at that level, and remove frees everything not yet spent. Lines
a celebration cannot do without (food, venue, cake, rituals, permits) have no remove amount:
never suggest removing them.
Choose options whose frees_inr amounts together reach shortfall_inr, using the gentlest
levels that do. Always offer at least 2 options so the user has a choice, even when one
alone would close the gap. If even every remove amount together cannot reach it, offer the biggest
sensible savings and say plainly in summary that the gap cannot be fully closed this way.
summary is one sentence on the overall approach.

{GUARDRAILS}"""


def build_payload(context: AlternativesContext) -> dict[str, Any]:
    """Build the JSON request for alternative suggestions."""
    return {
        "task": "alternatives",
        "shortfall_inr": context.shortfall_inr,
        "frees_inr": saving_table(context.plan),
        "plan": plan_snapshot(context.plan),
    }


def saving_table(plan: EventPlan) -> dict[str, dict[str, int]]:
    """Rupees each option would free, per line that has anything to free."""
    table: dict[str, dict[str, int]] = {}
    for category in plan.categories:
        options = {
            level.value: freed_inr(plan, category.id, SavingAction.REDUCE, level)
            for level in SavingLevel
        }
        if category.id not in ESSENTIAL_CATEGORIES:
            options["remove"] = freed_inr(plan, category.id, SavingAction.REMOVE, SavingLevel.LARGE)
        if any(options.values()):
            table[category.id] = options
    return table


_EXAMPLE_PAYLOAD: dict[str, Any] = {
    "task": "alternatives",
    "shortfall_inr": 30000,
    "frees_inr": {
        "decor": {"small": 8000, "medium": 20000, "large": 40000, "remove": 80000},
        "photography": {"small": 6000, "medium": 15000, "large": 30000, "remove": 60000},
        "transport": {"small": 2000, "medium": 5000, "large": 10000, "remove": 20000},
    },
    "plan": {
        "event_type": "wedding",
        "event_date": "2027-01-20",
        "guest_count": 150,
        "total_budget_inr": 600000,
        "categories": [
            {"id": "catering", "name": "Catering", "allocated_inr": 180000, "protected": True},
            {
                "id": "decor",
                "name": "Decor and flowers",
                "allocated_inr": 80000,
                "protected": False,
            },
            {
                "id": "photography",
                "name": "Photography and video",
                "allocated_inr": 60000,
                "protected": False,
            },
            {
                "id": "transport",
                "name": "Guest transport",
                "allocated_inr": 20000,
                "protected": False,
            },
        ],
        "expenses": [
            {
                "id": "exp-1",
                "category_id": "catering",
                "label": quote("Caterer advance"),
                "amount_inr": 210000,
                "protected": True,
            }
        ],
    },
}

_EXAMPLE_ANSWER = Alternatives(
    summary="Swap premium choices for local ones and trim extras before touching essentials.",
    items=[
        AIAlternative(
            category_id="decor",
            action=SavingAction.REPLACE,
            level=SavingLevel.MEDIUM,
            title="Seasonal marigold and mogra instead of imported flowers",
            why="January is peak season for local flowers, so they look fresh and cost less.",
        ),
        AIAlternative(
            category_id="photography",
            action=SavingAction.REDUCE,
            level=SavingLevel.SMALL,
            title="Drop the drone and second videographer",
            why="A single experienced team still covers every ritual for 150 guests.",
        ),
        AIAlternative(
            category_id="transport",
            action=SavingAction.REDUCE,
            level=SavingLevel.MEDIUM,
            title="Shared shuttles at fixed times",
            why="Scheduled shuttles replace individual cabs for out-of-town guests.",
        ),
    ],
)

FEW_SHOTS: tuple[tuple[dict[str, Any], Alternatives], ...] = ((_EXAMPLE_PAYLOAD, _EXAMPLE_ANSWER),)
