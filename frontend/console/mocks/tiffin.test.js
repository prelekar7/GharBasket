// The tiffin-menu mocks keep M2's menu statuses (draft, approved, withdrawn); the paths are a proposal.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { tiffinHandlers } from "./tiffin.js";

let keys = 0;
/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method === "POST") headers["idempotency-key"] = `t${++keys}`;
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

test("menus carry the table's columns and no per-dish kitchen pay", async () => {
  const { body } = await call("GET", LIST);
  const items = /** @type {any[]} */ (body.items);
  assert.ok(items.length >= 4);
  for (const m of items) {
    assert.ok(m.partner_id && m.date && ["lunch", "dinner"].includes(m.meal));
    assert.ok(["draft", "approved", "withdrawn"].includes(m.status));
    assert.equal("kitchen_pay_paise" in m, false);
  }
  assert.equal((await call("GET", LIST, { role: "franchise_owner" })).status, 403);
});

test("a hub manager approves a draft, sends one back with a note, and reopens", async () => {
  const drafts = /** @type {any[]} */ ((await call("GET", `${LIST}?status=draft`)).body.items);
  const [first, second] = drafts;
  const approved = await call("POST", `/v1/tiffin-menus/${first.id}/approve`, { body: {} });
  assert.equal(approved.body.status, "approved");
  assert.equal(
    (await call("POST", `/v1/tiffin-menus/${first.id}/approve`, { body: {} })).body.code,
    "tiffin.not_waiting",
  );
  const noNote = await call("POST", `/v1/tiffin-menus/${second.id}/send-back`, {
    body: { note: " " },
  });
  assert.equal(noNote.status, 422);
  assert.deepEqual(noNote.body.errors[0].loc, ["body", "note"]);
  const back = await call("POST", `/v1/tiffin-menus/${second.id}/send-back`, {
    body: { note: "Less oil" },
  });
  assert.deepEqual([back.body.status, back.body.note], ["withdrawn", "Less oil"]);
  assert.equal(
    (await call("POST", `/v1/tiffin-menus/${second.id}/reopen`, { body: {} })).body.status,
    "draft",
  );
});
