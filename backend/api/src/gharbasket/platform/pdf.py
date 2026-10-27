"""A small PDF builder for the documents the API writes itself (M0; M8's income certificate §3.7,
M5's statement PDF later): an A4 page of headings, paragraphs and tables, built with fpdf2.

    doc = PdfDocument(title="Income certificate", author="GharBasket", created=now,
                      footer="Computer-generated document.", banner="PROTOTYPE: ...")
    doc.heading("Income certificate")
    doc.paragraph("This is to certify that ", Bold("Asha Patil"), " has worked with us.")
    doc.table(["Month", "Earned"], [["May 2026", rupees(120000)]], totals=["Total", rupees(120000)],
              widths=[40, 40], align=["L", "R"])
    body = doc.output()                       # bytes; CPU work, so call it in asyncio.to_thread

Fonts are Noto Sans (regular, bold) with Noto Sans Devanagari as the fallback, bundled in
`fonts/` (SIL OFL); the PDF core fonts have no ₹ glyph and are never used. Text goes through
uharfbuzz shaping, so Devanagari names render correctly. The document embeds no links, JavaScript
or attachments, and its creation date is the issue time given by the caller.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import Align, XPos, YPos
from fpdf.fonts import FontFace

FONTS = Path(__file__).parent / "fonts"
FAMILY, FALLBACK = "NotoSans", "NotoSansDevanagari"
MARGIN = 15.0  # mm
PAGE_WIDTH = 210.0 - 2 * MARGIN  # A4 portrait, the width a table can use


def indian(number: int) -> str:
    """An integer with Indian digit grouping: 1234567 → '12,34,567'."""
    digits = str(abs(number))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = [head[max(0, i - 2) : i] for i in range(len(head), 0, -2)][::-1]
        digits = ",".join([*groups, tail])
    return ("-" if number < 0 else "") + digits


def rupees(paise: int) -> str:
    """₹ with Indian grouping and always two decimals: 12345600 → '₹1,23,456.00'."""
    whole, rest = divmod(abs(paise), 100)
    return f"{'-' if paise < 0 else ''}₹{indian(whole)}.{rest:02d}"


@dataclass(frozen=True)
class Bold:
    text: str


class _Page(FPDF):
    """FPDF with the banner (every page's top) and the footer (note and 'Page n of m')."""

    banner: str | None = None
    footer_note: str = ""

    def header(self) -> None:
        if self.banner:
            self.set_font(FAMILY, "B", 8.5)
            self.set_fill_color(253, 236, 234)
            self.set_text_color(160, 30, 20)
            self.multi_cell(
                0,
                6,
                text=self.banner,
                align=Align.C,
                fill=True,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            self.set_text_color(0, 0, 0)
            self.ln(3)

    def footer(self) -> None:
        self.set_y(-18)
        self.set_font(FAMILY, size=7.5)
        self.set_text_color(90, 90, 90)
        self.multi_cell(0, 3.8, text=self.footer_note, align=Align.C, new_x=XPos.LMARGIN)
        self.cell(0, 4, text=f"Page {self.page_no()} of {{nb}}", align=Align.C)
        self.set_text_color(0, 0, 0)


class PdfDocument:
    def __init__(
        self,
        *,
        title: str,
        author: str,
        created: datetime,
        footer: str,
        banner: str | None = None,
    ) -> None:
        pdf = _Page(format="A4")
        pdf.banner, pdf.footer_note = banner, footer
        for style, suffix in (("", "Regular"), ("B", "Bold")):
            pdf.add_font(FAMILY, style, FONTS / f"NotoSans-{suffix}.ttf")
            pdf.add_font(FALLBACK, style, FONTS / f"NotoSansDevanagari-{suffix}.ttf")
        pdf.set_text_shaping(True)
        pdf.set_fallback_fonts([FALLBACK])
        pdf.alias_nb_pages()
        pdf.set_margins(MARGIN, MARGIN, MARGIN)
        pdf.set_auto_page_break(True, margin=24)
        pdf.set_title(title)
        pdf.set_author(author)
        pdf.set_creator(author)
        pdf.set_creation_date(created)
        pdf.add_page()
        pdf.set_font(FAMILY, size=10)
        self._pdf = pdf

    def heading(self, text: str, *, size: float = 14) -> None:
        self._pdf.ln(2)
        self._pdf.set_font(FAMILY, "B", size)
        self._pdf.multi_cell(0, size * 0.5, text=text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._pdf.set_font(FAMILY, size=10)
        self._pdf.ln(1.5)

    def paragraph(self, *parts: str | Bold, size: float = 10) -> None:
        """Text that wraps; a `Bold` part is set in bold. Each call is a paragraph."""
        pdf = self._pdf
        for part in parts:
            pdf.set_font(FAMILY, "B" if isinstance(part, Bold) else "", size)
            pdf.write(size * 0.5, text=part.text if isinstance(part, Bold) else part)
        pdf.set_font(FAMILY, size=10)
        pdf.ln(size * 0.5 + 1.5)

    def table(
        self,
        headings: list[str],
        rows: list[list[str]],
        *,
        widths: list[float],
        align: list[str],
        totals: list[str] | None = None,
        size: float = 8.5,
    ) -> None:
        """A table with a shaded heading row and, if given, a bold totals row at the bottom."""
        pdf = self._pdf
        pdf.set_font(FAMILY, size=size)
        with pdf.table(
            col_widths=tuple(widths),
            text_align=tuple(align),
            headings_style=FontFace(emphasis="BOLD", fill_color=(236, 238, 241)),
            line_height=size * 0.55,
            padding=1.2,
        ) as table:
            table.row(headings)
            for cells in rows:
                table.row(cells)
            if totals is not None:
                table.row(totals, style=FontFace(emphasis="BOLD", fill_color=(246, 246, 246)))
        pdf.set_font(FAMILY, size=10)
        pdf.ln(3)

    def output(self) -> bytes:
        return bytes(self._pdf.output())
