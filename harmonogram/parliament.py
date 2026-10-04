"""Harmonogramy zasedání Poslanecké sněmovny a Senátu.

Stránky psp.cz a senat.cz se stahují za běhu aplikace (přes Windows včetně
firemní proxy s ověřením, viz net.py), výsledek se ukládá do mezipaměti v domovské složce uživatele, aby
aplikace fungovala i offline. Parser je záměrně tolerantní: z každého
řádku/bloku stránky vezme první datum a podle klíčových slov určí typ týdne.
Co parser nepozná, lze v aplikaci ručně opravit u konkrétního týdne.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from .calendar_cz import CzechHolidayCalendar
from .net import ResilientDownloader


class WeekType(str, Enum):
    PLENARY = "plenary"     # PSP: „Poslanecká sněmovna“; Senát: plenární týden
    VARIABLE = "variable"   # PSP: variabilní týden (případná mimořádná schůze)
    EXTRA = "extra"         # PSP: ručně zadaná mimořádná schůze
    OTHER = "other"         # výbory, kluby, poslanecký/senátorský týden…

    def label_for(self, chamber: str) -> str:
        if chamber == "senat":
            return {"plenary": "plenární schůze"}.get(self.value, "žádná schůze")
        return {"plenary": "řádná schůze", "variable": "variabilní týden",
                "extra": "mimořádná schůze", "other": "žádná schůze"}[self.value]

    @property
    def priority(self) -> int:
        return {"extra": 4, "plenary": 3, "variable": 2, "other": 1}[self.value]


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
@dataclass
class ChamberConfig:
    key: str                       # "psp" | "senat"
    name: str
    start_url: str
    link_pattern: re.Pattern       # odkazy na další období harmonogramu
    classify: Callable[[str, str], WeekType]   # (text náplně, HTML řádku) → typ týdne
    content_index: int             # index buňky „Náplň“ mezi buňkami ZA buňkou s datem
    max_pages: int = 8


def _classify_psp(text: str, row_html: str) -> WeekType:
    """psp.cz – sloupec „Náplň“: „Poslanecká sněmovna“, „variabilní týden“, „výbory“, „poslanecký týden“."""
    t = text.lower()
    if "variabil" in t:
        return WeekType.VARIABLE
    if "sněmovn" in t:
        return WeekType.PLENARY
    return WeekType.OTHER


def _classify_senat(text: str, row_html: str) -> WeekType:
    """senat.cz – „Plenární týden“ (třída řádku napln-2), „Výborový týden“, „Senátorský týden“."""
    t = text.lower()
    if "plenár" in t or "plén" in t or re.search(r"\bnapln-2\b", row_html[:200]):
        return WeekType.PLENARY
    return WeekType.OTHER


CHAMBERS: dict[str, ChamberConfig] = {
    "psp": ChamberConfig(
        "psp", "Poslanecká sněmovna",
        # aktuální pololetí; odkazy dole vedou na další pololetí (k=201&r=RRRR&q=1|2)
        "https://www.psp.cz/sqw/hp.sqw?k=200",
        re.compile(r"hp\.sqw\?k=20[01](?:&(?:amp;)?[a-z]=\d+)*$", re.I),
        _classify_psp,
        content_index=-1,          # Týden | Datum | Týden cyklu | Náplň
    ),
    "senat": ChamberConfig(
        "senat", "Senát",
        # rozcestník – odkazy na detaily pololetí (?akce=detail&id=NN)
        "https://www.senat.cz/cinnost/harmonogram/index.php",
        re.compile(r"akce=detail", re.I),
        _classify_senat,
        content_index=0,           # Týden | Datum | Náplň Senátu | Poznámky
    ),
}


# ---------------------------------------------------------------------------
@dataclass
class PageLink:
    url: str
    text: str

    @property
    def year(self) -> int | None:
        """Rok období: z textu odkazu („1. pololetí roku 2027“), jinak z URL (r=2027).

        Parametr ke_dni (dnešní datum, které senat.cz přidává ke všem odkazům) se ignoruje.
        """
        for source in (self.text, re.sub(r"ke_dni=[\d.]+", "", self.url)):
            years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", source)
            if years:
                return max(int(y) for y in years)
        return None


class HarmonogramPageParser:
    """Vytáhne z HTML stránky týdny a jejich typ a odkazy na další období.

    Obě komory mají tabulku s řádkem na týden: číslo týdne, rozsah dat bez roku
    („6. - 12. 7.“, „28. 12. - 3. 1. 2027“, „17. 8. – 23. 8.“) a náplň. Rok se
    bere z popisku tabulky („od 17.8.2026 do 3.1.2027“) nebo z nadpisu
    („2. pololetí 2026“) a při přechodu přes Nový rok se zvýší.
    """

    RANGE_RE = re.compile(
        r"^\s*(\d{1,2})\.\s*(?:(\d{1,2})\.\s*(?:(\d{4})\s*)?)?[-–—]\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})?")
    YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

    def __init__(self, config: ChamberConfig):
        self.config = config

    def parse(self, page: str, base_url: str) -> tuple[dict[date, WeekType], list[str]]:
        links = self.links(page, base_url)
        return self.weeks(page), [link.url for link in links]

    # --- týdny --------------------------------------------------------------
    def weeks(self, page: str) -> dict[date, WeekType]:
        body = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
        year = self._start_year(body)
        weeks: dict[date, WeekType] = {}
        if year is None:
            return weeks
        prev_month = None
        for row in re.findall(r"<tr\b.*?</tr>", body, flags=re.S | re.I):
            cells = [self._text(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
            for i, cell in enumerate(cells):
                match = self.RANGE_RE.match(cell)
                if match:
                    break
            else:
                continue
            d1, m1, y1, d2, m2, y2 = match.groups()
            d1, d2, m2 = int(d1), int(d2), int(m2)
            m1 = int(m1) if m1 else m2
            # rok konce týdne
            if y2:
                end_year = int(y2)
            else:
                end_year = year
                if prev_month is not None and m2 < prev_month - 6:   # přechod přes Nový rok
                    end_year += 1
            start_year = int(y1) if y1 else (end_year if m1 <= m2 else end_year - 1)
            try:
                start = date(start_year, m1, d1)
            except ValueError:
                continue
            year, prev_month = end_year, m2

            rest = cells[i + 1:]
            content = ""
            if rest:
                idx = self.config.content_index
                content = rest[idx] if -len(rest) <= idx < len(rest) else ""
            week_type = self.config.classify(content, row)
            key = monday(start)
            if key not in weeks or week_type.priority > weeks[key].priority:
                weeks[key] = week_type
        return weeks

    def _start_year(self, body: str) -> int | None:
        caption = re.search(r"<caption[^>]*>(.*?)</caption>", body, re.S | re.I)
        if caption:
            found = re.search(r"od\s*\d{1,2}\.\s*\d{1,2}\.\s*(\d{4})", self._text(caption.group(1)))
            if found:
                return int(found.group(1))
        for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<title>(.*?)</title>"):
            for block in re.findall(pattern, body, re.S | re.I):
                found = self.YEAR_RE.search(self._text(block))
                if found:
                    return int(found.group(1))
        return None

    @staticmethod
    def _text(fragment: str) -> str:
        text = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text).replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    # --- odkazy -------------------------------------------------------------
    def links(self, page: str, base_url: str) -> list[PageLink]:
        host = urlparse(base_url).netloc
        result: list[PageLink] = []
        seen = set()
        for href, inner in re.findall(r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, re.S | re.I):
            href = html_lib.unescape(href)
            if not self.config.link_pattern.search(href):
                continue
            absolute = urljoin(base_url, href)
            if urlparse(absolute).netloc != host or absolute in seen:
                continue
            seen.add(absolute)
            result.append(PageLink(absolute, self._text(inner)))
        return result


# ---------------------------------------------------------------------------
@dataclass
class ChamberCalendar:
    key: str
    weeks: dict[date, WeekType] = field(default_factory=dict)
    fetched_at: str | None = None
    sources: list[str] = field(default_factory=list)
    error: str | None = None
    method: str | None = None     # čím se stránka stáhla (WinINet, PowerShell, urllib)

    @property
    def first(self) -> date | None:
        return min(self.weeks) if self.weeks else None

    @property
    def last(self) -> date | None:
        return max(self.weeks) if self.weeks else None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "weeks": {d.isoformat(): t.value for d, t in sorted(self.weeks.items())},
            "fetched_at": self.fetched_at,
            "sources": self.sources,
            "error": self.error,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChamberCalendar":
        weeks = {}
        for key, value in (data.get("weeks") or {}).items():
            try:
                weeks[date.fromisoformat(key)] = WeekType(value)
            except ValueError:
                continue
        return cls(data.get("key", ""), weeks, data.get("fetched_at"), data.get("sources") or [],
                   data.get("error"), data.get("method"))


class PageDownloader:
    """Stáhne stránku (i přes proxy s ověřením Windows) a převede ji na text."""

    def __init__(self, downloader: ResilientDownloader | None = None):
        self.downloader = downloader or ResilientDownloader()

    @property
    def last_method(self) -> str | None:
        return self.downloader.last_method

    def get(self, url: str) -> str:
        return self.decode(self.downloader.fetch(url))

    @staticmethod
    def decode(raw: bytes, charset: str | None = None) -> str:
        if not charset:
            meta = re.search(rb"charset\s*=\s*[\"']?([\w-]+)", raw[:4000], re.I)
            charset = meta.group(1).decode("ascii") if meta else None
        for enc in filter(None, (charset, "utf-8", "windows-1250")):
            try:
                return raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", errors="replace")


class ParliamentCalendarStore:
    """Mezipaměť harmonogramů obou komor + jejich aktualizace z webu."""

    def __init__(self, cache_file: Path | None = None, downloader: PageDownloader | None = None):
        self.cache_file = cache_file or Path.home() / ".legislativni_harmonogram" / "parlament.json"
        self.downloader = downloader or PageDownloader()
        self.calendars: dict[str, ChamberCalendar] = {k: ChamberCalendar(k) for k in CHAMBERS}
        self.refreshing = False
        self._lock = threading.Lock()
        self._load()

    # --- mezipaměť ----------------------------------------------------------
    CACHE_VERSION = 2   # zvýšit při změně parseru – stará data se zahodí

    def _load(self) -> None:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("version") != self.CACHE_VERSION:
            return
        for key in CHAMBERS:
            if key in data:
                self.calendars[key] = ChamberCalendar.from_dict(data[key])

    def _save(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: c.to_dict() for k, c in self.calendars.items()}
            payload["version"] = self.CACHE_VERSION
            self.cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def is_stale(self, days: int = 7) -> bool:
        for calendar in self.calendars.values():
            if not calendar.fetched_at:
                return True
            try:
                if datetime.now() - datetime.fromisoformat(calendar.fetched_at) > timedelta(days=days):
                    return True
            except ValueError:
                return True
        return False

    # --- stahování ----------------------------------------------------------
    def refresh(self, chambers: Iterable[str] = CHAMBERS) -> None:
        with self._lock:
            self.refreshing = True
            try:
                for key in chambers:
                    self.calendars[key] = self._merge(self.calendars[key], self._fetch_chamber(CHAMBERS[key]))
                self._save()
            finally:
                self.refreshing = False

    def refresh_async(self) -> None:
        if not self.refreshing:
            threading.Thread(target=self.refresh, daemon=True).start()

    def _fetch_chamber(self, config: ChamberConfig) -> ChamberCalendar:
        parser = HarmonogramPageParser(config)
        queue, seen = [config.start_url], set()
        result = ChamberCalendar(config.key)
        errors = []
        while queue and len(seen) < config.max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                page = self.downloader.get(url)
            except Exception as exc:  # síť, proxy, HTTP chyba
                errors.append(f"{url}: {exc}")
                continue
            weeks = parser.weeks(page)
            links = parser.links(page, url)
            if weeks:
                result.sources.append(url)
            for day, week_type in weeks.items():
                if day not in result.weeks or week_type.priority > result.weeks[day].priority:
                    result.weeks[day] = week_type
            queue.extend(u for u in self.relevant_links(links) if u not in seen and u not in queue)
        result.fetched_at = datetime.now().isoformat(timespec="seconds")
        result.method = self.downloader.last_method
        if not result.weeks:
            result.error = "; ".join(errors) or "Na stránce nebyly nalezeny žádné týdny."
        elif errors:
            result.error = f"Některé stránky se nepodařilo načíst ({len(errors)})."
        return result

    @staticmethod
    def relevant_links(links: list[PageLink], today: date | None = None) -> list[str]:
        """Jen aktuální a budoucí období (letošní a pozdější), nejbližší první."""
        this_year = (today or date.today()).year
        scored = [(link.year or this_year, link.url) for link in links
                  if link.year is None or link.year >= this_year]
        return [url for _, url in sorted(scored)]

    @staticmethod
    def _merge(old: ChamberCalendar, new: ChamberCalendar) -> ChamberCalendar:
        """Nová data platí v celém staženém rozsahu; starší týdny mimo něj se zachovají."""
        if not new.weeks:
            old.error = new.error
            return old
        first, last = new.first, new.last
        merged = {d: t for d, t in old.weeks.items() if d < first or d > last}
        merged.update(new.weeks)
        new.weeks = merged
        return new

    def import_html(self, chamber: str, page: str, source: str = "soubor") -> int:
        """Ruční načtení uložené stránky (když web není z aplikace dostupný)."""
        parser = HarmonogramPageParser(CHAMBERS[chamber])
        weeks = parser.weeks(page)
        if weeks:
            calendar = self.calendars[chamber]
            calendar.weeks.update(weeks)
            calendar.fetched_at = datetime.now().isoformat(timespec="seconds")
            calendar.sources.append(source)
            calendar.error = None
            self._save()
        return len(weeks)


# ---------------------------------------------------------------------------
PSP_DAYS = frozenset({1, 2, 3, 4})        # út–pá
THIRD_READING_DAYS = frozenset({2, 4})    # st, pá
SENAT_DAYS = frozenset({1, 2, 3, 4})


@dataclass
class SittingRule:
    """Kdy může daná fáze (schůze komory) proběhnout."""

    chamber: str
    weekdays: frozenset[int] = PSP_DAYS


class SittingPolicy:
    """Rozhoduje, zda komora může v daný den zasedat."""

    def __init__(self, store_calendars: dict[str, ChamberCalendar], holidays: CzechHolidayCalendar,
                 psp_allow_variable: bool = True,
                 manual: dict[str, dict[date, WeekType]] | None = None):
        self.calendars = store_calendars
        self.holidays = holidays
        self.psp_allow_variable = psp_allow_variable
        self.manual = manual or {}

    SUMMER_MONTHS = (7, 8)   # PSP o prázdninách nezasedá, neříká-li harmonogram jinak

    def week_info(self, chamber: str, day: date) -> tuple[WeekType | None, str | None]:
        """Typ týdne a jeho zdroj: "manual" | "web" | "summer" (předpoklad) | None (neznámo)."""
        key = monday(day)
        manual = self.manual.get(chamber, {}).get(key)
        if manual:
            return manual, "manual"
        web = self.calendars[chamber].weeks.get(key)
        if web:
            return web, "web"
        if chamber == "psp" and day.month in self.SUMMER_MONTHS:
            return WeekType.OTHER, "summer"
        return None, None

    def week_type(self, chamber: str, day: date) -> tuple[WeekType | None, bool]:
        """Typ týdne a příznak, zda jde o ruční opravu."""
        week_type, source = self.week_info(chamber, day)
        return week_type, source == "manual"

    def is_known(self, chamber: str, day: date) -> bool:
        """Den spadá do zveřejněného harmonogramu nebo ručně nastaveného týdne."""
        return self.week_info(chamber, day)[1] in ("manual", "web")

    def allowed_week(self, chamber: str, week_type: WeekType | None) -> bool:
        if week_type is None:              # mimo zveřejněný harmonogram → nevíme, nepřekážíme
            return True
        if week_type is WeekType.PLENARY:
            return True
        if week_type is WeekType.EXTRA:
            return chamber == "psp"
        return chamber == "psp" and week_type is WeekType.VARIABLE and self.psp_allow_variable

    def allows(self, rule: SittingRule, day: date, ignore_customs: bool) -> bool:
        if not self.holidays.is_working_day(day) or day.weekday() not in rule.weekdays:
            return False
        if ignore_customs:
            return True
        return self.allowed_week(rule.chamber, self.week_type(rule.chamber, day)[0])

    def describe(self, rule: SittingRule, day: date) -> str:
        week_type, source = self.week_info(rule.chamber, day)
        name = CHAMBERS[rule.chamber].name
        if week_type is None:
            return f"{name}: týden mimo zveřejněný harmonogram"
        if source == "summer":
            return f"{name}: prázdniny – předpoklad, že nezasedá"
        suffix = " (ručně)" if source == "manual" else ""
        return f"{name}: {week_type.label_for(rule.chamber)}{suffix}"

    def first_allowed(self, rule: SittingRule, start: date, ignore: bool, limit_days: int = 400) -> date | None:
        day = start
        for _ in range(limit_days):
            if self.allows(rule, day, ignore):
                return day
            day += timedelta(days=1)
        return None

    def last_allowed(self, rule: SittingRule, end: date, not_before: date, ignore: bool) -> date | None:
        day = end
        while day >= not_before:
            if self.allows(rule, day, ignore):
                return day
            day -= timedelta(days=1)
        return None
