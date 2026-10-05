"""The app's "today" is India's date (the market's calendar), never the server's clock.

In a container the clock runs on UTC, five and a half hours behind India: at 01:50 in Mumbai it
is still yesterday there, so "yesterday's" BSE price file was the day before (the owner saw the
28 Sep close on 30 Sep). One function says what today is, and a guard keeps it the only one.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path

from app.clock import IST, india_today
from app.worker import WorkerContext

SRC = Path(__file__).resolve().parents[2] / "src" / "app"


def test_after_midnight_in_india_it_is_already_the_next_day() -> None:
    assert india_today(datetime(2026, 9, 29, 20, 20, tzinfo=UTC)) == date(2026, 9, 30)  # 01:50 IST


def test_before_midnight_in_india_it_is_still_the_same_day() -> None:
    assert india_today(datetime(2026, 9, 29, 18, 29, tzinfo=UTC)) == date(2026, 9, 29)  # 23:59 IST


def test_the_day_turns_at_1830_utc() -> None:
    assert india_today(datetime(2026, 9, 29, 18, 30, tzinfo=UTC)) == date(2026, 9, 30)


def test_without_an_instant_it_reads_the_clock() -> None:
    assert india_today() == datetime.now(IST).date()


def test_the_worker_counts_days_in_india() -> None:
    assert WorkerContext.__dataclass_fields__["today"].default is india_today


def test_nothing_else_reads_todays_date_from_the_server_clock() -> None:
    """The tests too: CI runs on UTC, so a test comparing against the server's date failed one
    night (2026-10-05, between 18:30 and 00:00 UTC) while passing on an IST laptop."""
    server_today = re.compile(r"date\.today\b|\.now\([^)]*\)\.date\(\)")
    backend = SRC.parents[1]
    offenders = [
        f"{path.relative_to(backend)}:{number}"
        for path in sorted([*SRC.rglob("*.py"), *(backend / "tests").rglob("*.py")])
        if path.name not in ("clock.py", "test_clock.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if server_today.search(line)
    ]
    assert offenders == []
