"""Services end to end: storage, scripted model, fixed clock (scenarios 1, 6, 7, 12, 13)."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.ai.client import ProviderUnavailableError
from app.ai.schemas import (
    AIAlternative,
    AIChangeOperation,
    AIEmphasis,
    AIIdea,
    AISuggestedTask,
    AIVendorNote,
    Alternatives,
    ChangeIntent,
    IntentStatus,
    PlanSuggestions,
)
from app.config import Settings
from app.domain.budget import (
    EmphasisDirection,
    EmphasisStrength,
    NewExpense,
    SavingAction,
    SavingLevel,
)
from app.domain.changes import ChangeKind, ChangeOperation, ChangeSet
from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.models import BudgetImpact, CategoryId, EventType, TaskId, TaskSource, VendorStatus
from app.domain.planning import EventDetails
from app.persistence.db import make_engine
from app.persistence.repositories import ProposalStatus
from app.persistence.tables import metadata
from app.services import Services, build_services
from app.services.change_manager import ApprovalStatus, OutcomeKind
from app.services.planning import to_personalization
from tests.fakes import FixedClock, ScriptedProvider

TODAY = date(2026, 10, 22)
ME, INTRUDER = "session-me", "session-intruder"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def services(engine: Engine, *answers: BaseModel | Exception) -> Services:
    script = [a if isinstance(a, Exception) else a.model_dump_json() for a in answers]
    chain = FallbackChain([ScriptedProvider("gemini:fake", script)], MemoryCache())
    return build_services(
        Settings(_env_file=None, app_env="test"), engine, FixedClock(TODAY), ai=chain
    )


def details(**overrides: object) -> EventDetails:
    values: dict[str, object] = {
        "event_type": EventType.BIRTHDAY,
        "title": "Aarav's 8th birthday",
        "city": "Pune",
        "event_date": TODAY + timedelta(days=40),
        "guest_count": 30,
        "total_budget_inr": 15_000,
        "preferences": "Space theme",
    }
    return EventDetails.model_validate(values | overrides)


SUGGESTIONS = PlanSuggestions(
    summary_why="A space party.",
    ideas=[
        AIIdea(
            title="Rocket launch corner",
            description="A cardboard launch pad for photos.",
            category_id="decor",
            budget_impact=BudgetImpact.NEUTRAL,
            why="He loves rockets.",
        ),
        AIIdea(
            title="",
            description="An empty title the guardrails trim to nothing.",
            budget_impact=BudgetImpact.SAVES,
            why="x",
        ),
    ],
    budget_emphasis=[
        AIEmphasis(
            category_id="entertainment",
            direction=EmphasisDirection.INCREASE,
            strength=EmphasisStrength.MEDIUM,
            why="Games matter.",
        )
    ],
    extra_tasks=[
        AISuggestedTask(
            title="Buy glow-in-the-dark stars",
            category_id="decor",
            days_before_event=7,
            why="Night sky effect.",
        )
    ],
    vendor_notes=[AIVendorNote(vendor_type="cake_shop", why="Ask for a rocket cake.")],
)


def intent(*operations: AIChangeOperation, **overrides: object) -> ChangeIntent:
    values: dict[str, object] = {
        "status": IntentStatus.OK,
        "operations": list(operations),
        "summary": "x",
    }
    return ChangeIntent.model_validate(values | overrides)


BUDGET_CUT = intent(
    AIChangeOperation(kind=ChangeKind.PROTECT, category_id="cake"),
    AIChangeOperation(kind=ChangeKind.SET_TOTAL_BUDGET, amount_inr=12_000),
)


# Creating events


def test_event_is_personalized_by_ai_and_saved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    created = app.planning.create_event(ME, details())
    plan = created.plan
    assert created.version == 1
    assert [i.title for i in plan.ideas] == ["Rocket launch corner"], "the empty idea was skipped"
    ai_task = next(t for t in plan.tasks if t.source is TaskSource.AI)
    assert (ai_task.title, ai_task.why) == ("Buy glow-in-the-dark stars", "Night sky effect.")
    assert (
        next(v for v in plan.vendors if v.vendor_type == "cake_shop").why
        == "Ask for a rocket cake."
    )
    assert created.ai is not None
    assert created.ai["provenance"]["provider"] == "gemini:fake"
    assert created.ai["provenance"]["fallback"] is False
    assert app.planning.get(ME, created.id).plan == plan


def test_event_is_still_created_when_the_ai_is_down(engine: Engine) -> None:
    outage = ProviderUnavailableError("503")
    created = services(engine, outage, outage).planning.create_event(ME, details())
    assert created.ai is not None
    assert created.ai["provenance"]["fallback"] is True
    assert len(created.plan.ideas) == 3, "labelled catalog ideas"


def test_off_topic_personalization_keeps_the_draft(engine: Engine) -> None:
    created = services(
        engine, PlanSuggestions(off_topic=True, summary_why="Not an event.")
    ).planning.create_event(ME, details())
    assert created.plan.ideas == ()


def test_invalid_event_details_are_rejected(engine: Engine) -> None:
    with pytest.raises(InvalidInputError):
        services(engine).planning.create_event(ME, details(event_date=TODAY - timedelta(days=1)))


def test_events_are_private_to_their_session(engine: Engine) -> None:
    """Scenario 12."""
    app = services(engine, SUGGESTIONS)
    created = app.planning.create_event(ME, details())
    with pytest.raises(NotFoundError):
        app.planning.get(INTRUDER, created.id)
    assert app.planning.list_events(INTRUDER) == []
    assert [e.id for e in app.planning.list_events(ME)] == [created.id]
    with pytest.raises(NotFoundError):
        app.changes.request_change(INTRUDER, created.id, "budget 12000")


# Change requests


def test_change_request_becomes_a_pending_proposal(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = app.planning.create_event(ME, details())
    outcome = app.changes.request_change(ME, event.id, "  budget dropped to 12k, keep the cake ")
    assert outcome.kind is OutcomeKind.PROPOSAL
    assert outcome.proposal is not None
    assert outcome.proposal.status is ProposalStatus.PENDING
    assert outcome.proposal.request_text == "budget dropped to 12k, keep the cake"
    assert outcome.proposal.intent["provenance"]["provider"] == "gemini:fake"
    assert outcome.proposal.proposal.after is not None
    assert outcome.proposal.proposal.after.total_budget_inr == 12_000
    assert app.planning.get(ME, event.id).version == 1, "nothing is saved before approval"


@pytest.mark.parametrize(
    ("answer", "kind", "message"),
    [
        (
            intent(status=IntentStatus.NEEDS_CLARIFICATION, clarification_question="Which part?"),
            OutcomeKind.CLARIFICATION,
            "Which part?",
        ),
        (intent(status=IntentStatus.OFF_TOPIC), OutcomeKind.OFF_TOPIC, "I can only help"),
        (
            intent(AIChangeOperation(kind=ChangeKind.SHIFT_DATE_DAYS, days=-60)),
            OutcomeKind.INVALID,
            "can't be in the past",
        ),
    ],
)
def test_change_outcomes(
    engine: Engine, answer: ChangeIntent, kind: OutcomeKind, message: str
) -> None:
    app = services(engine, SUGGESTIONS, answer)
    event = app.planning.create_event(ME, details())
    outcome = app.changes.request_change(ME, event.id, "some request")
    assert outcome.kind is kind
    assert outcome.message is not None and message in outcome.message
    assert app.changes.history(ME, event.id) == []


def test_ai_outage_offers_the_form_instead(engine: Engine) -> None:
    outage = ProviderUnavailableError("503")
    app = services(engine, SUGGESTIONS, outage, outage)
    event = app.planning.create_event(ME, details())
    outcome = app.changes.request_change(ME, event.id, "budget 12000")
    assert outcome.kind is OutcomeKind.UNAVAILABLE
    assert outcome.provenance is not None and outcome.provenance.fallback


def test_empty_request_is_rejected_without_an_ai_call(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    assert app.changes.request_change(ME, event.id, "   ").kind is OutcomeKind.INVALID


def test_repeated_change_has_nothing_to_change(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    app.edits.add_expense(
        ME, event.id, NewExpense(category_id=CategoryId("decor"), label="Balloons", amount_inr=500)
    )
    form = ChangeSet(
        operations=(
            ChangeOperation(
                kind=ChangeKind.ADD_EXPENSE,
                category_id=CategoryId("decor"),
                label="balloons",
                amount_inr=500,
            ),
        )
    )
    outcome = app.changes.propose_from_form(ME, event.id, form, "Add balloons")
    assert outcome.kind is OutcomeKind.NOTHING_TO_CHANGE


# Approve and reject


def test_approval_saves_a_new_version_and_logs_it(engine: Engine) -> None:
    """Scenario 7."""
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = app.planning.create_event(ME, details())
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    approved = app.changes.approve(ME, event.id, proposal.id)
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.event is not None
    assert (approved.event.version, approved.event.plan.total_budget_inr) == (2, 12_000)
    assert [p.status for p in app.changes.history(ME, event.id)] == [ProposalStatus.APPROVED]
    again = app.changes.approve(ME, event.id, proposal.id)
    assert again.status is ApprovalStatus.ALREADY_DECIDED


def test_reject_leaves_the_plan_unchanged(engine: Engine) -> None:
    """Scenario 6."""
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = app.planning.create_event(ME, details())
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    assert app.changes.reject(ME, event.id, proposal.id)
    assert not app.changes.reject(ME, event.id, proposal.id)
    assert app.planning.get(ME, event.id).plan == event.plan
    assert [p.status for p in app.changes.history(ME, event.id)] == [ProposalStatus.REJECTED]


def test_proposal_made_stale_by_an_edit_is_refused(engine: Engine) -> None:
    """Scenario 13."""
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = app.planning.create_event(ME, details())
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    app.edits.set_task_done(ME, event.id, TaskId("task-1"), done=True)
    outcome = app.changes.approve(ME, event.id, proposal.id)
    assert outcome.status is ApprovalStatus.STALE
    assert app.changes.history(ME, event.id)[0].status is ProposalStatus.STALE
    assert app.planning.get(ME, event.id).plan.total_budget_inr == 15_000


def test_infeasible_proposal_is_kept_but_cannot_be_approved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    app.edits.protect_category(ME, event.id, CategoryId("catering"), protected=True)
    app.edits.protect_category(ME, event.id, CategoryId("venue"), protected=True)
    form = ChangeSet(
        operations=(ChangeOperation(kind=ChangeKind.SET_TOTAL_BUDGET, amount_inr=2_000),)
    )
    outcome = app.changes.propose_from_form(ME, event.id, form, "Budget 2,000")
    assert outcome.kind is OutcomeKind.PROPOSAL
    assert outcome.proposal is not None
    assert outcome.proposal.proposal.shortfall_inr > 0
    assert (
        app.changes.approve(ME, event.id, outcome.proposal.id).status is ApprovalStatus.INFEASIBLE
    )


def test_another_session_cannot_approve_or_reject(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = app.planning.create_event(ME, details())
    proposal = app.changes.request_change(ME, event.id, "budget 12k").proposal
    assert proposal is not None
    with pytest.raises(NotFoundError):
        app.changes.approve(INTRUDER, event.id, proposal.id)
    with pytest.raises(NotFoundError):
        app.changes.reject(INTRUDER, event.id, proposal.id)
    with pytest.raises(NotFoundError):
        app.changes.history(INTRUDER, event.id)


# Direct edits


def test_direct_edits_save_new_versions(engine: Engine) -> None:
    """Scenario 1 through the services: totals update after adding an expense."""
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    v2 = app.edits.add_expense(
        ME, event.id, NewExpense(category_id=CategoryId("cake"), label="Cake", amount_inr=1_800)
    )
    expense_id = v2.plan.expenses[0].id
    v3 = app.edits.update_expense(ME, event.id, expense_id, 2_000)
    v4 = app.edits.protect_expense(ME, event.id, expense_id, protected=True)
    vendor = v4.plan.vendors[0]
    v5 = app.edits.set_vendor_status(ME, event.id, vendor.id, VendorStatus.CONTACTED)
    v6 = app.edits.protect_expense(ME, event.id, expense_id, protected=False)
    v7 = app.edits.remove_expense(ME, event.id, expense_id)
    assert [v.version for v in (v2, v3, v4, v5, v6, v7)] == [2, 3, 4, 5, 6, 7]
    assert v3.plan.expenses[0].amount_inr == 2_000
    assert v5.plan.vendor(vendor.id).status is VendorStatus.CONTACTED
    assert v7.plan.expenses == ()


def test_invalid_edits_are_rejected_and_nothing_is_saved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    with pytest.raises(InvalidInputError):
        app.edits.add_expense(
            ME,
            event.id,
            NewExpense(category_id=CategoryId("cake"), label="Cake", amount_inr=50_000),
        )
    assert app.planning.get(ME, event.id).version == 1


# Alternatives


def test_alternatives_are_priced_by_code(engine: Engine) -> None:
    answer = Alternatives(
        summary="Trim decor.",
        items=[
            AIAlternative(
                category_id="decor",
                action=SavingAction.REDUCE,
                level=SavingLevel.MEDIUM,
                title="Paper decor",
                why="Cheaper.",
            )
        ],
    )
    app = services(engine, SUGGESTIONS, answer)
    event = app.planning.create_event(ME, details())
    app.edits.add_expense(
        ME,
        event.id,
        NewExpense(category_id=CategoryId("catering"), label="Caterer", amount_inr=16_000),
    )
    view = app.alternatives.suggest(ME, event.id)
    decor = next(c for c in app.planning.get(ME, event.id).plan.categories if c.id == "decor")
    option = view.options[0]
    assert view.shortfall_inr == 1_000
    assert option.saving_inr == decor.allocated_inr * 25 // 100
    assert option.shortfall_after_inr == max(0, 1_000 - option.saving_inr)
    assert view.provenance is not None and view.provenance.provider == "gemini:fake"


def test_no_alternatives_needed_within_budget(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = app.planning.create_event(ME, details())
    view = app.alternatives.suggest(ME, event.id)
    assert (view.shortfall_inr, view.options, view.provenance) == (0, (), None)


def test_items_that_still_fail_validation_are_skipped() -> None:
    suggestions = PlanSuggestions(
        summary_why="x",
        ideas=[AIIdea(title="Fine", description="ok", budget_impact=BudgetImpact.SAVES, why="")],
        extra_tasks=[
            AISuggestedTask(title="", days_before_event=3, why="no title"),
            AISuggestedTask(title="Book a magician", days_before_event=10, why="Kids love magic."),
        ],
        vendor_notes=[AIVendorNote(vendor_type="cake_shop", why="")],
    )
    personalization = to_personalization(suggestions)
    assert [t.title for t in personalization.tasks] == ["Book a magician"]
    assert personalization.ideas == ()
    assert personalization.vendor_notes == {}
