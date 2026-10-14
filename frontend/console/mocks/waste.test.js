// The waste mocks answer with the frozen M6 §3.5 shapes and the cascade's rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { addDays, mockToday } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { partnersHandlers } from "./partners.js";
import { procurementHandlers } from "./procurement.js";
import { wasteHandlers } from "./waste.js";

const handlers = [...wasteHandlers, ...procurementHandlers, ...partnersHandlers];
const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
const today = mockToday();
let n = 0;

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (method === "POST") headers["idempotency-key"] = `k-waste-${++n}`;
  if (body !== undefined) headers["content-type"] = "application/json";
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

const offers = async (/** @type {string} */ status = "open") =>
  (await call("GET", `${HUB}/surplus-offers?date=${today}&status=${status}`)).body.items;
const kitchen = async () =>
  (await call("GET", `${HUB}/partners?kind=kitchen&status=active`)).body.items.find(
    (/** @type {any} */ p) => p.kind === "kitchen",
  );

test("today's logs are newest first with the spec's fields, and filter by day and destination", async () => {
  const { status, body } = await call("GET", `${HUB}/waste-logs?from=${today}&to=${today}`);
  assert.equal(status, 200);
  assert.equal(body.next_cursor, null);
  assert.ok(body.items.length >= 3);
  const ats = body.items.map((/** @type {any} */ l) => l.at);
  assert.deepEqual(ats, [...ats].sort().reverse());
  assert.deepEqual(
    Object.keys(body.items[0]).sort(),
    "at cascade_to hub_id id kg logged_by lot_id name note offer_id product_id qty reason slug taken_by unit".split(
      " ",
    ),
  );
  const bin = await call("GET", `${HUB}/waste-logs?cascade_to=bin&from=${today}&to=${today}`);
  assert.ok(bin.body.items.every((/** @type {any} */ l) => l.cascade_to === "bin"));
  const kitchenLog = body.items.find((/** @type {any} */ l) => l.cascade_to === "kitchen");
  assert.equal(kitchenLog.taken_by.kind, "kitchen");
  assert.equal(kitchenLog.reason, "surplus");
  // 4 bunches of coriander have a weight (200 g a bunch); tomatoes in kg are the quantity itself
  const coriander = body.items.find((/** @type {any} */ l) => l.slug === "coriander");
  assert.equal(coriander.unit, "bunch");
  assert.equal(coriander.kg, 0.8);
  const page = await call("GET", `${HUB}/waste-logs?limit=2`);
  assert.equal(page.body.items.length, 2);
  assert.equal(page.body.next_cursor, "2");
  assert.equal((await call("GET", `${HUB}/waste-logs?cascade_to=compost`)).status, 422);
});

test("a write-off goes to the bin; stock and quantity rules apply", async () => {
  const products = await call("GET", `${HUB}/waste-logs?from=${today}&to=${today}`);
  const tomato = products.body.items.find((/** @type {any} */ l) => l.slug === "tomato");
  const body = { product_id: tomato.product_id, qty: 1.25, reason: "spoiled", note: "Soft" };
  const made = await call("POST", `${HUB}/waste-logs`, { body });
  assert.equal(made.status, 201);
  assert.deepEqual(
    [made.body.cascade_to, made.body.kg, made.body.logged_by.name, made.body.lot_id],
    ["bin", 1.25, "Vikram Desai", null],
  );

  const both = await call("POST", `${HUB}/waste-logs`, { body: { ...body, lot_id: "x" } });
  assert.equal(both.status, 422);
  assert.equal(
    (await call("POST", `${HUB}/waste-logs`, { body: { ...body, reason: "surplus" } })).status,
    422,
  );
  assert.equal(
    (await call("POST", `${HUB}/waste-logs`, { body: { ...body, qty: 1.2345 } })).body.code,
    "hub_ops.qty_invalid",
  );
  const coriander = products.body.items.find((/** @type {any} */ l) => l.slug === "coriander");
  assert.equal(
    (
      await call("POST", `${HUB}/waste-logs`, {
        body: { product_id: coriander.product_id, qty: 1.5, reason: "pest" },
      })
    ).body.code,
    "hub_ops.qty_invalid",
    "bunches are whole",
  );
  assert.equal(
    (
      await call("POST", `${HUB}/waste-logs`, {
        body: { lot_id: coriander.lot_id, qty: 5, reason: "pest" },
      })
    ).body.code,
    "hub_ops.qty_exceeds_stock",
  );
  const lotted = await call("POST", `${HUB}/waste-logs`, {
    body: { lot_id: coriander.lot_id, qty: 2, reason: "pest" },
  });
  assert.equal(lotted.status, 201);
  assert.equal(lotted.body.lot_id, coriander.lot_id);
  assert.equal(
    (await call("POST", `${HUB}/waste-logs`, { role: "franchise_owner", body })).status,
    403,
  );
});

test("settling: a kitchen takes part, the rest goes to an NGO; an NGO decline goes to the bin", async () => {
  const open = await offers();
  const palak = open.find((/** @type {any} */ o) => o.slug === "palak");
  assert.deepEqual([palak.offered_to, palak.status, palak.qty], ["kitchen", "open", 6]);
  const k = await kitchen();
  assert.ok(k, "the partners mock lists a kitchen");

  const path = `/v1/surplus-offers/${palak.id}/settle`;
  assert.equal(
    (
      await call("POST", path, {
        body: { outcome: "taken", qty: 4, taken_by: { kind: "ngo", name: "Someone" } },
      })
    ).body.code,
    "hub_ops.taker_invalid",
  );
  assert.equal(
    (
      await call("POST", path, {
        body: { outcome: "taken", qty: 4, taken_by: { kind: "kitchen", partner_id: "nobody" } },
      })
    ).body.code,
    "hub_ops.taker_invalid",
  );
  assert.equal(
    (
      await call("POST", path, {
        body: { outcome: "taken", qty: 7, taken_by: { kind: "kitchen", partner_id: k.id } },
      })
    ).body.code,
    "hub_ops.qty_invalid",
  );
  assert.equal(
    (
      await call("POST", path, {
        body: { outcome: "declined", taken_by: { kind: "ngo", name: "x" } },
      })
    ).body.code,
    "hub_ops.taker_invalid",
  );

  const done = await call("POST", path, {
    body: { outcome: "taken", qty: 4, taken_by: { kind: "kitchen", partner_id: k.id } },
  });
  assert.equal(done.status, 200);
  assert.deepEqual([done.body.offer.status, done.body.offer.taken_qty], ["taken", 4]);
  assert.equal(done.body.waste_logs.length, 1);
  assert.deepEqual(
    [
      done.body.waste_logs[0].cascade_to,
      done.body.waste_logs[0].qty,
      done.body.waste_logs[0].reason,
    ],
    ["kitchen", 4, "surplus"],
  );
  assert.deepEqual(
    [done.body.next.offered_to, done.body.next.qty, done.body.next.previous_offer_id],
    ["ngo", 2, palak.id],
  );
  assert.equal(
    (await call("POST", path, { body: { outcome: "declined" } })).body.code,
    "hub_ops.offer_settled",
  );

  // the NGO declines: everything goes to the bin
  const declined = await call("POST", `/v1/surplus-offers/${done.body.next.id}/settle`, {
    body: { outcome: "declined" },
  });
  assert.equal(declined.body.next, null);
  assert.deepEqual(
    [declined.body.waste_logs[0].cascade_to, declined.body.waste_logs[0].qty],
    ["bin", 2],
  );
  assert.equal(
    (await call("POST", "/v1/surplus-offers/nope/settle", { body: { outcome: "declined" } })).body
      .code,
    "hub_ops.offer_not_found",
  );

  // the seeded bhindi NGO offer is taken by a named NGO
  const ngo = (await offers()).find((/** @type {any} */ o) => o.slug === "bhindi");
  assert.equal(ngo.offered_to, "ngo");
  assert.equal(ngo.previous_offer_id !== null, true);
  const taken = await call("POST", `/v1/surplus-offers/${ngo.id}/settle`, {
    body: { outcome: "taken", taken_by: { kind: "ngo", name: "Asha NGO" } },
  });
  assert.deepEqual(
    [taken.body.offer.taken_qty, taken.body.waste_logs[0].taken_by],
    [1.5, { kind: "ngo", name: "Asha NGO" }],
  );
  assert.equal(taken.body.next, null);
});

test("the 12:30 run closes the day's buying once and offers what cannot be packed tomorrow", async () => {
  const before = (await call("GET", `${HUB}/purchase-orders?date=${today}`)).body.items;
  assert.ok(before.length > 0);
  const run = await call("POST", `${HUB}/surplus/run`);
  assert.equal(run.status, 200);
  assert.deepEqual(Object.keys(run.body).sort(), [
    "binned",
    "closed_purchase_orders",
    "drawn_tasks",
    "offers",
  ]);
  assert.equal(run.body.closed_purchase_orders > 0, true);
  assert.ok(run.body.drawn_tasks > 0, "packed bags are drawn down from stock");
  const after = (await call("GET", `${HUB}/purchase-orders?date=${today}`)).body.items;
  assert.ok(after.every((/** @type {any} */ o) => ["received", "cancelled"].includes(o.status)));
  const plan = await call("GET", `${HUB}/procurement-plans?date=${today}`);
  assert.equal(plan.body.status, "closed");
  const again = await call("POST", `${HUB}/surplus/run`);
  assert.deepEqual(
    [
      again.body.drawn_tasks,
      again.body.closed_purchase_orders,
      again.body.offers,
      again.body.binned,
    ],
    [0, 0, 0, 0],
  );
});

test("the summary adds up and the KPI is the server's number", async () => {
  const { status, body } = await call("GET", `${HUB}/waste-summary`);
  assert.equal(status, 200);
  assert.equal(body.days.length, 7);
  assert.equal(body.to, addDays(today, -1));
  const sum = (/** @type {string} */ k) =>
    Math.round(
      body.days.reduce((/** @type {number} */ s, /** @type {any} */ d) => s + d[k], 0) * 1000,
    ) / 1000;
  assert.equal(body.totals.sold_kg, sum("sold_kg"));
  assert.equal(body.totals.wasted_kg, sum("wasted_kg"));
  assert.equal(
    body.totals.waste_per_100kg,
    Math.round((body.totals.wasted_kg / body.totals.sold_kg) * 10_000) / 100,
  );
  assert.deepEqual(
    Object.keys(body.days[0]).sort(),
    "date rescued_kg sold_kg stockout_pct unweighed_lines waste_per_100kg wasted_kg".split(" "),
  );
  const withToday = await call(
    "GET",
    `${HUB}/waste-summary?from=${addDays(today, -6)}&to=${today}`,
  );
  assert.ok(withToday.body.days.at(-1).wasted_kg > 0, "today's write-offs count");
  const future = await call(
    "GET",
    `${HUB}/waste-summary?from=${addDays(today, 1)}&to=${addDays(today, 1)}`,
  );
  assert.equal(future.body.days[0].waste_per_100kg, null, "nothing sold, no ratio");
  assert.equal(
    (await call("GET", `${HUB}/waste-summary?from=${addDays(today, -40)}&to=${today}`)).status,
    422,
  );
});
