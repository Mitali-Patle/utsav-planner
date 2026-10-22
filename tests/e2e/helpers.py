"""Shared steps and data for the browser tests."""

from collections.abc import Callable
from datetime import date, timedelta

from playwright.sync_api import Page

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
