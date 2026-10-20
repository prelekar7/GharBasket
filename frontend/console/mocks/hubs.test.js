// The hubs: the mocks answer with the real contract's shapes (M6 hub_ops: HubsOut, HubOut, HubPatch + If-Match).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { hubadminHandlers } from "./hubadmin.js";
import { hubsHandlers, KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";

const handlers = [...hubsHandlers, ...hubadminHandlers];

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, headers?: Record<string, string> }} [options]
 */
async function call(method, path, { body, role = "admin", headers = {} } = {}) {
  const h = /** @type {Record<string, string>} */ ({ "x-gb-role": role, ...headers });
  if (body !== undefined) h["content-type"] = "application/json";
  const response = await getResponse(
    handlers,
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

test("GET /v1/hubs is HubsOut: an admin sees every hub, a hub manager their own", async () => {
  const admin = await call("GET", "/v1/hubs");
  assert.deepEqual(Object.keys(admin.body), ["items"]);
  assert.deepEqual(Object.keys(admin.body.items[0]).sort(), ["id", "name", "slug", "status"]);
  assert.deepEqual(
    admin.body.items.map((/** @type {{ id: string }} */ h) => h.id),
    [KHARGHAR_HUB_ID, TALOJA_HUB_ID],
  );
  const manager = await call("GET", "/v1/hubs", { role: "hub_manager" });
  assert.deepEqual(
    manager.body.items.map((/** @type {{ id: string }} */ h) => h.id),
    [KHARGHAR_HUB_ID],
  );
});

test("an NGO coordinator and a catalog editor do not hold hubs:read", async () => {
  for (const role of ["ngo_coordinator", "catalog_editor"]) {
    assert.equal((await call("GET", "/v1/hubs", { role })).status, 403);
    assert.equal((await call("GET", `/v1/hubs/${KHARGHAR_HUB_ID}`, { role })).status, 403);
  }
});

test("GET /v1/hubs/{id} is HubOut with an ETag; an unknown hub is hub_ops.hub_not_found", async () => {
  const hub = await call("GET", `/v1/hubs/${KHARGHAR_HUB_ID}`);
  assert.equal(hub.status, 200);
  assert.deepEqual(Object.keys(hub.body).sort(), [
    "address",
    "created_at",
    "id",
    "location",
    "name",
    "pre_live_box_fee_paise",
    "procurement_buffer_pct",
    "service_area",
    "slug",
    "status",
    "timings",
    "updated_at",
    "version",
  ]);
  assert.equal(hub.etag, `"${hub.body.version}"`);
  const missing = await call("GET", "/v1/hubs/0199a000-0000-7000-8000-0000000000ff");
  assert.equal(missing.status, 404);
  assert.equal(missing.body.code, "hub_ops.hub_not_found");
  // A hub manager does not read the second hub.
  assert.equal(
    (await call("GET", `/v1/hubs/${TALOJA_HUB_ID}`, { role: "hub_manager" })).status,
    403,
  );
});

test("PATCH /v1/admin/hubs/{id} needs If-Match and moves the version", async () => {
  const path = `/v1/admin/hubs/${TALOJA_HUB_ID}`;
  const area = {
    type: "Polygon",
    coordinates: [
      [
        [73.0, 19.0],
        [73.1, 19.0],
        [73.1, 19.1],
        [73.0, 19.0],
      ],
    ],
  };
  const before = (await call("GET", `/v1/hubs/${TALOJA_HUB_ID}`)).body.version;
  assert.equal((await call("PATCH", path, { body: { service_area: area } })).status, 428);
  const stale = await call("PATCH", path, {
    body: { service_area: area },
    headers: { "if-match": `"${before + 7}"` },
  });
  assert.equal(stale.status, 412);
  const saved = await call("PATCH", path, {
    body: { service_area: area },
    headers: { "if-match": `"${before}"` },
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.version, before + 1);
  assert.deepEqual((await call("GET", `/v1/hubs/${TALOJA_HUB_ID}`)).body.service_area, area);
  assert.equal(
    (
      await call("PATCH", path, {
        body: { service_area: null },
        headers: { "if-match": `"${before + 1}"` },
      })
    ).status,
    200,
  );
  assert.equal((await call("PATCH", path, { body: {}, role: "hub_manager" })).status, 403);
});
