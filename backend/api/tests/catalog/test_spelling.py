"""Hinglish spellings of Devanagari names, the language of an answer, and the seed data."""

import json

import pytest

from gharbasket.modules.catalog.service import spellings, step_label
from gharbasket.modules.catalog.spelling import has_devanagari, hinglish
from gharbasket.platform.locale import localised, parse_accept_language
from gharbasket.tools.seed import CATALOG_SEED


@pytest.mark.parametrize(
    ("devanagari", "typed"),
    [
        ("भिंडी", "bhindi"),
        ("भेंडी", "bhendi"),
        ("पालक", "palak"),
        ("टमाटर", "tamatar"),
        ("टोमॅटो", "tomato"),
        ("कांदा", "kanda"),
        ("कोथिंबीर", "kothimbir"),
        ("तूर डाळ", "tur dal"),
        ("साबुदाणा", "sabudana"),
    ],
)
def test_devanagari_names_become_what_people_type(devanagari: str, typed: str) -> None:
    assert has_devanagari(devanagari)
    assert hinglish(devanagari) == typed


def test_spellings_include_names_synonyms_and_transliterations_once() -> None:
    found = spellings(
        {"en": "Okra (bhindi)", "hi": "भिंडी", "mr": "भेंडी"}, ("okra", "bhendi", "Okra")
    )

    assert found == {
        "Okra (bhindi)": ("en", "name"),
        "भिंडी": ("hi", "name"),
        "भेंडी": ("mr", "name"),
        "okra": ("hinglish", "synonym"),
        "bhendi": ("hinglish", "synonym"),
        "bhindi": ("hinglish", "transliteration"),
    }


@pytest.mark.parametrize(
    ("header", "lang"),
    [
        (None, "en"),
        ("", "en"),
        ("hi-IN", "hi"),
        ("mr-IN,mr;q=0.9,en;q=0.8", "mr"),
        ("fr-FR,hi;q=0.5", "hi"),
        ("en;q=0.2,hi;q=0.9", "hi"),
        ("de, fr", "en"),
        ("hi;q=bogus,mr", "mr"),
    ],
)
def test_the_answer_language_comes_from_accept_language(header: str | None, lang: str) -> None:
    assert parse_accept_language(header) == lang


def test_names_fall_back_to_english() -> None:
    assert localised({"en": "Okra", "hi": "भिंडी"}, "mr") == "Okra"
    assert localised({"hi": "भिंडी"}, "mr") == "भिंडी"


def test_step_labels_per_language() -> None:
    assert [step_label(1, "bunch", lang) for lang in ("en", "hi", "mr")] == [
        "1 bunch",
        "1 गड्डी",
        "1 जुडी",
    ]


def test_the_seed_has_every_name_and_valid_categories() -> None:
    products = json.loads((CATALOG_SEED / "products.json").read_text())["products"]
    seeded = json.loads((CATALOG_SEED / "categories.json").read_text())["categories"]
    categories = {category["slug"] for category in seeded}

    assert len({product["slug"] for product in products}) == len(products)
    for product in products:
        assert all(product["names"][lang].strip() for lang in ("en", "hi", "mr")), product["slug"]
        assert product["category"] in categories
