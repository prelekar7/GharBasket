import assert from "node:assert/strict";
import { test } from "node:test";
import { cookView } from "./view.js";

/** @param {Partial<import("../../../../mocks/public.js").PublicCook>} over @returns {import("../../../../mocks/public.js").PublicCook} */
const cook = (over) => ({
  handle: "k7m2qx4rta",
  display_name: "Lata tai",
  photo_url: null,
  hub: { name: "Kharghar hub" },
  since: "2026-09",
  fssai: { reg_no: "11521999000123", valid_until: "2028-03-31" },
  hygiene: { grade: "A", inspected_on: "2026-10-20" },
  trainings: [
    { code: "app_basics", title: "Using the GharBasket app" },
    { code: "hygiene", title: "Kitchen hygiene" },
  ],
  rating: { average: 4.66, count: 23 },
  skills: ["veg", "jain"],
  dishes: [{ name: "Jain thali" }],
  ...over,
});

const fact = (/** @type {ReturnType<typeof cookView>} */ v, /** @type {string} */ k) =>
  v.facts.find((f) => f.k === k)?.v;

test("a verified cook shows her checks, rating, hub and month", () => {
  const v = cookView(cook({}), "en");
  assert.equal(fact(v, "FSSAI registration"), "11521999000123 · valid until March 2028");
  assert.equal(fact(v, "Kitchen inspection"), "Grade A · 20 Oct 2026");
  assert.equal(fact(v, "Families’ rating"), "4.7 · 23 ratings");
  assert.equal(fact(v, "Checked by"), "Kharghar hub");
  assert.equal(fact(v, "Cooking with GharBasket since"), "September 2026");
  assert.deepEqual(v.skills, ["Vegetarian", "Jain"]);
  assert.deepEqual(v.pills, ["FSSAI registered", "Hygiene trained"]);
  assert.equal(v.initial, "L");
});

test("a new cook leaves out what the API has not got, instead of printing null", () => {
  const v = cookView(
    cook({ fssai: null, hygiene: null, rating: null, trainings: [], dishes: [] }),
    "en",
  );
  assert.deepEqual(
    v.facts.map((f) => f.k),
    ["Families’ rating", "Checked by", "Cooking with GharBasket since"],
  );
  assert.equal(fact(v, "Families’ rating"), "Not enough ratings yet");
  assert.deepEqual(v.pills, []);
  assert.ok(!JSON.stringify(v).includes("null"));
});

test("an FSSAI number without a validity date shows the number alone", () => {
  const v = cookView(cook({ fssai: { reg_no: "11521999000123", valid_until: null } }), "en");
  assert.equal(fact(v, "FSSAI registration"), "11521999000123");
});

test("the copy names her and never guesses a gender", () => {
  assert.equal(
    cookView(cook({}), "en").privacy,
    "Shown with Lata tai’s consent. No phone number or address is ever shared.",
  );
});

test("an unknown skill code is left out rather than shown raw", () => {
  const v = cookView(cook({ skills: /** @type {any} */ (["veg", "halal"]) }), "en");
  assert.deepEqual(v.skills, ["Vegetarian"]);
});

test("Hindi uses Hindi labels and dates", () => {
  const v = cookView(cook({}), "hi");
  assert.deepEqual(v.skills, ["शाकाहारी", "जैन"]);
  assert.equal(fact(v, "परिवारों की रेटिंग"), "4.7 · 23 रेटिंग");
  assert.match(fact(v, "GharBasket के साथ कब से") ?? "", /2026/);
});
