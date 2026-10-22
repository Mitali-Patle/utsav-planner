"""Template environment safety settings."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from jinja2 import UndefinedError

from app.persistence.repositories import ProposalStatus
from app.web.templating import environment
from app.web.views import history_row


def test_autoescape_escapes_html() -> None:
    rendered = environment.from_string("{{ value }}").render(value="<script>alert(1)</script>")
    assert rendered == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_undefined_variables_raise() -> None:
    with pytest.raises(UndefinedError):
        environment.from_string("{{ missing }}").render()


def test_history_dates_are_shown_in_india() -> None:
    """20:00 UTC on 22 Oct is already 23 Oct in India (review, 23 Oct)."""
    late = SimpleNamespace(
        request_text="x",
        status=ProposalStatus.APPROVED,
        created_at=datetime(2026, 10, 22, 20, 0, tzinfo=UTC),
        proposal=SimpleNamespace(
            field_changes=(), budget_changes=(), expense_changes=(), new_tasks=()
        ),
    )
    assert history_row(cast(Any, late))["created"] == "23 Oct 2026"
