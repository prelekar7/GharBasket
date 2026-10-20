// Role grants and societies: the mocks answer with the real contract's fields and rules (M1, M9).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { adminHandlers } from "./admin.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, key?: string }} [options]
 */
async function call(method, path, { body, role = "admin", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (key) headers["idempotency-key"] = key;
  const response = await getResponse(
    adminHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: response.status === 204 ? null : await response.json() };
}

/** @param {object} o */
const keys = (o) => Object.keys(o).sort();
const grant = (/** @type {unknown} */ body) =>
  call("POST", "/v1/admin/user-roles", { body, key: crypto.randomUUID() });

test("granting a role takes phone_e164 and answers RoleGrantOut, which has no id", async () => {
  const old = await grant({ user_id: "x", role: "admin", scope_type: null, scope_id: null });
  assert.equal(old.status, 422);
  assert.deepEqual(
    old.body.errors.map((/** @type {{ loc: string[] }} */ e) => e.loc.at(-1)).sort(),
    ["phone_e164", "scope_type", "user_id"],
  );

  const ok = await grant({
    phone_e164: "+919800000001",
    role: "hub_manager",
    scope_id: KHARGHAR_HUB_ID,
  });
  assert.equal(ok.status, 201);
  assert.deepEqual(keys(ok.body), ["role", "scope_id", "scope_type", "status", "user_id"]);
  assert.equal(ok.body.scope_type, "hub");
  assert.equal(ok.body.status, "verified");
  assert.equal(
    (await grant({ phone_e164: "+919800000001", role: "hub_manager", scope_id: KHARGHAR_HUB_ID }))
      .body.code,
    "identity.role_exists",
  );
});

test("a role grant follows the real scope rules and error codes", async () => {
  assert.equal(
    (await grant({ phone_e164: "+919800000001", role: "franchise_owner" })).body.code,
    "identity.invalid_scope",
  );
  assert.equal(
    (await grant({ phone_e164: "+919800000001", role: "admin", scope_id: KHARGHAR_HUB_ID })).body
      .code,
    "identity.invalid_scope",
  );
  assert.equal(
    (await grant({ phone_e164: "+919800000099", role: "admin" })).body.code,
    "identity.user_not_found",
  );
  assert.equal((await grant({ phone_e164: "12345", role: "admin" })).body.code, "request.invalid");
  assert.equal(
    (await grant({ phone_e164: "+919800000001", role: "janitor" })).body.code,
    "request.invalid",
  );
  assert.equal(
    (
      await call("POST", "/v1/admin/user-roles", {
        role: "hub_manager",
        body: { phone_e164: "+919800000001", role: "admin" },
        key: "k",
      })
    ).status,
    403,
  );
});

test("GET /v1/societies is SocietySummary, A to Z; the detail has towers with label; unlock has the threshold", async () => {
  const list = await call("GET", "/v1/societies");
  assert.deepEqual(keys(list.body), ["items", "next_cursor"]);
  assert.deepEqual(keys(list.body.items[0]), [
    "area",
    "distance_m",
    "id",
    "name",
    "slug",
    "status",
  ]);
  const names = list.body.items.map((/** @type {{ name: string }} */ s) => s.name);
  assert.deepEqual(names, [...names].sort());
  const one = await call("GET", `/v1/societies/${SOCIETY_ID["sunrise-towers"]}`);
  assert.deepEqual(keys(one.body), [
    "area",
    "captain",
    "distance_m",
    "flats",
    "id",
    "name",
    "slug",
    "status",
    "towers",
  ]);
  assert.deepEqual(keys(one.body.towers[0]), ["id", "label"]);
  const unlock = await call("GET", `/v1/societies/${SOCIETY_ID["sunrise-towers"]}/unlock`);
  assert.deepEqual(keys(unlock.body), ["joined", "society_id", "status", "threshold"]);
});

test("the admin society list (a proposal) keeps the boundary and the hub filters", async () => {
  const all = await call(
    "GET",
    `/v1/admin/societies?hub_id=${KHARGHAR_HUB_ID}&with_unassigned=true`,
  );
  const sunrise = all.body.items.find(
    (/** @type {{ id: string }} */ s) => s.id === SOCIETY_ID["sunrise-towers"],
  );
  assert.ok(sunrise.boundary);
  assert.equal(sunrise.towers[0].label, "Wing A");
  assert.equal(
    all.body.items.some((/** @type {{ hub_id: null }} */ s) => s.hub_id === null),
    true,
  );
  assert.equal((await call("GET", "/v1/admin/societies", { role: "franchise_owner" })).status, 403);
});
