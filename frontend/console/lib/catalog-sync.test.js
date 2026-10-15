import assert from "node:assert/strict";
import { test } from "node:test";
import { categoriesOf, describeProblem } from "./catalog.js";
import { groupDays, ruleSummary } from "./festivals.js";
import { featureLabel } from "./plans.js";
import { buildItems, compare, parseCsv } from "./priceList.js";
import { isNotShipped, orNotShipped } from "./proposal.js";

test("categories are derived from the products' category, with counts", () => {
  const cat = (/** @type {string} */ slug, /** @type {string} */ name) => ({
    id: `id-${slug}`,
    slug,
    name,
  });
  const rows = categoriesOf(
    /** @type {any} */ ([
      { category: cat("vegetables", "Vegetables") },
      { category: cat("fruits", "Fruits") },
      { category: cat("vegetables", "Vegetables") },
    ]),
  );
  assert.deepEqual(
    rows.map((c) => [c.slug, c.product_count]),
    [
      ["fruits", 1],
      ["vegetables", 2],
    ],
  );
});

const known = [
  { id: "u-1", slug: "bhindi", name: "Okra", step: "250 g", nowPaise: 2000, mrpPaise: 2400 },
  { id: "u-2", slug: "palak", name: "Spinach", step: "1 bunch", nowPaise: 2500, mrpPaise: null },
  { id: "u-3", slug: "tomato", name: "Tomato", step: "500 g", nowPaise: null, mrpPaise: null },
];

test("a publish sends the whole list: what the file leaves out keeps its price and MRP", () => {
  const { lines, hasMrp } = parseCsv("product_id,price\nu-1,22\ntomato,30\n");
  assert.equal(hasMrp, false);
  assert.deepEqual(buildItems(lines, known, hasMrp), [
    { product_id: "u-1", price_paise: 2200, mrp_paise: 2400 }, // a file without MRPs keeps the MRP
    { product_id: "u-2", price_paise: 2500, mrp_paise: null },
    { product_id: "u-3", price_paise: 3000, mrp_paise: null }, // by slug, resolved to the uuid
  ]);
});

test("a blank MRP in the file removes it; a price above the MRP is flagged before sending", () => {
  const { lines, hasMrp } = parseCsv("product_id,price,mrp\nu-1,25,\nu-2,26,25\n");
  assert.equal(buildItems(lines, known, hasMrp)[0].mrp_paise, null);
  const { rows } = compare(lines, known, (p) => `₹${p / 100}`);
  assert.deepEqual(
    rows.filter((r) => r.state === "error").map((r) => r.id),
    ["u-2"],
  );
});

test("festival rows of consecutive dates become one run", () => {
  const day = (/** @type {string} */ date, code = "navratri") => ({
    date,
    code,
    name: "Navratri",
    region: "all",
    rules: { fasting: true, no_onion_garlic: true },
    verified: false,
  });
  const runs = groupDays([
    day("2026-10-13"),
    day("2026-10-11"),
    day("2026-10-12"),
    day("2026-11-05", "ekadashi"),
  ]);
  assert.deepEqual(
    runs.map((r) => [r.id, r.starts_on, r.ends_on]),
    [
      ["navratri:all:2026-10-11", "2026-10-11", "2026-10-13"],
      ["ekadashi:all:2026-11-05", "2026-11-05", "2026-11-05"],
    ],
  );
  assert.equal(ruleSummary({ fasting: true, no_onion_garlic: false }), "Fasting day");
});

test("feature codes read as words; an unknown code is spaced out", () => {
  assert.equal(featureLabel("weekly_box"), "A planned box every delivery day");
  assert.equal(featureLabel("free_delivery"), "Free delivery");
});

test("a route that does not exist is told from a domain 404", async () => {
  assert.equal(isNotShipped({ code: "http.not_found" }), true);
  assert.equal(isNotShipped({ code: "http.method_not_allowed" }), true);
  assert.equal(isNotShipped({ code: "catalog.product_not_found" }), false);
  assert.equal(await orNotShipped(Promise.reject({ code: "http.not_found" })), null);
  await assert.rejects(orNotShipped(Promise.reject({ code: "request.invalid" })));
  assert.match(describeProblem({ code: "http.not_found" }).message, /not available yet/);
});
