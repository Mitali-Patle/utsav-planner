"""Prompt for `personalize`: tailor a drafted plan to this specific event."""

from typing import Any

from app.ai.contexts import PersonalizeContext
from app.ai.guardrails import MAX_REQUEST_CHARS, MAX_TITLE_CHARS, quote
from app.ai.prompts import GUARDRAILS
from app.ai.schemas import AIEmphasis, AIIdea, AISuggestedTask, AIVendorNote, PlanSuggestions
from app.domain.budget import EmphasisDirection, EmphasisStrength
from app.domain.models import BudgetImpact

VERSION = "personalize-v2"

SYSTEM = f"""\
You are Utsav, an experienced event planner for Indian families and communities.
You receive a drafted plan: event details, budget categories, standard tasks and vendors.
Personalize it for THIS event using its type, city, month, guest count, budget, chosen
functions and the user's preferences.

Return:
- ideas: 3 to 5 practical, culturally fitting ideas. Mark each as saving money, adding cost
  or neutral, and say why it suits this event.
- budget_emphasis: at most 4 categories that deserve a little more or less than standard,
  with direction (increase or decrease), strength (small or medium) and why.
- extra_tasks: at most 5 tasks the standard list misses for this event, with days before the
  event and why. Do not repeat existing tasks.
- vendor_notes: at most 6 notes on which listed vendors matter most here and what to ask.
- summary_why: one or two sentences on your overall approach.
If previous_ideas is given, the user asked for different ideas: return 3 to 5 new ideas
that clearly differ from every previous one, and leave budget_emphasis, extra_tasks and
vendor_notes empty.
Match the requested tone. If the event details are not about an event, set off_topic to true
and leave the lists empty.

{GUARDRAILS}"""


def build_payload(context: PersonalizeContext) -> dict[str, Any]:
    """Build the JSON request for a personalization call."""
    plan = context.plan
    payload: dict[str, Any] = {
        "task": "personalize",
        "tone": context.tone,
        "event": {
            "type": plan.event_type.value,
            "title": quote(plan.title, 80),
            "city": quote(plan.city, 60),
            "date": plan.event_date.isoformat(),
            "month": plan.event_date.strftime("%B"),
            "days_until_event": (plan.event_date - context.today).days,
            "guests": plan.guest_count,
            "budget_inr": plan.total_budget_inr,
            "functions": list(plan.sub_events),
            "preferences": quote(plan.preferences, MAX_REQUEST_CHARS + 200),
        },
        "categories": [{"id": c.id, "name": c.name} for c in plan.categories],
        "existing_tasks": [t.title for t in plan.tasks],
        "vendors": [{"vendor_type": v.vendor_type, "name": v.name} for v in plan.vendors],
    }
    if context.previous_ideas:
        payload["previous_ideas"] = [quote(t, MAX_TITLE_CHARS) for t in context.previous_ideas]
    return payload


_EXAMPLE_PAYLOAD: dict[str, Any] = {
    "task": "personalize",
    "tone": "warm, practical and concise",
    "event": {
        "type": "birthday",
        "title": "<<<Meera's 6th birthday>>>",
        "city": "<<<Kochi>>>",
        "date": "2026-07-18",
        "month": "July",
        "days_until_event": 35,
        "guests": 25,
        "budget_inr": 20000,
        "functions": ["kids_games"],
        "preferences": "<<<She loves animals. Prefer less plastic.>>>",
    },
    "categories": [
        {"id": "venue", "name": "Venue"},
        {"id": "catering", "name": "Food and drinks"},
        {"id": "decor", "name": "Decorations"},
        {"id": "entertainment", "name": "Entertainment and games"},
        {"id": "return_gifts", "name": "Return gifts"},
    ],
    "existing_tasks": ["Book the venue or prepare the home space", "Order the cake"],
    "vendors": [{"vendor_type": "entertainer", "name": "Entertainer"}],
}

_EXAMPLE_ANSWER = PlanSuggestions(
    summary_why=(
        "An animal theme with reusable decor and rain-proof indoor games for a July party in Kochi."
    ),
    ideas=[
        AIIdea(
            title="Jungle safari theme",
            description="Animal masks, a 'spot the animal' trail and a leafy photo corner.",
            category_id="decor",
            budget_impact=BudgetImpact.NEUTRAL,
            why="She loves animals, and the theme ties games, decor and photos together.",
        ),
        AIIdea(
            title="Seed-ball return gifts",
            description="Each child plants seed balls and takes them home in paper pouches.",
            category_id="return_gifts",
            budget_impact=BudgetImpact.SAVES,
            why="Plastic-free, cheap, and doubles as an activity.",
        ),
        AIIdea(
            title="Indoor backup plan",
            description="Keep games that work indoors in case of rain.",
            category_id="entertainment",
            budget_impact=BudgetImpact.NEUTRAL,
            why="July is monsoon season in Kochi, so outdoor games may be rained out.",
        ),
    ],
    budget_emphasis=[
        AIEmphasis(
            category_id="entertainment",
            direction=EmphasisDirection.INCREASE,
            strength=EmphasisStrength.SMALL,
            why="Games are the main event for six-year-olds.",
        ),
        AIEmphasis(
            category_id="decor",
            direction=EmphasisDirection.DECREASE,
            strength=EmphasisStrength.SMALL,
            why="Reusable, handmade decor costs less than balloons.",
        ),
    ],
    extra_tasks=[
        AISuggestedTask(
            title="Check the monsoon forecast and confirm the indoor space",
            category_id="venue",
            days_before_event=3,
            why="Heavy rain is common in Kochi in July.",
        )
    ],
    vendor_notes=[
        AIVendorNote(
            vendor_type="entertainer",
            why="Ask for an animal-themed show that works indoors.",
        )
    ],
)

FEW_SHOTS: tuple[tuple[dict[str, Any], PlanSuggestions], ...] = (
    (_EXAMPLE_PAYLOAD, _EXAMPLE_ANSWER),
)
