// The vendor mocks answer with the real M6 §3.1 shapes and rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { vendorsHandlers } from "./vendors.js";

const LIST = `/v1/hubs/${KHARGHAR_HUB_ID}/vendors`;

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, headers?: Record<string, string> }} [options]
 */
async function call(method, path, { body, role = "hub_manager", headers = {} } = {}) {
  const h = /** @type {Record<string, string>} */ ({ "x-gb-role": role, ...headers });
  if (body !== undefined) h["content-type"] = "application/json";
  const response = await getResponse(
    vendorsHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers: h,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return {
    status: response.status,
    body: await response.json(),
    etag: response.headers.get("etag"),
  };
}

/** @param {unknown} body @param {string} key */
const create = (body, key) => call("POST", LIST, { body, headers: { "idempotency-key": key } });

test("the list is { items } by name, with the real vendor fields", async () => {
  const { status, body } = await call("GET", LIST);
  assert.equal(status, 200);
  assert.deepEqual(Object.keys(body), ["items"]);
  const names = body.items.map((/** @type {{ name: string }} */ v) => v.name);
  assert.deepEqual(
    names,
    [...names].sort((a, b) => a.localeCompare(b)),
  );
  assert.deepEqual(
    Object.keys(body.items[0]).sort(),
    "active created_at hub_id id is_default kind name notes phone score version whatsapp".split(
      " ",
    ),
  );
  assert.equal(
    body.items.filter((/** @type {{ is_default: boolean }} */ v) => v.is_default).length,
    1,
  );
});

test("a vendor is created with a key, a name that is unique, and one default", async () => {
  const body = {
    name: "Kale Wholesale",
    kind: "wholesaler",
    phone: "+919800002099",
    is_default: true,
  };
  const first = await create(body, "k-vendor-1");
  assert.equal(first.status, 201);
  assert.equal(first.etag, '"1"');
  assert.equal(first.body.is_default, true);
  assert.equal(first.body.score, null);
  const replay = await create(body, "k-vendor-1");
  assert.equal(replay.body.id, first.body.id);

  const list = await call("GET", LIST);
  assert.equal(
    list.body.items.filter((/** @type {{ is_default: boolean }} */ v) => v.is_default).length,
    1,
  );

  const taken = await create({ name: "kale wholesale", kind: "farmer" }, "k-vendor-2");
  assert.equal(taken.status, 409);
  assert.equal(taken.body.code, "hub_ops.vendor_name_taken");

  const noKey = await call("POST", LIST, { body });
  assert.equal(noKey.body.code, "request.idempotency_key_required");
  const bad = await create({ name: "K", kind: "shop", phone: "98" }, "k-vendor-3");
  assert.equal(bad.status, 422);
  assert.deepEqual(
    bad.body.errors.map((/** @type {{ loc: string[] }} */ e) => e.loc.at(-1)).sort(),
    ["kind", "name", "phone"],
  );
});

test("a PATCH needs If-Match, deactivating clears the default, and nothing changed keeps the version", async () => {
  const { body: list } = await call("GET", `${LIST}?active=true`);
  const def = list.items.find((/** @type {{ is_default: boolean }} */ v) => v.is_default);
  const path = `/v1/vendors/${def.id}`;

  assert.equal((await call("PATCH", path, { body: { notes: "x" } })).status, 428);
  const stale = await call("PATCH", path, {
    body: { notes: "x" },
    headers: { "if-match": '"99"' },
  });
  assert.equal(stale.status, 412);
  assert.equal(stale.body.code, "request.version_mismatch");

  const same = await call("PATCH", path, {
    body: { name: def.name },
    headers: { "if-match": `"${def.version}"` },
  });
  assert.equal(same.body.version, def.version);

  const off = await call("PATCH", path, {
    body: { active: false },
    headers: { "if-match": `"${def.version}"` },
  });
  assert.equal(off.status, 200);
  assert.equal(off.body.active, false);
  assert.equal(off.body.is_default, false);
  assert.equal(off.body.version, def.version + 1);
  assert.equal(off.etag, `"${def.version + 1}"`);

  const nulled = await call("PATCH", path, {
    body: { name: null },
    headers: { "if-match": `"${def.version + 1}"` },
  });
  assert.equal(nulled.status, 422);
  assert.equal(
    (await call("PATCH", "/v1/vendors/nope", { body: {}, headers: { "if-match": '"1"' } })).body
      .code,
    "hub_ops.vendor_not_found",
  );
});

test("hub staff of another hub and non-staff are refused", async () => {
  assert.equal(
    (await call("GET", "/v1/hubs/00000000-0000-0000-0000-000000000000/vendors")).body.code,
    "hub_ops.hub_not_found",
  );
  const owner = await create({ name: "Owner Try", kind: "farmer" }, "k-vendor-9");
  assert.equal(owner.status, 201);
  const forbidden = await call("POST", LIST, {
    role: "franchise_owner",
    body: { name: "Another", kind: "farmer" },
    headers: { "idempotency-key": "k-vendor-10" },
  });
  assert.equal(forbidden.status, 403);
  assert.equal(forbidden.body.code, "auth.forbidden");
  assert.equal((await call("GET", LIST, { role: "franchise_owner" })).status, 200);
});
