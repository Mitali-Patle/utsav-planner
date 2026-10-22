"""System clock uses India's time zone."""

from datetime import date, datetime, timedelta

from app.domain.clock import INDIA_TZ, SystemClock


def test_system_clock_returns_today_in_india() -> None:
    before = datetime.now(INDIA_TZ).date()
    today = SystemClock().today()
    assert isinstance(today, date)
    assert before <= today <= before + timedelta(days=1)
