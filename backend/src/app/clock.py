"""What "today" is: India's date, the market's calendar.

BSE's daily price files, filing dates and event dates all follow India's day. The server's own
clock does not: in a container it runs on UTC, five and a half hours behind, so between midnight
and 05:30 in Mumbai it still says yesterday. Everything that needs today's date asks here.
"""

from datetime import UTC, date, datetime, timedelta, timezone

# India Standard Time: UTC+05:30 all year (no daylight saving), so a fixed offset is exact and
# needs no time-zone database in the image.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def india_today(now: datetime | None = None) -> date:
    """Today's date in India, at ``now`` (default: this moment)."""
    return (now or datetime.now(UTC)).astimezone(IST).date()
