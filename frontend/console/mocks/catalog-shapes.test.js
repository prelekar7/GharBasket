// The catalog, price, festival and plan mocks answer with exactly the real contract's fields (M2, M3), and
// the publish follows the real rules and error codes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { catalogHandlers } from "./catalog.js";
import { festivalsHandlers } from "./festivals.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { plansHandlers } from "./plans.js";
import { pricesHandlers } from "./prices.js";

const handlers = [...catalogHandlers, ...pricesHandlers, ...festivalsHandlers, ...plansHandlers];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, key?: string }} [options]
 */
async function call(method, path, { body, role = "admin", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (key) headers["idempotency-key"] = key;
  const response = await getResponse(
    handlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

/** @param {object} o */
const keys = (o) => Object.keys(o).sort();

test("GET /v1/products rows are ProductOut: uuid ids, no `active`, a category object", async () => {
  const { body } = await call("GET", "/v1/products?limit=3");
  assert.deepEqual(keys(body), ["items", "next_cursor"]);
  for (const p of body.items) {
    assert.deepEqual(keys(p), [
      "category",
      "id",
      "image_url",
      "kind",
      "name",
      "names",
      "price",
      "shelf_life_h",
      "slug",
      "step",
      "tags",
    ]);
    assert.match(p.id, UUID);
    assert.deepEqual(keys(p.category), ["id", "name", "slug"]);
    assert.match(p.category.id, UUID);
  }
  // the contract has no category list (GET /v1/categories): the page derives it from the products
});

test("GET /v1/prices is PriceListOut and joins to products by product_id", async () => {
  const { status, body } = await call("GET", `/v1/prices?hub_id=${KHARGHAR_HUB_ID}`);
  assert.equal(status, 200);
  assert.deepEqual(keys(body), ["hub_id", "id", "items", "valid_from"]);
  assert.deepEqual(keys(body.items[0]), ["mrp_paise", "price_paise", "product_id"]);
  const products = (await call("GET", "/v1/products?limit=100")).body.items;
  for (const i of body.items)
    assert.ok(products.some((/** @type {{ id: string }} */ p) => p.id === i.product_id));
  assert.equal((await call("GET", "/v1/prices")).status, 422);
});

test("publishing a price list: PriceListCreated, and the real error codes", async () => {
  const products = (await call("GET", "/v1/products?limit=100")).body.items;
  const [a, b] = products;
  const publish = (/** @type {object} */ body, key = crypto.randomUUID()) =>
    call("POST", `/v1/hubs/${KHARGHAR_HUB_ID}/price-lists`, { body, key });
  const items = [{ product_id: a.id, price_paise: 2600, mrp_paise: null }];

  assert.equal(
    (await call("POST", `/v1/hubs/${KHARGHAR_HUB_ID}/price-lists`, { body: {} })).body.code,
    "request.idempotency_key_required",
  );
  // `lines` was the old name: every request field is checked, so an extra one is a 422
  const old = await publish({ valid_from: "2099-01-01", lines: items });
  assert.equal(old.status, 422);
  assert.equal(old.body.code, "request.invalid");
  assert.ok(old.body.errors.every((/** @type {{ loc: unknown[] }} */ e) => Array.isArray(e.loc)));

  assert.equal(
    (await publish({ valid_from: "2020-01-01", items })).body.code,
    "catalog.valid_from_past",
  );
  const above = await publish({
    valid_from: "2099-01-01",
    items: [{ product_id: a.id, price_paise: 3000, mrp_paise: 2500 }],
  });
  assert.equal(above.body.code, "catalog.price_above_mrp");
  assert.deepEqual(above.body.product_ids, [a.id]);
  const unknown = await publish({
    valid_from: "2099-01-01",
    items: [{ product_id: "0199b000-0000-7000-8000-ffffffffffff", price_paise: 100 }],
  });
  assert.equal(unknown.body.code, "catalog.product_unknown");
  assert.deepEqual(unknown.body.product_ids, ["0199b000-0000-7000-8000-ffffffffffff"]);

  const created = await publish({
    valid_from: "2099-01-01",
    items: [...items, { product_id: b.id, price_paise: 900, mrp_paise: 1000 }],
  });
  assert.equal(created.status, 201);
  assert.deepEqual(keys(created.body), ["hub_id", "id", "items_count", "valid_from"]);
  assert.equal(created.body.items_count, 2);
  assert.equal(
    (await publish({ valid_from: "2099-01-01", items })).body.code,
    "catalog.price_list_exists",
  );
  // later in time, so the list in use today is unchanged; the date lookup finds the new one
  const future = await call("GET", `/v1/prices?hub_id=${KHARGHAR_HUB_ID}&date=2099-06-01`);
  assert.equal(future.body.items.length, 2);
  assert.equal(
    (
      await call("POST", `/v1/hubs/${KHARGHAR_HUB_ID}/price-lists`, {
        body: { valid_from: "2099-02-02", items },
        key: "k",
        role: "franchise_owner",
      })
    ).status,
    403,
  );
});

test("GET /v1/festivals is one FestivalOut per date", async () => {
  const { body } = await call("GET", "/v1/festivals?from=2026-10-11&to=2026-10-19");
  assert.deepEqual(keys(body), ["items"]);
  assert.equal(body.items.length, 9);
  assert.deepEqual(keys(body.items[0]), ["code", "date", "name", "region", "rules", "verified"]);
  assert.deepEqual(keys(body.items[0].rules), ["fasting", "no_onion_garlic"]);
});

test("GET /v1/plans is PlanOut of the live plans; /v1/admin/plans adds state and subscribers and is read-only", async () => {
  const { body } = await call("GET", "/v1/plans");
  assert.deepEqual(keys(body), ["items"]);
  assert.deepEqual(keys(body.items[0]), ["code", "features", "fee_paise", "name"]);
  assert.equal(body.items[0].fee_paise, 0);
  assert.deepEqual(
    body.items.map((/** @type {{ code: string }} */ p) => p.code),
    ["autopilot", "tiffin"],
  );
  const admin = await call("GET", "/v1/admin/plans");
  assert.deepEqual(
    admin.body.items.map((/** @type {{ code: string, state: string }} */ p) => [p.code, p.state]),
    [
      ["autopilot", "live"],
      ["tiffin", "live"],
      ["salad", "draft"],
    ],
  );
  assert.ok(
    admin.body.items.every((/** @type {{ subscriber_count: unknown }} */ p) =>
      Number.isInteger(p.subscriber_count),
    ),
  );
  // Create and edit were dropped (M3 §3.5): the mock has no route for them.
  assert.deepEqual(
    plansHandlers.map((h) => h.info.method),
    ["GET", "GET"],
  );
  assert.deepEqual(keys(admin.body.items[0]), [
    "code",
    "features",
    "fee_paise",
    "name",
    "state",
    "subscriber_count",
  ]);
  assert.equal((await call("GET", "/v1/admin/plans", { role: "hub_manager" })).status, 403);
});
