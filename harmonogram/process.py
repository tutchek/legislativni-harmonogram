"""Výchozí podoba legislativního procesu (zákon)."""

from __future__ import annotations

from datetime import date

from .calendar_cz import CzechHolidayCalendar
from .deadlines import DayUnit
from .parliament import PSP_DAYS, SENAT_DAYS, THIRD_READING_DAYS, SittingRule
from .model import (
    Constraint,
    ConstraintKind,
    LegislativePlan,
    OptionalGroup,
    Phase,
    ScheduleSettings,
    WeekdayRule,
)

D = DayUnit.CALENDAR
PD = DayUnit.WORKING
MAX = ConstraintKind.MAX


class DefaultProcessFactory:
    """Sestaví výchozí fáze, lhůty a globální nastavení."""

    def __init__(self, calendar: CzechHolidayCalendar | None = None):
        self.calendar = calendar or CzechHolidayCalendar()

    def build(self, today: date | None = None) -> LegislativePlan:
        start = self.calendar.next_working_day(today or date.today(), inclusive=True)
        settings = ScheduleSettings(
            start_date=start,
            weekday_rules={
                "odeslani_porada": WeekdayRule("odeslani_porada", "Odeslání na poradu vedení", 3),
                "porada": WeekdayRule("porada", "Porada vedení", 1),
                "vlada": WeekdayRule("vlada", "Jednání vlády", 0),
            },
        )
        groups = {
            "porada2": OptionalGroup("porada2", "Druhá porada vedení po MPŘ", True),
            "psp_senat": OptionalGroup("psp_senat", "Senát vrátil – PSP znovu hlasuje", False),
            "psp_prezident": OptionalGroup("psp_prezident", "Prezident vrátil – PSP znovu hlasuje", False),
        }
        return LegislativePlan(settings, self._phases(), groups)

    @staticmethod
    def _phases() -> list[Phase]:
        return [
            Phase("vpr", "Rozeslání do vnitřního připomínkového řízení",
                  note="Začátek harmonogramu – datum zahájení.",
                  section="VPŘ", doc_label="rozeslání do vnitřního"),
            Phase("vpr_vyp", "Vypořádání vnitřního připomínkového řízení",
                  constraints=[Constraint(5, PD, note="délka vnitřního připomínkového řízení")],
                  section="VPŘ", doc_label="vypořádání připomínek",
                  deadline_row=("uplatnění připomínek",
                                "Lhůta k uplatnění připomínek je {c0}.")),
            Phase("porada1_odeslani", "Odeslání na poradu vedení", weekday_key="odeslani_porada",
                  constraints=[Constraint(3, PD, note="doba na vypořádání")],
                  section="VPŘ", doc_label="předložení poradě vedení"),
            Phase("porada1", "Porada vedení", weekday_key="porada",
                  constraints=[Constraint(0, D)],
                  section="VPŘ", doc_label="porada vedení"),
            Phase("mpr", "Rozeslání do meziresortního připomínkového řízení",
                  constraints=[Constraint(0, D)],
                  section="MPŘ", doc_label="rozeslání do meziresortního"),
            Phase("mpr_vyp", "Vypořádání MPŘ",
                  constraints=[Constraint(20, PD, note="délka MPŘ")],
                  section="MPŘ", doc_label="vypořádání připomínek",
                  deadline_row=("termín pro uplatnění připomínek",
                                "Lhůta pro uplatnění připomínek podle čl. 8 odst. 3 LPV je {c0}.")),
            Phase("porada2_odeslani", "Odeslání na poradu vedení (2.)", weekday_key="odeslani_porada",
                  group="porada2", constraints=[Constraint(7, D, note="vypořádání trvá nejméně týden")],
                  section="MPŘ", doc_label="předložení poradě vedení"),
            Phase("porada2", "Porada vedení (2.)", weekday_key="porada", group="porada2",
                  constraints=[Constraint(0, D)],
                  section="MPŘ", doc_label="porada vedení"),
            Phase("vlada_predani", "Předání vládě",
                  note="Bez 2. porady se vazba počítá od vypořádání MPŘ.",
                  constraints=[Constraint(0, D)],
                  section="Vláda", doc_label="předložení vládě"),
            Phase("vlada", "Jednání vlády", weekday_key="vlada",
                  constraints=[Constraint(60, D, note="projednání v LRV")],
                  section="Vláda", doc_label="jednání vlády",
                  doc_note="Lhůta pro projednání návrhu v LRV podle čl. 10 odst. 2 LPV je {c0} od předložení."),
            Phase("psp_predani", "Předání do Poslanecké sněmovny",
                  constraints=[Constraint(1, PD)],
                  section="PS", doc_label="předložení"),
            Phase("cteni1", "1. čtení", sitting=SittingRule("psp", PSP_DAYS),
                  constraints=[Constraint(10, D, legal_limit=10,
                                          note="nejméně 10 dnů od předání do PSP")],
                  section="PS", doc_label="1. čtení",
                  doc_note="Projednávání lze zahájit až po uplynutí 10 dnů od doručení návrhu poslancům."),
            Phase("vybor1", "Garanční výbor",
                  constraints=[Constraint(1, D)],
                  section="PS", doc_label="výbory"),
            Phase("cteni2", "2. čtení", sitting=SittingRule("psp", PSP_DAYS),
                  constraints=[Constraint(1, PD),
                               Constraint(60, D, ref="cteni1", note="lhůta k projednání ve výborech")],
                  section="PS", doc_label="2. čtení",
                  doc_note="Lhůta pro projednání ve výborech je {c1} (standardně 60 dnů, lze zkrátit)."),
            Phase("vybor2", "Garanční výbor (po 2. čtení)",
                  constraints=[Constraint(1, PD)],
                  section="PS", doc_label="garanční výbor",
                  doc_note="Usnesení garančního výboru nejméně 24 hodin před zahájením 3. čtení."),
            Phase("cteni3", "3. čtení", sitting=SittingRule("psp", THIRD_READING_DAYS),
                  constraints=[Constraint(1, PD),
                               Constraint(14, D, ref="cteni2", note="3. čtení nejdříve 14 dnů po 2. čtení")],
                  section="PS", doc_label="3. čtení",
                  note="Jen ve středu nebo v pátek.",
                  doc_note="Lhůta mezi 2. a 3. čtením je {c1} (standardně 14 dnů, lze zkrátit)."),
            Phase("senat", "Senát", sitting=SittingRule("senat", SENAT_DAYS),
                  note="Poslední plenární den Senátu v lhůtě. Lhůta běží od doručení Senátu – zde od 3. čtení.",
                  constraints=[Constraint(30, D, MAX, legal_limit=30, note="Senát do 30 dnů")],
                  section="Senát", doc_label="projednání",
                  doc_note="Senát se musí usnést do 30 dnů od postoupení; nevyjádří-li se, platí, že zákon je přijat."),
            Phase("psp_po_senatu", "PSP po vrácení Senátem", sitting=SittingRule("psp", PSP_DAYS), group="psp_senat",
                  constraints=[Constraint(10, D, legal_limit=10, note="nejméně 10 dnů po vrácení")],
                  section="PS", doc_label="projednání",
                  doc_note="Vrácený návrh lze projednat nejdříve za 10 dnů od doručení poslancům (§ 97 JŘ PS)."),
            Phase("prezident", "Prezident",
                  note="Plánováno na konec lhůty.",
                  constraints=[Constraint(15, D, MAX, legal_limit=15, note="prezident do 15 dnů od předložení")],
                  section="Prezident", doc_label="vyjádření",
                  doc_note="Lhůta {c0} ode dne předložení zákona k podpisu."),
            Phase("psp_po_prezidentovi", "PSP po vrácení prezidentem", sitting=SittingRule("psp", PSP_DAYS), group="psp_prezident",
                  constraints=[Constraint(10, D, legal_limit=10, note="nejméně 10 dnů po vrácení")],
                  section="PS", doc_label="projednání",
                  doc_note="Vrácený zákon lze projednat nejdříve za 10 dnů od doručení poslancům."),
            Phase("sbirka", "Předání do Sbírky zákonů a vyhlášení",
                  constraints=[Constraint(7, D, note="zpracování pro Sbírku")],
                  section="Sbírka zákonů", doc_label="vyhlášení"),
        ]
