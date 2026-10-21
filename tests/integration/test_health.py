"""Health endpoint and environment-dependent API docs exposure."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.persistence.db import make_engine


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_docs_hidden_in_production() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql://user:pass@localhost/db",
        session_secret="s" * 48,
    )
    client = TestClient(create_app(settings))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_api_docs_available_outside_production(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200


def test_readyz_ok_when_database_reachable(db_client: TestClient) -> None:
    response = db_client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_unavailable_without_database(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readyz_unavailable_hides_connection_details(settings: Settings) -> None:
    engine = make_engine("postgresql://user:secretpass@127.0.0.1:1/db?connect_timeout=2")
    response = TestClient(create_app(settings, engine=engine)).get("/readyz")
    assert response.status_code == 503
    assert "secretpass" not in response.text
    assert "127.0.0.1" not in response.text


def test_healthz_does_not_touch_database(settings: Settings) -> None:
    engine = make_engine("postgresql://user:secretpass@127.0.0.1:1/db?connect_timeout=2")
    response = TestClient(create_app(settings, engine=engine)).get("/healthz")
    assert response.status_code == 200
