// The issue-triage mocks keep M4 §3.6's shapes and refusals; the timeline is the JSON form of §3.3.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";
import { orderIssuesHandlers } from "./orderIssues.js";
import { mockUuid } from "./util.js";

let keys = 0;
/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string, key?: string }} [options] */
async function call(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method === "POST") headers["idempotency-key"] = key ?? `k${++keys}`;
  const response = await getResponse(
    orderIssuesHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: /** @type {any} */ (await response.json()) };
}

const QUEUE = `/v1/admin/order-issues?hub_id=${KHARGHAR_HUB_ID}`;
const TOMATO = mockUuid("b900", 1);
const LATE = mockUuid("b900", 2);

test("the queue is oldest first, per hub, and carries the triage fields", async () => {
  const { status, body } = await call("GET", QUEUE);
  assert.equal(status, 200);
  const created = body.items.map((/** @type {any} */ i) => i.created_at);
  assert.deepEqual(created, [...created].sort());
  const first = body.items[0];
  for (const f of ["max_credit_paise", "creditable_paise", "order_total_paise", "callback_due_at"])
    assert.ok(f in first, f);
  assert.equal(first.max_credit_paise, 4000);
  assert.ok(body.items.every((/** @type {any} */ i) => i.hub_id === KHARGHAR_HUB_ID));
});

test("hub staff need hub_id and their own hub; an admin may leave it out", async () => {
  assert.equal((await call("GET", "/v1/admin/order-issues")).body.code, "orders.hub_required");
  const other = await call("GET", `/v1/admin/order-issues?hub_id=${TALOJA_HUB_ID}`);
  assert.equal(other.status, 403);
  assert.equal((await call("GET", "/v1/admin/order-issues", { role: "admin" })).status, 200);
  assert.equal((await call("GET", QUEUE, { role: "franchise_owner" })).status, 403);
});

test("the detail adds photo links with an expiry and the customer's contact", async () => {
  const { body } = await call("GET", `/v1/admin/order-issues/${TOMATO}`);
  assert.equal(body.photos.length, body.photo_count);
  assert.ok(Date.parse(body.photos[0].expires_at) > Date.now());
  assert.match(body.contact.phone_e164, /^\+91/);
  assert.equal((await call("GET", `/v1/admin/order-issues/${mockUuid("b900", 4)}`)).status, 403);
  assert.equal(
    (await call("GET", `/v1/admin/order-issues/${mockUuid("b900", 99)}`)).body.code,
    "orders.issue_not_found",
  );
});

test("resolve refuses a credit above the maximum, with max_paise, and needs a note to reject", async () => {
  const over = await call("POST", `/v1/admin/order-issues/${TOMATO}/resolve`, {
    body: { outcome: "credit", credit_paise: 4001 },
  });
  assert.equal(over.status, 422);
  assert.equal(over.body.code, "orders.issue_credit_invalid");
  assert.equal(over.body.max_paise, 4000);
  const bare = await call("POST", `/v1/admin/order-issues/${TOMATO}/resolve`, {
    body: { outcome: "reject" },
  });
  assert.equal(bare.status, 422);
  assert.equal(bare.body.errors[0].loc.at(-1), "note");
  const noKey = await getResponse(
    orderIssuesHandlers,
    new Request(`http://mock.gharbasket.local/v1/admin/order-issues/${TOMATO}/resolve`, {
      method: "POST",
      headers: { "x-gb-role": "admin", "content-type": "application/json" },
      body: JSON.stringify({ outcome: "reject", note: "x" }),
    }),
  );
  assert.equal(noKey?.status, 400);
});

test("a full wallet keeps the issue open; a later credit works once and the same key replays", async () => {
  const path = `/v1/admin/order-issues/${LATE}/resolve`;
  const full = await call("POST", path, { body: { outcome: "credit", credit_paise: 20000 } });
  assert.equal(full.body.code, "billing.wallet_limit");
  assert.equal(full.body.room_paise, 1500);
  const ok = await call("POST", path, {
    key: "same",
    body: { outcome: "credit", credit_paise: 1500 },
  });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.status, "credited");
  assert.equal(ok.body.resolution.by, "hub");
  const replay = await call("POST", path, {
    key: "same",
    body: { outcome: "credit", credit_paise: 1500 },
  });
  assert.equal(replay.status, 200);
  const changed = await call("POST", path, {
    key: "same",
    body: { outcome: "credit", credit_paise: 1400 },
  });
  assert.equal(changed.body.code, "request.idempotency_mismatch");
  const again = await call("POST", path, { body: { outcome: "reject", note: "late" } });
  assert.equal(again.body.code, "orders.issue_not_open");
});

test("the order timeline is OrderEventsOut: oldest first, actor kinds only", async () => {
  const issue = (await call("GET", `/v1/admin/order-issues/${TOMATO}`)).body;
  const { status, body } = await call("GET", `/v1/orders/${issue.order_id}/events`);
  assert.equal(status, 200);
  assert.equal(body.status, "delivered");
  const at = body.items.map((/** @type {any} */ e) => e.at);
  assert.deepEqual(at, [...at].sort());
  assert.ok(body.items.every((/** @type {any} */ e) => e.id && e.status && e.actor));
  assert.equal((await call("GET", `/v1/orders/${mockUuid("b910", 77)}/events`)).status, 404);
});
