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


# Contrast (WCAG 2.2 AA): 4.5:1 for text, 3:1 for focus rings and control borders.
# Pairs mirror how app.css uses each colour; add a pair when a new combination appears.
TEXT = 4.5
NON_TEXT = 3.0
PLAIN = ("--color-bg", "--color-surface", "--color-surface-subtle")
TINTED = (
    "--color-notice-info-bg",
    "--color-notice-success-bg",
    "--color-error-bg",
    "--color-ai-bg",
    "--color-why-bg",
    "--color-badge-warning-bg",
)
CONTRAST_PAIRS: tuple[tuple[str, str, float], ...] = (
    *((fg, bg, TEXT) for fg in ("--color-text", "--color-text-muted") for bg in PLAIN),
    *(("--color-text", bg, TEXT) for bg in TINTED),
    # Links and bold row labels use the accent colour as normal-size text.
    *(("--color-primary", bg, TEXT) for bg in (*PLAIN, "--color-ai-bg", "--color-error-bg")),
    *(("--color-primary-hover", bg, TEXT) for bg in PLAIN),
    *((fg, bg, TEXT) for fg in ("--color-error", "--color-warning") for bg in PLAIN),
    ("--color-error", "--color-error-bg", TEXT),
    ("--color-warning", "--color-badge-warning-bg", TEXT),
    ("--color-ai-text", "--color-ai-bg", TEXT),
    ("--color-why-text", "--color-why-bg", TEXT),
    ("--color-primary-text", "--color-primary", TEXT),
    ("--color-primary-text", "--color-primary-hover", TEXT),
    ("--color-secondary-text", "--color-secondary", TEXT),
    ("--color-secondary-text", "--color-secondary-hover", TEXT),
    *(("--color-heading", bg, TEXT) for bg in PLAIN),
    *(("--color-secondary", bg, TEXT) for bg in PLAIN),
    ("--color-hero-text", "--color-hero-bg", TEXT),
    ("--color-hero-muted", "--color-hero-bg", TEXT),
    *(("--color-meter", track, NON_TEXT) for track in ("--color-track",)),
    ("--color-error", "--color-track", NON_TEXT),
    *(("--color-focus", bg, NON_TEXT) for bg in PLAIN),
    *(("--color-border", bg, NON_TEXT) for bg in ("--color-bg", "--color-surface")),
)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--color-[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", block))


def _themes() -> dict[str, dict[str, str]]:
    light = re.search(r":root\s*\{([^}]*)\}", CSS)
    dark = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}", CSS)
    assert light and dark, "app.css must define light and dark colour tokens on :root"
    base = _tokens(light.group(1))
    return {"light": base, "dark": {**base, **_tokens(dark.group(1))}}


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio of two `#rrggbb` colours.

    Examples:
        >>> round(contrast_ratio("#000000", "#ffffff"), 1)
        21.0
    """
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colour_pairs_meet_wcag_contrast(theme: str) -> None:
    tokens = _themes()[theme]
    failures = [
        f"{fg} on {bg}: {contrast_ratio(tokens[fg], tokens[bg]):.2f} < {need}"
        for fg, bg, need in CONTRAST_PAIRS
        if fg in tokens and bg in tokens and contrast_ratio(tokens[fg], tokens[bg]) < need
    ]
    assert not failures, failures


def test_every_contrast_token_is_defined() -> None:
    tokens = _themes()["light"]
    missing = {name for pair in CONTRAST_PAIRS for name in pair[:2] if name not in tokens}
    assert not missing, missing


def test_print_hides_controls_and_shows_print_only_text() -> None:
    print_rules = CSS[CSS.index("@media print") :]
    for selector in (".site-main form", ".no-print", "#change-manager", ".ai-panel"):
        assert selector in print_rules.split("display: none")[0]
    assert re.search(r"\.print-only \{\s*display: inline", print_rules)
    assert "display: none" in _block(".print-button[hidden]")
