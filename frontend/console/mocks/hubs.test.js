// The hub mocks answer with the real M6 §3.1 shapes and rules: GET /v1/hubs/{id} (ETag = version) and
// PATCH /v1/admin/hubs/{id} (admin, If-Match).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { hubadminHandlers } from "./hubadmin.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
const ADMIN = `/v1/admin/hubs/${KHARGHAR_HUB_ID}`;

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, headers?: Record<string, string> }} [options]
 */
async function call(method, path, { body, role = "admin", headers = {} } = {}) {
  const h = /** @type {Record<string, string>} */ ({ "x-gb-role": role, ...headers });
  if (body !== undefined) h["content-type"] = "application/json";
  const response = await getResponse(
    hubadminHandlers,
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

/** The current version, from the ETag. */
async function version() {
  const { etag } = await call("GET", HUB);
  return Number(etag?.replace(/"/g, ""));
}

test("GET /v1/hubs/{id} is a HubOut and the ETag is its version", async () => {
  const { status, body, etag } = await call("GET", HUB, { role: "hub_manager" });
  assert.equal(status, 200);
  assert.deepEqual(
    Object.keys(body).sort(),
    "address created_at green_saving_max_paise green_saving_share_pct id location name pre_live_box_fee_paise procurement_buffer_pct service_area slug status timings updated_at version".split(
      " ",
    ),
  );
  assert.deepEqual(Object.keys(body.timings), ["receiving", "packing", "dispatch", "surplus"]);
  assert.equal(etag, `"${body.version}"`);
  assert.equal((await call("GET", "/v1/hubs/0199a000-0000-7000-8000-00000000ffff")).status, 404);
});

test("PATCH needs an admin and If-Match", async () => {
  assert.equal((await call("PATCH", ADMIN, { role: "hub_manager", body: {} })).status, 403);
  const missing = await call("PATCH", ADMIN, { body: { status: "paused" } });
  assert.equal(missing.status, 428);
  assert.equal(missing.body.code, "request.precondition_required");
});

test("PATCH changes only the fields sent, bumps the version, and a stale If-Match is 412", async () => {
  const v = await version();
  const done = await call("PATCH", ADMIN, {
    body: { procurement_buffer_pct: 15, timings: { packing: "05:30" } },
    headers: { "if-match": `"${v}"` },
  });
  assert.equal(done.status, 200);
  assert.equal(done.body.procurement_buffer_pct, 15);
  assert.deepEqual(done.body.timings, {
    receiving: "04:00",
    packing: "05:30",
    dispatch: "06:30",
    surplus: "12:30",
  });
  assert.equal(done.body.pre_live_box_fee_paise, 3000);
  assert.equal(done.body.version, v + 1);
  assert.equal(done.etag, `"${v + 1}"`);

  const stale = await call("PATCH", ADMIN, {
    body: { status: "paused" },
    headers: { "if-match": `"${v}"` },
  });
  assert.equal(stale.status, 412);
  assert.equal(stale.body.code, "request.version_mismatch");
  assert.equal((await call("GET", HUB)).body.status, "active");
});

test("a PATCH that changes nothing keeps the version", async () => {
  const v = await version();
  const same = await call("PATCH", ADMIN, {
    body: { procurement_buffer_pct: 15 },
    headers: { "if-match": `"${v}"` },
  });
  assert.equal(same.status, 200);
  assert.equal(same.body.version, v);
});

test("PATCH checks the values: ranges, null, unknown fields and the order of the times", async () => {
  const v = await version();
  const headers = { "if-match": `"${v}"` };
  for (const body of [
    { procurement_buffer_pct: 51 },
    { pre_live_box_fee_paise: 10_001 },
    { pre_live_box_fee_paise: 12.5 },
    { status: "planned" },
    { name: "ab" },
    { address: null },
    { city: "Pune" },
  ]) {
    const res = await call("PATCH", ADMIN, { body, headers });
    assert.equal(res.status, 422, JSON.stringify(body));
    assert.equal(res.body.code, "request.invalid");
  }
  const order = await call("PATCH", ADMIN, { body: { timings: { packing: "03:00" } }, headers });
  assert.equal(order.status, 422);
  assert.equal(order.body.code, "hub_ops.timings_invalid");
  assert.equal(await version(), v);
});
