"""Request safety: CSRF, body size, sessions, error pages, status messages and AI rate limits."""

import asyncio
import re
from collections.abc import Iterator, Sequence
from datetime import date

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from starlette.types import Message, Receive, Scope, Send

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.config import Settings
from app.domain.errors import (
    InfeasibleChangeError,
    InvalidInputError,
    NotFoundError,
    StaleProposalError,
)
from app.main import create_app
from app.persistence.db import make_engine
from app.persistence.rate_limits import Counter, RateLimiter
from app.persistence.tables import metadata
from app.services import build_services
from app.web.guard import MAX_BODY_BYTES, RequestGuardMiddleware
from app.web.limits import AIRateLimit, EventCreationLimit, client_ip
from app.web.session import flash
from tests.fakes import FixedClock, ScriptedProvider

TOKEN = re.compile(r'"X-CSRF-Token": "([^"]+)"')


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def build(settings: Settings, engine: Engine) -> FastAPI:
    app = create_app(settings, engine=engine)

    async def echo(request: Request) -> PlainTextResponse:
        form = await request.form()
        return PlainTextResponse(f"ok:{form.get('name', '')}")

    async def fail(kind: str) -> PlainTextResponse:
        errors = {
            "missing": NotFoundError("No such event."),
            "stale": StaleProposalError("Reload."),
            "bad": InvalidInputError("Bad amount."),
            "short": InfeasibleChangeError("This change doesn't fit the budget."),
        }
        raise errors[kind]

    async def remember(request: Request) -> PlainTextResponse:
        flash(request, "Saved your change.", "success")
        return PlainTextResponse("queued")

    app.add_api_route("/test/echo", echo, methods=["POST"])
    app.add_api_route("/test/fail/{kind}", fail, methods=["GET"])
    app.add_api_route("/test/flash", remember, methods=["GET"])
    return app


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    return TestClient(build(settings, engine))


def token_for(client: TestClient) -> str:
    match = TOKEN.search(client.get("/").text)
    assert match, "every page carries the CSRF token for htmx"
    return match.group(1)


# CSRF


def test_post_without_a_token_is_refused(client: TestClient) -> None:
    client.get("/")
    response = client.post("/test/echo", data={"name": "x"})
    assert response.status_code == 403
    assert "expired" in response.text


def test_post_with_the_header_token_is_accepted(client: TestClient) -> None:
    token = token_for(client)
    response = client.post("/test/echo", data={"name": "Priya"}, headers={"X-CSRF-Token": token})
    assert response.text == "ok:Priya", "the guard replays the body to the route"


def test_post_with_the_form_token_is_accepted(client: TestClient) -> None:
    token = token_for(client)
    assert client.post("/test/echo", data={"name": "Priya", "csrf_token": token}).text == "ok:Priya"


def test_wrong_token_or_new_session_is_refused(
    client: TestClient, settings: Settings, engine: Engine
) -> None:
    token = token_for(client)
    assert client.post("/test/echo", data={"csrf_token": token + "x"}).status_code == 403
    stranger = TestClient(build(settings, engine))
    assert stranger.post("/test/echo", data={"csrf_token": token}).status_code == 403


def test_safe_methods_are_not_checked(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200


# Body size


def test_oversized_body_is_refused(client: TestClient) -> None:
    token = token_for(client)
    response = client.post(
        "/test/echo", content=b"x" * (MAX_BODY_BYTES + 1), headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 413


def test_oversized_streamed_body_is_refused(client: TestClient) -> None:
    token = token_for(client)

    def chunks() -> Iterator[bytes]:
        for _ in range(40):
            yield b"x" * 1024

    response = client.post("/test/echo", content=chunks(), headers={"X-CSRF-Token": token})
    assert response.status_code == 413


# Sessions


def test_session_cookie_is_http_only_and_same_site(client: TestClient) -> None:
    cookie = client.get("/").headers["set-cookie"]
    assert cookie.startswith("utsav_session=")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "secure" not in cookie.lower()


def test_session_cookie_is_secure_in_production(engine: Engine) -> None:
    production = Settings(
        _env_file=None, app_env="production", database_url="sqlite://", session_secret="s" * 48
    )
    cookie = TestClient(create_app(production, engine=engine)).get("/").headers["set-cookie"]
    assert "secure" in cookie.lower()


def test_status_messages_show_once(client: TestClient) -> None:
    client.get("/test/flash")
    first = client.get("/").text
    assert "Saved your change." in first
    assert 'role="status"' in first
    assert "Saved your change." not in client.get("/").text


# Error pages


@pytest.mark.parametrize(
    ("kind", "status", "message"),
    [
        ("missing", 404, "No such event."),
        ("stale", 409, "Reload."),
        ("bad", 422, "Bad amount."),
        ("short", 422, "This change doesn&#39;t fit the budget."),
    ],
)
def test_domain_errors_become_friendly_pages(
    client: TestClient, kind: str, status: int, message: str
) -> None:
    page = client.get(f"/test/fail/{kind}")
    assert page.status_code == status
    assert message in page.text
    assert "<main" in page.text, "a full page"
    fragment = client.get(f"/test/fail/{kind}", headers={"HX-Request": "true"})
    assert fragment.status_code == status
    assert "<main" not in fragment.text, "a fragment for htmx"
    assert 'role="alert"' in fragment.text


def test_shutting_down_closes_the_ai_clients(settings: Settings, engine: Engine) -> None:
    class ClientHolder(ScriptedProvider):
        closed = False

        def close(self) -> None:
            self.closed = True

    holder = ClientHolder("groq:fake", [])
    services = build_services(
        settings, engine, FixedClock(date(2026, 10, 22)), ai=FallbackChain([holder], MemoryCache())
    )
    with TestClient(create_app(settings, engine=engine, services=services)):
        while_running = holder.closed
    assert (while_running, holder.closed) == (False, True)
    with TestClient(create_app(settings)):
        pass  # no database, so no services to close


def test_routes_need_a_database(settings: Settings) -> None:
    app = create_app(settings)
    assert app.state.services is None


# AI rate limits


def request_from(forwarded: str = "", host: str = "10.0.0.1") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (host, 50000)}
    )


def limit(engine: Engine, per_client: int = 2, daily: int = 100) -> AIRateLimit:
    settings = Settings(
        _env_file=None, app_env="test", ai_rate_limit_per_10min=per_client, ai_daily_soft_cap=daily
    )
    return AIRateLimit(RateLimiter(engine), settings, b"secret")


def test_ai_limit_per_client(engine: Engine) -> None:
    ai_limit = limit(engine)
    me, other = request_from("1.2.3.4"), request_from("5.6.7.8")
    assert [ai_limit.allow(me) for _ in range(3)] == [True, True, False]
    assert ai_limit.allow(other)


def test_ai_global_daily_cap(engine: Engine) -> None:
    ai_limit = limit(engine, per_client=100, daily=2)
    results = [ai_limit.allow(request_from(f"10.0.0.{i}")) for i in range(3)]
    assert results == [True, True, False]


def test_ai_limit_never_blocks_on_database_errors(engine: Engine) -> None:
    class BrokenLimiter:
        def hit_all(self, counters: Sequence[Counter]) -> bool:
            raise OperationalError("select", {}, Exception("down"))

    settings = Settings(_env_file=None, app_env="test")
    assert AIRateLimit(BrokenLimiter(), settings, b"s").allow(request_from("1.1.1.1"))
    assert AIRateLimit(None, settings, b"s").allow(request_from())


def test_event_creation_limit_counts_per_client_and_never_blocks_on_errors(
    engine: Engine,
) -> None:
    class BrokenLimiter:
        def hit_all(self, counters: Sequence[Counter]) -> bool:
            raise OperationalError("insert", {}, Exception("down"))

    settings = Settings(_env_file=None, app_env="test", event_create_limit_per_hour=1)
    limit = EventCreationLimit(RateLimiter(engine), settings, b"s")
    assert [limit.allow(request_from("1.1.1.1")) for _ in range(2)] == [True, False]
    assert limit.allow(request_from("2.2.2.2")), "another client has its own count"
    assert EventCreationLimit(BrokenLimiter(), settings, b"s").allow(request_from())
    assert EventCreationLimit(None, settings, b"s").allow(request_from())


def test_client_ip_prefers_the_first_forwarded_address() -> None:
    assert client_ip(request_from("203.0.113.9, 10.0.0.1")) == "203.0.113.9"
    assert client_ip(request_from()) == "10.0.0.1"


def test_guard_replays_the_body_once_then_passes_through() -> None:
    """An app that reads the request twice gets the body, then the real channel."""
    seen: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(await receive())
        seen.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    incoming: list[Message] = [
        {"type": "http.request", "body": b"csrf_token=t", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        return None

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "session": {"csrf": "t"},
    }
    asyncio.run(RequestGuardMiddleware(app)(scope, receive, send))
    assert seen == [
        {"type": "http.request", "body": b"csrf_token=t", "more_body": False},
        {"type": "http.disconnect"},
    ]
