"""Vendor checklist: book-by dates, urgency, priority order and booking status."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from types import MappingProxyType

from app.domain.catalog import VendorSpec
from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_LEAD_DAYS,
    MAX_PRIORITY,
    DomainModel,
    EventPlan,
    ShortText,
    VendorId,
    VendorItem,
    VendorStatus,
    evolve,
    id_sequence,
    next_id,
)
from app.domain.timeline import deadline

OWN_VENDOR_TYPE = "own"
"""vendor_type of vendors the user added; only these can be removed."""

ALLOWED_STATUS_CHANGES: Mapping[VendorStatus, frozenset[VendorStatus]] = MappingProxyType(
    {
        VendorStatus.TODO: frozenset({VendorStatus.CONTACTED, VendorStatus.BOOKED}),
        VendorStatus.CONTACTED: frozenset({VendorStatus.TODO, VendorStatus.BOOKED}),
        VendorStatus.BOOKED: frozenset({VendorStatus.CONTACTED}),
    }
)


def build_vendors(
    specs: Sequence[VendorSpec],
    event_date: date,
    today: date,
    *,
    existing_ids: Iterable[str] = (),
) -> tuple[VendorItem, ...]:
    """Turn vendor specs into a prioritized checklist.

    A vendor whose booking window has already passed is due today and flagged urgent.

    Args:
        specs: Vendors to create.
        event_date: Date of the event.
        today: Current date.
        existing_ids: Vendor ids already in the plan.

    Returns:
        Vendors in priority order (1 = book first).
    """
    ids = id_sequence("vendor", existing_ids)
    vendors = []
    for spec in specs:
        book_by, urgent = deadline(event_date, spec.lead_days, today)
        vendors.append(
            VendorItem(
                id=VendorId(next(ids)),
                vendor_type=spec.vendor_type,
                name=spec.name,
                category_id=spec.category_id,
                lead_days=spec.lead_days,
                book_by=book_by,
                urgent=urgent,
                priority=1,
            )
        )
    return prioritize(vendors)


def prioritize(vendors: Sequence[VendorItem]) -> tuple[VendorItem, ...]:
    """Number vendors by urgency: unbooked first, earliest book-by date first.

    Ties go to the vendor with the longer lead time, then to the original order, so the
    result is deterministic. Runs in O(n log n).
    """
    ranked = sorted(
        enumerate(vendors),
        key=lambda pair: (
            pair[1].status is VendorStatus.BOOKED,
            pair[1].book_by,
            -pair[1].lead_days,
            pair[0],
        ),
    )
    return tuple(evolve(vendor, priority=rank) for rank, (_, vendor) in enumerate(ranked, start=1))


def reschedule_vendors(
    vendors: Sequence[VendorItem], new_event_date: date, today: date
) -> tuple[VendorItem, ...]:
    """Recompute book-by dates after the event date changes.

    Booked vendors are left as they are; the rest move with the event.
    """
    updated = []
    for vendor in vendors:
        if vendor.status is VendorStatus.BOOKED:
            updated.append(vendor)
            continue
        book_by, urgent = deadline(new_event_date, vendor.lead_days, today)
        updated.append(evolve(vendor, book_by=book_by, urgent=urgent))
    return prioritize(updated)


def set_vendor_status(plan: EventPlan, vendor_id: VendorId, status: VendorStatus) -> EventPlan:
    """Move a vendor to a new booking status.

    Allowed moves: to-do to contacted or booked; contacted to to-do or booked; booked back
    to contacted (for example, when a booking falls through).

    Raises:
        NotFoundError: If the vendor does not exist.
        InvalidInputError: If the move is not allowed.
    """
    vendor = plan.vendor(vendor_id)
    if status not in ALLOWED_STATUS_CHANGES[vendor.status]:
        raise InvalidInputError(
            f"'{vendor.name}' can't go from {vendor.status} to {status}.",
            code="invalid_status_change",
        )
    vendors = tuple(evolve(v, status=status) if v.id == vendor_id else v for v in plan.vendors)
    return plan.with_changes(vendors=prioritize(vendors))


class NewVendor(DomainModel):
    """A vendor the user wants on the checklist, with the date to book them by."""

    name: ShortText
    book_by: date


def add_own_vendor(plan: EventPlan, draft: NewVendor, today: date) -> tuple[EventPlan, VendorItem]:
    """Add the user's own vendor and re-rank the checklist.

    The lead time is kept, so the book-by date moves with the event like every other vendor.

    Raises:
        InvalidInputError: If the date is in the past, after the event or too far before
            it, or the checklist is full.

    Examples:
        >>> from app.domain.models import EventType
        >>> from app.domain.planning import EventDetails, draft_plan
        >>> plan = draft_plan(EventDetails(event_type=EventType.BIRTHDAY, title="Aarav",
        ...     city="Pune", event_date=date(2026, 12, 13), guest_count=30,
        ...     total_budget_inr=40_000), today=date(2026, 10, 28))
        >>> plan, vendor = add_own_vendor(plan, NewVendor(name="Magician",
        ...     book_by=date(2026, 11, 1)), today=date(2026, 10, 28))
        >>> vendor.lead_days, plan.vendor(vendor.id).priority
        (42, 1)
    """
    if len(plan.vendors) >= MAX_PRIORITY:
        raise InvalidInputError(
            f"A checklist can have at most {MAX_PRIORITY} vendors.", code="too_many_vendors"
        )
    if draft.book_by < today:
        raise InvalidInputError("Choose today or a later date.", code="date_in_past")
    lead = (plan.event_date - draft.book_by).days
    if lead < 0:
        raise InvalidInputError("Choose a date on or before the event day.", code="after_event")
    if lead > MAX_LEAD_DAYS:
        raise InvalidInputError(
            f"Choose a date within {MAX_LEAD_DAYS} days of the event.", code="date_too_early"
        )
    vendor = VendorItem(
        id=VendorId(next_id("vendor", (v.id for v in plan.vendors))),
        vendor_type=OWN_VENDOR_TYPE,
        name=draft.name,
        lead_days=lead,
        book_by=draft.book_by,
        priority=1,
    )
    plan = plan.with_changes(vendors=prioritize((*plan.vendors, vendor)))
    return plan, plan.vendor(vendor.id)


def remove_own_vendor(plan: EventPlan, vendor_id: VendorId) -> EventPlan:
    """Remove a vendor the user added and re-rank the rest.

    Raises:
        NotFoundError: If the vendor does not exist.
        InvalidInputError: If the vendor came with the plan.
    """
    if plan.vendor(vendor_id).vendor_type != OWN_VENDOR_TYPE:
        raise InvalidInputError("Only vendors you added can be removed.", code="not_removable")
    return plan.with_changes(
        vendors=prioritize(tuple(v for v in plan.vendors if v.id != vendor_id))
    )
