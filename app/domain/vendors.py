"""Vendor checklist: book-by dates, urgency, priority order and booking status."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from types import MappingProxyType

from app.domain.catalog import VendorSpec
from app.domain.errors import InvalidInputError
from app.domain.models import (
    EventPlan,
    VendorId,
    VendorItem,
    VendorStatus,
    evolve,
    id_sequence,
)
from app.domain.timeline import deadline

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
