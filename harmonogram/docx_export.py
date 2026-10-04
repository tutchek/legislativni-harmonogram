"""Export harmonogramu do Wordu podle přiložené šablony.

Ze šablony se převezme vše kromě obsahu: styly, okraje stránky, záhlaví
„HARMONOGRAM“, vlastnosti tabulky a řádek s hlavičkou (PROCES / TERMÍN /
POZNÁMKA). Název návrhu a řádky tabulky se vygenerují znovu ve stejné úpravě:
Times New Roman 9 b., oddíl ve sloučené první buňce, tlusté čáry mezi oddíly.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from .scheduler import ScheduleResult, month_label

MONTHS_GENITIVE = ("ledna", "února", "března", "dubna", "května", "června", "července",
                   "srpna", "září", "října", "listopadu", "prosince")


class CzechDateFormatter:
    @staticmethod
    def long(day: date) -> str:
        return f"{day.day}. {MONTHS_GENITIVE[day.month - 1]} {day.year}"


@dataclass
class DocRow:
    section: str
    label: str
    terms: list[str]                 # jeden termín za každou variantu („—“, když v ní fáze není)
    note: str = ""
    bold_terms: list[bool] | None = None

    @property
    def term(self) -> str:
        return self.terms[0]


@dataclass
class VariantResult:
    name: str
    result: ScheduleResult


class DocRowsBuilder:
    """Převede výsledky výpočtu (jedné či více variant) na řádky tabulky ve struktuře šablony.

    Řádky jsou sjednocením fází všech variant v pořadí procesu; varianta, v níž
    fáze není (jiný první krok, vypnutá volitelná fáze), má v řádku „—“.
    Poznámky se berou z hlavní (právě otevřené) varianty.
    """

    MISSING = "—"

    @staticmethod
    def _term(day: date, approximate: bool) -> str:
        """Mimo zveřejněný harmonogram komory jen měsíc a rok („říjen 2027“)."""
        return month_label(day) if approximate else CzechDateFormatter.long(day)

    def build(self, result: ScheduleResult) -> list[DocRow]:
        return self.build_many([VariantResult("", result)])

    def build_many(self, variants: list[VariantResult], primary: int = 0) -> list[DocRow]:
        order = [primary] + [i for i in range(len(variants)) if i != primary]   # pořadí pro poznámky
        by_id = [{r.phase.id: r for r in v.result.active} for v in variants]
        phases = variants[primary].result.plan.phases
        rows: list[DocRow] = []
        for phase in phases:
            present = [m.get(phase.id) for m in by_id]
            if not any(present):
                continue
            source = next(present[i] for i in order if present[i])       # poznámky z hlavní varianty
            src_phase = source.phase
            if phase.deadline_row and any(r and r.deadline_date for r in present):
                rows.append(DocRow(
                    phase.section, phase.deadline_row[0],
                    [self._term(r.deadline_date, r.approximate) if r and r.deadline_date else self.MISSING
                     for r in present],
                    src_phase.deadline_text))
            rows.append(DocRow(
                phase.section, phase.doc_label or phase.name,
                [self._term(r.date, r.approximate) if r else self.MISSING for r in present],
                src_phase.doc_text))
        # Tučně první termín každého oddílu – jako v šabloně.
        previous = None
        for row in rows:
            starts = row.section != previous
            row.bold_terms = [starts and t != self.MISSING for t in row.terms]
            previous = row.section
        effective = [v.result.plan.settings.effective.strip() for v in variants]
        if any(effective):
            rows.append(DocRow("Účinnost", "", [e or self.MISSING for e in effective],
                               bold_terms=[True] * len(variants)))
        return rows


class _Xml:
    """Stavební bloky WordprocessingML v úpravě šablony."""

    FONT = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
    SIZE = '<w:sz w:val="18"/><w:szCs w:val="18"/>'
    THICK = 'w:val="single" w:sz="12" w:space="0" w:color="auto"'

    @classmethod
    def run(cls, text: str, bold: bool = False) -> str:
        rpr = cls.FONT + ("<w:b/>" if bold else "") + cls.SIZE
        return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'

    @classmethod
    def para(cls, text: str = "", jc: str = "center", bold: bool = False, spaced: bool = False) -> str:
        spacing = '<w:spacing w:before="120" w:after="120"/>' if spaced else ""
        rpr = cls.FONT + ("<w:b/>" if bold else "") + cls.SIZE
        ppr = f'<w:pPr><w:pStyle w:val="Popisky"/>{spacing}<w:jc w:val="{jc}"/><w:rPr>{rpr}</w:rPr></w:pPr>'
        return f"<w:p>{ppr}{cls.run(text, bold) if text else ''}</w:p>"

    @classmethod
    def cell(cls, width: int, paragraphs: str, borders: dict[str, bool],
             vmerge: str | None = None, span: int = 1) -> str:
        border_xml = "".join(f"<w:{side} {cls.THICK}/>" for side, on in borders.items() if on)
        width_xml = '<w:tcW w:w="0" w:type="auto"/>' if width == 0 else f'<w:tcW w:w="{width}" w:type="dxa"/>'
        span_xml = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ""
        merge_xml = {None: "", "restart": '<w:vMerge w:val="restart"/>', "continue": "<w:vMerge/>"}[vmerge]
        tcpr = (f"<w:tcPr>{width_xml}{span_xml}{merge_xml}<w:tcBorders>{border_xml}</w:tcBorders>"
                '<w:shd w:val="clear" w:color="auto" w:fill="auto"/><w:vAlign w:val="center"/></w:tcPr>')
        return f"<w:tc>{tcpr}{paragraphs}</w:tc>"


class DocxHarmonogramExporter:
    """Naplní šablonu .docx harmonogramem a vrátí hotový soubor jako bytes.

    Jedna varianta → sloupec „TERMÍN“ jako v šabloně. Více variant → místo něj
    sloupec pro každou variantu s jejím názvem v záhlaví; sloupec POZNÁMKA se zúží.
    """

    WIDTHS = (1051, 1628, 1984, 5915)   # šířky sloupců ze šablony (twipy)
    MIN_TERM = 1300
    MIN_NOTE = 2400
    HEADER_FILL = '<w:shd w:val="clear" w:color="auto" w:fill="D9D9D9" w:themeFill="background1" w:themeFillShade="D9"/>'

    def __init__(self, template: Path):
        self.template = template
        self.rows_builder = DocRowsBuilder()

    def export(self, result: ScheduleResult) -> bytes:
        return self.export_variants([VariantResult("", result)])

    def export_variants(self, variants: list[VariantResult], primary: int = 0) -> bytes:
        rows = self.rows_builder.build_many(variants, primary)
        headers = ["TERMÍN"] if len(variants) == 1 else [v.name or f"Varianta {i + 1}" for i, v in enumerate(variants)]
        title = variants[primary].result.plan.settings.title.strip() or "Návrh zákona"
        output = io.BytesIO()
        with zipfile.ZipFile(self.template) as src, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "word/document.xml":
                    data = self._document(data.decode("utf-8"), title, headers, rows).encode("utf-8")
                dst.writestr(item, data)
        return output.getvalue()

    # ------------------------------------------------------------------
    def _widths(self, count: int) -> tuple[int, int, list[int], int]:
        w1, w2, w3, w4 = self.WIDTHS
        if count == 1:
            return w1, w2, [w3], w4
        available = w3 + w4
        term = max(self.MIN_TERM, min(w3, (available - self.MIN_NOTE) // count))
        note = max(self.MIN_NOTE, available - term * count)
        return w1, w2, [term] * count, note

    def _document(self, xml: str, title: str, headers: list[str], rows: list[DocRow]) -> str:
        xml = self._replace_title(xml, title)
        table = re.search(r"<w:tbl>.*?</w:tbl>", xml, re.S)
        if not table:
            raise ValueError("Šablona neobsahuje tabulku.")
        tbl = table.group(0)
        w1, w2, terms, note = self._widths(len(headers))
        total = w1 + w2 + sum(terms) + note
        tbl_pr = re.search(r"<w:tblPr>.*?</w:tblPr>", tbl, re.S).group(0)
        tbl_pr = re.sub(r'<w:tblW [^>]*/>', f'<w:tblW w:w="{total}" w:type="dxa"/>', tbl_pr)
        grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in [w1, w2, *terms, note]) + "</w:tblGrid>"
        new_tbl = ("<w:tbl>" + tbl_pr + grid + self._header_xml(headers, w1, w2, terms, note)
                   + self._rows_xml(rows, w1, w2, terms, note) + "</w:tbl>")
        return xml[:table.start()] + new_tbl + xml[table.end():]

    def _header_xml(self, headers: list[str], w1: int, w2: int, terms: list[int], note: int) -> str:
        def cell(width: int, text: str, borders: dict[str, bool]) -> str:
            c = _Xml.cell(width, _Xml.para(text, bold=True, spaced=True), borders)
            return c.replace('<w:shd w:val="clear" w:color="auto" w:fill="auto"/>', self.HEADER_FILL)
        cells = cell(0, "", {"top": True, "left": True, "bottom": True})
        cells += cell(w2, "PROCES", {"top": True, "bottom": True})
        cells += "".join(cell(w, h, {"top": True, "bottom": True}) for w, h in zip(terms, headers))
        cells += cell(note, "POZNÁMKA", {"top": True, "bottom": True, "right": True})
        return f'<w:tr><w:trPr><w:tblHeader/><w:trHeight w:val="320"/></w:trPr>{cells}</w:tr>'

    @staticmethod
    def _replace_title(xml: str, title: str) -> str:
        """1. nadpis (HARMONOGRAM) ponechá, 2. nahradí názvem, další odstraní."""
        pattern = re.compile(r'<w:p [^>]*>(?:(?!</w:p>).)*?<w:pStyle w:val="nadpiszkona"/>.*?</w:p>', re.S)
        found = list(pattern.finditer(xml))
        if len(found) < 2:
            return xml
        second = found[1].group(0)
        ppr = re.search(r"<w:pPr>.*?</w:pPr>", second, re.S)
        runs = "".join(
            f'<w:r><w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
            f'{"<w:br/>" if i else ""}<w:t xml:space="preserve">{escape(line)}</w:t></w:r>'
            for i, line in enumerate(title.splitlines() or [title])
        )
        replacement = f"<w:p>{ppr.group(0) if ppr else ''}{runs}</w:p>"
        out, last = [], 0
        for i, m in enumerate(found[1:], start=1):
            out.append(xml[last:m.start()])
            if i == 1:
                out.append(replacement)
            last = m.end()
        out.append(xml[last:])
        return "".join(out)

    def _rows_xml(self, rows: list[DocRow], w1: int, w2: int, terms: list[int], note: int) -> str:
        parts = []
        for i, row in enumerate(rows):
            is_last = i == len(rows) - 1
            starts = i == 0 or rows[i - 1].section != row.section
            note_paras = "".join(_Xml.para(line, jc="both") for line in row.note.splitlines()) or _Xml.para(jc="both")
            bold = row.bold_terms or [False] * len(row.terms)

            if row.section == "Účinnost":
                cells = _Xml.cell(w1 + w2, _Xml.para("Účinnost", bold=True),
                                  {"top": True, "left": True, "bottom": True}, span=2)
                edge = {"top": True, "bottom": True}
            else:
                section = _Xml.para(row.section if starts else "", bold=True, spaced=True)
                cells = (_Xml.cell(0, section, {"top": starts, "left": True, "bottom": is_last},
                                   vmerge="restart" if starts else "continue")
                         + _Xml.cell(w2, _Xml.para(row.label), {"top": starts, "bottom": is_last}))
                edge = {"top": starts, "bottom": is_last}
            cells += "".join(_Xml.cell(w, _Xml.para(t, bold=b), edge)
                             for w, t, b in zip(terms, row.terms, bold))
            cells += _Xml.cell(note, note_paras, {**edge, "right": True})
            parts.append(f'<w:tr><w:trPr><w:trHeight w:val="372"/></w:trPr>{cells}</w:tr>')
        return "".join(parts)
