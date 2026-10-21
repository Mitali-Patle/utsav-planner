"""Template environment safety settings."""

import pytest
from jinja2 import UndefinedError

from app.web.templating import environment


def test_autoescape_escapes_html() -> None:
    rendered = environment.from_string("{{ value }}").render(value="<script>alert(1)</script>")
    assert rendered == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_undefined_variables_raise() -> None:
    with pytest.raises(UndefinedError):
        environment.from_string("{{ missing }}").render()
