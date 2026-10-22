"""The app through HTTP: every flow with and without JavaScript, plus an accessibility check."""

import re
from collections.abc import Iterator
from datetime import date, timedelta
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.ai.client import ProviderUnavailableError
from app.ai.schemas import AIAlternative, Alternatives
from app.config import Settings
from app.domain.budget import SavingAction, SavingLevel
from app.main import create_app
from app.persistence.db import make_engine
from app.persistence.tables import metadata
from app.services import build_services
from tests.fakes import FixedClock, ScriptedProvider
from tests.unit.test_services import BUDGET_CUT, SUGGESTIONS

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


def client_for(engine: Engine, *answers: BaseModel | Exception, rate_limit: int = 20) -> TestClient:
    settings = Settings(_env_file=None, app_env="test", ai_rate_limit_per_10min=rate_limit)
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


def test_invalid_event_keeps_the_input_and_explains(engine: Engine) -> None:
    client = client_for(engine)
    csrf = token(client)
    page = client.post("/events", data=event_form(csrf, guest_count="0", title="Mehendi night"))
    assert page.status_code == 422
    assert 'value="Mehendi night"' in page.text
    assert 'aria-invalid="true"' in page.text
    assert 'aria-describedby="f-guest_count-error"' in page.text
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
    assert "Aarav&#39;s 8th birthday" in client.get("/").text, "listed under Your events"


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
    second = proposal_id(
        client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "b"}, headers=HTMX).text
    )
    client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    again = client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    assert again.status_code == 409
    assert "already approved" in again.text
    stale = client.post(
        f"{url}/proposals/{second}/approve", data={"csrf_token": csrf}, headers=HTMX
    )
    assert stale.status_code == 409
    assert "changed after this preview" in stale.text


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


def test_infeasible_change_offers_priced_alternatives(engine: Engine) -> None:
    alternatives = Alternatives(
        summary="Trim decorations.",
        items=[
            AIAlternative(
                category_id="decor",
                action=SavingAction.REDUCE,
                level=SavingLevel.LARGE,
                title="Paper decor",
                why="Cheaper.",
            )
        ],
    )
    client = client_for(engine, SUGGESTIONS, alternatives)
    csrf = token(client)
    url = create(client, csrf)
    client.post(f"{url}/categories/catering/protect", data={"csrf_token": csrf})
    client.post(f"{url}/categories/venue/protect", data={"csrf_token": csrf})
    result = client.post(
        f"{url}/changes/form", data={"csrf_token": csrf, "total_budget_inr": "2000"}, headers=HTMX
    )
    assert "be approved as it is" in result.text
    assert "Approve this change" not in result.text
    options = client.post(
        f"{url}/alternatives",
        data={"csrf_token": csrf, "proposal_id": proposal_id(result.text)},
        headers=HTMX,
    )
    assert "Paper decor" in options.text
    assert "saves about ₹" in options.text
    assert "Suggested by AI" in options.text


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
    assert "You are over budget by ₹1,000." in page.text
    with_options = client.post(f"{url}/alternatives", data={"csrf_token": csrf})
    assert "Fewer balloons" in with_options.text
    assert_accessible(with_options.text)


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


# Privacy


def test_another_session_cannot_see_or_change_my_event(engine: Engine) -> None:
    """Must-pass scenario 12 through HTTP."""
    mine = client_for(engine, SUGGESTIONS)
    url = create(mine, token(mine))
    intruder = TestClient(mine.app)
    csrf = token(intruder)
    assert intruder.get(url).status_code == 404
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
