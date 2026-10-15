import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { communityHandlers } from "./community.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { NGO_ID } from "./ids.js";

const handlers = communityHandlers;
let keys = 0;

/**
 * @param {string} method @param {string} path
 * @param {{ role?: string | null, body?: unknown, key?: string }} [options]
 */
async function call(method, path, { role = "hub_manager", body, key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({});
  if (role) headers["x-gb-role"] = role;
  if (method === "POST") headers["idempotency-key"] = key ?? `test-key-${++keys}`;
  return /** @type {Response} */ (
    await getResponse(
      handlers,
      new Request(`http://mock.gharbasket.local/v1/${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    )
  );
}
const id = (/** @type {string} */ group, /** @type {number} */ n) =>
  `0199${group}-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;

describe("point rates and drives", () => {
  test("rates carry the code, unit, points and the cap per donation", async () => {
    const { items } = await (await call("GET", "point-rates")).json();
    assert.deepEqual(
      items.find((/** @type {{ code: string }} */ r) => r.code === "clothes"),
      {
        code: "clothes",
        label: "Clothes",
        unit: "kg",
        points: 40,
        max_qty: 15,
      },
    );
    assert.equal((await call("GET", "point-rates", { role: null })).status, 401);
  });

  test("drives: running and upcoming by default, `state` asks for another; uuids and the contract's fields", async () => {
    const run = await (await call("GET", "drives")).json();
    assert.deepEqual(
      run.items.map((/** @type {{ state: string }} */ d) => d.state),
      ["running"],
    );
    const [diwali] = run.items;
    assert.match(diwali.id, /^[0-9a-f-]{36}$/);
    for (const key of [
      "code",
      "scope",
      "hub_id",
      "society_id",
      "codes",
      "multiplier",
      "ngo",
      "goal_items",
      "progress",
    ])
      assert.ok(key in diwali, key);
    const ended = await (await call("GET", "drives?state=ended")).json();
    assert.equal(ended.items[0].state, "ended");
    assert.equal(ended.next_cursor, null);
  });

  test("a hub manager starts a hub drive; platform drives are for admins; dates are checked", async () => {
    const body = {
      code: "winter-2026",
      name: { en: "Winter drive", hi: "Winter drive", mr: "Winter drive" },
      description: null,
      scope: "hub",
      hub_id: KHARGHAR_HUB_ID,
      society_id: null,
      codes: ["clothes"],
      multiplier: 1.5,
      starts_on: "2026-12-01",
      ends_on: "2026-12-31",
      ngo_id: null,
      goal_items: null,
    };
    const made = await call("POST", "admin/drives", { body });
    assert.equal(made.status, 201);
    assert.equal((await made.json()).state, "upcoming");
    const platform = await call("POST", "admin/drives", {
      body: { ...body, scope: "platform", hub_id: null },
    });
    assert.equal(platform.status, 403);
    assert.equal((await platform.json()).code, "auth.forbidden");
    assert.equal(
      (
        await call("POST", "admin/drives", {
          role: "admin",
          body: { ...body, scope: "platform", hub_id: null },
        })
      ).status,
      201,
    );
    const backwards = await call("POST", "admin/drives", {
      body: { ...body, ends_on: "2026-11-01" },
    });
    assert.equal((await backwards.json()).code, "community.drive_dates_invalid");
    const noKey = await getResponse(
      handlers,
      new Request("http://mock.gharbasket.local/v1/admin/drives", {
        method: "POST",
        headers: { "x-gb-role": "admin" },
        body: "{}",
      }),
    );
    assert.equal(noKey?.status, 400);
  });

  test("a repeated Idempotency-Key replays the first drive", async () => {
    const body = {
      code: "once",
      name: { en: "Once", hi: "Once", mr: "Once" },
      scope: "hub",
      hub_id: KHARGHAR_HUB_ID,
      codes: ["toys"],
      multiplier: 2,
      starts_on: "2026-11-01",
      ends_on: "2026-11-02",
    };
    const a = await (await call("POST", "admin/drives", { body, key: "same" })).json();
    const b = await (await call("POST", "admin/drives", { body, key: "same" })).json();
    assert.equal(a.id, b.id);
  });
});

describe("NGOs", () => {
  test("only admins add one; a coordinator lists only their own; it starts unverified", async () => {
    assert.equal(
      (await call("POST", "admin/ngos", { body: { name: "X", kind: "school" } })).status,
      403,
    );
    const made = await call("POST", "admin/ngos", {
      role: "admin",
      body: { name: "Seva Shala", kind: "school", hub_ids: [KHARGHAR_HUB_ID] },
    });
    assert.equal(made.status, 201);
    const ngo = await made.json();
    assert.equal(ngo.verified, false);
    assert.equal(ngo.coordinator, null);
    const own = await (await call("GET", "ngos", { role: "ngo_coordinator" })).json();
    assert.deepEqual(
      own.items.map((/** @type {{ id: string }} */ n) => n.id),
      [NGO_ID.first],
    );
    const bad = await call("POST", "admin/ngos", {
      role: "admin",
      body: { name: "", kind: "nope" },
    });
    assert.equal(bad.status, 422);
  });
});

describe("donations", () => {
  test("the queue is oldest first, with the donor's place and photo links, filtered by status", async () => {
    const { items } = await (
      await call("GET", `admin/donations?hub_id=${KHARGHAR_HUB_ID}&status=picked_up`)
    ).json();
    assert.equal(items.length, 2);
    assert.ok(
      items.every(
        (/** @type {Record<string, any>} */ d) =>
          d.status === "picked_up" && d.tower && d.flat && d.photo_urls.length === d.photos,
      ),
    );
    assert.equal((await call("GET", "admin/donations", { role: "ngo_coordinator" })).status, 403);
  });

  test("verify: points by the spec's rule, counted may add a code, verified once, a second verify is 409", async () => {
    const [first] = (await (await call("GET", "admin/donations?status=picked_up")).json()).items;
    const nothing = await call("POST", `donations/${first.id}/verify`, {
      body: { counted: { toys: 0, books: 0 }, has_unsuitable: false },
    });
    assert.equal((await nothing.json()).code, "community.nothing_counted");
    const step = await call("POST", `donations/${first.id}/verify`, {
      body: { counted: { toys: 1.5 }, has_unsuitable: false },
    });
    assert.equal((await step.json()).code, "community.donation_qty_invalid");
    const ok = await call("POST", `donations/${first.id}/verify`, {
      body: { counted: { toys: 4, books: 5 }, has_unsuitable: false },
    });
    assert.equal(ok.status, 200);
    const done = await ok.json();
    assert.equal(done.status, "verified");
    assert.equal(done.points_awarded, (4 * 15 + 5 * 10) * 2);
    assert.equal(
      done.items.reduce(
        (/** @type {number} */ n, /** @type {{ points: number }} */ i) => n + i.points,
        0,
      ),
      done.points_awarded,
    );
    const again = await call("POST", `donations/${first.id}/verify`, {
      body: { counted: { toys: 1 }, has_unsuitable: false },
    });
    assert.equal(again.status, 409);
    assert.equal((await again.json()).code, "community.donation_already_verified");
  });

  test("reject: a reason is needed, it earns nothing, and it cannot then be verified", async () => {
    const [second] = (await (await call("GET", "admin/donations?status=picked_up")).json()).items;
    assert.equal(
      (await call("POST", `donations/${second.id}/reject`, { body: { reason: "nope" } })).status,
      422,
    );
    const done = await (
      await call("POST", `donations/${second.id}/reject`, { body: { reason: "unusable" } })
    ).json();
    assert.equal(done.status, "rejected");
    assert.equal(done.points_awarded, null);
    const late = await call("POST", `donations/${second.id}/verify`, {
      body: { counted: { books: 1 } },
    });
    assert.equal(late.status, 409);
  });

  test("an unknown donation is 404 community.donation_not_found", async () => {
    const res = await call("POST", `donations/${id("ba00", 999)}/verify`, {
      body: { counted: { books: 1 } },
    });
    assert.equal(res.status, 404);
    assert.equal((await res.json()).code, "community.donation_not_found");
  });
});
