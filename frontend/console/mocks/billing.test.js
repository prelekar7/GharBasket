// The refunds mock answers with the real RefundRow page of GET /v1/admin/refunds (M5 §3.8).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { billingHandlers } from "./billing.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";

/** @param {string} query @param {string} [role] */
async function list(query, role = "hub_manager") {
  const response = await getResponse(
    billingHandlers,
    new Request(`http://mock.gharbasket.local/v1/admin/refunds${query}`, {
      headers: { "x-gb-role": role },
    }),
  );
  assert.ok(response);
  return { status: response.status, body: /** @type {any} */ (await response.json()) };
}

const HUB = `hub_id=${KHARGHAR_HUB_ID}`;

test("a refund row has exactly the contract's fields", async () => {
  const { status, body } = await list(`?${HUB}`);
  assert.equal(status, 200);
  assert.deepEqual(Object.keys(body).sort(), ["items", "next_cursor"]);
  assert.deepEqual(Object.keys(body.items[0]).sort(), [
    "amount_paise",
    "created_at",
    "delivery_date",
    "flat_label",
    "id",
    "issue_id",
    "order_id",
    "order_kind",
    "product_id",
    "reason",
    "refund_id",
    "society",
    "status",
    "to",
  ]);
  assert.deepEqual(Object.keys(body.items[0].society).sort(), ["id", "name"]);
  assert.ok(body.items.every((/** @type {any} */ r) => Number.isInteger(r.amount_paise)));
});

test("pages follow next_cursor to the end, newest first, without repeats", async () => {
  const first = await list(`?${HUB}&limit=25`);
  assert.equal(first.body.items.length, 25);
  assert.ok(first.body.next_cursor);
  /** @type {any[]} */
  const all = [...first.body.items];
  let cursor = first.body.next_cursor;
  while (cursor) {
    const page = await list(`?${HUB}&limit=25&cursor=${cursor}`);
    all.push(...page.body.items);
    cursor = page.body.next_cursor;
  }
  assert.equal(new Set(all.map((r) => r.id)).size, all.length);
  const times = all.map((r) => r.created_at);
  assert.deepEqual(times, [...times].sort().reverse());
});

test("reason and destination narrow the list; the refusals are the contract's", async () => {
  const bank = await list(`?${HUB}&destination=source`);
  assert.ok(bank.body.items.length > 0);
  assert.ok(bank.body.items.every((/** @type {any} */ r) => r.to === "source" && r.refund_id));
  assert.equal((await list(`?${HUB}&reason=nonsense`)).body.code, "request.invalid");
  assert.equal((await list(`?${HUB}&from=2026-08-01&to=2026-10-01`)).status, 422);
  assert.equal((await list(`?${HUB}&cursor=garbage`)).body.code, "request.invalid_cursor");
  assert.equal((await list("")).body.code, "orders.hub_required");
  assert.equal(
    (await list("?hub_id=0199a000-0000-7000-8000-00000000a002")).body.code,
    "auth.forbidden",
  );
  assert.equal((await list(`?${HUB}`, "ngo_coordinator")).status, 403);
  assert.equal((await list("", "admin")).status, 200);
});
