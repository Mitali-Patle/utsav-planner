"""Tasks, vendors and your own items through HTTP, with the calendar file and printing."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import Engine

from tests.integration.web import (
    HTMX,
    TODAY,
    assert_accessible,
    client_for,
    create,
    token,
)
from tests.unit.test_services import SUGGESTIONS


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


def test_next_up_lists_the_pressing_tasks_and_moves_on_when_one_is_done(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    page = client.get(url).text
    next_up = page[page.index('id="next-up"') : page.index('id="budget"')]
    assert next_up.count('<span class="next-what">') == 4, "three tasks and one vendor"
    assert "Book: " in next_up
    first = re.search(r'<span class="next-what">([^<]+)</span>', next_up)
    assert first
    task = re.search(
        rf'<span class="task-title">{re.escape(first.group(1))}</span>.*?/tasks/(task-\d+)/done',
        page,
        re.S,
    )
    assert task
    after = client.post(
        f"{url}/tasks/{task.group(1)}/done", data={"csrf_token": csrf}, headers=HTMX
    )
    moved = after.text[after.text.index('id="next-up"') : after.text.index('id="budget"')]
    assert f'<span class="next-what">{first.group(1)}</span>' not in moved
    assert '<a href="#next-up">Next up</a>' in page
    for form in ("add-expense-form", "add-task-form", "add-vendor-form"):
        tag = page[page.index(f'id="{form}"') :].split(">", 1)[0]
        assert 'hx-sync="this:drop"' in tag, f"a double click must not add twice ({form})"
    assert_accessible(page)


def test_budget_is_labelled_as_estimates_and_vendors_come_with_questions(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    page = client.get(url).text
    assert "Planned amounts are estimates from Indian planning guides, not vendor quotes." in page
    assert page.count('class="vendor-questions no-print"') >= 5
    assert "Is the quote a hall rental or per plate, and is GST included?" in page
    own = client.post(
        f"{url}/vendors",
        data={"csrf_token": csrf, "name": "Puppet show", "book_by": str(TODAY)},
        headers=HTMX,
    ).text
    row = own[own.index("Puppet show") : own.index("Puppet show") + 1500]
    assert "Questions to ask" not in row.split("</tr>")[0], "no standard questions for your own"


def test_own_task_and_vendor_are_added_listed_exported_and_removed(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    due = str(TODAY + timedelta(days=10))
    added = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Pick up the rangoli colours", "due_date": due},
        headers=HTMX,
    )
    assert added.status_code == 200
    assert "Added Pick up the rangoli colours to the timeline." in added.text
    task_id = re.search(r'id="remove-task-(task-\d+)"', added.text)
    assert task_id, "a task you added can be removed"
    assert added.text.count('id="remove-task-') == 1, "the plan's own tasks cannot"
    assert_accessible(added.text, page=False)

    vendor = client.post(
        f"{url}/vendors",
        data={"csrf_token": csrf, "name": "Puppet show", "book_by": str(TODAY + timedelta(days=5))},
        headers=HTMX,
    )
    assert 'Puppet show <span class="badge">Added by you</span>' in vendor.text
    vendor_id = re.search(r'id="remove-vendor-(vendor-\d+)"', vendor.text)
    assert vendor_id and vendor.text.count('id="remove-vendor-') == 1

    calendar = client.get(f"{url}/calendar.ics").text
    assert (
        "SUMMARY:Pick up the rangoli colours" in calendar and "SUMMARY:Book Puppet show" in calendar
    )

    gone = client.post(
        f"{url}/tasks/{task_id.group(1)}/remove", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Task removed." in gone.text and "Pick up the rangoli colours" not in gone.text
    gone = client.post(
        f"{url}/vendors/{vendor_id.group(1)}/remove", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Vendor removed." in gone.text and "Puppet show" not in gone.text


def test_own_task_added_while_personalizing_is_kept(engine: Engine) -> None:
    """CI race (28 Oct): a task added before personalization lands must survive it."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Pick up the rangoli colours", "due_date": str(TODAY)},
        headers=HTMX,
    )
    personalized = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "Rocket launch corner" in personalized.text, "personalization still applied"
    assert "Pick up the rangoli colours" in personalized.text, "and the user's task kept"


def test_own_task_without_javascript_redirects_with_a_message(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    response = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Call the pandit", "due_date": str(TODAY)},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == url
    page = client.get(url).text
    assert "Added Call the pandit to the timeline." in page and "Call the pandit" in page


@pytest.mark.parametrize(
    ("limit", "path", "form"),
    [
        (
            "app.domain.expenses.MAX_EXPENSES",
            "expenses",
            {"category_id": "venue", "label": "Hall", "amount_inr": "500"},
        ),
        ("app.domain.timeline.MAX_TASKS", "tasks", {"title": "Call the pandit", "due_date": ""}),
        ("app.domain.vendors.MAX_PRIORITY", "vendors", {"name": "Puppet show", "book_by": ""}),
    ],
)
def test_a_full_plan_is_a_notice_not_an_error_under_a_field(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, limit: str, path: str, form: dict[str, str]
) -> None:
    """Review, 29 Oct: "at most 200 tasks" used to appear under the task's date field."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    monkeypatch.setattr(limit, 0)
    dated = {key: value or str(TODAY) for key, value in form.items()}
    full = client.post(f"{url}/{path}", data={"csrf_token": csrf, **dated}, headers=HTMX)
    assert full.status_code == 422
    assert full.headers["HX-Retarget"] == "#status-region"
    assert "at most 0" in full.text and "field-error" not in full.text

    page = client.post(f"{url}/{path}", data={"csrf_token": csrf, **dated})
    assert page.status_code == 422 and "at most 0" in page.text, "and without JavaScript"


def test_own_items_explain_bad_input_next_to_their_forms(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    empty = client.post(f"{url}/tasks", data={"csrf_token": csrf, "title": "", "due_date": ""})
    assert empty.status_code == 422
    assert 'id="t-title-error"' in empty.text and 'id="t-due_date-error"' in empty.text
    assert_accessible(empty.text)
    past = client.post(
        f"{url}/tasks",
        data={"csrf_token": csrf, "title": "Too late", "due_date": str(TODAY - timedelta(days=1))},
        headers=HTMX,
    )
    assert past.status_code == 422
    assert "Choose today or a later date." in past.text and 'value="Too late"' in past.text
    after = client.post(
        f"{url}/vendors",
        data={
            "csrf_token": csrf,
            "name": "Tent house",
            "book_by": str(TODAY + timedelta(days=41)),
        },
        headers=HTMX,
    )
    assert after.status_code == 422
    assert "Choose a date on or before the event day." in after.text
    nameless = client.post(
        f"{url}/vendors",
        data={"csrf_token": csrf, "name": " ", "book_by": str(TODAY)},
        headers=HTMX,
    )
    assert nameless.status_code == 422 and 'id="v-name-error"' in nameless.text
    assert_accessible(after.text, page=False)


def test_only_your_own_items_can_be_removed(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    catalog = client.post(f"{url}/tasks/task-1/remove", data={"csrf_token": csrf}, headers=HTMX)
    assert catalog.status_code == 422
    assert "Only tasks you added can be removed." in catalog.text
    vendor = client.post(f"{url}/vendors/vendor-1/remove", data={"csrf_token": csrf}, headers=HTMX)
    assert "Only vendors you added can be removed." in vendor.text
    stranger = client_for(engine)
    stranger_csrf = token(stranger)
    response = stranger.post(
        f"{url}/tasks",
        data={"csrf_token": stranger_csrf, "title": "Sneaky", "due_date": str(TODAY)},
        headers=HTMX,
    )
    assert response.status_code == 404


def test_dashboard_offers_the_dates_as_a_calendar_file(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    url = create(client, token(client))
    assert f'href="{url}/calendar.ics"' in client.get(url).text
    response = client.get(f"{url}/calendar.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["content-disposition"] == 'attachment; filename="utsav-plan.ics"'
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    event_day = (TODAY + timedelta(days=40)).strftime("%Y%m%d")
    assert f"DTSTART;VALUE=DATE:{event_day}" in body
    assert body.count("BEGIN:VEVENT") > 1


def test_calendar_leaves_out_tasks_already_done(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    assert "-task-1@" in client.get(f"{url}/calendar.ics").text
    client.post(f"{url}/tasks/task-1/done", data={"csrf_token": csrf}, headers=HTMX)
    assert "-task-1@" not in client.get(f"{url}/calendar.ics").text


def test_dashboard_can_be_printed_with_status_text_instead_of_controls(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    page = client.get(create(client, token(client))).text
    assert '<script src="/static/js/print.js" defer></script>' in page
    assert 'id="print-plan" class="banner-button print-button" data-print hidden' in page
    assert '<span class="print-only">To contact</span>' in page
