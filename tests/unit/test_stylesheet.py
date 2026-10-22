"""The stylesheet keeps the rules that behaviour and accessibility depend on.

The design can change freely; these selectors are part of docs/UI_CONTRACT.md.
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[2] / "public" / "static" / "css" / "app.css").read_text(
    encoding="utf-8"
)


def _block(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"app.css must style {selector}"
    return match.group(1)


def test_busy_indicator_is_hidden_until_a_request_is_in_flight() -> None:
    assert "display: none" in _block(".htmx-indicator")
    assert ".htmx-indicator.htmx-request" in CSS


@pytest.mark.parametrize("selector", [".sr-only", ".skip-link:focus", ":focus-visible"])
def test_accessibility_rules_are_present(selector: str) -> None:
    assert _block(selector).strip()


def test_reduced_motion_and_dark_theme_are_respected() -> None:
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "@media (prefers-color-scheme: dark)" in CSS
