"""Helpers shared by the web integration tests: an app client, forms and an accessibility audit."""

import re
from datetime import date, timedelta
from html.parser import HTMLParser

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.config import Settings
from app.main import create_app
from app.services import build_services
from tests.fakes import FixedClock, ScriptedProvider

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
