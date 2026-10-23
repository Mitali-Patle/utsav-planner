"""User journeys in a real browser, with and without JavaScript."""

import re

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e.helpers import VAGUE, NewPage, create_event

pytestmark = pytest.mark.e2e


def request_change(page: Page, text: str) -> None:
    page.get_by_label("Your change").fill(text)
    page.locator("#change-manager > form").get_by_role("button", name="Preview the change").click()


def test_plan_personalize_change_and_approve(page: Page) -> None:
    create_event(page)
    expect(page.get_by_text("Suggested by AI, each with a reason.")).to_be_visible()
    expect(page.get_by_role("heading", name="Rocket launch corner")).to_be_visible()
    expect(page.locator("#status-region")).to_contain_text("personalized")

    request_change(page, "10 more guests are coming, keep the cake budget as it is")
    expect(page.get_by_role("heading", name="Here is what would change")).to_be_visible()
    expect(page.get_by_text("Kept as they are: Cake.")).to_be_visible()
    expect(page.get_by_text("Interpreted by AI (gemini:fake-e2e")).to_be_visible()

    page.get_by_role("button", name="Approve this change").click()
    expect(page.locator("#status-region")).to_contain_text("Your plan is now version 3")
    expect(page.locator(".event-header")).to_contain_text("50 guests")
    expect(page.locator("#history")).to_contain_text("Approved")


def test_typing_survives_the_personalization_swap(page: Page) -> None:
    held: list[Route] = []

    def hold(route: Route) -> None:
        held.append(route)

    page.route("**/personalize", hold)
    create_event(page)
    page.locator("#x-label").fill("Return gifts")
    page.locator("#x-amount_inr").fill("2500")
    assert held, "the personalization request should still be waiting"
    held[0].continue_()

    expect(page.locator("#status-region")).to_contain_text("personalized")
    expect(page.get_by_role("heading", name="Rocket launch corner")).to_be_visible()
    expect(page.locator("#x-label")).to_have_value("Return gifts")
    expect(page.locator("#x-amount_inr")).to_have_value("2500")
    expect(page.locator("#x-amount_inr")).to_be_focused()

    page.get_by_role("button", name="Add expense").click()
    expect(page.locator("#expenses")).to_contain_text("Return gifts")
    expect(page.locator("#x-label")).to_have_value("")


def test_every_step_works_without_javascript(new_page: NewPage) -> None:
    page = new_page(java_script_enabled=False)
    create_event(page)
    expect(page.get_by_role("heading", name="Personalizing your plan")).to_be_visible()
    page.get_by_role("button", name="Personalize with AI").click()
    expect(page.get_by_text("Suggested by AI, each with a reason.")).to_be_visible()

    page.get_by_text("Or use the change form (works without AI)").click()
    page.get_by_label("New number of guests").fill("45")
    page.locator(".change-form").get_by_role("button", name="Preview the change").click()
    expect(page.get_by_role("heading", name="Here is what would change")).to_be_visible()

    page.get_by_role("button", name="Approve this change").click()
    expect(page.locator("#status-region")).to_contain_text("Change approved")
    expect(page.locator(".event-header")).to_contain_text("45 guests")


def test_vague_request_asks_first_and_reject_keeps_the_plan(page: Page) -> None:
    create_event(page)
    expect(page.get_by_text("Suggested by AI, each with a reason.")).to_be_visible()

    request_change(page, f"{VAGUE} invite a few more people")
    expect(page.get_by_role("heading", name="One question first")).to_be_visible()
    expect(page.get_by_text("How many more guests are coming?", exact=True)).to_be_visible()

    request_change(page, "10 more guests are coming")
    page.get_by_role("button", name="Reject").click()
    expect(page.locator("#status-region")).to_contain_text("Your plan is unchanged")
    expect(page.locator(".event-header")).to_contain_text("40 guests")
    expect(page.locator("#history")).to_contain_text("Rejected")


def test_over_budget_shows_the_gap_and_priced_alternatives(page: Page) -> None:
    create_event(page)
    expect(page.get_by_text("Suggested by AI, each with a reason.")).to_be_visible()

    page.get_by_label("What is it?").fill("Party hall booking")
    page.get_by_label("Amount in rupees", exact=True).fill("70000")
    page.get_by_role("button", name="Add expense").click()
    expect(page.get_by_text("You are over budget by")).to_be_visible()
    expect(page.get_by_text("Venue costs more than planned")).to_be_visible()

    page.get_by_role("button", name="Suggest ways to save").click()
    expect(page.get_by_role("heading", name="Paper decorations")).to_be_visible()
    expect(page.locator("#plan-alternatives")).to_contain_text("Suggested by AI")
    option = page.locator("#plan-alternatives li", has_text="Paper decorations")
    remaining = re.search(r"still (₹[\d,]+) short", option.inner_text())
    assert remaining, "the option says what gap would remain"

    option.get_by_role("button", name="Preview this change").click()
    expect(page.get_by_role("heading", name="Here is what would change")).to_be_focused()
    page.get_by_role("button", name="Approve this change").click()
    expect(page.locator("#status-region")).to_contain_text("Change approved")
    expect(page.get_by_text(f"You are over budget by {remaining.group(1)}.")).to_be_visible()
