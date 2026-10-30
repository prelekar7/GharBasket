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

const ADMIN_SOCIETY_KEYS = [
  "area",
  "boundary",
  "created_at",
  "flats",
  "households",
  "hub",
  "id",
  "live_at",
  "name",
  "slug",
  "status",
  "threshold_source",
  "towers",
  "unlock_threshold",
  "unlock_threshold_pinned",
  "updated_at",
];
const RANK = /** @type {Record<string, number>} */ ({
  live: 0,
  unlocking: 1,
  prospect: 2,
  paused: 3,
});
const TALOJA_HUB_ID = "0199a000-0000-7000-8000-00000000a002";
const create = (/** @type {unknown} */ body, role = "admin") =>
  call("POST", "/v1/admin/societies", { body, role, key: crypto.randomUUID() });
/** @param {{ body: any }} r */
const fieldsOf = (r) => r.body.errors.map((/** @type {{ loc: string[] }} */ e) => e.loc.at(-1));
/** @param {{ hub: { id: string } | null }[]} items */
const hubless = (items) => items.some((s) => s.hub === null);

test("GET /v1/admin/societies is Page[AdminSociety]: hub, households, threshold source, no captain or point", async () => {
  const all = await call("GET", "/v1/admin/societies");
  assert.deepEqual(keys(all.body), ["items", "next_cursor"]);
  assert.deepEqual(keys(all.body.items[0]), ADMIN_SOCIETY_KEYS);
  const sunrise = all.body.items.find(
    (/** @type {{ id: string }} */ s) => s.id === SOCIETY_ID["sunrise-towers"],
  );
  assert.deepEqual(sunrise.hub, { id: KHARGHAR_HUB_ID, name: "Kharghar hub" });
  assert.equal(sunrise.households, 46);
  assert.ok(sunrise.boundary);
  assert.equal(sunrise.towers[0].label, "Wing A");
  // live, unlocking, prospect, paused, then by name; an admin sees the hub-less one too
  const order = all.body.items.map((/** @type {{ status: string }} */ s) => RANK[s.status]);
  assert.deepEqual(
    order,
    [...order].sort((a, b) => a - b),
  );
  assert.equal(hubless(all.body.items), true);
  assert.equal((await call("GET", "/v1/admin/societies", { role: "franchise_owner" })).status, 403);
});

test("the list is scoped by hub: a hub manager never sees a hub-less society, and another hub is 403", async () => {
  const mine = await call("GET", "/v1/admin/societies", { role: "hub_manager" });
  assert.equal(
    mine.body.items.every(
      (/** @type {{ hub: { id: string } | null }} */ s) => s.hub?.id === KHARGHAR_HUB_ID,
    ),
    true,
  );
  const other = await call("GET", `/v1/admin/societies?hub_id=${TALOJA_HUB_ID}`, {
    role: "hub_manager",
  });
  assert.equal(other.status, 403);
  assert.equal(other.body.code, "auth.forbidden");
  const hubOnly = `/v1/admin/societies?hub_id=${KHARGHAR_HUB_ID}`;
  assert.equal(hubless((await call("GET", hubOnly)).body.items), false);
  // with_unassigned adds the hub-less ones for an admin and is ignored for hub staff
  assert.equal(hubless((await call("GET", `${hubOnly}&with_unassigned=true`)).body.items), true);
  const staff = await call("GET", `${hubOnly}&with_unassigned=true`, { role: "hub_manager" });
  assert.equal(hubless(staff.body.items), false);
});

test("POST /v1/admin/societies takes no status: a new society is a prospect; towers get a, b, c", async () => {
  const noKey = await call("POST", "/v1/admin/societies", { body: { name: "Palm Court" } });
  assert.equal(noKey.body.code, "request.idempotency_key_required");
  const withStatus = await create({ name: "Palm Court", status: "prospect" });
  assert.equal(withStatus.status, 422);
  assert.deepEqual(fieldsOf(withStatus), ["status"]);
  assert.deepEqual(fieldsOf(await create({ name: "P" })), ["name"]);

  const made = await create({
    name: "Palm Court",
    area: "Sector 9, Kamothe",
    unlock_threshold: 30,
    flats: 120,
    towers: [{ label: "A wing" }, { id: "b", label: "B wing" }, { label: "C wing" }],
  });
  assert.equal(made.status, 201);
  assert.deepEqual(keys(made.body), ADMIN_SOCIETY_KEYS);
  assert.equal(made.body.status, "prospect");
  assert.equal(made.body.hub, null); // an admin who names no hub
  assert.equal(made.body.threshold_source, "hub");
  assert.equal(made.body.unlock_threshold, 30);
  assert.deepEqual(
    made.body.towers.map((/** @type {{ id: string }} */ t) => t.id),
    ["a", "b", "c"],
  );
  const again = await create({ name: "Palm Court" });
  assert.equal(again.body.slug, "palm-court-2");
  assert.equal(again.body.threshold_source, "default");
  assert.equal(again.body.unlock_threshold, 20);
  // a hub manager's society goes on their hub; naming another hub is 403, and an unknown one 422
  assert.equal((await create({ name: "Lotus Park" }, "hub_manager")).body.hub.id, KHARGHAR_HUB_ID);
  const elsewhere = await create({ name: "Lotus Park", hub_id: TALOJA_HUB_ID }, "hub_manager");
  assert.equal(elsewhere.status, 403);
  const unknown = await create({
    name: "Lotus Park",
    hub_id: "0199a000-0000-7000-8000-0000000000ff",
  });
  assert.deepEqual(fieldsOf(unknown), ["hub_id"]);
});

test("PATCH /v1/admin/societies/{id} takes boundary and towers, answers the unlock view, and refuses hub_id, flats and prospect", async () => {
  const id = SOCIETY_ID["panchavati-residency"];
  const patch = (/** @type {unknown} */ body, role = "admin", sid = id) =>
    call("PATCH", `/v1/admin/societies/${sid}`, { body, role });
  const find = async () =>
    (await call("GET", "/v1/admin/societies")).body.items.find(
      (/** @type {{ id: string }} */ s) => s.id === id,
    );
  for (const bad of [{ hub_id: KHARGHAR_HUB_ID }, { flats: 10 }, { name: "X Y" }, { slug: "x" }]) {
    const r = await patch(bad);
    assert.equal(r.status, 422, JSON.stringify(bad));
    assert.equal(r.body.code, "request.invalid");
  }
  assert.deepEqual(fieldsOf(await patch({ status: "prospect" })), ["status"]);
  assert.deepEqual(fieldsOf(await patch({ towers: null })), ["towers"]);
  assert.deepEqual(fieldsOf(await patch({ unlock_threshold: 0 })), ["unlock_threshold"]);
  const open = { type: "Polygon", coordinates: [[[73, 19]]] };
  assert.equal((await patch({ boundary: open })).body.code, "community.boundary_invalid");

  const ring = [
    [73.09, 19.01],
    [73.1, 19.01],
    [73.1, 19.02],
    [73.09, 19.01],
  ];
  const ok = await patch({
    boundary: { type: "Polygon", coordinates: [ring] },
    towers: [{ label: "Tower 1" }],
    unlock_threshold: 25,
    status: "unlocking",
  });
  assert.equal(ok.status, 200);
  assert.deepEqual(keys(ok.body), [
    "box_fee_paise",
    "joined",
    "live_at",
    "society_id",
    "status",
    "threshold",
    "threshold_source",
    "unlock_threshold_pinned",
  ]);
  assert.equal(ok.body.threshold_source, "hub");
  const listed = await find();
  assert.deepEqual(listed.towers, [{ id: "a", label: "Tower 1" }]);
  assert.deepEqual(listed.boundary.coordinates[0], ring);
  // a null boundary clears it; a missing one leaves the rest alone
  assert.equal((await patch({ boundary: null })).status, 200);
  const cleared = await find();
  assert.equal(cleared.boundary, null);
  assert.equal(cleared.towers.length, 1);
  // the hub-less society is only an admin's: 404 for a hub manager
  const loose = SOCIETY_ID["sai-darshan-apartments"];
  assert.equal((await patch({ unlock_threshold: 10 }, "hub_manager", loose)).status, 404);
});

const REQUEST_KEYS = [
  "area",
  "created_at",
  "duplicate_of",
  "id",
  "location",
  "name",
  "note",
  "reviewed_at",
  "society_id",
  "status",
];
const requests = async (/** @type {string} */ query = "") =>
  (await call("GET", `/v1/admin/society-requests${query}`)).body.items;
const review = (
  /** @type {string} */ id,
  /** @type {string} */ action,
  body = {},
  role = "admin",
) =>
  call("POST", `/v1/admin/society-requests/${id}/${action}`, {
    body,
    role,
    key: crypto.randomUUID(),
  });

test("GET /v1/admin/society-requests is Page[SocietyRequestAdmin]: newest first, uuid ids, a status filter, staff only", async () => {
  const all = await call("GET", "/v1/admin/society-requests");
  assert.deepEqual(keys(all.body), ["items", "next_cursor"]);
  assert.deepEqual(keys(all.body.items[0]), REQUEST_KEYS);
  const dates = all.body.items.map((/** @type {{ created_at: string }} */ r) => r.created_at);
  assert.deepEqual(dates, [...dates].sort().reverse());
  assert.equal(
    all.body.items.every((/** @type {{ id: string }} */ r) => /^[0-9a-f-]{36}$/.test(r.id)),
    true,
  );
  const fresh = await requests("?status=received");
  assert.equal(
    fresh.every((/** @type {{ status: string }} */ r) => r.status === "received"),
    true,
  );
  assert.equal(
    (await call("GET", "/v1/admin/society-requests", { role: "franchise_owner" })).status,
    403,
  );
});

test("accepting a request makes a prospect society and closes the request (201 { request, society })", async () => {
  const [req] = await requests("?status=received");
  assert.equal((await review(req.id, "accept", { status: "live" })).status, 422);
  const foreign = await review(req.id, "accept", { hub_id: TALOJA_HUB_ID }, "hub_manager");
  assert.equal(foreign.status, 403);
  const done = await review(req.id, "accept");
  assert.equal(done.status, 201);
  assert.deepEqual(keys(done.body), ["request", "society"]);
  assert.equal(done.body.request.status, "accepted");
  assert.equal(done.body.request.society_id, done.body.society.id);
  assert.ok(done.body.request.reviewed_at);
  assert.deepEqual(keys(done.body.society), ADMIN_SOCIETY_KEYS);
  assert.equal(done.body.society.status, "prospect");
  assert.equal(done.body.society.hub, null); // an admin who names no hub
  assert.equal(done.body.society.name, req.name);
  assert.equal((await review(req.id, "accept")).body.code, "community.request_closed");
  assert.equal((await review(req.id, "duplicate", {})).body.code, "community.request_closed");
  const gone = await review("0199b710-0000-7000-8000-0000000000ff", "accept");
  assert.equal(gone.body.code, "community.request_not_found");
});

test("a hub manager's accepted society goes on their hub", async () => {
  const [req] = await requests("?status=received");
  const done = await review(req.id, "accept", {}, "hub_manager");
  assert.equal(done.body.society.hub.id, KHARGHAR_HUB_ID);
});

test("duplicate needs a listed society (422 on society_id) and PATCH { status: received } undoes it", async () => {
  const [req] = await requests("?status=received");
  const missing = await review(req.id, "duplicate", {});
  assert.equal(missing.status, 422);
  assert.deepEqual(fieldsOf(missing), ["society_id"]);
  const unlisted = await review(req.id, "duplicate", {
    society_id: "0199a000-0000-7000-8000-0000000000ff",
  });
  assert.equal(unlisted.status, 422);

  const marked = await review(req.id, "duplicate", { society_id: SOCIETY_ID["krishna-kunj"] });
  assert.equal(marked.status, 200);
  assert.deepEqual(keys(marked.body), REQUEST_KEYS);
  assert.equal(marked.body.status, "duplicate");
  assert.equal(marked.body.duplicate_of, SOCIETY_ID["krishna-kunj"]);

  const undo = (/** @type {unknown} */ body) =>
    call("PATCH", `/v1/admin/society-requests/${req.id}`, { body });
  assert.equal((await undo({ status: "accepted" })).status, 422);
  assert.equal((await undo({ status: "received", name: "x" })).status, 422);
  const back = await undo({ status: "received" });
  assert.equal(back.status, 200);
  assert.equal(back.body.status, "received");
  assert.equal(back.body.duplicate_of, null);
  assert.equal(back.body.reviewed_at, null);
  // only a duplicate can be undone
  assert.equal((await undo({ status: "received" })).body.code, "community.request_closed");
});
