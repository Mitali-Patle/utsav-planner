"""Export the plan's dates as an iCalendar (`.ics`) file for any calendar app (RFC 5545).

The event day, every unfinished task and every vendor still to book become all-day
entries. Each entry keeps the same UID across exports, so importing a newer file updates
the entries instead of duplicating them in calendars that honour UIDs.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.domain.models import EventPlan, VendorStatus

PRODUCT_ID = "-//Utsav Planner//Event timeline//EN"
UID_DOMAIN = "utsav-planner"
LINE_OCTETS = 75


def escape_text(value: str) -> str:
    r"""Escape a TEXT value so commas, semicolons and line breaks stay literal.

    Examples:
        >>> escape_text("Mehendi; henna, music")
        'Mehendi\\; henna\\, music'
        >>> escape_text("a\\b\nc")
        'a\\\\b\\nc'
    """
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    r"""Fold a content line at 75 octets without splitting a UTF-8 character.

    Continuation lines start with a space, which counts towards their 75 octets.

    Examples:
        >>> fold("SUMMARY:short")
        'SUMMARY:short'
        >>> [len(part.encode()) for part in fold("X:" + "a" * 160).split("\r\n")]
        [75, 75, 14]
    """
    parts: list[str] = []
    current, size = "", 0
    for char in line:
        width = len(char.encode())
        limit = LINE_OCTETS if not parts else LINE_OCTETS - 1
        if size + width > limit:
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


@dataclass(frozen=True)
class _Entry:
    uid: str
    day: date
    summary: str
    description: str
    busy: bool = False

    def lines(self, stamp: str) -> list[str]:
        return [
            "BEGIN:VEVENT",
            f"UID:{self.uid}@{UID_DOMAIN}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{self.day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{self.day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{escape_text(self.summary)}",
            f"DESCRIPTION:{escape_text(self.description)}",
            "TRANSP:OPAQUE" if self.busy else "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]


def plan_calendar(plan: EventPlan, event_id: str, updated_at: datetime) -> str:
    """Build the calendar file for a plan.

    Args:
        plan: The plan to export.
        event_id: The saved event's id, which keeps every entry's UID stable.
        updated_at: When the plan was last saved; used as each entry's timestamp.

    Returns:
        The iCalendar text, with CRLF line endings.
    """
    stamp = f"{updated_at.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    on_the_day = f"{plan.title} in {plan.city}, {plan.guest_count} guests."
    entries = [_Entry(f"{event_id}-event", plan.event_date, plan.title, on_the_day, busy=True)]
    entries += [
        _Entry(
            f"{event_id}-{task.id}",
            task.due_date,
            task.title,
            f"Task for {plan.title}." + (f" Why: {task.why}" if task.why else ""),
        )
        for task in plan.tasks
        if not task.done
    ]
    entries += [
        _Entry(
            f"{event_id}-{vendor.id}",
            vendor.book_by,
            f"Book {vendor.name}",
            f"Vendor to book for {plan.title}." + (f" Why: {vendor.why}" if vendor.why else ""),
        )
        for vendor in plan.vendors
        if vendor.status is not VendorStatus.BOOKED
    ]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODUCT_ID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(plan.title)}",
        *(line for entry in entries for line in entry.lines(stamp)),
        "END:VCALENDAR",
    ]
    return "".join(f"{fold(line)}\r\n" for line in lines)
