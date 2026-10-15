// The console's packing mocks answer with the real M6 §3.2 shapes (PackingTaskOut, PackingSummary, BuildOut).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { addDays, mockToday } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { packingHandlers } from "./packing.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
const today = mockToday();
let n = 0;

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (method === "POST") headers["idempotency-key"] = `k-pack-${++n}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await getResponse(
    packingHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

test("the queue is a cursor page of PackingTaskOut, by seq, and filters by status and society", async () => {
  const first = await call("GET", `${HUB}/packing-tasks?limit=50`);
  assert.equal(first.status, 200);
  assert.equal(first.body.items.length, 50);
  assert.equal(first.body.next_cursor, "50");
  assert.deepEqual(
    Object.keys(first.body.items[0]).sort(),
    "bag_qr bags crate_no delivery_date flat_label hub_id id lines_done lines_total order_id order_kind packed_at packer route seq slot society started_at status version".split(
      " ",
    ),
  );
  assert.match(first.body.items[0].bag_qr, /^GB1:[0-9a-f]{32}$/);
  assert.equal(first.body.items[0].route, null);
  const rest = await call("GET", `${HUB}/packing-tasks?limit=50&cursor=50`);
  assert.equal(rest.body.next_cursor, null);
  const seqs = [...first.body.items, ...rest.body.items].map((/** @type {any} */ t) => t.seq);
  assert.deepEqual(
    seqs,
    [...seqs].sort((a, b) => a - b),
  );

  const packed = await call("GET", `${HUB}/packing-tasks?status=packed&limit=200`);
  assert.ok(
    packed.body.items.every(
      (/** @type {any} */ t) => t.status === "packed" && t.crate_no >= 1 && t.packed_at,
    ),
  );
  const picking = await call("GET", `${HUB}/packing-tasks?status=picking`);
  assert.ok(picking.body.items.every((/** @type {any} */ t) => t.packer && t.started_at));
  const society = first.body.items[0].society.id;
  const own = await call("GET", `${HUB}/packing-tasks?society_id=${society}&limit=200`);
  assert.ok(own.body.items.every((/** @type {any} */ t) => t.society.id === society));
  assert.equal((await call("GET", `${HUB}/packing-tasks?status=done`)).status, 422);
  assert.equal(
    (await call("GET", `${HUB}/packing-tasks?date=${addDays(today, 3)}`)).body.code,
    "hub_ops.date_out_of_range",
  );
  assert.deepEqual(
    (await call("GET", `${HUB}/packing-tasks?date=${addDays(today, 1)}`)).body.items,
    [],
  );
});

test("the summary matches the queue and counts crates per society", async () => {
  const { body: sum } = await call("GET", `${HUB}/packing-summary`);
  const { body: all } = await call("GET", `${HUB}/packing-tasks?limit=200`);
  const count = (/** @type {string} */ s) =>
    all.items.filter((/** @type {any} */ t) => t.status === s).length;
  assert.deepEqual(
    [sum.queued, sum.picking, sum.packed],
    [count("queued"), count("picking"), count("packed")],
  );
  assert.equal(sum.date, today);
  for (const s of sum.societies) {
    const own = all.items.filter((/** @type {any} */ t) => t.society.id === s.society_id);
    assert.equal(s.bags, own.length);
    assert.equal(s.packed_bags, own.filter((/** @type {any} */ t) => t.status === "packed").length);
  }
  assert.ok(sum.societies.some((/** @type {any} */ s) => s.crates >= 1));
  assert.deepEqual(
    (await call("GET", `${HUB}/packing-summary?date=${addDays(today, 1)}`)).body.societies,
    [],
  );
});

test("building adds the late orders once, for a hub manager, with a key and a valid day", async () => {
  const first = await call("POST", `${HUB}/packing-tasks/build`, { body: { date: today } });
  assert.equal(first.status, 200);
  assert.deepEqual(Object.keys(first.body).sort(), ["created", "existing"]);
  assert.equal(first.body.created, 2);
  const again = await call("POST", `${HUB}/packing-tasks/build`, { body: { date: today } });
  assert.deepEqual([again.body.created, again.body.existing], [0, first.body.existing + 2]);
  assert.deepEqual(
    (await call("POST", `${HUB}/packing-tasks/build`, { body: { date: addDays(today, 1) } })).body,
    { created: 0, existing: 0 },
  );
  assert.equal(
    (await call("POST", `${HUB}/packing-tasks/build`, { body: { date: addDays(today, 5) } })).body
      .code,
    "hub_ops.date_out_of_range",
  );
  assert.equal((await call("POST", `${HUB}/packing-tasks/build`, { body: {} })).status, 422);
  assert.equal(
    (
      await call("POST", `${HUB}/packing-tasks/build`, {
        role: "franchise_owner",
        body: { date: today },
      })
    ).status,
    403,
  );
  const noKey = await getResponse(
    packingHandlers,
    new Request(`http://mock.gharbasket.local${HUB}/packing-tasks/build`, {
      method: "POST",
      body: JSON.stringify({ date: today }),
    }),
  );
  assert.ok(noKey);
  assert.equal((await noKey.json()).code, "request.idempotency_key_required");
  const { body: sum } = await call("GET", `${HUB}/packing-summary`);
  assert.equal(sum.queued + sum.picking + sum.packed, first.body.existing + 2);
});

test("release frees a started task and refuses queued and packed ones", async () => {
  const find = async (/** @type {string} */ status) =>
    (await call("GET", `${HUB}/packing-tasks?status=${status}&limit=200`)).body.items[0];
  const started = await find("picking");
  const released = await call("POST", `/v1/packing-tasks/${started.id}/release`);
  assert.equal(released.status, 200);
  assert.equal(released.body.packer, null);
  assert.equal(released.body.status, "picking");
  assert.equal(released.body.version, started.version + 1);
  assert.ok(
    Array.isArray(released.body.lines) && released.body.lines.length === started.lines_total,
  );
  assert.deepEqual(
    Object.keys(released.body.lines[0]).sort(),
    "done name picked_qty product_id qty short_qty slug step substitute".split(" "),
  );
  // releasing again changes nothing
  assert.equal(
    (await call("POST", `/v1/packing-tasks/${started.id}/release`)).body.version,
    released.body.version,
  );

  assert.equal(
    (await call("POST", `/v1/packing-tasks/${(await find("queued")).id}/release`)).body.code,
    "hub_ops.task_not_started",
  );
  assert.equal(
    (await call("POST", `/v1/packing-tasks/${(await find("packed")).id}/release`)).body.code,
    "hub_ops.task_packed",
  );
  assert.equal(
    (await call("POST", "/v1/packing-tasks/nope/release")).body.code,
    "hub_ops.task_not_found",
  );
  assert.equal((await call("GET", `/v1/packing-tasks/${started.id}`)).body.id, started.id);
});
