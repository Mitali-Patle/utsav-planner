"""The app through HTTP: every flow with and without JavaScript, plus an accessibility check."""

import re
from collections.abc import Iterator
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, event

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.ai.client import ProviderUnavailableError
from app.ai.schemas import AIAlternative, Alternatives, ChangeIntent, IntentStatus
from app.config import Settings
from app.domain.budget import SavingAction, SavingLevel
from app.main import create_app
from app.persistence.db import make_engine
from app.persistence.tables import metadata
from app.services import build_services
from app.web import views
from tests.fakes import FixedClock, ScriptedProvider
from tests.unit.test_services import BUDGET_CUT, DECOR_CUT, DIFFERENT, SUGGESTIONS

TODAY = date(2026, 10, 22)
HTMX = {"HX-Request": "true"}
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


class LabelAudit(HTMLParser):
    """Collects form controls, labels, ids, buttons and h1s from rendered HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[str] = []
        self.label_for: set[str] = set()
        self.ids: list[str] = []
        self.h1 = 0
        self._button_depth = 0
        self.empty_buttons = 0
        self._button_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag == "label" and attributes.get("for"):
            self.label_for.add(str(attributes["for"]))
        if tag in {"select", "textarea"} or (
            tag == "input" and attributes.get("type") not in {"hidden", "submit"}
        ):
            self.controls.append(str(attributes.get("id", "")))
        if tag == "h1":
            self.h1 += 1
        if tag == "button":
            self._button_depth += 1
            self._button_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self._button_depth -= 1
            if not self._button_text.strip():
                self.empty_buttons += 1

    def handle_data(self, data: str) -> None:
        if self._button_depth:
            self._button_text += data


def assert_accessible(html: str, *, page: bool = True) -> None:
    audit = LabelAudit()
    audit.feed(html)
    unlabeled = [c for c in audit.controls if not c or c not in audit.label_for]
    assert not unlabeled, f"controls without a label: {unlabeled}"
    duplicates = {i for i in audit.ids if audit.ids.count(i) > 1}
    assert not duplicates, f"duplicate ids: {duplicates}"
    assert audit.empty_buttons == 0, "every button needs a visible or screen-reader name"
    if page:
        assert audit.h1 == 1, "a page has exactly one h1"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def client_for(
    engine: Engine,
    *answers: BaseModel | Exception,
    rate_limit: int = 20,
    create_limit: int = 20,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_rate_limit_per_10min=rate_limit,
        event_create_limit_per_hour=create_limit,
    )
    script = [a if isinstance(a, Exception) else a.model_dump_json() for a in answers]
    chain = FallbackChain([ScriptedProvider("gemini:fake", script)], MemoryCache())
    services = build_services(settings, engine, FixedClock(TODAY), ai=chain)
    return TestClient(
        create_app(settings, engine=engine, services=services, clock=FixedClock(TODAY))
    )


def token(client: TestClient) -> str:
    match = TOKEN.search(client.get("/").text)
    assert match
    return match.group(1)


def event_form(csrf: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "csrf_token": csrf,
        "event_type": "birthday",
        "title": "Aarav's 8th birthday",
        "city": "Pune",
        "event_date": str(TODAY + timedelta(days=40)),
        "guest_count": "30",
        "total_budget_inr": "15000",
        "preferences": "Space theme",
        "sub_events": ["kids_games", "sangeet"],
    }
    return values | overrides


def create(client: TestClient, csrf: str, *, personalize: bool = True) -> str:
    response = client.post("/events", data=event_form(csrf), follow_redirects=False)
    assert response.status_code == 303
    url = str(response.headers["location"])
    if personalize:
        client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    return url


def proposal_id(html: str) -> str:
    match = re.search(r"/proposals/([0-9a-f]{32})/", html)
    assert match
    return match.group(1)


# Landing and creating events


def test_landing_page_is_accessible(engine: Engine) -> None:
    page = client_for(engine).get("/")
    assert page.status_code == 200
    assert "Start a new plan" in page.text
    assert_accessible(page.text)


@pytest.mark.parametrize(
    ("example", "title"),
    [
        ("wedding", "Priya and Arjun&#39;s wedding"),
        ("birthday", "Aarav&#39;s 8th birthday"),
        ("community", "Shanti Nagar Ganeshotsav"),
    ],
)
def test_each_example_fills_the_form_and_creates_a_plan(
    engine: Engine, example: str, title: str
) -> None:
    client = client_for(engine)
    page = client.get(f"/?example={example}")
    assert f'href="/?example={example}#create-heading"' in client.get("/").text
    assert f'value="{title}"' in page.text
    checked = f'id="type-{example}" name="event_type" value="{example}"[^>]* checked'
    assert re.search(checked, page.text)
    assert_accessible(page.text)
    values = views.example_form(example, TODAY).values
    response = client.post(
        "/events", data={"csrf_token": token(client), **values}, follow_redirects=False
    )
    assert response.status_code == 303
    dashboard = client.get(str(response.headers["location"])).text
    assert title in dashboard


def test_unknown_example_shows_an_empty_form(engine: Engine) -> None:
    page = client_for(engine).get("/?example=<script>").text
    assert 'value=""' in page.split('name="title"')[1].split(">")[0]
    assert " checked" not in page.split('name="event_type"')[1].split(">")[0]
    assert "<script>" not in page.split("</head>")[1]


def test_invalid_event_keeps_the_input_and_explains(engine: Engine) -> None:
    client = client_for(engine)
    csrf = token(client)
    page = client.post("/events", data=event_form(csrf, guest_count="0", title="Mehendi night"))
    assert page.status_code == 422
    assert 'value="Mehendi night"' in page.text
    assert 'aria-invalid="true"' in page.text
    assert 'aria-describedby="f-guest_count-error"' in page.text
    assert_accessible(page.text)


def test_empty_fields_are_listed_as_required_with_links(engine: Engine) -> None:
    client = client_for(engine)
    page = client.post("/events", data=event_form(token(client), guest_count="", city=""))
    assert page.status_code == 422
    assert '<a href="#f-city">City: This field is required.</a>' in page.text
    assert '<a href="#f-guest_count">Number of guests: This field is required.</a>' in page.text
    assert "Enter a whole number" not in page.text, "empty is not a number problem"
    assert_accessible(page.text)


def test_past_date_is_explained_on_the_date_field(engine: Engine) -> None:
    client = client_for(engine)
    page = client.post(
        "/events", data=event_form(token(client), event_date=str(TODAY - timedelta(days=1)))
    )
    assert page.status_code == 422
    assert "be in the past" in page.text


def test_new_event_opens_its_dashboard_with_personalization_pending(engine: Engine) -> None:
    client = client_for(engine)
    url = create(client, token(client), personalize=False)
    page = client.get(url)
    assert page.status_code == 200
    assert 'hx-trigger="load"' in page.text
    assert "Personalize with AI" in page.text, "works without JavaScript too"
    assert "Your plan is ready" in page.text
    assert "Kids&#39; games" not in page.text
    assert_accessible(page.text)
    assert "Aarav&#39;s 8th birthday" in client.get("/plans").text, "listed under My plans"


def test_session_lasts_a_year_and_renews_on_every_visit(engine: Engine) -> None:
    client = client_for(engine)
    first = client.get("/")
    later = client.get("/plans")
    for response in (first, later):
        cookie = response.headers.get("set-cookie", "")
        assert cookie.startswith("utsav_session="), "renewed, not only set once"
        assert "Max-Age=31536000" in cookie


def test_my_plans_lists_only_this_visitors_plans(engine: Engine) -> None:
    client = client_for(engine)
    empty = client.get("/plans")
    assert empty.status_code == 200
    assert "No plans yet" in empty.text
    assert 'href="/plans" class="nav-plans" aria-current="page"' in empty.text
    assert_accessible(empty.text)
    url = create(client, token(client), personalize=False)
    page = client.get("/plans").text
    assert f'href="{url}"' in page and "<strong>40</strong> days to go" in page
    assert 'id="add-plan"' in page
    assert "reel-slide" not in page, "the photos belong to the home page only"
    assert_accessible(page)
    dashboard = client.get(url).text
    assert "https://calendar.google.com/calendar/render?action=TEMPLATE&amp;text=" in dashboard
    assert 'rel="noopener noreferrer"' in dashboard
    stranger = TestClient(client.app)
    assert f'href="{url}"' not in stranger.get("/plans").text


def test_home_page_has_no_plan_list_and_a_picture_panel(engine: Engine) -> None:
    client = client_for(engine)
    create(client, token(client), personalize=False)
    page = client.get("/").text
    assert "Aarav&#39;s 8th birthday" not in page, "plans live on their own page"
    assert 'aria-current="page"' not in page
    assert page.count('class="reel-slide">') == len(views.CELEBRATION_SLIDES)
    assert page.count('class="reel-slide" aria-hidden="true"') == len(views.CELEBRATION_SLIDES)
    assert '<script src="/static/js/reel.js" defer></script>' in page


def test_every_celebration_photo_exists_is_small_and_described(engine: Engine) -> None:
    folder = Path(__file__).resolve().parents[2] / "public" / "static" / "img" / "celebrations"
    page = client_for(engine).get("/").text
    assert "<figcaption" not in page, "the photos carry no titles"
    for slide in views.CELEBRATION_SLIDES:
        assert (folder / slide.image).stat().st_size < 500_000, slide.image
        assert slide.alt, f"{slide.image} has alt text"
        assert f'alt="{slide.alt}"' in page
        assert page.count(f"/static/img/celebrations/{slide.image}") == 2, "copy for the loop"


def test_creating_events_is_limited_per_client(engine: Engine) -> None:
    """Event creation is the one way to grow storage without bound (review, 23 Oct)."""
    client = client_for(engine, SUGGESTIONS, create_limit=1)
    csrf = token(client)
    first = client.post("/events", data=event_form(csrf), follow_redirects=False)
    assert first.status_code == 303
    second = client.post("/events", data=event_form(csrf), follow_redirects=False)
    assert second.status_code == 429
    assert "created many plans in a short time" in second.text
    assert "Please check the highlighted fields" not in second.text
    assert_accessible(second.text)


def test_other_types_functions_are_ignored(engine: Engine) -> None:
    client = client_for(engine)
    create(client, token(client), personalize=False)
    assert "Choreographer" not in client.get(client.get("/").url).text


# Personalization


def test_personalization_fragment_with_status_notice(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    fragment = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert fragment.status_code == 200
    assert 'id="plan-area"' in fragment.text
    assert "Rocket launch corner" in fragment.text
    assert 'hx-swap-oob="innerHTML"' in fragment.text
    assert "How this was generated" in fragment.text
    assert "hx-preserve" in fragment.text
    assert "hx-preserve" not in client.get(url).text
    assert_accessible(fragment.text, page=False)


def test_personalization_without_javascript_redirects(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    response = client.post(f"{url}/personalize", data={"csrf_token": csrf}, follow_redirects=True)
    assert "Your plan has been personalized" in response.text
    assert "Suggested by AI" in response.text
    assert_accessible(response.text)


def test_ai_outage_shows_labelled_standard_ideas(engine: Engine) -> None:
    outage = ProviderUnavailableError("503 UNAVAILABLE")
    client = client_for(engine, outage, outage)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    page = client.post(f"{url}/personalize", data={"csrf_token": csrf}, follow_redirects=True)
    assert "not AI-generated" in page.text


def test_standard_ideas_can_be_retried_with_ai(engine: Engine) -> None:
    """A fallback is not final: the user can ask again once a model is back (review, 23 Oct)."""
    client = client_for(engine, ProviderUnavailableError("503 UNAVAILABLE"), SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    standard = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "not AI-generated" in standard.text
    assert 'id="retry-personalize"' in standard.text
    retried = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "Suggested by AI" in retried.text
    assert "Rocket launch corner" in retried.text
    assert "not AI-generated" not in retried.text, "the standard ideas were replaced"
    assert_accessible(retried.text, page=False)


def test_a_limited_repeat_never_relabels_ai_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    again = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "You've made many AI requests" not in again.text, "no AI call was needed"
    assert "Your plan has been personalized" in again.text


def test_different_ideas_replace_the_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, DIFFERENT)
    csrf = token(client)
    url = create(client, csrf)
    assert 'id="new-ideas"' in client.get(url).text
    fragment = client.post(f"{url}/ideas", data={"csrf_token": csrf}, headers=HTMX)
    assert fragment.status_code == 200
    assert "<h3>Planet piñata</h3>" in fragment.text
    assert "<h3>Rocket launch corner</h3>" not in fragment.text
    assert "Here are different ideas" in fragment.text
    assert 'id="ideas-heading" tabindex="-1" autofocus' in fragment.text
    assert "hx-preserve" in fragment.text
    assert_accessible(fragment.text, page=False)


def test_different_ideas_without_javascript_return_to_the_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, ProviderUnavailableError("503"))
    csrf = token(client)
    url = create(client, csrf)
    response = client.post(f"{url}/ideas", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.headers["location"] == f"{url}#ideas"
    page = client.get(url)
    assert "your ideas are unchanged" in page.text
    assert "Rocket launch corner" in page.text
    assert "autofocus" not in page.text


def test_different_ideas_respect_the_ai_limit(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    fragment = client.post(f"{url}/ideas", data={"csrf_token": csrf}, headers=HTMX)
    assert "many AI requests in a short time, so your ideas are unchanged" in fragment.text


def test_standard_ideas_offer_no_different_ideas_button(engine: Engine) -> None:
    client = client_for(engine, ProviderUnavailableError("503"))
    csrf = token(client)
    url = create(client, csrf)
    page = client.get(url).text
    assert 'id="retry-personalize"' in page
    assert 'id="new-ideas"' not in page


# Change Manager


def test_change_preview_then_approve(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    preview = client.post(
        f"{url}/changes",
        data={"csrf_token": csrf, "request": "budget 12k, keep the cake"},
        headers=HTMX,
    )
    assert preview.status_code == 200
    assert "Approve this change" in preview.text
    assert "Protected, kept as it is" in preview.text
    assert "Interpreted by AI" in preview.text
    assert_accessible(preview.text, page=False)
    approved = client.post(
        f"{url}/proposals/{proposal_id(preview.text)}/approve",
        data={"csrf_token": csrf},
        headers=HTMX,
    )
    assert approved.headers["HX-Redirect"] == url
    page = client.get(url)
    assert "Change approved" in page.text
    assert "₹12,000" in page.text
    assert "budget 12k, keep the cake" in page.text, "logged in the history"
    history = page.text.split('id="history-heading"')[1]
    assert "Total budget ₹15,000 → ₹12,000" in history, "the history says what changed"


def test_change_without_javascript_renders_the_dashboard(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"})
    assert page.status_code == 200
    assert "Here is what would change" in page.text
    assert_accessible(page.text)


def test_approving_twice_or_stale_is_refused(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    first = proposal_id(
        client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "a"}, headers=HTMX).text
    )
    client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    again = client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    assert again.status_code == 409
    assert "already approved" in again.text

    second = proposal_id(
        client.post(
            f"{url}/changes/form", data={"csrf_token": csrf, "guest_count": "45"}, headers=HTMX
        ).text
    )
    client.post(f"{url}/tasks/task-1/done", data={"csrf_token": csrf}, headers=HTMX)
    stale = client.post(
        f"{url}/proposals/{second}/approve", data={"csrf_token": csrf}, headers=HTMX
    )
    assert stale.status_code == 409
    assert "changed after this preview" in stale.text


def test_only_the_newest_preview_can_be_approved(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    older = proposal_id(
        client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "a"}, headers=HTMX).text
    )
    client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "b"}, headers=HTMX)
    response = client.post(
        f"{url}/proposals/{older}/approve", data={"csrf_token": csrf}, headers=HTMX
    )
    assert response.status_code == 409
    assert "This preview is out of date" in response.text


def test_reject_leaves_the_plan_unchanged(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    before = client.get(url).text
    pid = proposal_id(
        client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "x"}, headers=HTMX).text
    )
    page = client.post(
        f"{url}/proposals/{pid}/reject", data={"csrf_token": csrf}, follow_redirects=True
    )
    assert "Change rejected. Your plan is unchanged." in page.text
    assert re.findall(r"<dd>₹[\d,]+</dd>", page.text) == re.findall(r"<dd>₹[\d,]+</dd>", before)
    again = client.post(
        f"{url}/proposals/{pid}/reject", data={"csrf_token": csrf}, follow_redirects=True
    )
    assert "already decided" in again.text


def test_rate_limited_page_without_javascript_keeps_ids_unique(engine: Engine) -> None:
    """The fallback form appears twice on this page (review, 23 Oct): labels must still work."""
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"})
    assert page.text.count("New number of guests") == 2
    assert_accessible(page.text)


def test_change_preview_shows_how_it_was_generated(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"})
    assert 'id="change-ai-sent"' in page.text, "the change has its own panel"
    assert 'id="plan-ai-sent"' in page.text, "next to the personalization panel"
    assert "interpret-change-v" in page.text
    assert_accessible(page.text)


def test_a_question_says_nothing_changed_and_how_to_answer(engine: Engine) -> None:
    question = ChangeIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        clarification_question="How many more guests are coming?",
        summary="More guests, number unknown.",
    )
    client = client_for(engine, SUGGESTIONS, question)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "more guests"})
    assert "How many more guests are coming?" in page.text
    assert views.QUESTION_NOTE in page.text
    assert 'href="#change-request"' in page.text
    assert 'id="change-ai-sent"' not in page.text, "nothing changed, so no generation panel"
    assert_accessible(page.text)


def test_rate_limited_change_offers_the_form(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    result = client.post(
        f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"}, headers=HTMX
    )
    assert "many AI requests" in result.text
    assert 'action="' + url + '/changes/form"' in result.text


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"total_budget_inr": "12,000", "protect_category": "cake"}, "Approve this change"),
        ({"set_category": "decor", "set_amount_inr": "5000"}, "Set to the amount you asked"),
        ({"set_category": "decor"}, "choose the line and enter its new amount"),
        ({}, "Fill in at least one change"),
        ({"guest_count": "lots"}, "whole numbers"),
        ({"guest_count": "0"}, "Must be at least 1."),
        ({"shift_days": "-90"}, "can&#39;t be in the past"),
    ],
)
def test_change_form_without_ai(engine: Engine, fields: dict[str, str], expected: str) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    result = client.post(f"{url}/changes/form", data={"csrf_token": csrf, **fields}, headers=HTMX)
    assert expected in result.text


def test_infeasible_change_explains_how_to_make_it_fit(engine: Engine) -> None:
    """Trimming other lines cannot help, so the preview says what would (review, 23 Oct)."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    client.post(f"{url}/categories/catering/protect", data={"csrf_token": csrf})
    client.post(f"{url}/categories/venue/protect", data={"csrf_token": csrf})
    result = client.post(
        f"{url}/changes/form", data={"csrf_token": csrf, "total_budget_inr": "2000"}, headers=HTMX
    )
    assert "be approved as it is" in result.text
    assert "Approve this change" not in result.text
    assert "ask for a total at least" in result.text
    assert "remove protection from Venue, Food and drinks first" in result.text
    assert "Suggest ways to close the gap" not in result.text
    assert_accessible(result.text, page=False)


# Direct edits


def test_add_expense_with_and_without_javascript(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    fragment = client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "cake",
            "label": "Rocket cake",
            "amount_inr": "1800",
        },
        headers=HTMX,
    )
    assert fragment.status_code == 200
    assert "Rocket cake" in fragment.text
    assert "Added Rocket cake" in fragment.text
    assert_accessible(fragment.text, page=False)
    page = client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "decor", "label": "Balloons", "amount_inr": "400"},
        follow_redirects=True,
    )
    assert "Added Balloons" in page.text


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"label": "", "amount_inr": "500"}, "This field is required."),
        ({"label": "Lights", "amount_inr": "0"}, "Must be at least 1."),
        ({"label": "Lights", "amount_inr": "99999"}, "twice the budget"),
    ],
)
def test_invalid_expense_is_shown_next_to_the_form(
    engine: Engine, fields: dict[str, str], message: str
) -> None:
    """Must-pass scenario 9 through HTTP: rejected with a clear message, input kept."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    data = {"csrf_token": csrf, "category_id": "decor", **fields}
    fragment = client.post(f"{url}/expenses", data=data, headers=HTMX)
    assert fragment.status_code == 422
    assert message in fragment.text
    assert 'aria-invalid="true"' in fragment.text
    page = client.post(f"{url}/expenses", data=data)
    assert page.status_code == 422
    assert_accessible(page.text)


def test_spending_past_the_total_shows_the_true_share(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "venue", "label": "Hall", "amount_inr": "18000"},
        follow_redirects=True,
    )
    assert "₹18,000 of ₹15,000 spent (120%)" in page.text, "not capped at 100%"


def test_over_budget_dashboard_offers_alternatives(engine: Engine) -> None:
    alternatives = Alternatives(
        summary="Trim decorations.",
        items=[
            AIAlternative(
                category_id="decor",
                action=SavingAction.REDUCE,
                level=SavingLevel.SMALL,
                title="Fewer balloons",
                why="Cheaper.",
            )
        ],
    )
    client = client_for(engine, SUGGESTIONS, alternatives)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
        follow_redirects=True,
    )
    catering = re.search(
        r"Food and drinks<progress[^>]*></progress></th>\s*<td[^>]*>₹([\d,]+)", page.text
    )
    assert catering, "the catering line is shown"
    gap = 16_000 - int(catering.group(1).replace(",", ""))
    assert f"You are over budget by ₹{gap:,}." in page.text, "the gap is catering's overspend"
    assert "Food and drinks costs more than planned" in page.text
    with_options = client.post(f"{url}/alternatives", data={"csrf_token": csrf})
    assert "Fewer balloons" in with_options.text
    assert 'id="alternatives-ai-sent"' in with_options.text
    assert_accessible(with_options.text)


def test_an_alternative_is_previewed_then_approved(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, DECOR_CUT)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
    )
    options = client.post(f"{url}/alternatives", data={"csrf_token": csrf}, headers=HTMX)
    still_short = re.search(r"still (₹[\d,]+) short", options.text)
    assert still_short, "the option says what gap would remain"
    apply = {"csrf_token": csrf, "category_id": "decor", "action": "reduce", "level": "medium"}

    preview = client.post(f"{url}/alternatives/apply", data=apply, headers=HTMX)
    assert "Here is what would change" in preview.text
    assert "Save on Decorations: reduce (medium)" in preview.text
    assert_accessible(preview.text, page=False)
    client.post(
        f"{url}/proposals/{proposal_id(preview.text)}/approve",
        data={"csrf_token": csrf},
        headers=HTMX,
    )
    page = client.get(url).text
    assert f"You are over budget by {still_short.group(1)}." in page

    no_js = client.post(f"{url}/alternatives/apply", data=apply, follow_redirects=True)
    assert "Here is what would change" in no_js.text
    assert_accessible(no_js.text)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"category_id": "decor", "action": "burn", "level": "small"}, "no longer available"),
        ({"category_id": "nope", "action": "reduce", "level": "small"}, "No budget category"),
    ],
)
def test_a_tampered_alternative_is_refused(
    engine: Engine, fields: dict[str, str], message: str
) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    answer = client.post(
        f"{url}/alternatives/apply", data={"csrf_token": csrf, **fields}, headers=HTMX
    )
    assert message in answer.text
    assert "Here is what would change" not in answer.text


def test_expense_update_protect_and_remove(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "cake", "label": "Cake", "amount_inr": "1800"},
    )
    updated = client.post(
        f"{url}/expenses/exp-1/update",
        data={"csrf_token": csrf, "amount_inr": "2000"},
        headers=HTMX,
    )
    assert "₹2,000" in updated.text
    protected = client.post(
        f"{url}/expenses/exp-1/protect", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Protected: changes will keep it as it is." in protected.text
    refused = client.post(f"{url}/expenses/exp-1/delete", data={"csrf_token": csrf}, headers=HTMX)
    assert refused.status_code == 422
    assert refused.headers["HX-Retarget"] == "#status-region"
    client.post(f"{url}/expenses/exp-1/unprotect", data={"csrf_token": csrf})
    removed = client.post(f"{url}/expenses/exp-1/delete", data={"csrf_token": csrf}, headers=HTMX)
    assert "No expenses yet." in removed.text
    assert (
        client.post(
            f"{url}/expenses/exp-1/update",
            data={"csrf_token": csrf, "amount_inr": "abc"},
            headers=HTMX,
        ).status_code
        == 422
    )


def test_tasks_vendors_and_protection(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    done = client.post(f"{url}/tasks/task-1/done", data={"csrf_token": csrf}, headers=HTMX)
    assert 'aria-pressed="true"' in done.text
    undone = client.post(f"{url}/tasks/task-1/undone", data={"csrf_token": csrf}, headers=HTMX)
    assert undone.status_code == 200
    contacted = client.post(
        f"{url}/vendors/vendor-1/status",
        data={"csrf_token": csrf, "status": "contacted"},
        headers=HTMX,
    )
    assert "Vendor status updated." in contacted.text
    assert (
        client.post(
            f"{url}/vendors/vendor-1/status",
            data={"csrf_token": csrf, "status": "maybe"},
            headers=HTMX,
        ).status_code
        == 422
    )
    category = client.post(
        f"{url}/categories/cake/protect", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Protected" in category.text
    assert (
        client.post(
            f"{url}/categories/cake/unprotect", data={"csrf_token": csrf}, headers=HTMX
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{url}/categories/cake/explode", data={"csrf_token": csrf}, headers=HTMX
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{url}/tasks/task-1/maybe", data={"csrf_token": csrf}, headers=HTMX
        ).status_code
        == 422
    )


# The user's own tasks and vendors


def test_own_task_and_vendor_are_added_listed_exported_and_removed(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    due = str(TODAY + timedelta(days=10))
    added = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Pick up the rangoli colours", "due_date": due},
        headers=HTMX,
    )
    assert added.status_code == 200
    assert "Added Pick up the rangoli colours to the timeline." in added.text
    task_id = re.search(r'id="remove-task-(task-\d+)"', added.text)
    assert task_id, "a task you added can be removed"
    assert added.text.count('id="remove-task-') == 1, "the plan's own tasks cannot"
    assert_accessible(added.text, page=False)

    vendor = client.post(
        f"{url}/vendors",
        data={"csrf_token": csrf, "name": "Puppet show", "book_by": str(TODAY + timedelta(days=5))},
        headers=HTMX,
    )
    assert 'Puppet show <span class="badge">Added by you</span>' in vendor.text
    vendor_id = re.search(r'id="remove-vendor-(vendor-\d+)"', vendor.text)
    assert vendor_id and vendor.text.count('id="remove-vendor-') == 1

    calendar = client.get(f"{url}/calendar.ics").text
    assert (
        "SUMMARY:Pick up the rangoli colours" in calendar and "SUMMARY:Book Puppet show" in calendar
    )

    gone = client.post(
        f"{url}/tasks/{task_id.group(1)}/remove", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Task removed." in gone.text and "Pick up the rangoli colours" not in gone.text
    gone = client.post(
        f"{url}/vendors/{vendor_id.group(1)}/remove", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Vendor removed." in gone.text and "Puppet show" not in gone.text


def test_own_task_without_javascript_redirects_with_a_message(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    response = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Call the pandit", "due_date": str(TODAY)},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == url
    page = client.get(url).text
    assert "Added Call the pandit to the timeline." in page and "Call the pandit" in page


def test_own_items_explain_bad_input_next_to_their_forms(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    empty = client.post(f"{url}/tasks", data={"csrf_token": csrf, "title": "", "due_date": ""})
    assert empty.status_code == 422
    assert 'id="t-title-error"' in empty.text and 'id="t-due_date-error"' in empty.text
    assert_accessible(empty.text)
    past = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Too late", "due_date": str(TODAY - timedelta(days=1))},
        headers=HTMX,
    )
    assert past.status_code == 422
    assert "Choose today or a later date." in past.text and 'value="Too late"' in past.text
    after = client.post(
        f"{url}/vendors",
        data={
            "csrf_token": csrf,
            "name": "Tent house",
            "book_by": str(TODAY + timedelta(days=41)),
        },
        headers=HTMX,
    )
    assert after.status_code == 422
    assert "Choose a date on or before the event day." in after.text
    nameless = client.post(
        f"{url}/vendors",
        data={"csrf_token": csrf, "name": " ", "book_by": str(TODAY)},
        headers=HTMX,
    )
    assert nameless.status_code == 422 and 'id="v-name-error"' in nameless.text
    assert_accessible(after.text, page=False)


def test_only_your_own_items_can_be_removed(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    catalog = client.post(f"{url}/tasks/task-1/remove", data={"csrf_token": csrf}, headers=HTMX)
    assert catalog.status_code == 422
    assert "Only tasks you added can be removed." in catalog.text
    vendor = client.post(f"{url}/vendors/vendor-1/remove", data={"csrf_token": csrf}, headers=HTMX)
    assert "Only vendors you added can be removed." in vendor.text
    stranger = client_for(engine)
    stranger_csrf = token(stranger)
    response = stranger.post(
        f"{url}/tasks",
        data={"csrf_token": stranger_csrf, "title": "Sneaky", "due_date": str(TODAY)},
        headers=HTMX,
    )
    assert response.status_code == 404


# Calendar export


def test_dashboard_offers_the_dates_as_a_calendar_file(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    url = create(client, token(client))
    assert f'href="{url}/calendar.ics"' in client.get(url).text
    response = client.get(f"{url}/calendar.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["content-disposition"] == 'attachment; filename="utsav-plan.ics"'
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    event_day = (TODAY + timedelta(days=40)).strftime("%Y%m%d")
    assert f"DTSTART;VALUE=DATE:{event_day}" in body
    assert body.count("BEGIN:VEVENT") > 1


def test_calendar_leaves_out_tasks_already_done(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    assert "-task-1@" in client.get(f"{url}/calendar.ics").text
    client.post(f"{url}/tasks/task-1/done", data={"csrf_token": csrf}, headers=HTMX)
    assert "-task-1@" not in client.get(f"{url}/calendar.ics").text


def test_dashboard_can_be_printed_with_status_text_instead_of_controls(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    page = client.get(create(client, token(client))).text
    assert '<script src="/static/js/print.js" defer></script>' in page
    assert 'id="print-plan" class="banner-button print-button" data-print hidden' in page
    assert '<span class="print-only">To contact</span>' in page


# Privacy


def test_another_session_cannot_see_or_change_my_event(engine: Engine) -> None:
    """Must-pass scenario 12 through HTTP."""
    mine = client_for(engine, SUGGESTIONS)
    url = create(mine, token(mine))
    intruder = TestClient(mine.app)
    csrf = token(intruder)
    assert intruder.get(url).status_code == 404
    assert intruder.get(f"{url}/calendar.ics").status_code == 404
    response = intruder.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "cake", "label": "x", "amount_inr": "1"},
    )
    assert response.status_code == 404
    assert (
        intruder.post(f"{url}/changes", data={"csrf_token": csrf, "request": "x"}).status_code
        == 404
    )


def test_event_routes_need_a_database(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    assert client.get("/events/abc").status_code == 503


def test_date_change_preview_shows_moved_deadlines(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    preview = client.post(
        f"{url}/changes/form",
        data={"csrf_token": csrf, "shift_days": "3", "guest_count": "35"},
        headers=HTMX,
    )
    assert "Event date: 1 Dec 2026 &rarr; 4 Dec 2026" in preview.text
    assert "Guests: 30 &rarr; 35" in preview.text
    assert "Deadlines that move" in preview.text


def test_alternatives_fall_back_honestly(engine: Engine) -> None:
    outage = ProviderUnavailableError("503 UNAVAILABLE")
    client = client_for(engine, SUGGESTIONS, outage, outage)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
    )
    options = client.post(f"{url}/alternatives", data={"csrf_token": csrf}, headers=HTMX)
    assert "Standard suggestions (not AI-generated)." in options.text


def test_unknown_event_type_is_rejected(engine: Engine) -> None:
    client = client_for(engine)
    page = client.post("/events", data=event_form(token(client), event_type="funeral"))
    assert page.status_code == 422
    assert "Choose the kind of event." in page.text


def test_database_work_per_request_stays_small(engine: Engine) -> None:
    """Each transaction is a new Neon connection in production (review, 23 Oct).

    A change request needs three: the rate-limit counters, reading the plan, saving the
    proposal; the AI call happens between them, outside any transaction.
    """
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    begins: list[int] = []
    event.listen(engine, "begin", lambda _connection: begins.append(1))
    client.get(url)
    assert len(begins) == 1, "the dashboard reads the plan and its history together"
    begins.clear()
    client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "x"}, headers=HTMX)
    assert len(begins) == 3
