// Slots, delivery days and orders: the mocks answer with the real contract's fields and rules (M3, M4).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { cyclesHandlers } from "./cycles.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { ordersHandlers } from "./orders.js";

const handlers = [...ordersHandlers, ...cyclesHandlers];

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string | null, key?: string }} [options]
 */
async function call(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({});
  if (role) headers["x-gb-role"] = role;
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

test("GET /v1/slots is SlotsOut: kind, start and end with seconds, available", async () => {
  const { body } = await call("GET", `/v1/slots?hub_id=${KHARGHAR_HUB_ID}&date=2026-10-06`);
  assert.deepEqual(keys(body), ["date", "items"]);
  assert.deepEqual(keys(body.items[0]), [
    "available",
    "booked",
    "capacity",
    "end",
    "hub_id",
    "id",
    "kind",
    "label",
    "start",
  ]);
  const am = body.items.find((/** @type {{ id: string }} */ s) => s.id === SLOT_ID.am);
  assert.equal(am.available, am.capacity - am.booked);
  assert.match(am.start, /^\d\d:\d\d:\d\d$/);
});

test("creating a slot: label and kind, 409 on a repeated label, 422 when it ends before it starts", async () => {
  const slot = {
    hub_id: KHARGHAR_HUB_ID,
    label: "Noon 12-2",
    kind: "box",
    start: "12:00",
    end: "14:00",
    capacity: 30,
  };
  const old = await call("POST", "/v1/admin/slots", {
    key: "k1",
    body: {
      hub_id: KHARGHAR_HUB_ID,
      start_time: "12:00",
      end_time: "14:00",
      capacity: 30,
      serves: "Boxes",
    },
  });
  assert.equal(old.status, 422);
  assert.equal(old.body.code, "request.invalid");
  const made = await call("POST", "/v1/admin/slots", { key: "k2", body: slot });
  assert.equal(made.status, 201);
  assert.deepEqual(keys(made.body), [
    "available",
    "booked",
    "capacity",
    "end",
    "hub_id",
    "id",
    "kind",
    "label",
    "start",
  ]);
  assert.equal(
    (await call("POST", "/v1/admin/slots", { key: "k3", body: slot })).body.code,
    "orders.slot_exists",
  );
  assert.equal(
    (
      await call("POST", "/v1/admin/slots", {
        key: "k4",
        body: { ...slot, label: "Bad", start: "14:00", end: "12:00" },
      })
    ).body.code,
    "orders.slot_invalid",
  );
  assert.equal(
    (await call("POST", "/v1/admin/slots", { body: slot })).body.code,
    "request.idempotency_key_required",
  );
});

test("PATCH a slot changes capacity and active only; times are refused", async () => {
  const ok = await call("PATCH", `/v1/admin/slots/${SLOT_ID.pm}`, { body: { capacity: 55 } });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.capacity, 55);
  const times = await call("PATCH", `/v1/admin/slots/${SLOT_ID.pm}`, {
    body: { capacity: 55, start: "18:00" },
  });
  assert.equal(times.status, 422);
  assert.deepEqual(times.body.errors[0].loc, ["body", "start"]);
  const off = await call("PATCH", `/v1/admin/slots/${SLOT_ID.early}`, { body: { active: false } });
  assert.equal(off.status, 200);
  const list = await call("GET", "/v1/slots?date=2026-10-06");
  assert.ok(!list.body.items.some((/** @type {{ id: string }} */ s) => s.id === SLOT_ID.early));
});

test("GET /v1/orders is OrderSummaryOut, paged by cursor, from and to", async () => {
  const first = await call(
    "GET",
    `/v1/orders?hub_id=${KHARGHAR_HUB_ID}&from=2026-10-06&to=2026-10-06&limit=20`,
  );
  assert.deepEqual(keys(first.body.items[0]), [
    "created_at",
    "cutoff_at",
    "cycle_id",
    "delivery_date",
    "discount_paise",
    "fees_paise",
    "household_id",
    "hub_id",
    "id",
    "kind",
    "locked_at",
    "slot",
    "society_id",
    "status",
    "subtotal_paise",
    "total_paise",
    "version",
  ]);
  assert.deepEqual(keys(first.body.items[0].slot), ["end", "id", "label", "start"]);
  assert.ok(first.body.next_cursor);
  assert.ok(
    first.body.items.every(
      (/** @type {{ status: string }} */ o) => o.status !== "awaiting_approval",
    ),
  );
  const all = [...first.body.items];
  let cursor = first.body.next_cursor;
  while (cursor) {
    const page = await call(
      "GET",
      `/v1/orders?hub_id=${KHARGHAR_HUB_ID}&from=2026-10-06&to=2026-10-06&limit=20&cursor=${cursor}`,
    );
    all.push(...page.body.items);
    cursor = page.body.next_cursor;
  }
  assert.equal(new Set(all.map((o) => o.id)).size, all.length);
  assert.equal(
    (await call("GET", `/v1/orders?from=2026-10-06&to=2026-10-06`)).body.code,
    "orders.hub_required",
  );
  const day = await call(
    "GET",
    `/v1/orders?hub_id=${KHARGHAR_HUB_ID}&from=2026-10-08&to=2026-10-08`,
  );
  assert.deepEqual(day.body.items, []);
});

test("delivery days: weekday 0 is Monday, 1 to 3 items, the response carries each day's slot", async () => {
  const read = await call("GET", `/v1/societies/${SOCIETY_ID["sunrise-towers"]}/delivery-days`);
  assert.deepEqual(keys(read.body), ["items"]);
  assert.deepEqual(keys(read.body.items[0]), ["slot", "weekday"]);
  assert.deepEqual(keys(read.body.items[0].slot), ["end", "id", "label", "start"]);
  const put = (/** @type {unknown} */ body, id = SOCIETY_ID["krishna-kunj"]) =>
    call("PUT", `/v1/admin/societies/${id}/delivery-days`, { key: crypto.randomUUID(), body });
  assert.equal((await put({ slot_id: SLOT_ID.am, weekdays: [2] })).body.code, "request.invalid");
  assert.equal((await put({ items: [] })).body.code, "request.invalid");
  assert.equal(
    (await put({ items: [0, 1, 2, 3].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })) }))
      .status,
    422,
  );
  assert.equal(
    (await put({ items: [{ weekday: 7, slot_id: SLOT_ID.am }] })).body.code,
    "request.invalid",
  );
  assert.equal(
    (await put({ items: [{ weekday: 1, slot_id: SLOT_ID.pm }] })).body.code,
    "subscriptions.delivery_days_invalid",
  );
  assert.equal(
    (
      await put(
        { items: [{ weekday: 1, slot_id: SLOT_ID.am }] },
        "0199a000-0000-7000-8000-0000000000ff",
      )
    ).body.code,
    "subscriptions.society_unknown",
  );
  const ok = await put({
    items: [
      { weekday: 0, slot_id: SLOT_ID.am },
      { weekday: 3, slot_id: SLOT_ID.am },
    ],
  });
  assert.equal(ok.status, 200);
  assert.deepEqual(
    ok.body.items.map((/** @type {{ weekday: number }} */ d) => d.weekday),
    [0, 3],
  );
  const hub = await call("GET", `/v1/hubs/${KHARGHAR_HUB_ID}/delivery-days`);
  // M3 §3.5: every society of the hub with its status (live, unlocking, prospect), no not_live list
  assert.deepEqual(keys(hub.body), ["hub_id", "items"]);
  assert.equal(hub.body.hub_id, KHARGHAR_HUB_ID);
  assert.deepEqual(keys(hub.body.items[0]), ["area", "items", "name", "society_id", "status"]);
  assert.deepEqual(
    hub.body.items.map((/** @type {{ status: string }} */ s) => s.status),
    ["live", "live", "unlocking", "unlocking", "prospect"],
  );
  const bare = hub.body.items.find(
    (/** @type {{ name: string }} */ s) => s.name === "Panchavati Residency",
  );
  assert.deepEqual(bare.items, []);
  assert.equal(
    (await call("GET", `/v1/hubs/${KHARGHAR_HUB_ID}/delivery-days`, { role: "customer" })).status,
    403,
  );
  assert.deepEqual(
    hub.body.items
      .find(
        (/** @type {{ society_id: string }} */ s) => s.society_id === SOCIETY_ID["krishna-kunj"],
      )
      .items.map((/** @type {{ weekday: number }} */ d) => d.weekday),
    [0, 3],
  );
});
