"""Expenses, protection and over-budget help through HTTP, including ways to save."""

import re

import pytest
from sqlalchemy import Engine

from app.ai.client import ProviderUnavailableError
from app.ai.schemas import (
    AIAlternative,
    Alternatives,
)
from app.domain.savings import SavingAction, SavingLevel
from tests.integration.web import (
    HTMX,
    assert_accessible,
    client_for,
    create,
    proposal_id,
    token,
)
from tests.unit.test_services import DECOR_CUT, SUGGESTIONS


def test_add_expense_with_and_without_javascript(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    fragment = client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "cake",
            "label": "Rocket cake",
            "amount_inr": "1800",
        },
        headers=HTMX,
    )
    assert fragment.status_code == 200
    assert "Rocket cake" in fragment.text
    assert "Added Rocket cake" in fragment.text
    assert_accessible(fragment.text, page=False)
    page = client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "decor", "label": "Balloons", "amount_inr": "400"},
        follow_redirects=True,
    )
    assert "Added Balloons" in page.text


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"label": "", "amount_inr": "500"}, "This field is required."),
        ({"label": "Lights", "amount_inr": "0"}, "Must be at least 1."),
        ({"label": "Lights", "amount_inr": "99999"}, "twice the budget"),
    ],
)
def test_invalid_expense_is_shown_next_to_the_form(
    engine: Engine, fields: dict[str, str], message: str
) -> None:
    """Must-pass scenario 9 through HTTP: rejected with a clear message, input kept."""
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    data = {"csrf_token": csrf, "category_id": "decor", **fields}
    fragment = client.post(f"{url}/expenses", data=data, headers=HTMX)
    assert fragment.status_code == 422
    assert message in fragment.text
    assert 'aria-invalid="true"' in fragment.text
    page = client.post(f"{url}/expenses", data=data)
    assert page.status_code == 422
    assert_accessible(page.text)


def test_spending_past_the_total_shows_the_true_share(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "venue", "label": "Hall", "amount_inr": "18000"},
        follow_redirects=True,
    )
    assert "₹18,000 of ₹15,000 spent (120%)" in page.text, "not capped at 100%"


def test_over_budget_dashboard_offers_alternatives(engine: Engine) -> None:
    alternatives = Alternatives(
        summary="Trim decorations.",
        items=[
            AIAlternative(
                category_id="decor",
                action=SavingAction.REDUCE,
                level=SavingLevel.SMALL,
                title="Fewer balloons",
                why="Cheaper.",
            )
        ],
    )
    client = client_for(engine, SUGGESTIONS, alternatives)
    csrf = token(client)
    url = create(client, csrf)
    page = client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
        follow_redirects=True,
    )
    catering = re.search(
        r"Food and drinks<progress[^>]*></progress></th>\s*<td[^>]*>₹([\d,]+)", page.text
    )
    assert catering, "the catering line is shown"
    gap = 16_000 - int(catering.group(1).replace(",", ""))
    assert f"You are over budget by ₹{gap:,}." in page.text, "the gap is catering's overspend"
    expenses = page.text[page.text.index('id="expenses"') : page.text.index('id="timeline"')]
    assert 'id="over-budget"' in expenses and 'id="suggest-savings"' in expenses, "by the form"
    assert f'Over budget by ₹{gap:,}. <a href="#over-budget">' in page.text, "and a link up top"
    assert "Food and drinks costs more than planned" in page.text
    with_options = client.post(f"{url}/alternatives", data={"csrf_token": csrf})
    assert "Fewer balloons" in with_options.text
    assert 'id="alternatives-ai-sent"' in with_options.text
    assert_accessible(with_options.text)


def test_an_alternative_is_previewed_then_approved(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, DECOR_CUT)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
    )
    options = client.post(f"{url}/alternatives", data={"csrf_token": csrf}, headers=HTMX)
    still_short = re.search(r"still (₹[\d,]+) short", options.text)
    assert still_short, "the option says what gap would remain"
    apply = {"csrf_token": csrf, "category_id": "decor", "action": "reduce", "level": "medium"}

    preview = client.post(f"{url}/alternatives/apply", data=apply, headers=HTMX)
    assert "Here is what would change" in preview.text
    assert "Save on Decorations: cut it back" in preview.text
    assert_accessible(preview.text, page=False)
    client.post(
        f"{url}/proposals/{proposal_id(preview.text)}/approve",
        data={"csrf_token": csrf},
        headers=HTMX,
    )
    page = client.get(url).text
    assert f"You are over budget by {still_short.group(1)}." in page

    tampered = {**apply, "category_id": "venue", "action": "remove"}
    venue = client.post(f"{url}/alternatives/apply", data=tampered, headers=HTMX)
    assert "Save on Venue: cut it back sharply" in venue.text, "the venue is never removed"

    no_js = client.post(f"{url}/alternatives/apply", data=apply, follow_redirects=True)
    assert "Here is what would change" in no_js.text
    assert_accessible(no_js.text)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"category_id": "decor", "action": "burn", "level": "small"}, "no longer available"),
        ({"category_id": "nope", "action": "reduce", "level": "small"}, "No budget category"),
    ],
)
def test_a_tampered_alternative_is_refused(
    engine: Engine, fields: dict[str, str], message: str
) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    answer = client.post(
        f"{url}/alternatives/apply", data={"csrf_token": csrf, **fields}, headers=HTMX
    )
    assert message in answer.text
    assert "Here is what would change" not in answer.text


def test_expense_update_protect_and_remove(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={"csrf_token": csrf, "category_id": "cake", "label": "Cake", "amount_inr": "1800"},
    )
    updated = client.post(
        f"{url}/expenses/exp-1/update",
        data={"csrf_token": csrf, "amount_inr": "2000"},
        headers=HTMX,
    )
    assert "₹2,000" in updated.text
    protected = client.post(
        f"{url}/expenses/exp-1/protect", data={"csrf_token": csrf}, headers=HTMX
    )
    assert "Protected: changes will keep it as it is." in protected.text
    refused = client.post(f"{url}/expenses/exp-1/delete", data={"csrf_token": csrf}, headers=HTMX)
    assert refused.status_code == 422
    assert refused.headers["HX-Retarget"] == "#status-region"
    client.post(f"{url}/expenses/exp-1/unprotect", data={"csrf_token": csrf})
    removed = client.post(f"{url}/expenses/exp-1/delete", data={"csrf_token": csrf}, headers=HTMX)
    assert "No expenses yet." in removed.text
    assert (
        client.post(
            f"{url}/expenses/exp-1/update",
            data={"csrf_token": csrf, "amount_inr": "abc"},
            headers=HTMX,
        ).status_code
        == 422
    )


def test_alternatives_fall_back_honestly(engine: Engine) -> None:
    outage = ProviderUnavailableError("503 UNAVAILABLE")
    client = client_for(engine, SUGGESTIONS, outage, outage)
    csrf = token(client)
    url = create(client, csrf)
    client.post(
        f"{url}/expenses",
        data={
            "csrf_token": csrf,
            "category_id": "catering",
            "label": "Caterer",
            "amount_inr": "16000",
        },
    )
    options = client.post(f"{url}/alternatives", data={"csrf_token": csrf}, headers=HTMX)
    assert "Standard suggestions (not AI-generated)." in options.text
