"""Landing page rendering, base-template accessibility contract and static files."""

import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_home_renders_html(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_base_template_accessibility_contract(client: TestClient) -> None:
    html = client.get("/").text
    assert '<html lang="en-IN">' in html
    assert '<a href="#main" class="skip-link">' in html
    assert '<main id="main"' in html
    for landmark in ("<header", "<nav", "<footer"):
        assert landmark in html
    assert 'aria-live="polite"' in html


def test_base_template_has_description_and_icon(client: TestClient) -> None:
    html = client.get("/").text
    assert re.search(r'<meta name="description" content="[^"]{20,}">', html)
    assert '<link rel="icon" type="image/svg+xml" href="/static/img/favicon.svg">' in html


def test_favicon_ico_redirects_to_svg(client: TestClient) -> None:
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/static/img/favicon.svg"


def test_base_template_has_no_inline_script_or_style(client: TestClient) -> None:
    html = client.get("/").text
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts, "expected the htmx script tag"
    assert all("src=" in tag for tag in scripts)
    assert "<style" not in html
    assert " style=" not in html


def test_base_template_loads_assets(client: TestClient) -> None:
    html = client.get("/").text
    assert 'href="/static/css/app.css"' in html
    assert re.search(r'<script src="/static/vendor/htmx\.min\.js" defer>', html)


def test_static_files_served_in_development() -> None:
    client = TestClient(create_app(Settings(_env_file=None, app_env="development")))
    for path in ("/static/css/app.css", "/static/vendor/htmx.min.js"):
        assert client.get(path).status_code == 200, path


def test_static_files_not_mounted_outside_development(client: TestClient) -> None:
    assert client.get("/static/css/app.css").status_code == 404
