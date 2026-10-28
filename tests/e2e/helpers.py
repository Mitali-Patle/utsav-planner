"""Shared steps and data for the browser tests."""

from collections.abc import Callable
from datetime import date, timedelta

from playwright.sync_api import Page, expect

TODAY = date(2026, 10, 22)
EVENT_DATE = (TODAY + timedelta(days=60)).isoformat()
NewPage = Callable[..., Page]
"""Opens a page in a fresh browser context; keyword arguments are context options."""

VAGUE = "maybe"
"""A change request containing this word gets a clarifying question from the fake AI."""


def create_event(page: Page, title: str = "Aarav turns 5") -> None:
    """Fill in and submit the new-event form for a 40-guest birthday."""
    page.goto("/")
    page.get_by_label("Birthday", exact=True).check()
    page.get_by_label("Event name").fill(title)
    page.get_by_label("City").fill("Indore")
    page.get_by_label("Event date").fill(EVENT_DATE)
    page.get_by_label("Number of guests").fill("40")
    page.get_by_label("Total budget in rupees").fill("60000")
    page.get_by_role("button", name="Create my plan").click()
    page.wait_for_url("**/events/*")


def wait_for_personalization(page: Page) -> None:
    """Wait until the AI's plan is in and htmx has wired up its forms.

    The new plan's text appears as soon as htmx swaps it in, but its forms only become htmx
    forms when the swap settles a moment later; a click in between would post the form
    without JavaScript and reload the page.
    """
    expect(page.get_by_text("Suggested by AI, each with a reason.")).to_be_visible()
    expect(page.locator(".htmx-settling, .htmx-swapping, .htmx-request")).to_have_count(0)
