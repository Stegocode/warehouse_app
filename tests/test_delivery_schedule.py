"""Tests for core/delivery_schedule.next_delivery_day — pure calendar logic, no I/O."""
from datetime import date

from warehouse_app.core.delivery_schedule import next_delivery_day

# 2026-07-16 Thu, 17 Fri, 18 Sat, 19 Sun, 20 (start of next week), 21 Tue.


def test_weekday_rolls_to_next_day():
    assert next_delivery_day(date(2026, 7, 16)) == date(2026, 7, 17)   # Thu -> Fri


def test_friday_skips_the_weekend():
    assert next_delivery_day(date(2026, 7, 17)) == date(2026, 7, 20)   # Fri -> next weekday


def test_saturday_rolls_to_next_weekday():
    assert next_delivery_day(date(2026, 7, 18)) == date(2026, 7, 20)


def test_sunday_rolls_to_next_weekday():
    assert next_delivery_day(date(2026, 7, 19)) == date(2026, 7, 20)


def test_start_of_week_rolls_to_next_day():
    assert next_delivery_day(date(2026, 7, 20)) == date(2026, 7, 21)
