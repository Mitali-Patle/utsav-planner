"""Creating plans and the pages around them: examples, validation, sessions and access."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event

from app.config import Settings
from app.main import create_app
from app.web.views.landing import CELEBRATION_SLIDES, example_form
from tests.integration.web import (
    HTMX,
    TODAY,
    assert_accessible,
    client_for,
    create,
    event_form,
    token,
)
from tests.unit.test_services import BUDGET_CUT, SUGGESTIONS


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
    values = example_form(example, TODAY).values
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
    assert page.count('class="reel-slide">') == len(CELEBRATION_SLIDES)
    assert page.count('class="reel-slide" aria-hidden="true"') == len(CELEBRATION_SLIDES)
    assert '<script src="/static/js/reel.js" defer></script>' in page


def test_every_celebration_photo_exists_is_small_and_described(engine: Engine) -> None:
    folder = Path(__file__).resolve().parents[2] / "public" / "static" / "img" / "celebrations"
    page = client_for(engine).get("/").text
    assert "<figcaption" not in page, "the photos carry no titles"
    for slide in CELEBRATION_SLIDES:
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
