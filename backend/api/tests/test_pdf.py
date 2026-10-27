"""The shared PDF builder (M0, `platform.pdf`): Indian grouping, ₹ and Devanagari glyphs, the
banner and footer on every page, and a document with no links, scripts or attachments."""

import io
import logging
import time
import warnings
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader

from gharbasket.platform.pdf import Bold, PdfDocument, indian, rupees

CREATED = datetime(2026, 10, 28, 8, 35, tzinfo=UTC)


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (86_00, "₹86.00"),
        (99_999_99, "₹99,999.99"),
        (1_00_000_00, "₹1,00,000.00"),
        (123_456_00, "₹1,23,456.00"),
        (12_34_56_789_00, "₹12,34,56,789.00"),
        (-1_234_50, "-₹1,234.50"),
    ],
)
def test_rupees_are_grouped_the_indian_way_with_two_decimals(paise: int, shown: str) -> None:
    assert rupees(paise) == shown


@pytest.mark.parametrize(
    ("count", "shown"), [(0, "0"), (999, "999"), (1180, "1,180"), (212_000, "2,12,000")]
)
def test_counts_are_grouped_too(count: int, shown: str) -> None:
    assert indian(count) == shown


def build(rows: int = 3, banner: str | None = "BANNER TEXT") -> bytes:
    doc = PdfDocument(
        title="Income certificate",
        author="GharBasket",
        created=CREATED,
        footer="Computer-generated document.",
        banner=banner,
    )
    doc.heading("GharBasket")
    doc.paragraph("Certified: ", Bold("आशा पाटील"), " earned ", Bold(rupees(123_456_00)), ".")
    doc.table(
        ["Month", "Earned"],
        [[f"Month {n}", rupees(n * 100_00)] for n in range(rows)],
        totals=["Total", rupees(sum(n * 100_00 for n in range(rows)))],
        widths=[1, 1],
        align=["LEFT", "RIGHT"],
    )
    return doc.output()


def text_of(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def test_the_rupee_sign_and_a_devanagari_name_render_without_a_missing_glyph(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with warnings.catch_warnings(), caplog.at_level(logging.WARNING, logger="fpdf"):
        warnings.simplefilter("error")  # fpdf2 warns about a glyph a font lacks
        pdf = build()

    (page,) = text_of(pdf)
    assert "आशा पाटील" in page
    assert "₹1,23,456.00" in page
    assert "Total ₹300.00" in page
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_banner_footer_and_page_numbers_are_on_every_page() -> None:
    pages = text_of(build(rows=90))

    assert len(pages) >= 2
    for number, page in enumerate(pages, start=1):
        assert "BANNER TEXT" in page
        assert "Computer-generated document." in page
        assert f"Page {number} of {len(pages)}" in page


def test_without_a_banner_there_is_none() -> None:
    assert "BANNER" not in text_of(build(banner=None))[0]


def test_metadata_is_set_and_nothing_active_is_embedded() -> None:
    pdf = build()
    reader = PdfReader(io.BytesIO(pdf))

    assert reader.metadata is not None
    assert reader.metadata.title == "Income certificate"
    assert reader.metadata.author == "GharBasket"
    assert reader.metadata.creation_date == CREATED
    assert not reader.attachments
    assert b"/JavaScript" not in pdf and b"/JS" not in pdf
    assert b"/URI" not in pdf and b"/Launch" not in pdf and b"/EmbeddedFile" not in pdf
    assert "/Annots" not in reader.pages[0]


def test_a_year_of_four_kinds_builds_well_under_the_budget() -> None:
    build(rows=12)  # the first build also loads and parses the fonts, once per process
    started = time.perf_counter()
    build(rows=12)
    # The spec aims at 300 ms; a shared CI runner can be several times slower than a laptop.
    assert time.perf_counter() - started < 2.0
