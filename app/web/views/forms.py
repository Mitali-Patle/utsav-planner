"""Form state and plain-language validation messages, including the error summary."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError


@dataclass(frozen=True)
class FormState:
    """Submitted values and per-field error messages, so a form can be shown again as typed."""

    values: Mapping[str, Any] = field(default_factory=dict)
    errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanForms:
    """The dashboard's add forms, each empty unless it is being shown again with errors."""

    expense: FormState = field(default_factory=FormState)
    task: FormState = field(default_factory=FormState)
    vendor: FormState = field(default_factory=FormState)


def form_errors(error: ValidationError) -> dict[str, str]:
    """A plain-language message per field from a Pydantic error (first message wins).

    Examples:
        >>> from pydantic import BaseModel, Field
        >>> class Form(BaseModel):
        ...     guests: int = Field(ge=1)
        >>> try:
        ...     Form.model_validate({"guests": "0"})
        ... except ValidationError as e:
        ...     form_errors(e)
        {'guests': 'Must be at least 1.'}
        >>> try:
        ...     Form.model_validate({"guests": ""})
        ... except ValidationError as e:
        ...     form_errors(e)
        {'guests': 'This field is required.'}
    """
    messages: dict[str, str] = {}
    for item in error.errors():
        name = str(item["loc"][0]) if item["loc"] else "form"
        error_type = "missing" if item.get("input") == "" else item["type"]
        messages.setdefault(name, _friendly(error_type, item.get("ctx") or {}, item["msg"]))
    return messages


def _friendly(error_type: str, ctx: Mapping[str, Any], fallback: str) -> str:
    """Plain wording for the validation errors users can trigger from forms."""
    if error_type == "missing" or (
        error_type in {"string_too_short", "too_short"} and ctx.get("min_length", 1) <= 1
    ):
        return "This field is required."
    messages = {
        "string_too_short": f"Use at least {ctx.get('min_length')} characters.",
        "string_too_long": f"Use at most {ctx.get('max_length')} characters.",
        "too_long": f"Use at most {ctx.get('max_length')} characters.",
        "greater_than_equal": f"Must be at least {ctx.get('ge')}.",
        "less_than_equal": f"Must be at most {ctx.get('le')}.",
        "int_parsing": "Enter a whole number.",
        "int_from_float": "Enter a whole number.",
        "date_from_datetime_parsing": "Enter a valid date.",
        "date_parsing": "Enter a valid date.",
        "enum": "Choose one of the options.",
        "literal_error": "Choose one of the options.",
    }
    return messages.get(error_type, fallback.removeprefix("Value error, ").rstrip(".") + ".")


CREATE_FIELDS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "event_type": ("Event type", "type-wedding"),
        "title": ("Event name", "f-title"),
        "city": ("City", "f-city"),
        "event_date": ("Event date", "f-event_date"),
        "guest_count": ("Number of guests", "f-guest_count"),
        "total_budget_inr": ("Total budget in rupees", "f-total_budget_inr"),
        "preferences": ("Preferences", "f-preferences"),
        "sub_events": ("Functions to include", "create-heading"),
    }
)
"""Each new-event field's label and the id its error summary links to."""


def error_links(errors: Mapping[str, str]) -> list[tuple[str, str]]:
    """The error summary: (link target, "Label: message") per field.

    Examples:
        >>> error_links({"city": "This field is required.", "form": "Try again."})
        [('f-city', 'City: This field is required.')]
        >>> error_links({"extra": "Not allowed."})
        [('create-heading', 'extra: Not allowed.')]
    """
    links = []
    for name, message in errors.items():
        if name == "form":
            continue
        label, target = CREATE_FIELDS.get(name, (name, "create-heading"))
        links.append((target, f"{label}: {message}"))
    return links
