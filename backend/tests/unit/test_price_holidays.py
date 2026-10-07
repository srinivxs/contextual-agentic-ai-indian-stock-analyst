"""BSE's declared trading holidays: days the worker never asks BSE for (the owner, 2026-10-07).

Without the list a holiday looked exactly like BSE saying "slow down", and cost three 20-minute
cool-downs before the worker believed it. With it, a known holiday is simply skipped.
"""

from datetime import date

from app.prices.holidays import BSE_HOLIDAYS, is_trading_day


def test_the_holidays_that_held_up_the_first_live_runs_are_listed() -> None:
    assert date(2026, 10, 2) in BSE_HOLIDAYS  # Mahatma Gandhi Jayanti
    assert date(2026, 9, 14) in BSE_HOLIDAYS  # Ganesh Chaturthi


def test_every_listed_holiday_is_a_weekday_in_2026() -> None:
    """A weekend in the list would be a typo: weekends are skipped anyway."""
    assert len(BSE_HOLIDAYS) == 16
    assert all(day.year == 2026 and day.isoweekday() < 6 for day in BSE_HOLIDAYS)


def test_a_trading_day_is_a_weekday_that_is_not_a_holiday() -> None:
    assert is_trading_day(date(2026, 10, 5))  # Monday
    assert not is_trading_day(date(2026, 10, 2))  # Friday, Gandhi Jayanti
    assert not is_trading_day(date(2026, 10, 3))  # Saturday
    assert not is_trading_day(date(2026, 10, 4))  # Sunday


def test_a_year_without_a_list_falls_back_to_weekdays() -> None:
    """2027's list is not in yet: its weekdays are asked for, and the cautious 406 rule decides."""
    assert is_trading_day(date(2027, 1, 26))  # Republic Day 2027, not listed yet
