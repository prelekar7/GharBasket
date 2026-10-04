# Catalog seed (synthetic, for local development and the simulator)

Loaded by `just seed` through `catalog.service` (M2), every row `data_source='synthetic'`.

- `categories.json`: categories with names in English, Hindi and Marathi.
- `prices.json`: approximate Kharghar retail prices (Oct 2026) for the seed hub, per step in paise, valid from 2026-10-01. MRP only for packaged goods.
- `products.json`: everyday Kharghar groceries. For each product:
  - `names` (en/hi/mr);
  - `step`, the unit a person adds or removes; prices are per step;
  - `shelf_life_h`;
  - `tags`;
  - `synonyms`: the other spellings people type, in English and Hinglish. The Hinglish form of each Devanagari name is generated automatically.

**Tags** are codes the planner and filters use:

| Tag | Meaning |
|---|---|
| `leafy` | Leafy greens |
| `root` | Grows underground (Jain households avoid these) |
| `onion_garlic` | Onion and garlic (avoided by Jain households and on some fast days) |
| `gourd` | The gourd family |
| `fasting_ok` | Commonly eaten on vrat days |
| `high_protein`, `high_fibre` | Nutrition hints, until IFCT 2017 data is added |
| `dairy`, `egg` | For diets that exclude them |

**Review needed (Rhythm Thakur, Manish Raje):** the Hindi and Marathi names, the synonyms, the `fasting_ok` tags (customs differ by family and region), the step sizes and the prices. Edit the JSON and run `just seed` again; products are matched by `slug`.
