// The payout mocks answer with the real M5 §3.5 shapes and rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { recentWeeks } from "../lib/payout-weeks.js";
import { payoutsHandlers } from "./payouts.js";

/**
 * @param {string} method @param {string} path
 * @param {{ role?: string, key?: string }} [options]
 */
async function call(method, path, { role = "admin", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (key) headers["idempotency-key"] = key;
  const response = await getResponse(
    payoutsHandlers,
    new Request(`http://mock.gharbasket.local${path}`, { method, headers }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

const WEEK = recentWeeks(Date.now(), 3)[0].week;

test("the list is a page of PayoutOut rows with the partner's name and kind", async () => {
  const { status, body } = await call("GET", `/v1/admin/payouts?week=${WEEK}`, {
    role: "hub_manager",
  });
  assert.equal(status, 200);
  assert.equal(body.next_cursor, null);
  assert.equal(body.items.length, 4);
  for (const p of /** @type {any[]} */ (body.items)) {
    assert.equal(p.week, WEEK);
    assert.equal(Number.isInteger(p.amount_paise), true);
    assert.ok(p.partner.display_name && p.partner.kind);
    assert.ok(["held", "pending", "processing", "processed", "failed"].includes(p.status));
  }
  assert.equal(/** @type {any[]} */ (body.items).filter((p) => p.status === "failed").length, 1);
});

test("an older week and a status filter narrow the list", async () => {
  const older = recentWeeks(Date.now(), 3)[1].week;
  const { body } = await call("GET", `/v1/admin/payouts?week=${older}&status=processed`);
  assert.equal(body.items.length, 4);
  assert.ok(/** @type {any[]} */ (body.items).every((p) => p.week === older));
});

test("only an admin retries, a replay answers the first call, and only a held or failed payout is retried", async () => {
  const { body } = await call("GET", `/v1/admin/payouts?week=${WEEK}&status=failed`);
  const failed = body.items[0];
  const path = `/v1/admin/payouts/${failed.id}/retry`;
  assert.equal((await call("POST", path, { role: "hub_manager", key: "k0" })).status, 403);
  assert.equal((await call("POST", path)).status, 400);
  const first = await call("POST", path, { key: "k1" });
  assert.equal(first.status, 200);
  assert.equal(first.body.status, "pending");
  assert.deepEqual((await call("POST", path, { key: "k1" })).body, first.body);
  const again = await call("POST", path, { key: "k2" });
  assert.equal(again.status, 409);
  assert.equal(again.body.code, "billing.payout_not_retryable");
});
