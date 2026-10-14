// The logistics mocks answer with the real M7 shapes and rules (docs/modules/M7-logistics.md §3.1 to §3.4).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { firstEvent } from "../lib/sse.js";
import { istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { logisticsHandlers } from "./logistics.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
const TODAY = istDay(0, 0, 0).slice(0, 10);
const TOMORROW = istDay(1, 0, 0).slice(0, 10);
let keyCount = 0;

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, key?: string }} [options]
 */
async function send(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method === "POST") headers["idempotency-key"] = key ?? `k${++keyCount}`;
  const response = await getResponse(
    logisticsHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return response;
}

/** @param {Parameters<typeof send>} args */
async function call(...args) {
  const response = await send(...args);
  return { status: response.status, body: /** @type {any} */ (await response.json()), response };
}

test("vehicles list by label and a new one takes the defaults of its kind", async () => {
  const list = await call("GET", `${HUB}/vehicles`);
  const labels = /** @type {any[]} */ (list.body.items).map((v) => v.label);
  assert.deepEqual(
    labels,
    [...labels].sort((a, b) => a.localeCompare(b, "en", { numeric: true })),
  );
  assert.equal((await call("GET", `${HUB}/vehicles?active=false`)).body.items.length, 1);
  const made = await call("POST", `${HUB}/vehicles`, {
    body: { label: "TW-2", kind: "two_wheeler" },
  });
  assert.equal(made.status, 201);
  assert.deepEqual(
    [
      made.body.capacity_kg,
      made.body.capacity_crates,
      made.body.capacity_tiffins,
      made.body.active,
    ],
    [40, 0, 30, true],
  );
});

test("a vehicle needs a free label, sane limits and a rider of the hub", async () => {
  const dup = await call("POST", `${HUB}/vehicles`, { body: { label: "ev-1", kind: "cargo_3w" } });
  assert.equal(dup.status, 409);
  const bad = await call("POST", `${HUB}/vehicles`, {
    body: { label: "EV-9", kind: "cargo_3w", capacity_kg: 5, capacity_crates: 400 },
  });
  assert.equal(bad.status, 422);
  assert.deepEqual(/** @type {any[]} */ (bad.body.errors).map((e) => e.loc.at(-1)).sort(), [
    "capacity_crates",
    "capacity_kg",
  ]);
  const rider = await call("POST", `${HUB}/vehicles`, {
    body: {
      label: "EV-9",
      kind: "cargo_3w",
      default_rider_user_id: "0199b310-0000-7000-8000-0000000000ff",
    },
  });
  assert.equal(rider.body.code, "logistics.rider_invalid");
  assert.equal(
    (await send("POST", `${HUB}/vehicles`, { body: {}, role: "franchise_owner" })).status,
    403,
  );
  assert.equal(
    (await send("POST", `${HUB}/vehicles`, { body: { label: "x" }, key: "" })).status,
    400,
  );
});

test("today's route is out with stops that carry the society's position, and its live snapshot comes first", async () => {
  const routes = await call("GET", `${HUB}/routes?date=${TODAY}`);
  assert.equal(routes.body.items.length, 1);
  const [summary] = routes.body.items;
  assert.equal(summary.status, "in_progress");
  assert.equal(summary.stops_done, 1);
  const detail = await call("GET", `/v1/routes/${summary.id}`);
  assert.equal(detail.response.headers.get("etag"), `"${summary.version}"`);
  assert.equal(detail.body.stops[0].society.location.lat > 0, true);
  assert.match(detail.body.stops[0].navigate_url, /google\.com\/maps/);
  const live = await send("GET", `/v1/routes/${summary.id}/live`);
  assert.match(live.headers.get("content-type") ?? "", /text\/event-stream/);
  const snapshot = await firstEvent(
    /** @type {ReadableStream<Uint8Array>} */ (live.body),
    "snapshot",
  );
  assert.equal(snapshot.route_id, summary.id);
  assert.ok(snapshot.position.lat && snapshot.position.age_s >= 0);
  assert.equal(snapshot.stops.length, 2);
  assert.equal((await call("GET", "/v1/routes/nope")).body.code, "logistics.route_not_found");
});

test("tomorrow's plan has problems: publishing needs them accepted, then it replaces nothing and its routes publish", async () => {
  const plans = await call("GET", `${HUB}/route-plans?date=${TOMORROW}&kind=box`);
  const [plan] = plans.body.items;
  assert.equal(plan.status, "solved");
  assert.equal(plan.routes, undefined);
  assert.ok(plan.summary.problems.length > 0);
  const refused = await call("POST", `/v1/route-plans/${plan.id}/publish`, { body: {} });
  assert.equal(refused.status, 409);
  assert.equal(refused.body.code, "logistics.plan_has_problems");
  const ok = await call("POST", `/v1/route-plans/${plan.id}/publish`, {
    body: { accept_problems: true },
  });
  assert.equal(ok.body.status, "published");
  assert.ok(ok.body.published_at);
  assert.ok(/** @type {any[]} */ (ok.body.routes).every((r) => r.status === "published"));
  assert.equal(
    (await call("POST", `/v1/route-plans/${plan.id}/publish`, { body: {} })).body.code,
    "logistics.plan_not_solved",
  );
});

test("a solve is accepted at once, only one runs at a time, and polling finishes it", async () => {
  const bad = await call("POST", `${HUB}/route-plans`, {
    body: { date: "2020-01-01", kind: "box" },
  });
  assert.equal(bad.body.code, "logistics.date_out_of_range");
  const started = await call("POST", `${HUB}/route-plans`, {
    body: { date: TOMORROW, kind: "box" },
  });
  assert.equal(started.status, 202);
  assert.equal(started.body.status, "queued");
  assert.equal(started.body.summary, null);
  const again = await call("POST", `${HUB}/route-plans`, { body: { date: TOMORROW, kind: "box" } });
  assert.equal(again.body.code, "logistics.solve_running");
  assert.equal((await call("GET", `/v1/route-plans/${started.body.id}`)).body.status, "solving");
  const done = await call("GET", `/v1/route-plans/${started.body.id}`);
  assert.equal(done.body.status, "solved");
  assert.ok(done.body.source && done.body.summary.km > 0);
  assert.equal(
    (
      await send("POST", `${HUB}/route-plans`, {
        body: { date: TOMORROW, kind: "box" },
        role: "admin",
      })
    ).status,
    403,
  );
});

test("deliveries are a page of Delivery rows, and a hub manager settles an order that waits", async () => {
  const list = await call("GET", `${HUB}/deliveries?date=${TODAY}`);
  assert.equal(list.body.next_cursor, null);
  const items = /** @type {any[]} */ (list.body.items);
  assert.ok(items.length > 50);
  assert.ok(items.every((d) => d.order_id && d.society_id && !("flat" in d) && !("name" in d)));
  const triage = items.find((d) => d.status === "triage");
  const delivered = items.find((d) => d.status === "delivered");
  const url = (/** @type {string} */ id) => `/v1/deliveries/${id}/resolve`;
  assert.equal(
    (await call("POST", url(delivered.order_id), { body: { outcome: "refund", note: "x" } })).body
      .code,
    "logistics.not_undelivered",
  );
  const noNote = await call("POST", url(triage.order_id), { body: { outcome: "refund" } });
  assert.equal(noNote.status, 422);
  assert.equal(noNote.body.errors[0].loc.at(-1), "note");
  const tooMuch = await call("POST", url(triage.order_id), {
    body: { outcome: "refund", refund_paise: 10_000_000, note: "x" },
  });
  assert.equal(tooMuch.body.code, "logistics.refund_invalid");
  assert.ok(tooMuch.body.max_paise > 0);
  const redeliver = await call("POST", url(triage.order_id), {
    body: { outcome: "redeliver", note: "family agreed" },
  });
  assert.deepEqual(
    [redeliver.body.status, redeliver.body.attempt, redeliver.body.by],
    ["redelivery", 2, "hub"],
  );
  assert.equal(
    (await call("POST", url(triage.order_id), { body: { outcome: "refund", note: "x" } })).body
      .code,
    "logistics.not_undelivered",
  );
  assert.equal(
    (await call("GET", `${HUB}/deliveries?date=${TODAY}&status=redelivery`)).body.items.length,
    1,
  );
});
