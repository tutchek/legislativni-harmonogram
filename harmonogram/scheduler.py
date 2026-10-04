"""Výpočet harmonogramu z plánu."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .calendar_cz import CzechHolidayCalendar
from .deadlines import DeadlineCalculator
from .parliament import CHAMBERS, ChamberCalendar, SittingPolicy, WeekType, monday
from .model import PREVIOUS, WEEKDAY_NAMES, Constraint, ConstraintKind, LegislativePlan, Phase


def fmt(d: date) -> str:
    return f"{d.day}. {d.month}. {d.year}"


MONTHS_NOMINATIVE = ("leden", "únor", "březen", "duben", "květen", "červen", "červenec",
                     "srpen", "září", "říjen", "listopad", "prosinec")


def month_label(d: date) -> str:
    """Orientační termín navenek: „říjen 2027“."""
    return f"{MONTHS_NOMINATIVE[d.month - 1]} {d.year}"


@dataclass
class Issue:
    level: str   # "error" | "warning" | "info"
    message: str


@dataclass
class Bounds:
    """Meze pro datum fáze plynoucí z jejích vazeb."""

    earliest: date | None = None
    latest: date | None = None
    hard_earliest: date | None = None
    hard_latest: date | None = None
    earliest_reason: str = ""
    latest_reason: str = ""

    def add_earliest(self, day: date, reason: str, hard: date | None) -> None:
        if self.earliest is None or day > self.earliest:
            self.earliest, self.earliest_reason = day, reason
        if hard and (self.hard_earliest is None or hard > self.hard_earliest):
            self.hard_earliest = hard

    def add_latest(self, day: date, reason: str, hard: date | None) -> None:
        if self.latest is None or day < self.latest:
            self.latest, self.latest_reason = day, reason
        if hard and (self.hard_latest is None or hard < self.hard_latest):
            self.hard_latest = hard


@dataclass
class PhaseResult:
    phase: Phase
    enabled: bool
    order: int | None = None
    date: date | None = None
    computed: date | None = None
    reason: str = ""
    gap_days: int | None = None
    constraint_refs: list[str] = field(default_factory=list)
    deadline_date: date | None = None   # konec lhůty první vazby (řádek „uplatnění připomínek“)
    sitting_note: str = ""              # typ týdne komory v den fáze
    # Orientační termín: schůze komory mimo zveřejněný harmonogram (nebo fáze na takové
    # závislá). Počítá se s konkrétním dnem, navenek se uvádí jen měsíc a rok.
    approximate: bool = False
    issues: list[Issue] = field(default_factory=list)


@dataclass
class ScheduleResult:
    plan: LegislativePlan
    phases: list[PhaseResult]
    policy: SittingPolicy | None = None

    @property
    def active(self) -> list[PhaseResult]:
        return [r for r in self.phases if r.enabled]


class ScheduleCalculator:
    """Prochází fáze popořadě a každé přiřadí datum."""

    def __init__(self, plan: LegislativePlan, calendar: CzechHolidayCalendar | None = None,
                 parliament: dict[str, ChamberCalendar] | None = None):
        self.plan = plan
        self.calendar = calendar or CzechHolidayCalendar()
        self.deadlines = DeadlineCalculator(self.calendar, plan.settings.shift_end_to_working_day)
        settings = plan.settings
        calendars = parliament if (parliament and settings.use_parliament_calendar) else {}
        calendars = {k: calendars.get(k) or ChamberCalendar(k) for k in CHAMBERS}
        manual = settings.week_overrides if settings.use_parliament_calendar else {}
        self.policy = SittingPolicy(calendars, self.calendar, settings.psp_allow_variable, manual)

    # ------------------------------------------------------------------
    def calculate(self) -> ScheduleResult:
        results: list[PhaseResult] = []
        dates: dict[str, date] = {}
        approx: dict[str, bool] = {}
        previous: PhaseResult | None = None

        for index, phase in enumerate(self.plan.phases):
            result = PhaseResult(phase, self.plan.is_enabled(phase))
            results.append(result)
            result.constraint_refs = [self._ref_name(c, index) for c in phase.constraints]
            if not result.enabled:
                continue
            result.order = sum(1 for r in results if r.enabled)

            if previous is None:
                start = self.plan.settings.start_date
                result.computed = self.calendar.next_working_day(start, inclusive=True)
                result.reason = "datum prvního kroku"
                if result.computed != start:
                    result.reason += " (posunuto na pracovní den)"
                bounds = Bounds()
                if phase.sitting and not self.policy.allows(phase.sitting, result.computed, phase.ignore_customs):
                    result.issues.append(Issue("warning", "Mimo obvyklý den zasedání – "
                                               f"{self.policy.describe(phase.sitting, result.computed)}."))
            else:
                bounds = self._bounds(phase, index, dates, previous)
                result.computed, result.reason = self._pick_date(phase, bounds, result)

            if phase.deadline_row and phase.constraints:
                result.deadline_date = self._period_end(phase.constraints[0], index, dates)

            if phase.override:
                result.date = phase.override
                self._validate_override(result, bounds, previous)
            else:
                result.date = result.computed
                if bounds.hard_latest and result.date > bounds.hard_latest:
                    result.issues.append(Issue("error", f"Zákonná lhůta uplyne {fmt(bounds.hard_latest)}."))

            if phase.sitting:
                result.sitting_note = self.policy.describe(phase.sitting, result.date)
            result.approximate = self._is_approximate(phase, index, result, previous, approx)
            approx[phase.id] = result.approximate
            if previous is not None:
                result.gap_days = (result.date - previous.date).days
            dates[phase.id] = result.date
            previous = result

        return ScheduleResult(self.plan, results, self.policy)

    # ------------------------------------------------------------------
    def _resolve_ref(self, constraint: Constraint, index: int) -> Phase | None:
        """Najde fázi, od níž lhůta běží (vypnuté fáze se přeskakují)."""
        phases = self.plan.phases
        if constraint.ref == PREVIOUS:
            candidates = range(index - 1, -1, -1)
        else:
            ref_index = next(i for i, p in enumerate(phases) if p.id == constraint.ref)
            candidates = range(ref_index, -1, -1)
        for i in candidates:
            if self.plan.is_enabled(phases[i]):
                return phases[i]
        return None

    def _period_end(self, constraint: Constraint, index: int, dates: dict[str, date]) -> date | None:
        ref = self._resolve_ref(constraint, index)
        if ref is None or ref.id not in dates:
            return None
        return self.deadlines.period_end(dates[ref.id], constraint.value, constraint.unit)

    def _ref_name(self, constraint: Constraint, index: int) -> str:
        ref = self._resolve_ref(constraint, index)
        return ref.name if ref else "—"

    def _bounds(self, phase: Phase, index: int, dates: dict[str, date], previous: PhaseResult) -> Bounds:
        bounds = Bounds()
        # Každá fáze následuje nejdříve pracovní den po předchozí.
        bounds.add_earliest(self.calendar.next_working_day(previous.date),
                            f"po fázi „{previous.phase.name}“", None)
        for constraint in phase.constraints:
            ref = self._resolve_ref(constraint, index)
            if ref is None or ref.id not in dates:
                continue
            trigger = dates[ref.id]
            unit = constraint.unit
            desc = f"{constraint.value} {unit.label} od fáze „{ref.name}“ ({fmt(trigger)})"
            if constraint.kind is ConstraintKind.MIN:
                day = self.deadlines.earliest_after(trigger, constraint.value, unit)
                hard = (self.deadlines.earliest_after(trigger, constraint.legal_limit, unit)
                        if constraint.is_hard else None)
                bounds.add_earliest(day, "nejdříve po " + desc, hard)
            else:
                day = self.deadlines.latest_by(trigger, constraint.value, unit)
                hard = (self.deadlines.latest_by(trigger, constraint.legal_limit, unit)
                        if constraint.is_hard else None)
                bounds.add_latest(day, "nejpozději do " + desc, hard)
        return bounds

    def _pick_date(self, phase: Phase, bounds: Bounds, result: PhaseResult) -> tuple[date, str]:
        weekday = self._weekday(phase)
        if bounds.latest is not None:
            # Fáze s lhůtou „nejpozději“ se plánuje na konec lhůty.
            day, reason = bounds.latest, bounds.latest_reason
            if weekday is not None:
                day = self._align_back(day, weekday)
            if day < bounds.earliest:
                result.issues.append(Issue("error", "Lhůty si odporují – konec lhůty nastává dříve, "
                                                    f"než fáze může nejdříve proběhnout ({fmt(bounds.earliest)})."))
                return bounds.earliest, bounds.earliest_reason
            if phase.sitting:
                return self._last_sitting(phase, bounds, day, reason, result)
            return day, reason

        day, reason = bounds.earliest, bounds.earliest_reason
        if weekday is not None:
            aligned = self._align_forward(day, weekday)
            if aligned != day:
                reason += f"; nejbližší {WEEKDAY_NAMES[weekday]}"
                if aligned.weekday() != weekday:
                    reason += " (připadl na svátek – posunuto na další pracovní den)"
            day = aligned
        if phase.sitting:
            day, reason = self._first_sitting(phase, day, reason, result)
        return day, reason

    def _is_approximate(self, phase: Phase, index: int, result: PhaseResult,
                        previous: PhaseResult | None, approx: dict[str, bool]) -> bool:
        if phase.override or previous is None:
            return False                       # ručně zadané datum / první krok je pevný
        settings = self.plan.settings
        if (phase.sitting and settings.use_parliament_calendar and not phase.ignore_customs
                and not self.policy.is_known(phase.sitting.chamber, result.date)):
            return True
        refs = [self._resolve_ref(c, index) for c in phase.constraints]
        refs.append(previous.phase)
        return any(approx.get(r.id) for r in refs if r is not None)

    # --- zasedání komor -----------------------------------------------------
    def _rule_text(self, phase: Phase) -> str:
        days = sorted(phase.sitting.weekdays)
        names = ", ".join(WEEKDAY_NAMES[d] for d in days) if len(days) < 4 else "út–pá"
        return names

    def _first_sitting(self, phase: Phase, day: date, reason: str, result: PhaseResult) -> tuple[date, str]:
        rule = phase.sitting
        found = self.policy.first_allowed(rule, day, phase.ignore_customs)
        if found is None:
            result.issues.append(Issue("warning", "Do roka nenalezen vhodný den schůze – zkontrolujte týdny komory."))
            return day, reason
        if found != day:
            what = "pracovní den" if phase.ignore_customs else "den schůze"
            reason += f"; nejbližší {what} ({self._rule_text(phase)})"
        return found, reason

    def _last_sitting(self, phase: Phase, bounds: Bounds, latest: date, reason: str,
                      result: PhaseResult) -> tuple[date, str]:
        rule = phase.sitting
        found = self.policy.last_allowed(rule, latest, bounds.earliest, phase.ignore_customs)
        if found is None:
            name = CHAMBERS[rule.chamber].name
            result.issues.append(Issue("warning", f"V lhůtě není den zasedání ({name}). U Senátu platí, "
                                                  "že nevyjádří-li se v lhůtě, je zákon přijat."))
            return latest, reason + " – marné uplynutí lhůty"
        if found != latest:
            reason += "; poslední den zasedání v lhůtě"
        return found, reason

    def _weekday(self, phase: Phase) -> int | None:
        if not phase.weekday_key:
            return None
        rule = self.plan.settings.weekday_rules.get(phase.weekday_key)
        return rule.weekday if rule else None

    def _align_forward(self, day: date, weekday: int) -> date:
        candidate = day + timedelta(days=(weekday - day.weekday()) % 7)
        # Připadne-li daný den na svátek, koná se nejbližší pracovní den poté.
        return self.calendar.next_working_day(candidate, inclusive=True)

    def _align_back(self, day: date, weekday: int) -> date:
        candidate = day - timedelta(days=(day.weekday() - weekday) % 7)
        return self.calendar.previous_working_day(candidate, inclusive=True)

    def _validate_override(self, result: PhaseResult, bounds: Bounds, previous: PhaseResult | None) -> None:
        day = result.date
        issues = result.issues
        if previous is not None and day <= previous.date:
            issues.append(Issue("error", f"Datum musí být až po předchozí fázi ({fmt(previous.date)})."))
        if bounds.hard_earliest and day < bounds.hard_earliest:
            issues.append(Issue("error", f"Porušen nepřekročitelný limit – nejdříve {fmt(bounds.hard_earliest)}."))
        elif bounds.earliest and day < bounds.earliest:
            issues.append(Issue("warning", f"Dříve, než dovolují nastavené lhůty ({fmt(bounds.earliest)})."))
        if bounds.hard_latest and day > bounds.hard_latest:
            issues.append(Issue("error", f"Po uplynutí zákonné lhůty ({fmt(bounds.hard_latest)})."))
        elif bounds.latest and day > bounds.latest:
            issues.append(Issue("warning", f"Po konci nastavené lhůty ({fmt(bounds.latest)})."))
        if not self.calendar.is_working_day(day):
            what = self.calendar.holiday_name(day) or WEEKDAY_NAMES[day.weekday()]
            issues.append(Issue("warning", f"Nepracovní den ({what})."))
        elif result.phase.sitting and not self.policy.allows(result.phase.sitting, day,
                                                             result.phase.ignore_customs):
            if day.weekday() not in result.phase.sitting.weekdays:
                issues.append(Issue("warning", f"Obvykle jen {self._rule_text(result.phase)}."))
            else:
                issues.append(Issue("warning", "Mimo týden zasedání – "
                                               f"{self.policy.describe(result.phase.sitting, day)}."))


class ScheduleSerializer:
    """Převod výsledku do JSON pro webové rozhraní."""

    def __init__(self, calendar: CzechHolidayCalendar):
        self.calendar = calendar

    def to_dict(self, result: ScheduleResult) -> dict[str, Any]:
        plan = result.plan
        active = result.active
        first, last = (active[0].date, active[-1].date) if active else (None, None)
        years = sorted({r.date.year for r in active if r.date}) if active else []
        return {
            "settings": {
                "start_date": plan.settings.start_date.isoformat(),
                "first_phase": plan.settings.first_phase,
                "shift_end_to_working_day": plan.settings.shift_end_to_working_day,
                "title": plan.settings.title,
                "effective": plan.settings.effective,
                "psp_allow_variable": plan.settings.psp_allow_variable,
                "use_parliament_calendar": plan.settings.use_parliament_calendar,
                "weekday_rules": [
                    {"key": r.key, "label": r.label, "weekday": r.weekday}
                    for r in plan.settings.weekday_rules.values()
                ],
            },
            "groups": [{"id": g.id, "label": g.label, "enabled": g.enabled} for g in plan.groups.values()],
            "phases": [self._phase(r, plan) for r in result.phases],
            "summary": {
                "start": first.isoformat() if first else None,
                "end": last.isoformat() if last else None,
                "end_approximate": bool(active and active[-1].approximate),
                "end_month": month_label(last) if last else None,
                "total_days": (last - first).days if first and last else None,
                "errors": sum(1 for r in active for i in r.issues if i.level == "error"),
                "warnings": sum(1 for r in active for i in r.issues if i.level == "warning"),
            },
            "holidays": [
                {"date": d.isoformat(), "name": n, "weekday": d.weekday()}
                for y in years for d, n in self.calendar.holidays(y).items()
            ],
            "weekday_names": list(WEEKDAY_NAMES),
            "parliament": self._parliament(result, first, last),
        }

    def _parliament(self, result: ScheduleResult, first: date | None, last: date | None) -> dict[str, Any]:
        policy = result.policy
        settings = result.plan.settings
        status = {}
        for key, cfg in CHAMBERS.items():
            cal = policy.calendars[key] if policy else ChamberCalendar(key)
            status[key] = {
                "name": cfg.name,
                "weeks": len(cal.weeks),
                "first": cal.first.isoformat() if cal.first else None,
                "last": (cal.last + timedelta(days=6)).isoformat() if cal.last else None,
                "fetched_at": cal.fetched_at,
                "error": cal.error,
                "method": cal.method,
                "sources": cal.sources,
            }
        weeks = []
        if policy and first and last:
            sitting_dates = {}
            for r in result.active:
                if r.phase.sitting:
                    sitting_dates.setdefault(monday(r.date), []).append(r.phase.name)
            day = monday(first)
            while day <= last:
                row = {"monday": day.isoformat(), "phases": sitting_dates.get(day, [])}
                for key in CHAMBERS:
                    auto = policy.calendars[key].weeks.get(day)
                    manual = settings.week_overrides.get(key, {}).get(day)
                    summer = (key == "psp" and auto is None
                              and (day + timedelta(days=1)).month in SittingPolicy.SUMMER_MONTHS)
                    row[key] = {"auto": auto.value if auto else None, "manual": manual.value if manual else None,
                                "summer": summer}
                weeks.append(row)
                day += timedelta(days=7)
        return {"status": status, "weeks": weeks}

    @staticmethod
    def _phase(r: PhaseResult, plan: LegislativePlan) -> dict[str, Any]:
        p = r.phase
        weekday_rule = plan.settings.weekday_rules.get(p.weekday_key) if p.weekday_key else None
        return {
            "id": p.id,
            "name": p.name,
            "note": p.note,
            "group": p.group,
            "group_first": p.group is not None and next(x for x in plan.phases if x.group == p.group) is p,
            "enabled": r.enabled,
            "before_start": plan.before_start(p),
            "is_first": r.order == 1,
            "order": r.order,
            "weekday_label": WEEKDAY_NAMES[weekday_rule.weekday] if weekday_rule else None,
            "constraints": [
                {
                    "value": c.value,
                    "unit": c.unit.value,
                    "kind": c.kind.value,
                    "legal_limit": c.legal_limit,
                    "note": c.note,
                    "ref_name": ref_name,
                }
                for c, ref_name in zip(p.constraints, r.constraint_refs)
            ],
            "date": r.date.isoformat() if r.date else None,
            "computed": r.computed.isoformat() if r.computed else None,
            "override": p.override.isoformat() if p.override else None,
            "weekday": r.date.weekday() if r.date else None,
            "gap_days": r.gap_days,
            "approximate": r.approximate,
            "month_label": month_label(r.date) if r.date else None,
            "reason": r.reason,
            "issues": [{"level": i.level, "message": i.message} for i in r.issues],
            "section": p.section,
            "doc_label": p.doc_label,
            "doc_note": p.doc_text,
            "doc_note_custom": p.user_note is not None,
            "deadline_label": p.deadline_row[0] if p.deadline_row else None,
            "deadline_date": r.deadline_date.isoformat() if r.deadline_date else None,
            "deadline_note": p.deadline_text if p.deadline_row else None,
            "deadline_note_custom": p.user_deadline_note is not None,
            "sitting": ({"chamber": p.sitting.chamber, "weekdays": sorted(p.sitting.weekdays)}
                        if p.sitting else None),
            "ignore_customs": p.ignore_customs,
            "sitting_note": r.sitting_note,
        }
