"""Labels, dates and the "How this was generated" panel shared by every view."""

from collections.abc import Mapping
from datetime import date
from typing import Any

from app.ai.client import Provenance
from app.domain.budget import ChangeReason
from app.domain.models import (
    EventType,
    TaskSource,
    VendorStatus,
)

EVENT_TYPE_LABELS: Mapping[EventType, str] = {
    EventType.WEDDING: "Wedding",
    EventType.BIRTHDAY: "Birthday",
    EventType.COMMUNITY: "Community event",
}


EVENT_TYPE_HINTS: Mapping[EventType, str] = {
    EventType.WEDDING: "Haldi, mehendi, sangeet, the ceremony and the reception",
    EventType.BIRTHDAY: "Cake, games and return gifts, at home or at a venue",
    EventType.COMMUNITY: "Pujas, festivals and society events on a shared fund",
}


REASON_LABELS: Mapping[ChangeReason, str] = {
    ChangeReason.UNCHANGED: "No change",
    ChangeReason.ADDED: "New budget line",
    ChangeReason.PROTECTED: "Protected, kept as it is",
    ChangeReason.GUEST_SCALED: "Scaled for the new guest count",
    ChangeReason.CUT_FIRST: "Cut first, as you asked",
    ChangeReason.PROPORTIONAL: "Adjusted in proportion",
    ChangeReason.FLOOR_REACHED: "Held at its protected expenses",
    ChangeReason.SET: "Set to the amount you asked",
}


TASK_SOURCE_LABELS: Mapping[TaskSource, str] = {
    TaskSource.CATALOG: "Standard step",
    TaskSource.AI: "Suggested by AI",
    TaskSource.USER: "Added by you",
    TaskSource.CHANGE: "Follow-up from a change",
}


VENDOR_STATUS_LABELS: Mapping[VendorStatus, str] = {
    VendorStatus.TODO: "To contact",
    VendorStatus.CONTACTED: "Contacted",
    VendorStatus.BOOKED: "Booked",
}


FIELD_LABELS: Mapping[str, str] = {
    "total_budget_inr": "Total budget",
    "guest_count": "Guests",
    "event_date": "Event date",
}


def format_date(value: date) -> str:
    """Readable date, for example `12 Dec 2026`.

    Examples:
        >>> format_date(date(2026, 12, 2))
        '2 Dec 2026'
    """
    return f"{value.day} {value:%b %Y}"


def ai_panel(record: Mapping[str, Any] | None, panel_id: str = "plan-ai") -> dict[str, Any] | None:
    """Data for a "How this was generated" panel, or None when no AI was asked.

    `panel_id` keeps element ids unique when a page shows more than one panel.
    """
    if record is None:
        return None
    provenance = record["provenance"]
    return {
        "id": panel_id,
        "provider": provenance["provider"] or "None (offline suggestions)",
        "fallback": provenance["fallback"],
        "cached": provenance["cached"],
        "latency_ms": provenance["latency_ms"],
        "prompt_version": provenance["prompt_version"],
        "attempts": [f"{a['provider']}: {a['outcome']}" for a in provenance["attempts"]],
        "request": provenance["request_payload"],
        "response": record["result"],
    }


def ai_note(provenance: Provenance, verb: str = "Interpreted") -> str:
    """One honest line on where an answer came from."""
    if provenance.fallback:
        return "Standard suggestions (not AI-generated)."
    source = "cached answer from " if provenance.cached else ""
    return f"{verb} by AI ({source}{provenance.provider}, {provenance.latency_ms} ms)."
