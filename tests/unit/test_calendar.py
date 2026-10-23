"""Calendar export (F14): RFC 5545 structure, escaping, folding and what is included."""

from datetime import date, datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from app.domain.calendar import LINE_OCTETS, escape_text, fold, plan_calendar
from app.domain.models import EventPlan, Task, TaskId, VendorId, VendorItem, VendorStatus
from tests.factories import make_plan

SAVED = datetime(2026, 10, 24, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def plan_with_dates() -> EventPlan:
    return make_plan().with_changes(
        title="Aarav's 8th, space; party",
        tasks=(
            Task(
                id=TaskId("task-1"),
                title="Send invites",
                offset_days=21,
                due_date=date(2026, 11, 21),
                why="Guests need three weeks",
            ),
            Task(
                id=TaskId("task-2"),
                title="Order cake",
                offset_days=7,
                due_date=date(2026, 12, 5),
                done=True,
            ),
        ),
        vendors=(
            VendorItem(
                id=VendorId("vendor-1"),
                vendor_type="decorator",
                name="Decorator",
                lead_days=20,
                book_by=date(2026, 11, 22),
                priority=1,
            ),
            VendorItem(
                id=VendorId("vendor-2"),
                vendor_type="cake_shop",
                name="Cake shop",
                lead_days=7,
                book_by=date(2026, 12, 5),
                priority=2,
                status=VendorStatus.BOOKED,
            ),
        ),
    )


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def entries(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            found.append({})
        elif found and ":" in line and not line.startswith("END:"):
            name, value = line.split(":", 1)
            found[-1].setdefault(name, value)
    return found


def test_calendar_has_the_event_day_open_tasks_and_vendors_to_book() -> None:
    text = plan_calendar(plan_with_dates(), "ev1", SAVED)
    found = entries(text)
    assert [(e["DTSTART;VALUE=DATE"], e["SUMMARY"]) for e in found] == [
        ("20261212", r"Aarav's 8th\, space\; party"),
        ("20261121", "Send invites"),
        ("20261122", "Book Decorator"),
    ]
    assert [e["DTEND;VALUE=DATE"] for e in found] == ["20261213", "20261122", "20261123"]
    assert [e["TRANSP"] for e in found] == ["OPAQUE", "TRANSPARENT", "TRANSPARENT"]
    assert "Why: Guests need three weeks" in found[1]["DESCRIPTION"]


def test_calendar_is_a_valid_crlf_document_with_stable_ids() -> None:
    plan = plan_with_dates()
    text = plan_calendar(plan, "ev1", SAVED)
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    found = entries(text)
    assert [e["UID"] for e in found] == [
        "ev1-event@utsav-planner",
        "ev1-task-1@utsav-planner",
        "ev1-vendor-1@utsav-planner",
    ]
    assert {e["DTSTAMP"] for e in found} == {"20261024T040000Z"}
    later = plan_calendar(plan, "ev1", SAVED + timedelta(days=1))
    assert [e["UID"] for e in entries(later)] == [e["UID"] for e in found]


def test_long_indian_script_titles_fold_without_breaking_characters() -> None:
    title = "मेहंदी और संगीत की रात " * 6
    text = plan_calendar(make_plan().with_changes(title=title.strip()[:80]), "ev1", SAVED)
    assert all(len(line.encode()) <= LINE_OCTETS for line in text.split("\r\n"))
    assert entries(text)[0]["SUMMARY"] == title.strip()[:80]


@given(st.text(min_size=1, max_size=300).map(escape_text))
def test_folding_is_reversible_and_respects_the_line_limit(value: str) -> None:
    folded = fold(f"DESCRIPTION:{value}")
    assert folded.replace("\r\n ", "") == f"DESCRIPTION:{value}"
    assert all(len(line.encode()) <= LINE_OCTETS for line in folded.split("\r\n"))


@given(st.text(max_size=200))
def test_escaped_text_has_no_raw_separators_or_line_breaks(value: str) -> None:
    escaped = escape_text(value.replace("\r", ""))
    unescaped = escaped.replace("\\\\", "").replace(r"\;", "").replace(r"\,", "")
    assert ";" not in unescaped and "," not in unescaped and "\n" not in escaped
