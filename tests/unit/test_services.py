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
    summarize,
)
from app.domain.changes import ChangeKind, ChangeOperation, ChangeSet
from app.domain.errors import InvalidInputError, NotFoundError, StaleProposalError
from app.domain.models import BudgetImpact, CategoryId, EventType, TaskId, TaskSource, VendorStatus
from app.domain.planning import EventDetails
from app.persistence.db import make_engine
from app.persistence.repositories import ProposalRepository, ProposalStatus, StoredEvent
from app.persistence.tables import metadata
from app.services import Services, build_services
from app.services.alternatives import _price
from app.services.change_manager import NOTHING_TO_FREE_MESSAGE, ApprovalStatus, OutcomeKind
from app.services.planning import PlanningService, to_personalization
from tests.factories import make_plan
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


def create(app: Services, **overrides: object) -> StoredEvent:
    draft = app.planning.create_draft(ME, details(**overrides))
    return app.planning.personalize(ME, draft.id)


BUDGET_CUT = intent(
    AIChangeOperation(kind=ChangeKind.PROTECT, category_id="cake"),
    AIChangeOperation(kind=ChangeKind.SET_TOTAL_BUDGET, amount_inr=12_000),
)


# Creating events


def test_event_is_personalized_by_ai_and_saved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    created = create(app)
    plan = created.plan
    assert created.version == 2, "draft is version 1; personalization saves version 2"
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
    created = create(services(engine, outage, outage))
    assert created.ai is not None
    assert created.ai["provenance"]["fallback"] is True
    assert len(created.plan.ideas) == 3, "labelled catalog ideas"


def test_off_topic_personalization_keeps_the_draft(engine: Engine) -> None:
    created = create(services(engine, PlanSuggestions(off_topic=True, summary_why="Not an event.")))
    assert created.plan.ideas == ()


def test_invalid_event_details_are_rejected(engine: Engine) -> None:
    with pytest.raises(InvalidInputError):
        services(engine).planning.create_draft(ME, details(event_date=TODAY - timedelta(days=1)))


def test_events_are_private_to_their_session(engine: Engine) -> None:
    """Scenario 12."""
    app = services(engine, SUGGESTIONS)
    created = create(app)
    with pytest.raises(NotFoundError):
        app.planning.get(INTRUDER, created.id)
    assert app.planning.list_events(INTRUDER) == []
    assert [e.id for e in app.planning.list_events(ME)] == [created.id]
    with pytest.raises(NotFoundError):
        app.changes.request_change(INTRUDER, created.id, "budget 12000")


def test_draft_is_saved_at_once_without_ai(engine: Engine) -> None:
    app = services(engine)
    draft = app.planning.create_draft(ME, details())
    assert (draft.version, draft.ai, draft.plan.ideas) == (1, None, ())
    assert draft.plan.tasks and draft.plan.vendors


def test_personalization_runs_once(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    first = create(app)
    again = app.planning.personalize(ME, first.id)
    assert again.version == first.version, "no second AI call, no new version"


def test_personalization_after_an_edit_only_adds_ideas(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    draft = app.planning.create_draft(ME, details())
    edited = app.edits.set_task_done(ME, draft.id, TaskId("task-1"), done=True)
    personalized = app.planning.personalize(ME, draft.id)
    assert [c.allocated_inr for c in personalized.plan.categories] == [
        c.allocated_inr for c in edited.plan.categories
    ]
    assert personalized.plan.tasks == edited.plan.tasks
    assert [i.title for i in personalized.plan.ideas] == ["Rocket launch corner"]


def test_rate_limited_personalization_uses_the_labelled_fallback(engine: Engine) -> None:
    app = services(engine)
    draft = app.planning.create_draft(ME, details())
    offline = app.planning.personalize(ME, draft.id, ai_allowed=lambda: False)
    assert offline.ai is not None
    assert offline.ai["provenance"]["fallback"] is True
    assert offline.ai["provenance"]["attempts"] == []


def test_a_retry_that_falls_back_again_saves_nothing(engine: Engine) -> None:
    app = services(engine, ProviderUnavailableError("down"), ProviderUnavailableError("down"))
    draft = app.planning.create_draft(ME, details())
    standard = app.planning.personalize(ME, draft.id)
    assert standard.ai is not None and standard.ai["provenance"]["fallback"]
    again = app.planning.personalize(ME, draft.id)
    assert again.version == standard.version, "a second fallback is not saved"


def test_a_limited_retry_keeps_the_standard_ideas_without_calling_a_model(engine: Engine) -> None:
    app = services(engine)
    draft = app.planning.create_draft(ME, details())
    standard = app.planning.personalize(ME, draft.id, ai_allowed=lambda: False)
    again = app.planning.personalize(ME, draft.id, ai_allowed=lambda: False)
    assert again.version == standard.version


def test_an_ai_plan_never_asks_the_rate_limit(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)

    def asked() -> bool:
        raise AssertionError("the rate limit was consulted for a plan that needs no AI call")

    assert app.planning.personalize(ME, event.id, ai_allowed=asked).version == event.version


def test_edit_during_personalization_is_not_overwritten(engine: Engine) -> None:
    """The user ticks a task while the AI is still thinking: their edit wins."""
    holder: dict[str, Services] = {}

    class EditingProvider:
        name = "gemini:slow"

        def generate(self, request: object) -> str:
            app = holder["app"]
            event = app.planning.list_events(ME)[0]
            app.edits.set_task_done(ME, event.id, TaskId("task-1"), done=True)
            return SUGGESTIONS.model_dump_json()

    chain = FallbackChain([EditingProvider()], MemoryCache())
    app = build_services(
        Settings(_env_file=None, app_env="test"), engine, FixedClock(TODAY), ai=chain
    )
    holder["app"] = app
    draft = app.planning.create_draft(ME, details())
    personalized = app.planning.personalize(ME, draft.id)
    assert personalized.version == 3
    assert personalized.plan.task(TaskId("task-1")).done
    assert [i.title for i in personalized.plan.ideas] == ["Rocket launch corner"]
    assert not any(t.source is TaskSource.AI for t in personalized.plan.tasks)


# Change requests


def test_change_request_becomes_a_pending_proposal(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = create(app)
    outcome = app.changes.request_change(ME, event.id, "  budget dropped to 12k, keep the cake ")
    assert outcome.kind is OutcomeKind.PROPOSAL
    assert outcome.proposal is not None
    assert outcome.proposal.status is ProposalStatus.PENDING
    assert outcome.proposal.request_text == "budget dropped to 12k, keep the cake"
    assert outcome.proposal.intent["provenance"]["provider"] == "gemini:fake"
    assert outcome.proposal.proposal.after is not None
    assert outcome.proposal.proposal.after.total_budget_inr == 12_000
    assert app.planning.get(ME, event.id).version == event.version, (
        "nothing is saved before approval"
    )


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
    event = create(app)
    outcome = app.changes.request_change(ME, event.id, "some request")
    assert outcome.kind is kind
    assert outcome.message is not None and message in outcome.message
    assert app.changes.overview(ME, event.id).history == []


def test_ai_outage_offers_the_form_instead(engine: Engine) -> None:
    outage = ProviderUnavailableError("503")
    app = services(engine, SUGGESTIONS, outage, outage)
    event = create(app)
    outcome = app.changes.request_change(ME, event.id, "budget 12000")
    assert outcome.kind is OutcomeKind.UNAVAILABLE
    assert outcome.provenance is not None and outcome.provenance.fallback


def test_empty_request_is_rejected_without_an_ai_call(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)
    assert app.changes.request_change(ME, event.id, "   ").kind is OutcomeKind.INVALID


def test_repeated_change_has_nothing_to_change(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)
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
    event = create(app)
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    approved = app.changes.approve(ME, event.id, proposal.id)
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.event is not None
    assert (approved.event.version, approved.event.plan.total_budget_inr) == (
        event.version + 1,
        12_000,
    )
    assert [p.status for p in app.changes.overview(ME, event.id).history] == [
        ProposalStatus.APPROVED
    ]
    again = app.changes.approve(ME, event.id, proposal.id)
    assert again.status is ApprovalStatus.ALREADY_DECIDED


def test_reject_leaves_the_plan_unchanged(engine: Engine) -> None:
    """Scenario 6."""
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = create(app)
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    assert app.changes.reject(ME, event.id, proposal.id)
    assert not app.changes.reject(ME, event.id, proposal.id)
    assert app.planning.get(ME, event.id).plan == event.plan
    assert [p.status for p in app.changes.overview(ME, event.id).history] == [
        ProposalStatus.REJECTED
    ]


def test_proposal_made_stale_by_an_edit_is_refused(engine: Engine) -> None:
    """Scenario 13."""
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = create(app)
    proposal = app.changes.request_change(ME, event.id, "budget 12k keep cake").proposal
    assert proposal is not None
    app.edits.set_task_done(ME, event.id, TaskId("task-1"), done=True)
    outcome = app.changes.approve(ME, event.id, proposal.id)
    assert outcome.status is ApprovalStatus.STALE
    assert app.changes.overview(ME, event.id).history == [], "a stale preview is not history"
    assert app.planning.get(ME, event.id).plan.total_budget_inr == 15_000


def test_infeasible_proposal_is_kept_but_cannot_be_approved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)
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


def test_a_decision_lost_to_another_tab_rolls_the_approval_back(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a reject wins between reading and deciding, the plan save is undone (review, 23 Oct)."""
    app = services(engine, SUGGESTIONS)
    event = create(app)
    form = ChangeSet(operations=(ChangeOperation(kind=ChangeKind.SET_GUEST_COUNT, guest_count=45),))
    outcome = app.changes.propose_from_form(ME, event.id, form, "45 guests")
    assert outcome.proposal is not None
    monkeypatch.setattr(ProposalRepository, "decide", lambda *_args: False)
    with pytest.raises(StaleProposalError, match="another tab"):
        app.changes.approve(ME, event.id, outcome.proposal.id)
    assert app.planning.get(ME, event.id).version == event.version, "the save was rolled back"


def test_another_session_cannot_approve_or_reject(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, BUDGET_CUT)
    event = create(app)
    proposal = app.changes.request_change(ME, event.id, "budget 12k").proposal
    assert proposal is not None
    with pytest.raises(NotFoundError):
        app.changes.approve(INTRUDER, event.id, proposal.id)
    with pytest.raises(NotFoundError):
        app.changes.reject(INTRUDER, event.id, proposal.id)
    with pytest.raises(NotFoundError):
        app.changes.overview(INTRUDER, event.id)


# Direct edits


def test_direct_edits_save_new_versions(engine: Engine) -> None:
    """Scenario 1 through the services: totals update after adding an expense."""
    app = services(engine, SUGGESTIONS)
    event = create(app)
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
    assert [v.version for v in (v2, v3, v4, v5, v6, v7)] == list(
        range(event.version + 1, event.version + 7)
    )
    assert v3.plan.expenses[0].amount_inr == 2_000
    assert v5.plan.vendor(vendor.id).status is VendorStatus.CONTACTED
    assert v7.plan.expenses == ()


def test_invalid_edits_are_rejected_and_nothing_is_saved(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)
    with pytest.raises(InvalidInputError):
        app.edits.add_expense(
            ME,
            event.id,
            NewExpense(category_id=CategoryId("cake"), label="Cake", amount_inr=50_000),
        )
    assert app.planning.get(ME, event.id).version == event.version


# Alternatives

DECOR_CUT = Alternatives(
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
    event = create(app)
    app.edits.add_expense(
        ME,
        event.id,
        NewExpense(category_id=CategoryId("catering"), label="Caterer", amount_inr=16_000),
    )
    view = app.alternatives.suggest(ME, event.id)
    lines = {c.id: c for c in app.planning.get(ME, event.id).plan.categories}
    option = view.options[0]
    gap = 16_000 - lines[CategoryId("catering")].allocated_inr
    assert view.shortfall_inr == gap, "the gap is catering's overspend"
    assert option.saving_inr == lines[CategoryId("decor")].allocated_inr * 25 // 100
    assert option.shortfall_after_inr == max(0, gap - option.saving_inr)
    assert view.provenance is not None and view.provenance.provider == "gemini:fake"


def test_an_alternative_becomes_a_proposal_that_closes_what_it_promised(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, DECOR_CUT)
    event = create(app)
    app.edits.add_expense(
        ME,
        event.id,
        NewExpense(category_id=CategoryId("catering"), label="Caterer", amount_inr=16_000),
    )
    option = app.alternatives.suggest(ME, event.id).options[0]
    assert option.applicable
    outcome = app.changes.propose_saving(ME, event.id, "decor", option.action, option.level)
    assert outcome.kind is OutcomeKind.PROPOSAL and outcome.proposal is not None
    assert outcome.proposal.request_text == "Save on Decorations: reduce (medium)"
    approved = app.changes.approve(ME, event.id, outcome.proposal.id)
    assert approved.event is not None
    assert summarize(approved.event.plan).shortfall_inr == option.shortfall_after_inr


def test_an_alternative_with_nothing_to_free_is_explained(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS, DECOR_CUT)
    event = create(app)
    decor = app.planning.get(ME, event.id).plan.category(CategoryId("decor")).allocated_inr
    for category, amount in (("catering", 16_000), ("decor", decor)):
        app.edits.add_expense(
            ME,
            event.id,
            NewExpense(category_id=CategoryId(category), label="Paid", amount_inr=amount),
        )
    action, level = SavingAction.REDUCE, SavingLevel.MEDIUM
    spent = app.changes.propose_saving(ME, event.id, "decor", action, level)
    unknown = app.changes.propose_saving(ME, event.id, "nope", action, level)
    assert (spent.kind, spent.message) == (OutcomeKind.INVALID, NOTHING_TO_FREE_MESSAGE)
    assert unknown.kind is OutcomeKind.INVALID and "nope" in (unknown.message or "")


def test_an_alternative_whose_money_has_nowhere_to_go_is_not_offered() -> None:
    plan = make_plan(protected=("catering", "cake", "other"))
    option = _price(plan, DECOR_CUT.items[0], 1_000, TODAY)
    assert (option.applicable, option.saving_inr, option.shortfall_after_inr) == (False, 0, 1_000)


def test_no_alternatives_needed_within_budget(engine: Engine) -> None:
    app = services(engine, SUGGESTIONS)
    event = create(app)
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


def test_concurrent_personalization_saves_only_once(engine: Engine) -> None:
    """Two tabs personalize at once: the slower one sees the result and does not overwrite it."""
    settings = Settings(_env_file=None, app_env="test")
    other = build_services(
        settings,
        engine,
        FixedClock(TODAY),
        ai=FallbackChain(
            [ScriptedProvider("groq:fast", [SUGGESTIONS.model_dump_json()])], MemoryCache()
        ),
    )
    target: dict[str, str] = {}

    class RacingProvider:
        name = "gemini:slow"

        def generate(self, request: object) -> str:
            other.planning.personalize(ME, target["id"])
            return SUGGESTIONS.model_dump_json()

    app = build_services(
        settings, engine, FixedClock(TODAY), ai=FallbackChain([RacingProvider()], MemoryCache())
    )
    target["id"] = app.planning.create_draft(ME, details()).id
    result = app.planning.personalize(ME, target["id"])
    assert result.version == 2
    assert result.ai is not None
    assert result.ai["provenance"]["provider"] == "groq:fast"


def test_personalization_gives_up_if_the_plan_keeps_changing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = services(engine, SUGGESTIONS)
    draft = app.planning.create_draft(ME, details())
    original_get = PlanningService.get
    calls = {"n": 0}

    def get_and_edit(self: PlanningService, session_id: str, event_id: str) -> StoredEvent:
        stored = original_get(self, session_id, event_id)
        calls["n"] += 1
        task = TaskId(f"task-{calls['n']}")
        app.edits.set_task_done(ME, event_id, task, done=True)
        return stored

    monkeypatch.setattr(PlanningService, "get", get_and_edit)
    with pytest.raises(StaleProposalError):
        app.planning.personalize(ME, draft.id)
