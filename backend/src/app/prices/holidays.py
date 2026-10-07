"""BSE's declared trading holidays (equity segment): days with no daily price file.

Why a list: BSE answers a closed day the same way it answers "slow down" (HTTP 406), so without it
the worker treats a holiday as a refusal, waits out the 20-minute cool-down three times, and only
then marks the day ``no_file`` (app/price_jobs.py). With it, a known holiday is never asked for.

Source: BSE's 2026 holiday list for the equity segment, as reproduced by brokers' pages (BSE's own
page refuses automated reads); two independent copies agree on all 16 dates. It only ever SKIPS
days: a holiday missing from the list falls back to the cautious 406 rule, so an error here costs
time, never data. Add the next year's list when BSE publishes it.
"""

from datetime import date

BSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 15),  # Municipal elections, Maharashtra
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 3),  # Holi
        date(2026, 3, 26),  # Shri Ram Navami
        date(2026, 3, 31),  # Shri Mahavir Jayanti
        date(2026, 4, 3),  # Good Friday
        date(2026, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),  # Maharashtra Day
        date(2026, 5, 28),  # Bakri Eid
        date(2026, 6, 26),  # Muharram
        date(2026, 9, 14),  # Ganesh Chaturthi
        date(2026, 10, 2),  # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali (Balipratipada)
        date(2026, 11, 24),  # Guru Nanak Jayanti
        date(2026, 12, 25),  # Christmas
    }
)


def is_trading_day(day: date) -> bool:
    """A weekday that is not a declared BSE holiday."""
    return day.isoweekday() < 6 and day not in BSE_HOLIDAYS
