// The inventory and lot-count mocks answer with the built M6 §3.4 shapes and refusals.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { stockHandlers } from "./stock.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
let n = 0;

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (method === "POST") headers["idempotency-key"] = `k-stock-${++n}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await getResponse(
    stockHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

const inventory = async () => (await call("GET", `${HUB}/inventory`)).body;

test("inventory lists products with their lots, first expiry first, and the sums add up", async () => {
  const { as_of, items } = await inventory();
  assert.ok(as_of && items.length >= 3);
  assert.deepEqual(
    Object.keys(items[0]).sort(),
    "lots name on_hand_qty packed_not_drawn_qty product_id shelf_life_h slug unit".split(" "),
  );
  for (const item of items) {
    const dates = item.lots.map((/** @type {any} */ l) => l.best_before);
    assert.deepEqual(dates, [...dates].sort());
    assert.ok(item.lots.every((/** @type {any} */ l) => l.qty_on_hand > 0));
    const sum = item.lots.reduce(
      (/** @type {number} */ s, /** @type {any} */ l) => s + l.qty_on_hand,
      0,
    );
    assert.ok(Math.abs(sum - item.on_hand_qty) < 1e-9);
  }
  const lot = items[0].lots[0];
  for (const k of "best_before grade hub_id id name product_id qty_in qty_on_hand receipt_id received_at slug source unit unit_cost_paise vendor".split(
    " ",
  ))
    assert.ok(k in lot, k);
});

test("expiring_before keeps the lots that go sooner, and needs a time zone", async () => {
  const soon = new Date(Date.now() + 12 * 3_600_000).toISOString();
  const { items } = (
    await call("GET", `${HUB}/inventory?expiring_before=${encodeURIComponent(soon)}`)
  ).body;
  const lots = items.flatMap((/** @type {any} */ i) => i.lots);
  assert.ok(lots.length >= 1 && lots.every((/** @type {any} */ l) => l.best_before < soon));
  assert.equal(
    (await call("GET", `${HUB}/inventory?expiring_before=2026-10-30T10:00:00`)).status,
    422,
  );
});

test("a lot shows its moves, and a count sets the stock with a count move", async () => {
  const lot = (await inventory()).items[0].lots[0];
  const detail = (await call("GET", `/v1/lots/${lot.id}`)).body;
  assert.equal(detail.moves[0].kind, "receive");
  assert.equal(detail.moves[0].qty, lot.qty_in);
  const target =
    lot.unit === "kg" || lot.unit === "l" ? lot.qty_on_hand - 1.5 : lot.qty_on_hand - 1;
  const counted = await call("POST", `/v1/lots/${lot.id}/count`, {
    body: { qty_on_hand: target, reason: "Shelf count" },
  });
  assert.equal(counted.status, 200);
  assert.equal(counted.body.qty_on_hand, target);
  const after = (await call("GET", `/v1/lots/${lot.id}`)).body;
  const last = after.moves.at(-1);
  assert.equal(last.kind, "count");
  assert.equal(last.reason, "Shelf count");
  assert.ok(last.qty < 0 && last.by.name);
});

test("a count is refused for a bad quantity, a short reason, an unknown lot or a franchise owner", async () => {
  const lot = (await inventory()).items[0].lots[0];
  const post = (/** @type {unknown} */ body, role = "hub_manager") =>
    call("POST", `/v1/lots/${lot.id}/count`, { body, role });
  assert.equal(
    (await post({ qty_on_hand: -1, reason: "Shelf count" })).body.code,
    "hub_ops.qty_invalid",
  );
  assert.equal(
    (await post({ qty_on_hand: 1.2345, reason: "Shelf count" })).body.code,
    "hub_ops.qty_invalid",
  );
  assert.equal((await post({ qty_on_hand: 1, reason: "no" })).body.code, "request.invalid");
  assert.equal(
    (await post({ qty_on_hand: 1, reason: "Shelf count" }, "franchise_owner")).status,
    403,
  );
  const missing = await call("POST", "/v1/lots/00000000-0000-7000-8000-000000000000/count", {
    body: { qty_on_hand: 1, reason: "Shelf count" },
  });
  assert.equal(missing.body.code, "hub_ops.lot_not_found");
});
