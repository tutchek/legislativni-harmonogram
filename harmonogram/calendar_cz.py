"""Český pracovní kalendář – státní svátky, ostatní svátky a Velikonoce.

Zdroj: zákon č. 245/2000 Sb., o státních svátcích, o ostatních svátcích,
o významných dnech a o dnech pracovního klidu (Velký pátek od roku 2016).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


class CzechHolidayCalendar:
    """Rozpoznává víkendy a svátky a posouvá data na pracovní dny."""

    FIXED_HOLIDAYS: tuple[tuple[int, int, str], ...] = (
        (1, 1, "Den obnovy samostatného českého státu, Nový rok"),
        (5, 1, "Svátek práce"),
        (5, 8, "Den vítězství"),
        (7, 5, "Den slovanských věrozvěstů Cyrila a Metoděje"),
        (7, 6, "Den upálení mistra Jana Husa"),
        (9, 28, "Den české státnosti"),
        (10, 28, "Den vzniku samostatného československého státu"),
        (11, 17, "Den boje za svobodu a demokracii a Mezinárodní den studentstva"),
        (12, 24, "Štědrý den"),
        (12, 25, "1. svátek vánoční"),
        (12, 26, "2. svátek vánoční"),
    )
    GOOD_FRIDAY_SINCE = 2016

    # --- Velikonoce --------------------------------------------------------
    @staticmethod
    def easter_sunday(year: int) -> date:
        """Velikonoční neděle (gregoriánský kalendář, anonymní algoritmus)."""
        a = year % 19
        b, c = divmod(year, 100)
        d, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = divmod(c, 4)
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month, day = divmod(h + l - 7 * m + 114, 31)
        return date(year, month, day + 1)

    # --- Svátky ------------------------------------------------------------
    @lru_cache(maxsize=64)
    def holidays(self, year: int) -> dict[date, str]:
        """Všechny dny pracovního klidu (mimo víkendy) v daném roce."""
        result = {date(year, m, d): name for m, d, name in self.FIXED_HOLIDAYS}
        easter = self.easter_sunday(year)
        if year >= self.GOOD_FRIDAY_SINCE:
            result[easter - timedelta(days=2)] = "Velký pátek"
        result[easter + timedelta(days=1)] = "Velikonoční pondělí"
        return dict(sorted(result.items()))

    def holiday_name(self, day: date) -> str | None:
        return self.holidays(day.year).get(day)

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays(day.year)

    @staticmethod
    def is_weekend(day: date) -> bool:
        return day.weekday() >= 5

    def is_working_day(self, day: date) -> bool:
        return not self.is_weekend(day) and not self.is_holiday(day)

    # --- Posuny ------------------------------------------------------------
    def next_working_day(self, day: date, inclusive: bool = False) -> date:
        """Nejbližší pracovní den po `day` (nebo `day` sám, je-li inclusive)."""
        current = day if inclusive else day + timedelta(days=1)
        while not self.is_working_day(current):
            current += timedelta(days=1)
        return current

    def previous_working_day(self, day: date, inclusive: bool = False) -> date:
        current = day if inclusive else day - timedelta(days=1)
        while not self.is_working_day(current):
            current -= timedelta(days=1)
        return current

    def add_working_days(self, day: date, count: int) -> date:
        """N-tý pracovní den následující po `day` (den `day` se nepočítá)."""
        current = day
        for _ in range(count):
            current = self.next_working_day(current)
        return current

    def working_days_between(self, start: date, end: date) -> int:
        """Počet pracovních dnů v intervalu (start, end]."""
        count, current = 0, start
        while current < end:
            current += timedelta(days=1)
            if self.is_working_day(current):
                count += 1
        return count
