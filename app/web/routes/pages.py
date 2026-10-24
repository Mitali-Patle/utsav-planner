"""Landing page and favicon."""

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import Services
from app.web.deps import SessionDep, TodayDep
from app.web.responses import render
from app.web.views import example_form, landing_context, plans_context

router = APIRouter(tags=["pages"])

FAVICON_PATH = "/static/img/favicon.svg"


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    """Redirect legacy `/favicon.ico` requests to the SVG icon served by the CDN.

    Returns:
        A permanent redirect.
    """
    return RedirectResponse(FAVICON_PATH, status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, today: TodayDep, example: str | None = None) -> HTMLResponse:
    """Landing page: start a plan.

    `?example=wedding` (or `birthday`, `community`) fills the form with a ready-made event
    that the visitor can change before creating it; it works without JavaScript.

    Returns:
        The rendered page.
    """
    return render(request, "landing.html", landing_context(example_form(example, today), today))


@router.get("/plans", response_class=HTMLResponse, include_in_schema=False)
def my_plans(request: Request, session: SessionDep, today: TodayDep) -> HTMLResponse:
    """This visitor's plans, soonest first, with a button to start a new one.

    Returns:
        The rendered page.
    """
    services: Services | None = request.app.state.services
    events = services.planning.list_events(session) if services is not None else []
    return render(request, "plans.html", plans_context(events, today))
