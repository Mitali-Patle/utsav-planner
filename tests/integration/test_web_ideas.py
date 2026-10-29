"""AI personalization through HTTP: ideas, labelled fallbacks, retries and different ideas."""

from sqlalchemy import Engine

from app.ai.client import ProviderUnavailableError
from tests.integration.web import (
    HTMX,
    assert_accessible,
    client_for,
    create,
    token,
)
from tests.unit.test_services import DIFFERENT, SUGGESTIONS


def test_personalization_fragment_with_status_notice(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    fragment = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert fragment.status_code == 200
    assert 'id="plan-area"' in fragment.text
    assert "Rocket launch corner" in fragment.text
    assert 'hx-swap-oob="innerHTML"' in fragment.text
    assert "How this was generated" in fragment.text
    assert "hx-preserve" in fragment.text
    assert "hx-preserve" not in client.get(url).text
    assert_accessible(fragment.text, page=False)


def test_personalization_without_javascript_redirects(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    response = client.post(f"{url}/personalize", data={"csrf_token": csrf}, follow_redirects=True)
    assert "Your plan has been personalized" in response.text
    assert "Suggested by AI" in response.text
    assert_accessible(response.text)


def test_ai_outage_shows_labelled_standard_ideas(engine: Engine) -> None:
    outage = ProviderUnavailableError("503 UNAVAILABLE")
    client = client_for(engine, outage, outage)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    page = client.post(f"{url}/personalize", data={"csrf_token": csrf}, follow_redirects=True)
    assert "not AI-generated" in page.text


def test_standard_ideas_can_be_retried_with_ai(engine: Engine) -> None:
    """A fallback is not final: the user can ask again once a model is back (review, 23 Oct)."""
    client = client_for(engine, ProviderUnavailableError("503 UNAVAILABLE"), SUGGESTIONS)
    csrf = token(client)
    url = create(client, csrf, personalize=False)
    standard = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "not AI-generated" in standard.text
    assert 'id="retry-personalize"' in standard.text
    retried = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "Suggested by AI" in retried.text
    assert "Rocket launch corner" in retried.text
    assert "not AI-generated" not in retried.text, "the standard ideas were replaced"
    assert_accessible(retried.text, page=False)


def test_a_limited_repeat_never_relabels_ai_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    again = client.post(f"{url}/personalize", data={"csrf_token": csrf}, headers=HTMX)
    assert "You've made many AI requests" not in again.text, "no AI call was needed"
    assert "Your plan has been personalized" in again.text


def test_different_ideas_replace_the_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, DIFFERENT)
    csrf = token(client)
    url = create(client, csrf)
    assert 'id="new-ideas"' in client.get(url).text
    fragment = client.post(f"{url}/ideas", data={"csrf_token": csrf}, headers=HTMX)
    assert fragment.status_code == 200
    assert "<h3>Planet piñata</h3>" in fragment.text
    assert "<h3>Rocket launch corner</h3>" not in fragment.text
    assert "Here are different ideas" in fragment.text
    assert 'id="ideas-heading" tabindex="-1" autofocus' in fragment.text
    assert "hx-preserve" in fragment.text
    assert_accessible(fragment.text, page=False)


def test_different_ideas_without_javascript_return_to_the_ideas(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, ProviderUnavailableError("503"))
    csrf = token(client)
    url = create(client, csrf)
    response = client.post(f"{url}/ideas", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.headers["location"] == f"{url}#ideas"
    page = client.get(url)
    assert "your ideas are unchanged" in page.text
    assert "Rocket launch corner" in page.text
    assert "autofocus" not in page.text


def test_different_ideas_respect_the_ai_limit(engine: Engine) -> None:
    client = client_for(engine, SUGGESTIONS, rate_limit=1)
    csrf = token(client)
    url = create(client, csrf)
    fragment = client.post(f"{url}/ideas", data={"csrf_token": csrf}, headers=HTMX)
    assert "many AI requests in a short time, so your ideas are unchanged" in fragment.text


def test_standard_ideas_offer_no_different_ideas_button(engine: Engine) -> None:
    client = client_for(engine, ProviderUnavailableError("503"))
    csrf = token(client)
    url = create(client, csrf)
    page = client.get(url).text
    assert 'id="retry-personalize"' in page
    assert 'id="new-ideas"' not in page
