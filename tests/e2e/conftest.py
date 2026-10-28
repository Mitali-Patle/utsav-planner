"""Browser tests against the real app in a background thread.

The app runs with a temporary SQLite file, a task-aware fake AI and no keys, so these
tests never reach Neon or a real model. Run them with `uv run pytest -m e2e`. They use
Playwright's Chromium; set `E2E_BROWSER_CHANNEL=chrome` to use an installed Chrome instead.
"""

import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from pydantic import BaseModel
from sqlalchemy import create_engine

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.ai.schemas import (
    AIAlternative,
    AIChangeOperation,
    Alternatives,
    ChangeIntent,
    IntentStatus,
    PlanSuggestions,
)
from app.config import Settings
from app.domain.budget import SavingAction, SavingLevel
from app.domain.change_set import ChangeKind
from app.main import create_app
from app.persistence.tables import metadata
from app.services import build_services
from tests.e2e.helpers import TODAY, VAGUE, NewPage
from tests.fakes import FixedClock, TaskAwareProvider
from tests.unit.test_services import DIFFERENT, SUGGESTIONS

CLARIFY = ChangeIntent(
    status=IntentStatus.NEEDS_CLARIFICATION,
    clarification_question="How many more guests are coming?",
    summary="The request needs a number.",
)
MORE_GUESTS = ChangeIntent(
    status=IntentStatus.OK,
    operations=[
        AIChangeOperation(kind=ChangeKind.ADJUST_GUEST_COUNT, delta=10),
        AIChangeOperation(kind=ChangeKind.PROTECT, category_id="cake"),
    ],
    summary="Ten more guests; keep the cake budget.",
)
SAVINGS = Alternatives(
    summary="Simplify the decorations.",
    items=[
        AIAlternative(
            category_id="decor",
            action=SavingAction.REDUCE,
            level=SavingLevel.LARGE,
            title="Paper decorations",
            why="They look festive and cost far less.",
        )
    ],
)


def _interpret(payload: str) -> ChangeIntent:
    return CLARIFY if VAGUE in payload.lower() else MORE_GUESTS


ANSWERS: dict[type[BaseModel], Callable[[str], BaseModel]] = {
    PlanSuggestions: lambda payload: DIFFERENT if '"previous_ideas"' in payload else SUGGESTIONS,
    ChangeIntent: _interpret,
    Alternatives: lambda _payload: SAVINGS,
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """URL of a live app with fake AI; development mode so it serves the static files."""
    database: Path = tmp_path_factory.mktemp("e2e") / "app.db"
    engine = create_engine(f"sqlite:///{database}")
    metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        app_env="development",
        ai_rate_limit_per_10min=1_000,
        event_create_limit_per_hour=1_000,
    )
    chain = FallbackChain([TaskAwareProvider("gemini:fake-e2e", ANSWERS)], MemoryCache())
    clock = FixedClock(TODAY)
    services = build_services(settings, engine, clock, ai=chain)
    app = create_app(settings, engine=engine, services=services, clock=clock)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the e2e server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
    engine.dispose()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        channel = os.environ.get("E2E_BROWSER_CHANNEL") or None
        launched = playwright.chromium.launch(channel=channel)
        yield launched
        launched.close()


@pytest.fixture
def new_page(browser: Browser, base_url: str) -> Iterator[NewPage]:
    """Open a page in a fresh browser context (its own session) with the given options."""
    contexts: list[BrowserContext] = []

    def open_page(**options: Any) -> Page:
        # Reduced motion keeps entrance animations from racing the checks.
        options = {"reduced_motion": "reduce", **options}
        context = browser.new_context(base_url=base_url, locale="en-IN", **options)
        contexts.append(context)
        return context.new_page()

    yield open_page
    for context in contexts:
        context.close()


@pytest.fixture
def page(new_page: NewPage) -> Page:
    return new_page()
