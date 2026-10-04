"""Build `data/seeds/catalog/nutrition.json` (per 100 g: kcal, protein, fat, carbohydrate,
fibre) for every seed product from IFCT 2017, the Indian Food Composition Tables (ICMR-NIN),
through the `@ifct2017/compositions` transcription (MIT) on unpkg.

    uv run python data/external/nutrition/build.py

The raw CSV lands next to this script and is git-ignored; the mapping `ifct_mapping.csv`
(product slug → IFCT food code, plus grams per piece or bunch for products whose step is not a
weight) and the handful of `MANUAL` rows (foods IFCT 2017 does not list) are reviewed by hand.
"""

import csv
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "ifct2017_compositions.csv"
MAPPING = HERE / "ifct_mapping.csv"
OUT = HERE.parent.parent / "seeds" / "catalog" / "nutrition.json"
SOURCE_URL = "https://unpkg.com/@ifct2017/compositions/index.csv"
# IFCT column suffixes; `enerc` is kJ per 100 g, so kcal = kJ / 4.184.
COLUMNS = {
    "kj": "enerc",
    "protein_g": "protcnt",
    "fat_g": "fatce",
    "carb_g": "choavldf",
    "fibre_g": "fibtg",
}
# Foods IFCT 2017 does not list: NIN "Nutritive Value of Indian Foods" (Gopalan et al.), per 100 g.
MANUAL = {
    "dill": {"kcal": 43, "protein_g": 3.5, "fat_g": 1.1, "carb_g": 7.0, "fibre_g": 2.1},
    "sabudana": {"kcal": 351, "protein_g": 0.2, "fat_g": 0.2, "carb_g": 87.1, "fibre_g": 0.9},
    "salt": {"kcal": 0, "protein_g": 0, "fat_g": 0, "carb_g": 0, "fibre_g": 0},
    "sugar": {"kcal": 398, "protein_g": 0.1, "fat_g": 0, "carb_g": 99.4, "fibre_g": 0},
    "curd": {"kcal": 60, "protein_g": 3.1, "fat_g": 4.0, "carb_g": 3.0, "fibre_g": 0},
}


def fetch() -> None:
    if RAW.exists():
        return
    print(f"fetching {SOURCE_URL}", file=sys.stderr)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as answer:
        RAW.write_bytes(answer.read())


def load_ifct() -> dict[str, dict[str, float]]:
    with RAW.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    columns = {
        key: next(k for k in rows[0] if k.endswith("; " + suffix))
        for key, suffix in COLUMNS.items()
    }
    code_col = next(k for k in rows[0] if k.endswith("; code"))
    name_col = next(k for k in rows[0] if k.endswith("; name"))
    foods: dict[str, dict[str, float]] = {}
    for row in rows:
        values = {key: float(row[col] or 0) for key, col in columns.items()}
        # Pure fats and a few others have no energy column: the Atwater factors fill it.
        kcal = values["kj"] / 4.184 or (
            4 * values["protein_g"] + 9 * values["fat_g"] + 4 * values["carb_g"]
        )
        foods[row[code_col]] = {
            "name": row[name_col],
            "kcal": round(kcal),
            "protein_g": round(values["protein_g"], 1),
            "fat_g": round(values["fat_g"], 1),
            "carb_g": round(values["carb_g"], 1),
            "fibre_g": round(values["fibre_g"], 1),
        }
    return foods


def main() -> int:
    fetch()
    foods = load_ifct()
    items = []
    with MAPPING.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            slug, code = row["slug"], row["ifct_code"].strip()
            if code:
                food = foods[code]
                per_100g = {k: food[k] for k in ("kcal", "protein_g", "fat_g", "carb_g", "fibre_g")}
                source = {"name": "IFCT 2017", "code": code, "food": food["name"]}
            else:
                per_100g = MANUAL[slug]
                source = {"name": "NIN Nutritive Value of Indian Foods", "code": None, "food": slug}
            item = {"slug": slug, "per_100g": per_100g, "source": source}
            if row["unit_g"].strip():
                item["unit_g"] = int(row["unit_g"])
            items.append(item)
    OUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).date().isoformat(),
                "source": (
                    "IFCT 2017 (ICMR-NIN) via @ifct2017/compositions; manual rows from NIN NVIF"
                ),
                "items": items,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"wrote {OUT} ({len(items)} products)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
