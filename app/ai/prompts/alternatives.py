"""Prompt for `alternatives`: ways to close a budget shortfall, each with a reason."""

from typing import Any

from app.ai.contexts import AlternativesContext
from app.ai.guardrails import plan_snapshot
from app.ai.prompts import GUARDRAILS
from app.ai.schemas import AIAlternative, AlternativeAction, Alternatives, SavingLevel

VERSION = "alternatives-v1"

SYSTEM = f"""\
A plan needs more money than its budget. Suggest 2 to 4 realistic ways to close the gap,
the kind an experienced Indian event planner would offer.
Each item names one unprotected category with money in it (category_id), an action
(reduce, replace or remove), how much it would free up (small, medium or large), a short
title and why it suits this event. Prefer swaps that keep the event special over simply
cutting. Never touch protected categories. Do not state amounts; the app calculates the
saving for each option. summary is one sentence on the overall approach.

{GUARDRAILS}"""


def build_payload(context: AlternativesContext) -> dict[str, Any]:
    """Build the JSON request for alternative suggestions."""
    return {
        "task": "alternatives",
        "shortfall_inr": context.shortfall_inr,
        "plan": plan_snapshot(context.plan),
    }


_EXAMPLE_PAYLOAD: dict[str, Any] = {
    "task": "alternatives",
    "shortfall_inr": 4000,
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
        "expenses": [],
    },
}

_EXAMPLE_ANSWER = Alternatives(
    summary="Swap premium choices for local ones and trim extras before touching essentials.",
    items=[
        AIAlternative(
            category_id="decor",
            action=AlternativeAction.REPLACE,
            level=SavingLevel.MEDIUM,
            title="Seasonal marigold and mogra instead of imported flowers",
            why="January is peak season for local flowers, so they look fresh and cost less.",
        ),
        AIAlternative(
            category_id="photography",
            action=AlternativeAction.REDUCE,
            level=SavingLevel.SMALL,
            title="Drop the drone and second videographer",
            why="A single experienced team still covers every ritual for 150 guests.",
        ),
        AIAlternative(
            category_id="transport",
            action=AlternativeAction.REDUCE,
            level=SavingLevel.MEDIUM,
            title="Shared shuttles at fixed times",
            why="Scheduled shuttles replace individual cabs for out-of-town guests.",
        ),
    ],
)

FEW_SHOTS: tuple[tuple[dict[str, Any], Alternatives], ...] = ((_EXAMPLE_PAYLOAD, _EXAMPLE_ANSWER),)
