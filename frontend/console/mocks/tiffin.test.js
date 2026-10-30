// The tiffin-menu review mocks follow M2 §3.4: TiffinMenuReview items, the menu statuses, and the
// catalog.menu_* refusals.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { tiffinHandlers } from "./tiffin.js";

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await getResponse(
    tiffinHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: /** @type {any} */ (await response.json()) };
}

const LIST = `/v1/hubs/${KHARGHAR_HUB_ID}/tiffin-menus`;
const waiting = async () =>
  /** @type {any[]} */ ((await call("GET", `${LIST}?status=submitted`)).body.items);

test("the list has every status, waiting first, in the TiffinMenuReview shape", async () => {
  const { body } = await call("GET", LIST);
  const items = /** @type {any[]} */ (body.items);
  assert.deepEqual([...new Set(items.map((m) => m.status))].sort(), [
    "approved",
    "sent_back",
    "submitted",
  ]);
  assert.equal(items[0].status, "submitted");
  for (const m of items) {
    assert.deepEqual(Object.keys(m).sort(), [
      "contains",
      "date",
      "decided_at",
      "decided_by",
      "dishes",
      "hub_id",
      "id",
      "kitchen",
      "meal",
      "note",
      "order_by",
      "price_paise",
      "product",
      "rules",
      "status",
      "submitted_at",
      "version",
    ]);
    assert.ok(["lunch", "dinner"].includes(m.meal));
    assert.ok(m.kitchen.partner_id && m.product.name && m.dishes.length > 0);
  }
  assert.equal((await call("GET", `${LIST}?from=2026-10-09&to=2026-10-01`)).status, 422);
  assert.equal((await call("GET", LIST, { role: "franchise_owner" })).status, 403);
});

test("approve, send back with a note, and reopen follow the refusals of M2 §3.4", async () => {
  const [first, second] = await waiting();
  const approved = await call("POST", `/v1/tiffin-menus/${first.id}/approve`, { body: {} });
  assert.equal(approved.body.status, "approved");
  assert.equal(approved.body.version, first.version + 1);
  assert.equal(approved.body.decided_by.name, "Vikram Desai");
  // an approved menu again: 200, unchanged
  const again = await call("POST", `/v1/tiffin-menus/${first.id}/approve`, { body: {} });
  assert.deepEqual([again.status, again.body.version], [200, approved.body.version]);
  assert.equal(
    (await call("POST", `/v1/tiffin-menus/${first.id}/send-back`, { body: { note: "x" } })).body
      .code,
    "catalog.menu_not_submitted",
  );
  const noNote = await call("POST", `/v1/tiffin-menus/${second.id}/send-back`, {
    body: { note: " " },
  });
  assert.equal(noNote.status, 422);
  assert.deepEqual(noNote.body.errors[0].loc, ["body", "note"]);
  const back = await call("POST", `/v1/tiffin-menus/${second.id}/send-back`, {
    body: { note: "Less oil" },
  });
  assert.deepEqual([back.body.status, back.body.note], ["sent_back", "Less oil"]);
  const reopened = await call("POST", `/v1/tiffin-menus/${second.id}/reopen`, { body: {} });
  assert.deepEqual(
    [reopened.body.status, reopened.body.note, reopened.body.decided_by],
    ["submitted", null, null],
  );
  assert.equal(
    (await call("POST", `/v1/tiffin-menus/${second.id}/reopen`, { body: {} })).body.code,
    "catalog.menu_not_decided",
  );
  const missing = await call(
    "POST",
    "/v1/tiffin-menus/0199a000-0000-7000-8000-0000000000ff/approve",
    {
      body: {},
    },
  );
  assert.deepEqual([missing.status, missing.body.code], [404, "catalog.menu_not_found"]);
});
