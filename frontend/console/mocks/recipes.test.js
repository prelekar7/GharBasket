// The recipes admin mocks serve the contract's shapes and refusals (M2 §3.3).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { catalogHandlers, catalogStore } from "./catalog.js";
import { recipesHandlers } from "./recipes.js";

const handlers = [...catalogHandlers, ...recipesHandlers];

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, key?: string, ifMatch?: string }} [options]
 */
async function call(method, path, { body, role = "admin", key, ifMatch } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (key) headers["idempotency-key"] = key;
  if (ifMatch) headers["if-match"] = ifMatch;
  const response = await getResponse(
    handlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, headers: response.headers, body: await response.json() };
}

const productId = (/** @type {string} */ slug) =>
  catalogStore().products.find((p) => p.slug === slug)?.id;
const names = { en: "Test sabzi", hi: "टेस्ट सब्ज़ी", mr: "टेस्ट भाजी" };
const newRecipe = (/** @type {object} */ over = {}) => ({
  slug: "test-sabzi",
  names,
  cuisine: "common",
  slots: ["lunch"],
  cook_minutes: 20,
  servings: 4,
  fasting_ok: false,
  diabetic_ok: false,
  ingredients: [{ product_id: productId("bhindi"), qty: 100, unit: "g" }],
  ...over,
});
const firstId = async () => (await call("GET", "/v1/admin/recipes?q=masoor")).body.items[0].id;

test("the list is alphabetical, has inactive recipes and no steps", async () => {
  const { status, body } = await call("GET", "/v1/admin/recipes");
  assert.equal(status, 200);
  assert.deepEqual(Object.keys(body).sort(), ["items", "next_cursor"]);
  const en = body.items.map((/** @type {{ names: { en: string } }} */ r) => r.names.en);
  assert.deepEqual(
    en,
    [...en].sort((a, b) => a.localeCompare(b)),
  );
  assert.ok(body.items.some((/** @type {{ active: boolean }} */ r) => !r.active));
  assert.ok(body.items.every((/** @type {{ steps: unknown }} */ r) => r.steps === null));
  const inactive = await call("GET", "/v1/admin/recipes?active=false");
  assert.ok(
    inactive.body.items.length > 0 &&
      inactive.body.items.every((/** @type {{ active: boolean }} */ r) => !r.active),
  );
  assert.equal((await call("GET", "/v1/admin/recipes?q=paneer")).body.items.length, 1);
});

test("one recipe carries its ETag, steps and reviews; unknown id is 404", async () => {
  const id = await firstId();
  const { body, headers } = await call("GET", `/v1/admin/recipes/${id}`, { role: "hub_manager" });
  assert.equal(headers.get("etag"), `"${body.version}"`);
  assert.ok(Array.isArray(body.steps));
  assert.deepEqual(Object.keys(body.reviews).sort(), ["diabetic_ok", "fasting_ok"]);
  const miss = await call("GET", "/v1/admin/recipes/nope");
  assert.equal(miss.status, 404);
  assert.equal(miss.body.code, "catalog.recipe_not_found");
});

test("PATCH needs If-Match: 428 without, 412 when stale, 200 and version + 1 when right", async () => {
  const id = await firstId();
  const patch = { servings: 5 };
  assert.equal(
    (await call("PATCH", `/v1/admin/recipes/${id}`, { body: patch })).body.code,
    "request.precondition_required",
  );
  const stale = await call("PATCH", `/v1/admin/recipes/${id}`, { body: patch, ifMatch: '"99"' });
  assert.equal(stale.status, 412);
  assert.equal(stale.body.code, "catalog.version_conflict");
  const ok = await call("PATCH", `/v1/admin/recipes/${id}`, { body: patch, ifMatch: '"1"' });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.version, 2);
  assert.equal(ok.body.servings, 5);
  assert.equal(ok.headers.get("etag"), '"2"');
});

test("a catalog editor may PATCH names and the curated flags only, and the flag review is recorded", async () => {
  const id = await firstId();
  const { body: now } = await call("GET", `/v1/admin/recipes/${id}`);
  const ifMatch = `"${now.version}"`;
  const refused = await call("PATCH", `/v1/admin/recipes/${id}`, {
    role: "catalog_editor",
    ifMatch,
    body: { servings: 2, fasting_ok: true },
  });
  assert.equal(refused.status, 403);
  assert.deepEqual(refused.body.fields, ["servings"]);
  const ok = await call("PATCH", `/v1/admin/recipes/${id}`, {
    role: "catalog_editor",
    ifMatch,
    body: { fasting_ok: true },
  });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.rules.fasting_ok, true);
  assert.equal(ok.body.reviews.fasting_ok.by.name, "Rekha Joshi");
  assert.equal(ok.body.servings, now.servings);
  assert.equal(
    (await call("PATCH", `/v1/admin/recipes/${id}`, { role: "hub_manager", ifMatch, body: {} }))
      .status,
    403,
  );
});

test("create: Idempotency-Key, 201 at version 1, then the refusals", async () => {
  assert.equal(
    (await call("POST", "/v1/admin/recipes", { body: newRecipe() })).body.code,
    "request.idempotency_key_required",
  );
  assert.equal(
    (
      await call("POST", "/v1/admin/recipes", {
        role: "catalog_editor",
        key: "r0",
        body: newRecipe(),
      })
    ).status,
    403,
  );
  const made = await call("POST", "/v1/admin/recipes", { key: "r1", body: newRecipe() });
  assert.equal(made.status, 201);
  assert.equal(made.body.version, 1);
  assert.equal(made.body.name, "Test sabzi");
  assert.equal(made.body.ingredients[0].name, "Okra (bhindi)");
  const dup = await call("POST", "/v1/admin/recipes", { key: "r2", body: newRecipe() });
  assert.equal(dup.status, 409);
  assert.equal(dup.body.code, "catalog.slug_taken");
  const half = await call("POST", "/v1/admin/recipes", {
    key: "r3",
    body: newRecipe({ slug: "half", names: { ...names, hi: " " } }),
  });
  assert.equal(half.body.code, "catalog.names_incomplete");
  assert.deepEqual(half.body.missing, ["hi"]);
  const unit = await call("POST", "/v1/admin/recipes", {
    key: "r4",
    body: newRecipe({
      slug: "bad-unit",
      ingredients: [{ product_id: productId("bhindi"), qty: 1, unit: "ml" }],
    }),
  });
  assert.equal(unit.body.code, "catalog.unit_mismatch");
  assert.deepEqual(unit.body.product_ids, [productId("bhindi")]);
  const unknown = await call("POST", "/v1/admin/recipes", {
    key: "r5",
    body: newRecipe({ slug: "ghost", ingredients: [{ product_id: "nope", qty: 1, unit: "g" }] }),
  });
  assert.equal(unknown.body.code, "catalog.product_unknown");
  const side = await call("POST", "/v1/admin/recipes", {
    key: "r6",
    body: newRecipe({ slug: "bad-side", sides: ["masoor-dal"] }),
  });
  assert.equal(side.body.code, "catalog.side_invalid");
  const field = await call("POST", "/v1/admin/recipes", {
    key: "r7",
    body: newRecipe({
      slug: "bad-qty",
      cook_minutes: 2,
      ingredients: [{ product_id: productId("bhindi"), qty: 0, unit: "g" }],
    }),
  });
  assert.equal(field.status, 422);
  assert.equal(field.body.code, "request.invalid");
  assert.deepEqual(
    field.body.errors.map((/** @type {{ loc: unknown[] }} */ e) => e.loc.join(".")),
    ["body.cook_minutes", "body.ingredients.0.qty"],
  );
});

test("PATCH refusals: slug is immutable, a side in use cannot be switched off, history lists the edits newest first", async () => {
  const id = await firstId();
  const v = (await call("GET", `/v1/admin/recipes/${id}`)).body.version;
  const slug = await call("PATCH", `/v1/admin/recipes/${id}`, {
    ifMatch: `"${v}"`,
    body: { slug: "other" },
  });
  assert.equal(slug.body.code, "catalog.field_immutable");
  assert.deepEqual(slug.body.fields, ["slug"]);
  const chapati = (await call("GET", "/v1/admin/recipes?q=chapati")).body.items[0];
  const inUse = await call("PATCH", `/v1/admin/recipes/${chapati.id}`, {
    ifMatch: `"${chapati.version}"`,
    body: { active: false },
  });
  assert.equal(inUse.status, 409);
  assert.equal(inUse.body.code, "catalog.recipe_in_use");
  assert.deepEqual(inUse.body.recipes, ["bhindi-masala"]);
  const { body } = await call("GET", `/v1/admin/recipes/${id}/history`, { role: "catalog_editor" });
  assert.ok(body.items.length >= 2);
  assert.ok(body.items[0].at >= body.items[1].at);
  assert.deepEqual(Object.keys(body.items[0]).sort(), ["action", "at", "by", "changed"]);
  assert.ok(
    "from" in Object.values(body.items[0].changed)[0] &&
      "to" in Object.values(body.items[0].changed)[0],
  );
});
