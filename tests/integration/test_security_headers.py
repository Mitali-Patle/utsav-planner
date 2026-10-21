"""Security headers on every app response."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.web.security import BASE_HEADERS


@pytest.mark.parametrize("path", ["/", "/healthz", "/does-not-exist"])
def test_security_headers_on_every_response(client: TestClient, path: str) -> None:
    response = client.get(path)
    for name, value in BASE_HEADERS.items():
        assert response.headers[name] == value


def test_csp_forbids_inline_scripts_and_framing(client: TestClient) -> None:
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_only_in_production(client: TestClient) -> None:
    assert "Strict-Transport-Security" not in client.get("/").headers
    production = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql://user:pass@localhost/db",
        session_secret="s" * 48,
    )
    response = TestClient(create_app(production)).get("/healthz")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
