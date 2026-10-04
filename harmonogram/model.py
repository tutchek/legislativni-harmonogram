"""Datový model harmonogramu: fáze, vazby mezi nimi a globální nastavení."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

from .deadlines import DayUnit
from .parliament import SittingRule, WeekType

WEEKDAY_NAMES = ("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")
PREVIOUS = "prev"  # odkaz na nejbližší předchozí zařazenou fázi


class ConstraintKind(str, Enum):
    MIN = "min"   # fáze nastane nejdříve po uplynutí lhůty
    MAX = "max"   # fáze musí nastat nejpozději do konce lhůty


@dataclass
class Constraint:
    """Časová vazba fáze na dřívější fázi."""

    value: int
    unit: DayUnit = DayUnit.CALENDAR
    kind: ConstraintKind = ConstraintKind.MIN
    ref: str = PREVIOUS
    legal_limit: int | None = None   # nepřekročitelný zákonný limit
    note: str = ""

    @property
    def is_hard(self) -> bool:
        return self.legal_limit is not None

    def set_value(self, value: Any) -> None:
        """Nastaví délku lhůty; zákonný limit nelze obejít."""
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        number = max(number, 1 if self.kind is ConstraintKind.MAX else 0)
        if self.legal_limit is not None:
            if self.kind is ConstraintKind.MIN:
                number = max(number, self.legal_limit)
            else:
                number = min(number, self.legal_limit)
        self.value = number

    def set_unit(self, value: Any) -> None:
        if self.legal_limit is None and value in (u.value for u in DayUnit):
            self.unit = DayUnit(value)


@dataclass
class Phase:
    """Jedna fáze (řádek) legislativního procesu."""

    id: str
    name: str
    note: str = ""
    constraints: list[Constraint] = field(default_factory=list)
    weekday_key: str | None = None    # vazba na globální den v týdnu
    group: str | None = None          # volitelná skupina fází
    override: date | None = None      # ručně zadané datum
    # --- podklady pro export do Wordu ---
    section: str = ""                 # oddíl v 1. sloupci (VPŘ, MPŘ, Vláda, PS…)
    doc_label: str = ""               # text ve sloupci PROCES
    doc_note: str = ""                # výchozí poznámka; {c0}, {c1} = délka lhůty
    deadline_row: tuple[str, str] | None = None  # řádek „uplatnění připomínek“ (popisek, poznámka)
    user_note: str | None = None      # poznámka zadaná uživatelem (přebíjí výchozí)
    user_deadline_note: str | None = None
    # --- zasedání komor ---
    sitting: SittingRule | None = None  # fáze je schůzí PSP / Senátu
    ignore_customs: bool = False        # „Ignorovat zvyklosti“ – jakýkoli pracovní týden

    def constraint_text(self, index: int) -> str:
        if index >= len(self.constraints):
            return ""
        c = self.constraints[index]
        return f"{c.value} {'pracovních dnů' if c.unit.value == 'working' else 'dnů'}"

    def render_note(self, template: str) -> str:
        values = {f"c{i}": self.constraint_text(i) for i in range(len(self.constraints))}
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            return template

    @property
    def default_note(self) -> str:
        return self.render_note(self.doc_note)

    @property
    def doc_text(self) -> str:
        return self.user_note if self.user_note is not None else self.default_note

    @property
    def default_deadline_note(self) -> str:
        return self.render_note(self.deadline_row[1]) if self.deadline_row else ""

    @property
    def deadline_text(self) -> str:
        return self.user_deadline_note if self.user_deadline_note is not None else self.default_deadline_note


@dataclass
class OptionalGroup:
    id: str
    label: str
    enabled: bool


@dataclass
class WeekdayRule:
    key: str
    label: str
    weekday: int  # 0 = pondělí


@dataclass
class ScheduleSettings:
    start_date: date
    weekday_rules: dict[str, WeekdayRule]
    shift_end_to_working_day: bool = True
    first_phase: str = "vpr"         # id fáze, kterou harmonogram začíná
    title: str = "Návrh zákona o …"
    effective: str = ""               # účinnost (volný text, např. „1. ledna 2028“)
    psp_allow_variable: bool = True   # schůze PSP i ve variabilních týdnech
    use_parliament_calendar: bool = True
    # ruční opravy typu týdne: {"psp": {pondělí: WeekType}, "senat": {...}}
    week_overrides: dict[str, dict[date, WeekType]] = field(default_factory=lambda: {"psp": {}, "senat": {}})


@dataclass
class LegislativePlan:
    settings: ScheduleSettings
    phases: list[Phase]
    groups: dict[str, OptionalGroup]

    def phase(self, phase_id: str) -> Phase:
        return next(p for p in self.phases if p.id == phase_id)

    def before_start(self, phase: Phase) -> bool:
        """Fáze předchází zvolenému prvnímu kroku (harmonogram začíná až později)."""
        ids = [p.id for p in self.phases]
        first = self.settings.first_phase if self.settings.first_phase in ids else ids[0]
        return ids.index(phase.id) < ids.index(first)

    def is_enabled(self, phase: Phase) -> bool:
        if self.before_start(phase):
            return False
        return phase.group is None or self.groups[phase.group].enabled


class PlanStateMapper:
    """Přenáší uživatelsky měnitelné hodnoty mezi plánem a JSON stavem.

    Struktura procesu (názvy, vazby, zákonné limity) je vždy dána kódem;
    stav nese jen to, co uživatel smí měnit. Uložené soubory tak přežijí
    i úpravu výchozího procesu.
    """

    @staticmethod
    def to_state(plan: LegislativePlan) -> dict[str, Any]:
        return {
            "settings": {
                "start_date": plan.settings.start_date.isoformat(),
                "first_phase": plan.settings.first_phase,
                "weekdays": {k: r.weekday for k, r in plan.settings.weekday_rules.items()},
                "shift_end_to_working_day": plan.settings.shift_end_to_working_day,
                "title": plan.settings.title,
                "effective": plan.settings.effective,
                "psp_allow_variable": plan.settings.psp_allow_variable,
                "use_parliament_calendar": plan.settings.use_parliament_calendar,
                "week_overrides": {
                    chamber: {d.isoformat(): t.value for d, t in sorted(weeks.items())}
                    for chamber, weeks in plan.settings.week_overrides.items()
                },
            },
            "groups": {k: g.enabled for k, g in plan.groups.items()},
            "phases": {
                p.id: {
                    "override": p.override.isoformat() if p.override else None,
                    "note": p.user_note,
                    "ignore_customs": p.ignore_customs,
                    "deadline_note": p.user_deadline_note,
                    "constraints": [{"value": c.value, "unit": c.unit.value} for c in p.constraints],
                }
                for p in plan.phases
            },
        }

    @classmethod
    def apply_state(cls, plan: LegislativePlan, state: dict[str, Any] | None) -> LegislativePlan:
        if not state:
            return plan
        settings = state.get("settings") or {}
        start = cls._parse_date(settings.get("start_date"))
        if start:
            plan.settings.start_date = start
        for key, weekday in (settings.get("weekdays") or {}).items():
            rule = plan.settings.weekday_rules.get(key)
            if rule and isinstance(weekday, int) and 0 <= weekday <= 4:
                rule.weekday = weekday
        if "shift_end_to_working_day" in settings:
            plan.settings.shift_end_to_working_day = bool(settings["shift_end_to_working_day"])

        if settings.get("first_phase") in {p.id for p in plan.phases}:
            plan.settings.first_phase = settings["first_phase"]
        for key in ("psp_allow_variable", "use_parliament_calendar"):
            if key in settings:
                setattr(plan.settings, key, bool(settings[key]))
        for chamber, weeks in (settings.get("week_overrides") or {}).items():
            if chamber not in plan.settings.week_overrides or not isinstance(weeks, dict):
                continue
            for day, value in weeks.items():
                parsed = cls._parse_date(day)
                if parsed and value in (t.value for t in WeekType):
                    plan.settings.week_overrides[chamber][parsed - timedelta(days=parsed.weekday())] = WeekType(value)
        for key in ("title", "effective"):
            if isinstance(settings.get(key), str):
                setattr(plan.settings, key, settings[key].strip()[:500])

        for key, enabled in (state.get("groups") or {}).items():
            if key in plan.groups:
                plan.groups[key].enabled = bool(enabled)

        phases_state = state.get("phases") or {}
        for phase in plan.phases:
            data = phases_state.get(phase.id)
            if not data:
                continue
            phase.override = cls._parse_date(data.get("override"))
            phase.ignore_customs = bool(data.get("ignore_customs")) and phase.sitting is not None
            for constraint, c_state in zip(phase.constraints, data.get("constraints") or []):
                constraint.set_unit(c_state.get("unit"))
                constraint.set_value(c_state.get("value"))
            phase.user_note = cls._note(data.get("note"), phase.default_note)
            phase.user_deadline_note = cls._note(data.get("deadline_note"), phase.default_deadline_note)
        return plan

    @staticmethod
    def _note(value: Any, default: str) -> str | None:
        """Vlastní poznámka; shodná s výchozí se neukládá (výchozí se pak mění s lhůtou)."""
        if not isinstance(value, str) or value.strip() == default.strip():
            return None
        return value.strip()[:2000]

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
