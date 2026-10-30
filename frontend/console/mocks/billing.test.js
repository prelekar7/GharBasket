import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { billingHandlers } from "./billing.js";

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
