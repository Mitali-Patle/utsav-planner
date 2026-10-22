"""Create an event, show its dashboard, and personalize it with AI."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.domain.catalog import CATALOG
from app.domain.errors import InvalidInputError
from app.domain.models import EventType
from app.domain.planning import EventDetails
from app.web.deps import AILimitDep, ServicesDep, SessionDep, TodayDep
from app.web.responses import Notice, after_post, render
from app.web.views import FormState, dashboard_context, form_errors, landing_context

router = APIRouter(tags=["events"])

PERSONALIZED = Notice("Your plan has been personalized. Each AI suggestion says why.")
FALLBACK = Notice(
    "The AI assistant is unavailable right now, so these are standard ideas (not AI-generated).",
    "info",
)
LIMITED = Notice(
    "You've made many AI requests in a short time, so these are standard ideas "
    "(not AI-generated). Try again in a few minutes.",
    "info",
)


@router.post("/events", include_in_schema=False)
def create_event(
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    event_type: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    event_date: Annotated[str, Form()] = "",
    guest_count: Annotated[str, Form()] = "",
    total_budget_inr: Annotated[str, Form()] = "",
    preferences: Annotated[str, Form()] = "",
    sub_events: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Save the code-built draft at once and open its dashboard.

    The form lists functions for every event type; only those of the chosen type are kept.
    Invalid input re-renders the form with the user's values and a message per field.
    """
    values = {
        "event_type": event_type,
        "title": title,
        "city": city,
        "event_date": event_date,
        "guest_count": guest_count,
        "total_budget_inr": total_budget_inr,
        "preferences": preferences,
        "sub_events": sub_events or [],
    }
    chosen = _sub_events_for(event_type, sub_events or [])
    try:
        details = EventDetails.model_validate({**values, "sub_events": chosen})
        stored = services.planning.create_draft(session, details)
    except ValidationError as error:
        return _invalid_form(
            request, services, session, today, FormState(values, form_errors(error))
        )
    except InvalidInputError as error:
        field = "event_date" if error.code.startswith("event_date") else "form"
        return _invalid_form(
            request, services, session, today, FormState(values, {field: error.message})
        )
    return after_post(
        request,
        f"/events/{stored.id}",
        Notice("Your plan is ready. The AI is personalizing it now."),
    )


@router.get("/events/{event_id}", response_class=HTMLResponse, include_in_schema=False)
def dashboard(
    request: Request, event_id: str, services: ServicesDep, session: SessionDep, today: TodayDep
) -> HTMLResponse:
    """The event dashboard."""
    stored = services.planning.get(session, event_id)
    history = services.changes.history(session, event_id)
    return render(request, "dashboard.html", dashboard_context(stored, history, today))


@router.post("/events/{event_id}/personalize", include_in_schema=False)
def personalize(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    ai_limit: AILimitDep,
) -> Response:
    """Tailor the plan with AI, or with labelled standard ideas that can be retried later.

    The AI rate limit is consulted only when a model would actually be called.
    """
    answers: list[bool] = []

    def allowed() -> bool:
        answers.append(ai_limit.allow(request))
        return answers[-1]

    stored = services.planning.personalize(session, event_id, ai_allowed=allowed)
    fallback = bool(stored.ai and stored.ai["provenance"]["fallback"])
    notice = (LIMITED if answers == [False] else FALLBACK) if fallback else PERSONALIZED
    return after_post(
        request,
        f"/events/{event_id}",
        notice,
        "partials/plan_area.html",
        dashboard_context(stored, [], today),
    )


def _sub_events_for(event_type: str, submitted: list[str]) -> list[str]:
    valid_types = {t.value for t in EventType}
    if event_type not in valid_types:
        return []
    options = {s.id for s in CATALOG[EventType(event_type)].sub_events}
    return [s for s in submitted if s in options]


def _invalid_form(
    request: Request, services: ServicesDep, session: str, today: TodayDep, form: FormState
) -> HTMLResponse:
    events = services.planning.list_events(session)
    context = landing_context(events, form, today)
    return render(request, "landing.html", context, status.HTTP_422_UNPROCESSABLE_CONTENT)
