"""Accessibility in a real browser: axe rules, phone width, keyboard and a clean console."""

from collections.abc import Callable
from functools import cache
from importlib.resources import files
from typing import Any

import pytest
from playwright.sync_api import ConsoleMessage, Page, expect

from tests.e2e.helpers import NewPage, create_event, wait_for_personalization

pytestmark = pytest.mark.e2e

WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _landing(page: Page) -> None:
    page.goto("/")


def _landing_errors(page: Page) -> None:
    page.goto("/")
    page.get_by_role("button", name="Create my plan").click()
    expect(page.get_by_text("Please check the highlighted fields.")).to_be_visible()


def _dashboard(page: Page) -> None:
    create_event(page)
    wait_for_personalization(page)


def _change_preview(page: Page) -> None:
    _dashboard(page)
    page.get_by_label("Your change").fill("10 more guests are coming")
    page.locator("#change-manager > form").get_by_role("button", name="Preview the change").click()
    expect(page.get_by_role("heading", name="Here is what would change")).to_be_visible()


def _over_budget(page: Page) -> None:
    _dashboard(page)
    page.get_by_label("What is it?").fill("Party hall booking")
    page.get_by_label("Amount in rupees", exact=True).fill("70000")
    page.get_by_role("button", name="Add expense").click()
    page.get_by_role("button", name="Suggest ways to save").click()
    expect(page.get_by_role("heading", name="Paper decorations")).to_be_visible()


def _not_found(page: Page) -> None:
    page.goto("/events/does-not-exist")


STATES: dict[str, Callable[[Page], None]] = {
    "landing": _landing,
    "landing with errors": _landing_errors,
    "dashboard": _dashboard,
    "change preview": _change_preview,
    "over budget with alternatives": _over_budget,
    "not found": _not_found,
}


@cache
def _axe_source() -> str:
    return (files("axe_playwright_python") / "axe.min.js").read_text(encoding="utf-8")


def axe_violations(page: Page) -> list[str]:
    """Run axe-core's WCAG 2.2 A/AA rules and describe each violation.

    Collapsed `<details>` panels are opened first, because axe skips hidden content.
    """
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
    page.evaluate(_axe_source())
    results: dict[str, Any] = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS
    )
    return [
        f"{v['id']} ({v['impact']}): {v['help']} at {[n['target'] for n in v['nodes']][:3]}"
        for v in results["violations"]
    ]


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", STATES)
def test_no_axe_violations(new_page: NewPage, state: str, scheme: str, width: int) -> None:
    """Phone and desktop layouts differ (tables become cards), so both are scanned."""
    page = new_page(color_scheme=scheme, viewport={"width": width, "height": 900})
    STATES[state](page)
    assert axe_violations(page) == []


@pytest.mark.parametrize("state", STATES)
def test_no_horizontal_scrolling_at_320px(new_page: NewPage, state: str) -> None:
    page = new_page(viewport={"width": 320, "height": 800})
    STATES[state](page)
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0


def test_skip_link_is_first_and_moves_focus_to_main(page: Page) -> None:
    page.goto("/")
    page.keyboard.press("Tab")
    skip = page.get_by_role("link", name="Skip to main content")
    expect(skip).to_be_focused()
    expect(skip).to_be_visible()
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()


def test_keyboard_focus_survives_plan_updates(page: Page) -> None:
    """After Mark done, focus stays on the same button instead of falling to the page top."""
    _dashboard(page)
    button = page.locator("#timeline button").first
    button_id = button.get_attribute("id")
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#status-region")).to_contain_text("Task done.")
    expect(page.locator(f"#{button_id}")).to_be_focused()
    expect(page.locator(f"#{button_id}")).to_have_attribute("aria-pressed", "true")


def test_change_preview_is_announced_and_focused(page: Page) -> None:
    _dashboard(page)
    page.get_by_label("Your change").fill("10 more guests are coming")
    page.get_by_label("Your change").press("Tab")
    page.keyboard.press("Enter")
    expect(page.locator("#change-result-heading")).to_be_focused()
    expect(page.locator("#status-region")).to_contain_text("Here is what would change")


def test_console_is_clean_in_every_state(page: Page) -> None:
    """No script errors or CSP violations; the 404 and 422 pages may log their status."""
    problems: list[str] = []

    def record(message: ConsoleMessage) -> None:
        expected_status = "Failed to load resource" in message.text
        if message.type in {"error", "warning"} and not expected_status:
            problems.append(message.text)

    page.on("console", record)
    page.on("pageerror", lambda error: problems.append(str(error)))
    for build in STATES.values():
        build(page)
    assert problems == []
