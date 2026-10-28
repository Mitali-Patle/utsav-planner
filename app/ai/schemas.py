"""Shapes the models must answer in.

Deliberately simple (plain strings, numbers, enums and short lists) so every provider can
follow them. Lists are not length-limited here: the plan checks in `guardrails` trim or
reject them, which is gentler on a model than a hard schema failure.
"""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.budget import EmphasisDirection, EmphasisStrength
from app.domain.change_set import ChangeKind
from app.domain.models import BudgetImpact
from app.domain.savings import SavingAction, SavingLevel


class AIIdea(BaseModel):
    """A personalized idea."""

    title: str = Field(description="Short name for the idea.")
    description: str = Field(description="One or two sentences on what to do.")
    category_id: str | None = Field(default=None, description="Related category id, if any.")
    budget_impact: BudgetImpact = Field(description="Whether it saves, adds or is neutral.")
    why: str = Field(description="Why it suits this specific event.")


class AIEmphasis(BaseModel):
    """A suggestion to give a budget category a little more or less."""

    category_id: str
    direction: EmphasisDirection
    strength: EmphasisStrength
    why: str


class AISuggestedTask(BaseModel):
    """An extra task specific to this event."""

    title: str
    category_id: str | None = None
    days_before_event: int = Field(description="Days before the event; negative means after.")
    why: str


class AIVendorNote(BaseModel):
    """Why a vendor matters for this event, and what to ask."""

    vendor_type: str
    why: str


class PlanSuggestions(BaseModel):
    """Answer for the `personalize` task."""

    off_topic: bool = Field(default=False, description="True if the input is not about an event.")
    summary_why: str = Field(description="One or two sentences on the overall approach.")
    ideas: list[AIIdea] = Field(default_factory=list)
    budget_emphasis: list[AIEmphasis] = Field(default_factory=list)
    extra_tasks: list[AISuggestedTask] = Field(default_factory=list)
    vendor_notes: list[AIVendorNote] = Field(default_factory=list)


def _list_every_field(schema: dict[str, Any]) -> None:
    """Mark every field required (still nullable), so a model writes each one out.

    Gemini tends to skip optional fields, `amount_inr` included, even when the request
    states an amount. Validation is unchanged: a missing field still defaults to None.
    """
    schema["required"] = list(schema["properties"])


class AIChangeOperation(BaseModel):
    """One operation, in the same flat shape as the domain's `ChangeOperation`."""

    model_config = ConfigDict(json_schema_extra=_list_every_field)

    kind: ChangeKind
    amount_inr: int | None = None
    category_id: str | None = None
    expense_id: str | None = None
    label: str | None = None
    guest_count: int | None = None
    days: int | None = None
    delta: int | None = None
    new_date: date | None = None


class IntentStatus(StrEnum):
    """Whether the request could be turned into operations."""

    OK = "ok"
    NEEDS_CLARIFICATION = "needs_clarification"
    OFF_TOPIC = "off_topic"


class ChangeIntent(BaseModel):
    """Answer for the `interpret_change` task."""

    status: IntentStatus
    clarification_question: str | None = None
    operations: list[AIChangeOperation] = Field(default_factory=list)
    cut_priority: list[str] = Field(default_factory=list)
    summary: str = Field(description="One sentence restating the request, with no new numbers.")


class AIAlternative(BaseModel):
    """A way to close a budget shortfall."""

    category_id: str
    action: SavingAction
    level: SavingLevel
    title: str
    why: str


class Alternatives(BaseModel):
    """Answer for the `alternatives` task."""

    items: list[AIAlternative] = Field(default_factory=list)
    summary: str
