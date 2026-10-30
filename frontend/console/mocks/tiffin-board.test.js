// The tiffin board mocks follow M8 §3.4: the board's shape, a round, and manual assignment with its refusals.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { tiffinBoardHandlers } from "./tiffin-board.js";

let keys = 0;
/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string, key?: string | null }} [options] */
async function call(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method === "POST" && key !== null) headers["idempotency-key"] = key ?? `k${++keys}`;
  const response = await getResponse(
    tiffinBoardHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: /** @type {any} */ (await response.json()) };
}

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
const iso = (/** @type {number} */ days) =>
  new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

test("the board has the day's meals, assignments and runs; staff roles only", async () => {
  const { status, body } = await call("GET", `${HUB}/tiffin-board?date=${iso(1)}`);
  assert.equal(status, 200);
  assert.equal(body.date, iso(1));
  assert.deepEqual(
    body.meals.map((/** @type {any} */ m) => m.meal),
    ["lunch", "dinner"],
  );
  const lunch = body.meals[0];
  assert.equal(lunch.assigned, 1);
  assert.equal(lunch.unassigned.length, 2);
  assert.deepEqual(
    lunch.unassigned.map((/** @type {any} */ u) => u.reason),
    ["no_skills", "out_of_reach"],
  );
  assert.equal(body.assignments[0].partner.display_name, "Lata tai");
  assert.equal(body.runs[0].meal, "lunch");
  assert.equal(
    (await call("GET", `${HUB}/tiffin-board?date=${iso(1)}`, { role: "ngo_coordinator" })).status,
    403,
  );
  assert.equal((await call("GET", `${HUB}/tiffin-board?date=soon`)).status, 422);
});

test("a round offers the waiting dinner tiffins and keeps the other reasons; the key is required and replays", async () => {
  const date = iso(2);
  assert.equal(
    (await call("POST", `${HUB}/tiffin-assignments/run`, { body: { date }, key: null })).body.code,
    "request.idempotency_key_required",
  );
  assert.equal(
    (await call("POST", `${HUB}/tiffin-assignments/run`, { body: { date: "x" } })).status,
    422,
  );
  const first = await call("POST", `${HUB}/tiffin-assignments/run`, {
    body: { date },
    key: "same",
  });
  assert.equal(first.status, 200);
  assert.equal(first.body.offers, 1);
  assert.equal(first.body.unassigned, 2);
  assert.deepEqual(
    (await call("POST", `${HUB}/tiffin-assignments/run`, { body: { date }, key: "same" })).body,
    first.body,
  );
  const board = (await call("GET", `${HUB}/tiffin-board?date=${date}`)).body;
  assert.equal(
    board.meals.find((/** @type {any} */ m) => m.meal === "dinner").unassigned.length,
    0,
  );
  assert.equal(board.runs.length, 2);
});

test("manual assignment is accepted at once, flags over-capacity, and refuses what the contract refuses", async () => {
  const date = iso(3);
  const board = (await call("GET", `${HUB}/tiffin-board?date=${date}`)).body;
  const [a, b] = board.meals[0].unassigned.map((/** @type {any} */ u) => u.order_id);
  const dinner = board.meals[1].unassigned.map((/** @type {any} */ u) => u.order_id);
  const url = `${HUB}/tiffin-assignments/manual`;
  const mixed = await call("POST", url, {
    body: { date, order_ids: [a, dinner[0]], partner_id: PARTNER_ID.lata },
  });
  assert.equal(mixed.body.code, "partners.order_not_assignable");
  assert.equal(
    (await call("POST", url, { body: { date, order_ids: [a], partner_id: PARTNER_ID.arjun } })).body
      .code,
    "partners.kitchen_not_found",
  );
  assert.equal(
    (await call("POST", url, { body: { date, order_ids: [], partner_id: PARTNER_ID.lata } }))
      .status,
    422,
  );
  const ok = await call("POST", url, {
    body: { date, order_ids: [a, b], partner_id: PARTNER_ID.lata, note: "phoned" },
  });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.status, "accepted");
  assert.equal(ok.body.source, "manual");
  assert.equal(ok.body.portions, 3);
  assert.equal(ok.body.over_capacity, true);
  const again = await call("POST", url, {
    body: { date, order_ids: [a], partner_id: PARTNER_ID.lata },
  });
  assert.equal(again.body.code, "partners.order_not_assignable");
  const after = (await call("GET", `${HUB}/tiffin-board?date=${date}`)).body;
  assert.equal(after.meals[0].unassigned.length, 0);
  assert.equal(after.assignments.length, 2);
});

test("manual assignment for a day that has passed is closed", async () => {
  const date = iso(-2);
  const board = (await call("GET", `${HUB}/tiffin-board?date=${date}`)).body;
  const order = board.meals[0].unassigned[0].order_id;
  const r = await call("POST", `${HUB}/tiffin-assignments/manual`, {
    body: { date, order_ids: [order], partner_id: PARTNER_ID.lata },
  });
  assert.equal(r.status, 409);
  assert.equal(r.body.code, "partners.assignment_closed");
  assert.equal(
    (
      await call("POST", `${HUB}/tiffin-assignments/run`, {
        body: { date },
        role: "franchise_owner",
      })
    ).status,
    403,
  );
});
