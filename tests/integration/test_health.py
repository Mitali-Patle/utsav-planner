"""Health endpoint and environment-dependent API docs exposure."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
