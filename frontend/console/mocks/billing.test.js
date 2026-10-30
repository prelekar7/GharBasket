// The billing mocks: reconciliation (resolve with a note) and the real RefundRow page of
// GET /v1/admin/refunds (M5 §3.8).
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { billingHandlers } from "./billing.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";

/**
 * @param {string} method @param {string} path
 * @param {{ role?: string | null, body?: unknown }} [options]
 */
async function call(method, path, { role = "admin", body } = {}) {
  const headers = /** @type {Record<string, string>} */ ({});
  if (role) headers["x-gb-role"] = role;
  return /** @type {Response} */ (
    await getResponse(
      billingHandlers,
      new Request(`http://mock.gharbasket.local/v1/${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    )
  );
}

describe("reconciliation mocks", () => {
  test("the only write is POST …/resolve with a note: credit, investigate and check have no handler", async () => {
    for (const path of [
      "admin/reconciliation/rec-1/credit",
      "admin/reconciliation/rec-1/investigate",
      "admin/reconciliation/check",
    ])
      assert.equal(await call("POST", path, { body: {} }), /** @type {any} */ (undefined), path);
  });

  test("resolve needs an admin and a note of 1 to 500 characters", async () => {
    const path = "admin/reconciliation/rec-1/resolve";
    assert.equal(
      (await call("POST", path, { role: "hub_manager", body: { note: "ok" } })).status,
      403,
    );
    for (const note of [undefined, "", "x".repeat(501), 7]) {
      const r = await call("POST", path, { body: { note } });
      assert.equal(r.status, 422, String(note));
      assert.equal((await r.json()).code, "request.invalid");
    }
  });

  test("resolve closes the item: 200 with its resolution, off the list, then 409, unknown id 404", async () => {
    const before = await (await call("GET", "admin/reconciliation")).json();
    assert.equal(before.items.length, 2);
    assert.equal(before.counts.no_wallet_entry, 1);

    const r = await call("POST", "admin/reconciliation/rec-1/resolve", {
      body: { note: "Credited by hand, see adjustment 42" },
    });
    assert.equal(r.status, 200);
    const item = await r.json();
    assert.equal(item.id, "rec-1");
    assert.equal(item.status, "resolved");
    assert.equal(item.kind, "captured_not_credited");
    assert.equal(item.resolution.note, "Credited by hand, see adjustment 42");

    const after = await (await call("GET", "admin/reconciliation")).json();
    assert.deepEqual(
      after.items.map((/** @type {{ id: string }} */ i) => i.id),
      ["rec-2"],
    );
    assert.equal(after.counts.no_wallet_entry, 0);
    assert.equal(after.counts.matched, before.counts.matched);

    const again = await call("POST", "admin/reconciliation/rec-1/resolve", {
      body: { note: "again" },
    });
    assert.equal(again.status, 409);
    assert.equal((await again.json()).code, "billing.recon_not_open");
    const missing = await call("POST", "admin/reconciliation/nope/resolve", {
      body: { note: "x" },
    });
    assert.equal(missing.status, 404);
    assert.equal((await missing.json()).code, "billing.recon_not_found");
  });
});

// Refunds

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
