"""The Change Manager through HTTP: previews, approval, rejection, questions and the form."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import Engine

from app.ai.schemas import (
    AIChangeOperation,
    ChangeIntent,
    IntentStatus,
)
from app.domain.change_set import ChangeKind
from app.web.views.proposals import QUESTION_NOTE
from tests.integration.web import (
    HTMX,
    TODAY,
    assert_accessible,
    client_for,
    create,
    proposal_id,
    token,
)
from tests.unit.test_services import BUDGET_CUT, SUGGESTIONS, intent


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
    history = page.text.split('id="history-heading"')[1]
    assert "Total budget ₹15,000 → ₹12,000" in history, "the history says what changed"


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
    client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    again = client.post(f"{url}/proposals/{first}/approve", data={"csrf_token": csrf}, headers=HTMX)
    assert again.status_code == 409
    assert "already approved" in again.text

    second = proposal_id(
        client.post(
            f"{url}/changes/form", data={"csrf_token": csrf, "guest_count": "45"}, headers=HTMX
        ).text
    )
    client.post(f"{url}/tasks/task-1/done", data={"csrf_token": csrf}, headers=HTMX)
    stale = client.post(
        f"{url}/proposals/{second}/approve", data={"csrf_token": csrf}, headers=HTMX
    )
    assert stale.status_code == 409
    assert "changed after this preview" in stale.text


def test_only_the_newest_preview_can_be_approved(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    older = proposal_id(
        client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "a"}, headers=HTMX).text
    )
    client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "b"}, headers=HTMX)
    response = client.post(
        f"{url}/proposals/{older}/approve", data={"csrf_token": csrf}, headers=HTMX
    )
    assert response.status_code == 409
    assert "This preview is out of date" in response.text


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


def test_rate_limited_page_without_javascript_keeps_ids_unique(engine: Engine) -> None:
    """The fallback form appears twice on this page (review, 23 Oct): labels must still work."""
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"})
    assert page.text.count("New number of guests") == 2
    assert_accessible(page.text)


def test_change_preview_lists_expense_changes_and_new_tasks(engine: Engine) -> None:
    expense_and_task = intent(
        AIChangeOperation(
            kind=ChangeKind.ADD_EXPENSE, category_id="cake", label="Cake advance", amount_inr=800
        ),
        AIChangeOperation(kind=ChangeKind.ADD_TASK, label="Collect the cake stand", days=1),
    )
    client = client_for(engine, SUGGESTIONS, expense_and_task)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(
        f"{url}/changes", data={"csrf_token": csrf, "request": "cake advance and a reminder"}
    ).text
    assert "<h4>Expenses</h4>" in page and "Cake advance: added, now ₹800" in page
    due = (TODAY + timedelta(days=39)).strftime("%-d %b %Y")
    assert "<h4>New tasks</h4>" in page
    assert f"Collect the cake stand, by {due} (Added by you)" in page
    assert_accessible(page)


def test_change_preview_shows_how_it_was_generated(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, BUDGET_CUT)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "budget 12k"})
    assert 'id="change-ai-sent"' in page.text, "the change has its own panel"
    assert 'id="plan-ai-sent"' in page.text, "next to the personalization panel"
    assert "interpret-change-v" in page.text
    assert_accessible(page.text)


def test_a_question_says_nothing_changed_and_how_to_answer(engine: Engine) -> None:
    question = ChangeIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        clarification_question="How many more guests are coming?",
        summary="More guests, number unknown.",
    )
    client = client_for(engine, SUGGESTIONS, question)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(f"{url}/changes", data={"csrf_token": csrf, "request": "more guests"})
    assert "How many more guests are coming?" in page.text
    assert QUESTION_NOTE in page.text
    assert 'href="#change-request"' in page.text
    assert 'id="change-ai-sent"' not in page.text, "nothing changed, so no generation panel"
    assert_accessible(page.text)


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
        ({"set_category": "decor", "set_amount_inr": "5000"}, "Set to the amount you asked"),
        ({"set_category": "decor"}, "choose the line and enter its new amount"),
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


def test_infeasible_change_explains_how_to_make_it_fit(engine: Engine) -> None:
    """Trimming other lines cannot help, so the preview says what would (review, 23 Oct)."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    client.post(f"{url}/categories/catering/protect", data={"csrf_token": csrf})
    client.post(f"{url}/categories/venue/protect", data={"csrf_token": csrf})
    result = client.post(
        f"{url}/changes/form", data={"csrf_token": csrf, "total_budget_inr": "2000"}, headers=HTMX
    )
    assert "be approved as it is" in result.text
    assert "Approve this change" not in result.text
    assert "ask for a total at least" in result.text
    assert "remove protection from Venue, Food and drinks first" in result.text
    assert "Suggest ways to close the gap" not in result.text
    assert_accessible(result.text, page=False)


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
