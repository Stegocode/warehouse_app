# Owns: pure delivery-calendar logic — which delivery date a pick build targets by default.
# Must not: import from adapters, services, infrastructure, or config; perform I/O.
# May import: standard library only.

from __future__ import annotations

from datetime import date, timedelta

# date.weekday() runs 0..6 across the week; Saturday=5 and Sunday=6 are the weekend. The
# business runs no weekend deliveries, so a pick build never targets one — a Friday picks
# for the start of the following week.
_WEEKEND = {5, 6}


def next_delivery_day(today: date) -> date:
    """The next delivery day strictly after ``today``, skipping weekends.

    Picking runs the day before delivery, so the default build target is tomorrow — unless
    tomorrow is a weekend, in which case it rolls forward to the next weekday. Holidays are
    not modelled (out of scope — pass an explicit --date to override on a holiday).
    """
    d = today + timedelta(days=1)
    while d.weekday() in _WEEKEND:
        d += timedelta(days=1)
    return d
