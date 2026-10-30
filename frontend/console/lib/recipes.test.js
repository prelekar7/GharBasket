import assert from "node:assert/strict";
import { test } from "node:test";
import {
  changeLines,
  check,
  describeRecipeProblem,
  fieldPaths,
  isStale,
  modeOf,
  toFields,
  toForm,
  toPatch,
} from "./recipes.js";

const recipe = /** @type {import("./recipes.js").AdminRecipe} */ ({
  id: "r1",
  slug: "dal",
  name: "Dal",
  names: { en: "Dal", hi: "दाल", mr: "डाळ" },
  cuisine: "common",
  slots: ["lunch"],
  cook_minutes: 30,
  servings: 4,
  diet: "veg",
  tags: ["one_pot"],
  is_side: false,
  sides: [],
  rules: { jain_ok: true, no_onion_garlic: true, fasting_ok: false, diabetic_ok: false },
  ingredients: [
    {
      product_id: "p1",
      slug: "dal",
      name: "Dal",
      qty: 40,
      unit: "g",
      label: "40 g",
      optional: false,
    },
  ],
  active: true,
  version: 3,
  steps: [{ n: 1, text: { en: "Boil.", hi: "उबालें।" } }],
  reviews: { fasting_ok: null, diabetic_ok: null },
  updated_at: "2026-10-01T00:00:00Z",
  updated_by: null,
});

test("a form round-trips to the same fields, and an untouched form patches nothing", () => {
  const form = toForm(recipe);
  assert.deepEqual(toFields(form).steps, [{ n: 1, text: { en: "Boil.", hi: "उबालें।" } }]);
  assert.deepEqual(toFields(form).ingredients, [
    { product_id: "p1", qty: 40, unit: "g", optional: false },
  ]);
  assert.deepEqual(toPatch(form, form, "full"), {});
});

test("a patch holds only what changed; a catalog editor's is cut to names and the curated flags", () => {
  const form = { ...toForm(recipe), servings: "6", fasting: true, hi: "डाल", active: false };
  assert.deepEqual(Object.keys(toPatch(toForm(recipe), form, "full")).sort(), [
    "active",
    "fasting_ok",
    "names",
    "servings",
  ]);
  assert.deepEqual(Object.keys(toPatch(toForm(recipe), form, "curate")).sort(), [
    "fasting_ok",
    "names",
  ]);
  assert.deepEqual(
    toPatch(toForm(recipe), form, "read") &&
      Object.keys(toPatch(toForm(recipe), { ...toForm(recipe), servings: "5" }, "curate")),
    [],
  );
});

test("modeOf: admin writes, catalog editor curates, others read", () => {
  assert.deepEqual(["admin", "catalog_editor", "hub_manager", undefined].map(modeOf), [
    "full",
    "curate",
    "read",
    "read",
  ]);
});

test("check finds what the API would refuse, by field path", () => {
  const bad = {
    ...toForm(null),
    slug: "Bad Slug",
    ingredients: [{ product_id: "", qty: "0", unit: "g", optional: false }],
    steps: [{ en: " ", hi: "", mr: "" }],
  };
  assert.deepEqual(Object.keys(check(bad, true)).sort(), [
    "cook_minutes",
    "ingredients.0.product_id",
    "ingredients.0.qty",
    "names.en",
    "names.hi",
    "names.mr",
    "slug",
    "steps.0.text",
  ]);
  assert.deepEqual(check(toForm(recipe), false), {});
});

test("fieldPaths keeps the row, unlike the last-name helper", () => {
  assert.deepEqual(fieldPaths([{ loc: ["body", "ingredients", 2, "qty"], message: "Too small" }]), {
    "ingredients.2.qty": "Too small",
  });
});

test("each refusal reads in plain language and points at its field", () => {
  const missing = describeRecipeProblem({
    code: "catalog.names_incomplete",
    extra: { missing: ["hi", "mr"] },
  });
  assert.deepEqual(missing.fields, {
    "names.hi": "Add the Hindi name",
    "names.mr": "Add the Marathi name",
  });
  assert.deepEqual(
    describeRecipeProblem({ code: "catalog.unit_mismatch", extra: { product_ids: ["p1"] } })
      .productIds,
    ["p1"],
  );
  assert.match(
    describeRecipeProblem({ code: "auth.forbidden", extra: { fields: ["servings"] } }).message,
    /servings/,
  );
  assert.match(
    describeRecipeProblem({ code: "catalog.recipe_in_use", extra: { recipes: ["bhindi-masala"] } })
      .message,
    /bhindi-masala/,
  );
  assert.equal(describeRecipeProblem({ code: "catalog.slug_taken" }).fields.slug, "Already used");
  assert.match(describeRecipeProblem({ code: "something.new" }).message, /did not go through/);
  assert.ok(
    isStale({ status: 412 }) &&
      isStale({ code: "catalog.version_conflict" }) &&
      !isStale({ status: 409 }),
  );
});

test("history changes read as lines", () => {
  const lines = changeLines({
    at: "",
    by: null,
    action: "catalog.recipe_updated",
    changed: { cook_minutes: { from: 25, to: 30 }, tags: { from: null, to: ["a"] } },
  });
  assert.deepEqual(lines, ["cook minutes: 25 → 30", 'tags: empty → ["a"]']);
});
