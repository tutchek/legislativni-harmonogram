"""Počítání lhůt „právnicky“.

Den události, od níž lhůta běží, se do lhůty nepočítá. Lhůta 10 dnů
od 1. dne tedy běží 2.–11. a navazující úkon může nastat nejdříve 12.
Úkony se konají jen v pracovní dny.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum

from .calendar_cz import CzechHolidayCalendar


class DayUnit(str, Enum):
    CALENDAR = "calendar"   # kalendářní dny
    WORKING = "working"     # pracovní dny

    @property
    def label(self) -> str:
        return "dnů" if self is DayUnit.CALENDAR else "prac. dnů"


class DeadlineCalculator:
    """Převádí délku lhůty na konkrétní data."""

    def __init__(self, calendar: CzechHolidayCalendar, shift_end_to_working_day: bool = True):
        self.calendar = calendar
        # § 40 odst. 1 písm. c) správního řádu: konec lhůty připadající
        # na sobotu, neděli nebo svátek se posouvá na nejbližší pracovní den.
        self.shift_end_to_working_day = shift_end_to_working_day

    def period_end(self, trigger: date, length: int, unit: DayUnit) -> date:
        """Poslední den lhůty (den `trigger` se nepočítá)."""
        if length <= 0:
            return trigger
        if unit is DayUnit.WORKING:
            return self.calendar.add_working_days(trigger, length)
        end = trigger + timedelta(days=length)
        if self.shift_end_to_working_day and not self.calendar.is_working_day(end):
            end = self.calendar.next_working_day(end, inclusive=True)
        return end

    def earliest_after(self, trigger: date, length: int, unit: DayUnit) -> date:
        """Nejdřívější pracovní den PO uplynutí lhůty."""
        return self.calendar.next_working_day(self.period_end(trigger, length, unit))

    def latest_by(self, trigger: date, length: int, unit: DayUnit) -> date:
        """Nejpozdější pracovní den, kdy je lhůta ještě zachována."""
        end = self.period_end(trigger, max(length, 1), unit)
        return self.calendar.previous_working_day(end, inclusive=True)
