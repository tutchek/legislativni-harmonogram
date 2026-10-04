import unittest
from datetime import date

from harmonogram.calendar_cz import CzechHolidayCalendar
from harmonogram.deadlines import DayUnit, DeadlineCalculator
from harmonogram.model import PlanStateMapper
from harmonogram.process import DefaultProcessFactory
from harmonogram.scheduler import ScheduleCalculator


class CalendarTests(unittest.TestCase):
    cal = CzechHolidayCalendar()

    def test_easter(self):
        self.assertEqual(self.cal.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(self.cal.easter_sunday(2027), date(2027, 3, 28))
        self.assertEqual(self.cal.easter_sunday(2025), date(2025, 4, 20))

    def test_holidays(self):
        h = self.cal.holidays(2026)
        self.assertIn(date(2026, 4, 3), h)   # Velký pátek
        self.assertIn(date(2026, 4, 6), h)   # Velikonoční pondělí
        self.assertIn(date(2026, 9, 28), h)
        self.assertEqual(len(h), 13)
        self.assertNotIn(date(2015, 4, 3), self.cal.holidays(2015))  # Velký pátek až od 2016

    def test_working_day(self):
        self.assertFalse(self.cal.is_working_day(date(2026, 10, 28)))
        self.assertFalse(self.cal.is_working_day(date(2026, 10, 3)))  # sobota
        self.assertTrue(self.cal.is_working_day(date(2026, 10, 29)))


class DeadlineTests(unittest.TestCase):
    calc = DeadlineCalculator(CzechHolidayCalendar())

    def test_legal_counting_example(self):
        # 1. 3. 2027 (po): lhůta 10 dnů běží 2.–11., úkon nejdříve 12.
        self.assertEqual(self.calc.earliest_after(date(2027, 3, 1), 10, DayUnit.CALENDAR), date(2027, 3, 12))

    def test_end_on_weekend_is_shifted(self):
        # 17. 2. 2027 + 10 = so 27. 2. → konec po 1. 3. → úkon út 2. 3.
        self.assertEqual(self.calc.earliest_after(date(2027, 2, 17), 10, DayUnit.CALENDAR), date(2027, 3, 2))

    def test_working_days_skip_holiday(self):
        # 27. 10. 2026 (út) + 5 PD, 28. 10. je svátek → 29, 30, 2, 3, 4 → úkon 5. 11.
        self.assertEqual(self.calc.earliest_after(date(2026, 10, 27), 5, DayUnit.WORKING), date(2026, 11, 5))

    def test_latest_by(self):
        self.assertEqual(self.calc.latest_by(date(2027, 5, 19), 30, DayUnit.CALENDAR), date(2027, 6, 18))


class ScheduleTests(unittest.TestCase):
    def build(self, state=None):
        plan = DefaultProcessFactory().build(date(2026, 10, 1))
        plan = PlanStateMapper.apply_state(plan, state)
        return {r.phase.id: r for r in ScheduleCalculator(plan).calculate().phases}

    def test_weekdays(self):
        r = self.build()
        self.assertEqual(r["porada1_odeslani"].date.weekday(), 3)
        self.assertEqual(r["porada1"].date.weekday(), 1)
        self.assertEqual(r["vlada"].date.weekday(), 0)

    def test_hard_limit_cannot_be_shortened(self):
        r = self.build({"phases": {"cteni1": {"constraints": [{"value": 3, "unit": "calendar"}]}}})
        self.assertGreaterEqual((r["cteni1"].date - r["psp_predani"].date).days, 11)

    def test_override_propagates_and_is_validated(self):
        base = self.build()
        state = {"phases": {"cteni1": {"override": base["psp_predani"].date.replace(day=base["psp_predani"].date.day + 1).isoformat()}}}
        r = self.build(state)
        self.assertTrue(any(i.level == "error" for i in r["cteni1"].issues))
        self.assertLess(r["vybor1"].date, base["vybor1"].date)

    def test_optional_group_skipped(self):
        r = self.build({"groups": {"porada2": False}})
        self.assertFalse(r["porada2"].enabled)
        self.assertGreater(r["vlada_predani"].date, r["mpr_vyp"].date)


class MultiVariantWordTests(unittest.TestCase):
    def export(self, states):
        import io, re, zipfile
        from harmonogram.server import HarmonogramService
        svc = HarmonogramService()
        payload = {"variants": [{"name": n, "state": s} for n, s in states], "primary": 0}
        xml = zipfile.ZipFile(io.BytesIO(svc.export_docx(payload))).read("word/document.xml").decode("utf-8")
        return xml, re.findall(r'<w:gridCol w:w="(\d+)"/>', xml)

    def test_single_variant_keeps_termin(self):
        xml, grid = self.export([("Základní", {"settings": {"start_date": "2026-10-01"}})])
        self.assertIn(">TERMÍN<", xml)
        self.assertEqual(grid, ["1051", "1628", "1984", "5915"])

    def test_column_per_variant(self):
        xml, grid = self.export([
            ("Rychlá", {"settings": {"start_date": "2026-10-01"}}),
            ("Jen parlament", {"settings": {"start_date": "2026-10-05", "first_phase": "psp_predani"}}),
            ("Se Senátem zpět", {"settings": {"start_date": "2026-10-01"}, "groups": {"psp_senat": True}}),
        ])
        self.assertNotIn(">TERMÍN<", xml)
        for name in ("Rychlá", "Jen parlament", "Se Senátem zpět"):
            self.assertIn(f">{name}<", xml)
        self.assertEqual(len(grid), 6)
        self.assertEqual(sum(map(int, grid)), 1051 + 1628 + 1984 + 5915)
        self.assertIn(">—<", xml)                   # „Jen parlament“ nemá VPŘ
        self.assertIn("5. října 2026", xml)


class FirstPhaseTests(unittest.TestCase):
    def test_start_from_parliament(self):
        plan = DefaultProcessFactory().build(date(2026, 10, 1))
        plan = PlanStateMapper.apply_state(plan, {"settings": {"first_phase": "psp_predani",
                                                              "start_date": "2026-10-05"}})
        result = ScheduleCalculator(plan).calculate()
        active = result.active
        self.assertEqual(active[0].phase.id, "psp_predani")
        self.assertEqual(active[0].date, date(2026, 10, 5))
        self.assertEqual(active[0].order, 1)
        self.assertFalse(any(r.phase.id in ("vpr", "vlada") for r in active))
        r = {x.phase.id: x for x in active}
        self.assertGreaterEqual((r["cteni1"].date - r["psp_predani"].date).days, 11)

    def test_word_export_starts_with_first_phase(self):
        import io, zipfile
        from harmonogram.docx_export import DocxHarmonogramExporter
        from harmonogram.server import template_path
        plan = PlanStateMapper.apply_state(DefaultProcessFactory().build(date(2026, 10, 1)),
                                           {"settings": {"first_phase": "cteni1"}})
        data = DocxHarmonogramExporter(template_path()).export(ScheduleCalculator(plan).calculate())
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
        self.assertNotIn(">VPŘ<", xml)
        self.assertNotIn("předložení vládě", xml)
        self.assertIn("1. čtení", xml)

    def test_unknown_first_phase_ignored(self):
        plan = PlanStateMapper.apply_state(DefaultProcessFactory().build(date(2026, 10, 1)),
                                           {"settings": {"first_phase": "neexistuje"}})
        self.assertEqual(ScheduleCalculator(plan).calculate().active[0].phase.id, "vpr")


class VariantStoreTests(unittest.TestCase):
    def test_roundtrip_and_normalize(self):
        import pathlib, tempfile
        from harmonogram.variants import VariantStore
        store = VariantStore(pathlib.Path(tempfile.mkdtemp()) / "v.json")
        self.assertEqual(store.load(), {"active": None, "variants": []})
        doc = store.save({"active": "b", "variants": [
            {"id": "a", "name": "Základní", "state": {"settings": {}}},
            {"id": "b", "name": "  ", "state": "nesmysl"},
            {"id": "a", "name": "Duplicitní id"},
        ]})
        self.assertEqual(doc["active"], "b")
        self.assertEqual(doc["variants"][1]["name"], "Varianta 2")
        self.assertIsNone(doc["variants"][1]["state"])
        self.assertNotEqual(doc["variants"][2]["id"], "a")
        self.assertEqual(store.load()["variants"][0]["name"], "Základní")
        self.assertEqual(store.save({"active": "zz", "variants": [{"id": "x", "name": "X"}]})["active"], "x")


class DocxExportTests(unittest.TestCase):
    def test_export_fills_template(self):
        import io, zipfile
        from harmonogram.docx_export import DocxHarmonogramExporter
        from harmonogram.server import template_path
        plan = DefaultProcessFactory().build(date(2026, 10, 1))
        plan.settings.title = "Návrh zákona o testu"
        plan.settings.effective = "1. ledna 2028"
        result = ScheduleCalculator(plan).calculate()
        data = DocxHarmonogramExporter(template_path()).export(result)
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
        self.assertIn("Návrh zákona o testu", xml)
        self.assertIn("1. října 2026", xml)
        self.assertIn("Účinnost", xml)
        self.assertNotIn("dorovnávacích", xml)


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


class ParliamentParserTests(unittest.TestCase):
    """Na skutečných stránkách psp.cz a senat.cz (uloženy 30. 9. 2026)."""

    def parse(self, name, chamber):
        from harmonogram.parliament import CHAMBERS, HarmonogramPageParser, PageDownloader
        page = PageDownloader.decode((FIXTURES / name).read_bytes())
        return page, HarmonogramPageParser(CHAMBERS[chamber])

    def test_psp_half_year(self):
        from harmonogram.parliament import WeekType as W
        page, parser = self.parse("psp_2026_2.html", "psp")
        weeks = parser.weeks(page)
        self.assertEqual(len(weeks), 26)
        expected = {
            date(2026, 7, 6): W.VARIABLE,     # „28. | 6. - 12. 7.“ – číslo týdne se nesmí splést s datem
            date(2026, 7, 20): W.OTHER,       # prázdná náplň
            date(2026, 8, 31): W.OTHER,       # výbory
            date(2026, 9, 7): W.PLENARY,      # Poslanecká sněmovna
            date(2026, 9, 21): W.VARIABLE,
            date(2026, 10, 5): W.OTHER,       # poslanecký týden
            date(2026, 10, 12): W.PLENARY,
            date(2026, 10, 19): W.PLENARY,    # „… 1. čt. zákona o st. rozpočtu“
            date(2026, 11, 30): W.PLENARY,
            date(2026, 12, 28): W.OTHER,      # „28. 12. - 3. 1. 2027“ – začátek v roce 2026
        }
        for day, week_type in expected.items():
            self.assertEqual(weeks[day], week_type, day)

    def test_psp_links_to_next_half(self):
        from harmonogram.parliament import ParliamentCalendarStore
        page, parser = self.parse("psp_2026_2.html", "psp")
        links = ParliamentCalendarStore.relevant_links(
            parser.links(page, "https://www.psp.cz/sqw/hp.sqw?k=200"), date(2026, 9, 30))
        self.assertIn("https://www.psp.cz/sqw/hp.sqw?k=201&r=2027&q=1", links)
        self.assertFalse(any("r=2025" in u or "k=202" in u for u in links))

    def test_senat_half_years(self):
        from harmonogram.parliament import WeekType as W
        page, parser = self.parse("senat_2026_2.html", "senat")
        weeks = parser.weeks(page)
        self.assertEqual(len(weeks), 20)
        self.assertEqual(weeks[date(2026, 8, 17)], W.PLENARY)
        self.assertEqual(weeks[date(2026, 9, 28)], W.PLENARY)
        self.assertEqual(weeks[date(2026, 10, 5)], W.OTHER)       # výborový
        self.assertEqual(weeks[date(2026, 10, 26)], W.OTHER)      # senátorský
        self.assertEqual(weeks[date(2026, 12, 28)], W.OTHER)      # „28. 12. – 3. 1.“
        page, parser = self.parse("senat_2027_1.html", "senat")
        weeks = parser.weeks(page)
        self.assertEqual(len(weeks), 34)
        self.assertEqual(weeks[date(2027, 1, 4)], W.OTHER)
        self.assertEqual(weeks[date(2027, 1, 11)], W.OTHER)       # poznámka „schůze Poslanecké sněmovny“
        self.assertEqual(weeks[date(2027, 1, 25)], W.PLENARY)
        self.assertEqual(weeks[date(2027, 8, 23)], W.PLENARY)

    def test_senat_index_links(self):
        from harmonogram.parliament import ParliamentCalendarStore
        page, parser = self.parse("senat_index.html", "senat")
        self.assertEqual(parser.weeks(page), {})
        links = ParliamentCalendarStore.relevant_links(
            parser.links(page, "https://www.senat.cz/cinnost/harmonogram/index.php"), date(2026, 9, 30))
        ids = sorted(int(__import__("re").search(r"id=(\d+)", u).group(1)) for u in links)
        self.assertEqual(ids, [44, 45, 46])                       # pololetí 2026 a 2027, ne starší

    def test_store_crawls_from_index(self):
        from harmonogram.parliament import CHAMBERS, ParliamentCalendarStore, WeekType

        pages = {
            CHAMBERS["senat"].start_url: "senat_index.html",
            "id=45": "senat_2026_2.html",
            "id=46": "senat_2027_1.html",
            "id=44": "senat_2026_2.html",
            CHAMBERS["psp"].start_url: "psp_2026_2.html",
        }

        class FakeDownloader:
            last_method = "test"
            def get(self, url):
                for key, name in pages.items():
                    if key == url or (key.startswith("id=") and key + "&" in url):
                        from harmonogram.parliament import PageDownloader
                        return PageDownloader.decode((FIXTURES / name).read_bytes())
                raise OSError("404")

        tmp = __import__("tempfile").mkdtemp()
        store = ParliamentCalendarStore(__import__("pathlib").Path(tmp) / "c.json", FakeDownloader())
        store.refresh()
        senat = store.calendars["senat"]
        self.assertEqual(senat.weeks[date(2027, 1, 25)], WeekType.PLENARY)
        self.assertEqual(senat.weeks[date(2026, 10, 19)], WeekType.PLENARY)
        self.assertEqual(store.calendars["psp"].weeks[date(2026, 9, 7)], WeekType.PLENARY)
        # mezipaměť se po znovunačtení použije
        again = ParliamentCalendarStore(__import__("pathlib").Path(tmp) / "c.json", FakeDownloader())
        self.assertEqual(len(again.calendars["senat"].weeks), len(senat.weeks))


class DownloaderTests(unittest.TestCase):
    def test_falls_back_and_remembers_working_method(self):
        from harmonogram.net import Fetcher, FetchError, ResilientDownloader

        class Failing(Fetcher):
            name = "proxy 407"
            calls = 0
            def fetch(self, url, timeout):
                Failing.calls += 1
                raise FetchError("HTTP 407")

        class Working(Fetcher):
            name = "ok"
            def fetch(self, url, timeout):
                return b"<html>ok</html>"

        d = ResilientDownloader([Failing(), Working()])
        self.assertEqual(d.fetch("https://x"), b"<html>ok</html>")
        self.assertEqual(d.last_method, "ok")
        d.fetch("https://y")
        self.assertEqual(Failing.calls, 1)   # podruhé se začíná funkční metodou

    def test_all_fail_reports_each(self):
        from harmonogram.net import Fetcher, FetchError, ResilientDownloader

        class Bad(Fetcher):
            def __init__(self, n): self.name = n
            def fetch(self, url, timeout): raise FetchError("nejde")

        with self.assertRaises(FetchError) as ctx:
            ResilientDownloader([Bad("A"), Bad("B")]).fetch("https://x")
        self.assertIn("A: nejde", str(ctx.exception))
        self.assertIn("B: nejde", str(ctx.exception))


class SittingRulesTests(unittest.TestCase):
    def calc(self, state=None, psp=None, senat=None):
        from harmonogram.parliament import ChamberCalendar
        plan = PlanStateMapper.apply_state(DefaultProcessFactory().build(date(2026, 10, 1)), state)
        parliament = {"psp": ChamberCalendar("psp", psp or {}), "senat": ChamberCalendar("senat", senat or {})}
        return {r.phase.id: r for r in ScheduleCalculator(plan, parliament=parliament).calculate().phases}

    def weeks(self, start, end, plenary_every=3):
        from harmonogram.parliament import WeekType
        result, day, i = {}, start, 0
        while day <= end:
            result[day] = WeekType.PLENARY if i % plenary_every == 0 else WeekType.OTHER
            day += __import__("datetime").timedelta(days=7)
            i += 1
        return result

    def test_psp_sittings_only_in_plenary_weeks_tue_fri(self):
        from harmonogram.parliament import WeekType, monday
        psp = self.weeks(date(2027, 1, 4), date(2027, 12, 27))
        r = self.calc(psp=psp)
        for pid in ("cteni1", "cteni2", "cteni3"):
            d = r[pid].date
            self.assertEqual(psp[monday(d)], WeekType.PLENARY, pid)
            self.assertIn(d.weekday(), (1, 2, 3, 4), pid)
        self.assertIn(r["cteni3"].date.weekday(), (2, 4))

    def test_ignore_customs(self):
        psp = self.weeks(date(2027, 1, 4), date(2027, 12, 27), plenary_every=8)
        strict = self.calc(psp=psp)
        loose = self.calc({"phases": {"cteni1": {"ignore_customs": True}}}, psp=psp)
        self.assertLessEqual(loose["cteni1"].date, strict["cteni1"].date)

    def test_senat_last_plenary_day_or_fiction(self):
        from harmonogram.parliament import WeekType, monday
        r = self.calc(senat=self.weeks(date(2027, 1, 4), date(2027, 12, 27), plenary_every=2))
        self.assertEqual(r["senat"].phase.sitting.chamber, "senat")
        self.assertLessEqual((r["senat"].date - r["cteni3"].date).days, 32)
        r2 = self.calc(senat={monday(date(2027, 1, 4)) + __import__("datetime").timedelta(days=7 * i): WeekType.OTHER
                              for i in range(60)})
        self.assertTrue(any("marné" in r2["senat"].reason or "V lhůtě" in i.message for i in r2["senat"].issues))

    def test_manual_extra_sitting_allows_psp(self):
        from harmonogram.parliament import WeekType
        psp = {date(2027, 3, 1) + __import__("datetime").timedelta(days=7 * i): WeekType.OTHER for i in range(30)}
        base = self.calc(psp=psp)
        extra = self.calc({"settings": {"week_overrides": {"psp": {"2027-03-01": "extra"}}}}, psp=psp)
        self.assertEqual(extra["cteni1"].date, date(2027, 3, 2))
        self.assertNotEqual(base["cteni1"].date, date(2027, 3, 2))
        self.assertIn("mimořádná", extra["cteni1"].sitting_note)

    def test_psp_summer_assumption(self):
        # bez harmonogramu PSP nezasedá v červenci a srpnu
        r = self.calc({"settings": {"first_phase": "psp_predani", "start_date": "2027-06-21"}})
        self.assertNotIn(r["cteni1"].date.month, (7, 8))
        self.assertEqual(r["cteni1"].date, date(2027, 9, 1))
        # …ale zveřejněný harmonogram má přednost
        from harmonogram.parliament import WeekType
        psp = {date(2027, 7, 5): WeekType.PLENARY}
        r = self.calc({"settings": {"first_phase": "psp_predani", "start_date": "2027-06-21"}}, psp=psp)
        self.assertEqual(r["cteni1"].date, date(2027, 7, 7))   # 6. 7. je svátek
        # Senát o prázdninách může (bez údajů se neomezuje)
        self.assertIsNotNone(r["senat"].date)

    def test_approximate_outside_known_calendar(self):
        from harmonogram.parliament import WeekType
        from harmonogram.scheduler import month_label
        psp = {date(2027, 3, 1): WeekType.PLENARY}    # známý jen jeden týden
        r = self.calc({"settings": {"first_phase": "psp_predani", "start_date": "2027-02-17"}}, psp=psp)
        self.assertFalse(r["psp_predani"].approximate)
        self.assertFalse(r["cteni1"].approximate)       # 2. 3. 2027 – ve známém týdnu
        self.assertTrue(r["cteni2"].approximate)        # mimo zveřejněný harmonogram
        self.assertTrue(r["sbirka"].approximate)        # závisí na orientačním termínu
        self.assertEqual(month_label(date(2027, 10, 13)), "říjen 2027")
        # ručně zadané datum je pevné
        fixed = self.calc({"settings": {"first_phase": "psp_predani", "start_date": "2027-02-17"},
                           "phases": {"cteni2": {"override": "2027-05-05"}}}, psp=psp)
        self.assertFalse(fixed["cteni2"].approximate)

    def test_word_uses_month_for_approximate(self):
        import io, zipfile
        from harmonogram.docx_export import DocRowsBuilder
        from harmonogram.parliament import ChamberCalendar
        plan = PlanStateMapper.apply_state(DefaultProcessFactory().build(date(2026, 10, 1)),
                                           {"settings": {"first_phase": "psp_predani", "start_date": "2027-02-17"}})
        cal = {"psp": ChamberCalendar("psp"), "senat": ChamberCalendar("senat")}
        rows = DocRowsBuilder().build(ScheduleCalculator(plan, parliament=cal).calculate())
        self.assertEqual(rows[0].term, "17. února 2027")
        self.assertTrue(all(__import__("re").fullmatch(r"[a-zěščřžýáíéůú]+ \d{4}", row.term) for row in rows[1:]))

    def test_president_15_days(self):
        r = self.calc()
        self.assertLessEqual((r["prezident"].date - r["senat"].date).days, 20)  # 15 dnů + posun přes víkend a svátky 5.–6. 7.


if __name__ == "__main__":
    unittest.main()
