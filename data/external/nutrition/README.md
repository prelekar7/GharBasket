# Nutrition data (IFCT 2017 and ICMR-NIN RDA 2020)

What the planner's nutrition term (M10 §3.2) and `GET /v1/products` (`nutrition`, per 100 g) use.

| File | What | Licence / source |
|---|---|---|
| `ifct2017_compositions.csv` (fetched, git-ignored) | The 542 foods of the Indian Food Composition Tables 2017 (ICMR-National Institute of Nutrition), one row per food, energy in kJ | Tables © ICMR-NIN 2017; the CSV transcription is the `@ifct2017/compositions` package (MIT), fetched from unpkg |
| `ifct_mapping.csv` (hand-made) | Seed product slug → IFCT food code, `unit_g` (grams of one piece or bunch for products whose step is not a weight), and a note on the choice | ours |
| `build.py` | Fetches the CSV if missing and writes `data/seeds/catalog/nutrition.json` (kcal, protein, fat, carbohydrate, fibre per 100 g, with the source row) | ours |
| `rda.json` | Daily energy, protein and fibre targets per age band (ICMR-NIN RDA 2020, sedentary, sexes averaged) with a ±20 % tolerance; **a hypothesis until ratified** (handbook 08 §8.5) | ours, from the public RDA tables |

Rebuild after a mapping change:

```
uv run python data/external/nutrition/build.py
just seed
```

Five foods IFCT 2017 does not list (dill leaves, sago, salt, refined sugar, curd) carry values from the
NIN "Nutritive Value of Indian Foods" and are marked `source.code: null`. IFCT leaves the energy column
empty for pure fats; `build.py` fills it from the Atwater factors (4/9/4). Two seed products share an
IFCT row (basmati and kolam rice: raw milled rice; chana dal and besan).

**Review needed (Rhythm Thakur, Manish Raje):** the mapping choices (varieties), the `unit_g` weights
and the RDA bands. The report cites IFCT 2017 (Longvah et al., ICMR-NIN) and ICMR-NIN RDA 2020.
