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


def test_different_ideas_replace_the_ideas_and_keep_focus_nearby(page: Page) -> None:
    create_event(page)
    expect(page.get_by_role("heading", name="Rocket launch corner")).to_be_visible()
    page.get_by_role("button", name="Suggest different ideas").click()
    expect(page.get_by_role("heading", name="Planet piñata")).to_be_visible()
    expect(page.get_by_role("heading", name="Rocket launch corner")).to_have_count(0)
    expect(page.get_by_role("heading", name="Ideas for your event")).to_be_focused()
    expect(page.locator("#status-region")).to_contain_text("Here are different ideas")


def test_an_example_fills_the_form_in_one_click(page: Page) -> None:
    page.goto("/")
    expect(page.get_by_role("link", name="Fill in a sample community event")).to_be_hidden()
    page.get_by_label("Community event", exact=True).check()
    page.get_by_role("link", name="Fill in a sample community event").click()
    expect(page.get_by_label("Event name")).to_have_value("Shanti Nagar Ganeshotsav")
    expect(page.get_by_label("Procession or visarjan")).to_be_checked()
    page.get_by_role("button", name="Create my plan").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Shanti Nagar Ganeshotsav")


def test_my_plans_page_opens_a_plan_and_starts_a_new_one(page: Page) -> None:
    create_event(page, "Diwali at the society")
    page.get_by_role("link", name="My plans").click()
    expect(page.get_by_role("heading", name="My plans", level=1)).to_be_visible()
    page.get_by_role("link", name="Diwali at the society").click()
    expect(page.get_by_role("heading", name="Diwali at the society", level=1)).to_be_visible()
    page.get_by_role("link", name="My plans").click()
    page.get_by_role("link", name="New plan").click()
    expect(page.get_by_role("heading", name="Start a new plan")).to_be_visible()


def test_picture_panel_runs_and_can_be_paused(new_page: NewPage) -> None:
    page = new_page(reduced_motion="no-preference")
    page.goto("/")
    track = page.locator(".reel-track")
    expect(track).to_have_css("animation-play-state", "running")
    pause = page.get_by_role("button", name="Pause pictures")
    expect(pause).to_have_css("opacity", "0")  # out of sight until reached by keyboard
    pause.focus()
    expect(pause).to_have_css("opacity", "1")
    page.keyboard.press("Enter")
    page.get_by_label("Event name").focus()
    expect(track).to_have_css("animation-play-state", "paused")
    expect(page.get_by_role("button", name="Play pictures")).to_be_attached()


def test_picture_panel_stays_still_with_reduced_motion(page: Page) -> None:
    page.goto("/")
    expect(page.get_by_role("button", name="Pause pictures")).to_be_hidden()
    expect(page.locator(".reel")).not_to_have_class("reel is-running")


def test_theme_switch_cycles_and_is_remembered(new_page: NewPage) -> None:
    page = new_page(color_scheme="dark")
    page.goto("/")
    switch = page.get_by_role("button", name=re.compile("^Theme: Auto"))
    body = page.locator("body")
    dark = page.evaluate("getComputedStyle(document.body).backgroundColor")
    switch.click()
    expect(page.get_by_role("button", name=re.compile("^Theme: Light"))).to_be_visible()
    # Retrying CSS assertions, not wait_for_function: its polling evaluates a string,
    # which the page's CSP (no 'unsafe-eval') blocks whenever the first check fails.
    expect(body).not_to_have_css("background-color", dark)
    page.reload()
    expect(page.get_by_role("button", name=re.compile("^Theme: Light"))).to_be_visible()
    expect(body).not_to_have_css("background-color", dark)
    page.get_by_role("button", name=re.compile("^Theme: Light")).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(body).to_have_css("background-color", dark)


def test_section_menu_marks_the_section_in_view(new_page: NewPage) -> None:
    page = new_page(viewport={"width": 1280, "height": 800})
    create_event(page)
    page.get_by_role("navigation", name="Plan sections").get_by_role("link", name="Vendors").click()
    expect(page.locator(".section-nav a[href='#vendors']")).to_have_attribute(
        "aria-current", "location"
    )
    # The rail runs the height of the window and fills up to the section in view.
    expect(page.locator(".section-nav a.is-past")).to_have_count(4)
    rail = page.locator(".section-nav").bounding_box()
    assert rail is not None
    assert rail["height"] > 600, "the window less the pinned header"


def test_timeline_downloads_as_a_calendar_file(page: Page) -> None:
    create_event(page)
    with page.expect_download() as download:
        page.get_by_role("link", name="Download all dates (.ics)").click()
    assert download.value.suggested_filename == "utsav-plan.ics"
    assert download.value.path().read_text(encoding="utf-8").startswith("BEGIN:VCALENDAR")
    google = page.get_by_role("link", name="Add to Google Calendar")
    expect(google).to_have_attribute("target", "_blank")
    href = google.get_attribute("href") or ""
    assert href.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE")
    assert "text=Aarav+turns+5" in href and "location=Indore" in href


def test_print_shows_the_plan_without_controls_in_ink_on_white(new_page: NewPage) -> None:
    page = new_page(color_scheme="dark")
    page.add_init_script("window.print = () => { window.printed = true; };")
    create_event(page)
    expect(page.get_by_role("heading", name="Rocket launch corner")).to_be_visible()
    page.get_by_role("button", name="Print or save as PDF").click()
    assert page.evaluate("window.printed") is True

    page.emulate_media(media="print")
    expect(page.get_by_role("heading", name="Timeline")).to_be_visible()
    expect(page.get_by_role("cell", name="To contact").first).to_be_visible()
    for hidden in ("Add expense", "Suggest different ideas", "Print or save as PDF"):
        expect(page.get_by_role("button", name=hidden)).to_be_hidden()
    expect(page.get_by_role("heading", name="Change something")).to_be_hidden()
    colours = page.evaluate(
        "[getComputedStyle(document.body).backgroundColor, getComputedStyle(document.body).color]"
    )
    assert colours == ["rgb(255, 255, 255)", "rgb(0, 0, 0)"]


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
    expect(page.get_by_role("button", name="Print or save as PDF")).to_be_hidden()
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
