# Production's first rows (the reviewed load)

Production starts empty (ADR-0036). These files are everything it gets before the pilot: the real
hub, the real societies, the catalog and price list, the festival calendar and the staff roles.
`just prod-load` reads them (handbook 12 §12.9, "The reviewed load"):

    infisical run --env=prod -- just prod-load --folder <folder>            # dry run: the report
    infisical run --env=prod -- just prod-load --folder <folder> --apply    # writes

The load only adds what is missing and never changes a row that already exists, so a row edited in
the console stays as it is. It refuses while any value here still starts with `TODO` (it lists
each one), and on any database not marked `prod`.

Every value starting with `TODO` is a placeholder; the text after it says what goes there. The
repository is public: **never commit a real phone number or a staff member's name.** Fill
`staff.json` only in a private copy of this folder (see below).

| File | What it holds | Who fills it |
|---|---|---|
| `hub.json` | The one real hub, with the fields of `POST /v1/hubs`: `slug` (never changes), `name`, `address`, `location` `{lat, lng}`, and `service_area`, which here is a list of at least 3 corners `[{"lat": …, "lng": …}, …]` instead of GeoJSON (not closed; the loader closes it). The optional hub settings (`timings`, `procurement_buffer_pct`, `pre_live_box_fee_paise`, …) take their defaults when left out | Prathamesh Relekar (owner) |
| `societies.json` | `societies`: one entry per real society. `slug` (lower-case words joined by `-`, never changes), `name`, `area` (sector and node, e.g. `Sector 20, Kharghar`), `hub` (the slug in `hub.json`, or `null` for a society no hub serves yet), `boundary` (at least 3 corners of the compound as above, or `null`: then addresses are not checked against it), `towers` (its wings, `[{"id": "a", "label": "A wing"}]`, or `[]`), `flats` (or `null`), `unlock_threshold` (households needed for free delivery, default 20) and `status` (`prospect`, `unlocking`, `live` or `paused`; normally `prospect`). Add or remove entries as needed | The owner, with the hub's team who walked the societies |
| `catalog.json` | `folder`: the catalog folder to load, relative to this one: categories, products (with nutrition), recipes and the festival calendar, in the format of `data/seeds/catalog` (its README). Today it is that folder. `reviewed`: who checked it and when; the names, tags, steps, recipes and festival days are listed under "Review needed" there | Rhythm Thakur and Manish Raje review; the owner fills `reviewed` |
| `prices.json` | The hub's opening price list in the format of `data/seeds/catalog/prices.json`: `valid_from` (`YYYY-MM-DD`), and `items` with each product's `slug`, `price_paise` (per step, integer paise) and `mrp_paise` (packaged goods only, else `null`). The items start as the seed's approximate Kharghar prices: check every one. `reviewed`: who checked them and when. Later lists are published in the console, not here | The owner, with the hub manager |
| `staff.json` | `staff`: the first staff roles. Each entry has `phone` (`+91…`), `role` (`admin`, `catalog_editor`, `hub_manager` or `franchise_owner`), `hub` (the hub's slug, for `hub_manager` and `franchise_owner` only) and an optional `name`. A person who has not signed in yet gets their account now. Riders, packers, kitchen partners and captains come through partner verification (M8) instead, and an NGO coordinator once the NGO exists in the console | The owner, **in a private copy only** |

## Loading

1. Fill `hub.json`, `societies.json`, `catalog.json` and `prices.json` here, in a PR reviewed like
   any other (the boundaries are easiest to check on a map).
2. Once it is merged, copy this folder somewhere outside the repository. In the copy, fill
   `staff.json` and set `catalog.json`'s `folder` to the catalog's absolute path.
3. Run the dry run with `--folder <the copy>`, check its report against the PR, then run it again
   with `--apply`. A second run reports nothing to add.
4. Delete the copy once the roles show in the console.
