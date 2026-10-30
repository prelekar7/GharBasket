import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { communityHandlers } from "./community.js";
import { handoffsHandlers, handoffsStore } from "./handoffs.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { NGO_ID } from "./ids.js";
import { impactHandlers } from "./impact.js";

const handlers = [...communityHandlers, ...handoffsHandlers, ...impactHandlers];
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
const _id = (/** @type {string} */ group, /** @type {number} */ n) =>
  `0199${group}-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;

describe("offers and hand-offs", () => {
  test("the first claim wins, the second is 409 community.offer_taken", async () => {
    const [open] = (
      await (await call("GET", `ngo/offers?hub_id=${KHARGHAR_HUB_ID}&status=open`)).json()
    ).items;
    assert.equal(
      (
        await call("POST", `ngo/offers/${open.offer_id}/claim`, {
          role: "hub_manager",
          body: { ngo_id: NGO_ID.first },
        })
      ).status,
      403,
    );
    const won = await call("POST", `ngo/offers/${open.offer_id}/claim`, {
      role: "ngo_coordinator",
      body: { ngo_id: NGO_ID.first },
    });
    assert.equal((await won.json()).status, "claimed");
    const lost = await call("POST", `ngo/offers/${open.offer_id}/claim`, {
      role: "ngo_coordinator",
      body: { ngo_id: NGO_ID.first },
    });
    assert.equal((await lost.json()).code, "community.offer_taken");
  });

  test("ready lists verified donations, claimed offers and the meals waiting", async () => {
    const ready = await (await call("GET", `ngo-handoffs/ready?hub_id=${KHARGHAR_HUB_ID}`)).json();
    assert.ok(ready.donations.length >= 1);
    assert.ok(ready.offers.every((/** @type {{ status: string }} */ o) => o.status === "claimed"));
    assert.equal(ready.meals_waiting, 6);
  });

  /** @param {Record<string, unknown>} over */
  const handoff = (over) => ({
    ngo_id: NGO_ID.first,
    hub_id: KHARGHAR_HUB_ID,
    kind: "meals",
    donation_ids: [],
    offer_ids: [],
    meals: 4,
    photo_keys: ["handoff_photo/u/p.jpg"],
    people_reached: 30,
    note: null,
    ...over,
  });

  test("a hand-off needs 1 to 3 handoff_photo keys, a kind the NGO accepts, and no more meals than wait", async () => {
    const noPhoto = await call("POST", "ngo-handoffs", { body: handoff({ photo_keys: [] }) });
    assert.equal((await noPhoto.json()).code, "community.photo_key_invalid");
    const second = await call("POST", "ngo-handoffs", { body: handoff({ ngo_id: NGO_ID.second }) });
    assert.equal((await second.json()).code, "community.ngo_does_not_accept");
    const tooMany = await call("POST", "ngo-handoffs", { body: handoff({ meals: 40 }) });
    const problem = await tooMany.json();
    assert.equal(problem.code, "community.meals_exceed_pledged");
    assert.equal(problem.meals_waiting, 6);
    const empty = await call("POST", "ngo-handoffs", { body: handoff({ meals: 0 }) });
    assert.equal((await empty.json()).code, "community.handoff_empty");
  });

  test("a hub's hand-off waits for the NGO; the coordinator confirms it once", async () => {
    const made = await call("POST", "ngo-handoffs", { body: handoff({}) });
    assert.equal(made.status, 201);
    const h = await made.json();
    assert.equal(h.recorded_by, "hub");
    assert.equal(h.confirmed_at, null);
    assert.equal(handoffsStore().mealsWaiting, 2);
    assert.equal((await call("POST", `ngo-handoffs/${h.id}/confirm`, { body: {} })).status, 403);
    const ok = await call("POST", `ngo-handoffs/${h.id}/confirm`, {
      role: "ngo_coordinator",
      body: { people_reached: null, photo_keys: [] },
    });
    assert.ok((await ok.json()).confirmed_at);
    const twice = await call("POST", `ngo-handoffs/${h.id}/confirm`, {
      role: "ngo_coordinator",
      body: {},
    });
    assert.equal((await twice.json()).code, "community.handoff_already_confirmed");
  });

  test("items: verified donations become handed_over; the same ones again are 409 donation_not_ready", async () => {
    const ready = await (await call("GET", `ngo-handoffs/ready?hub_id=${KHARGHAR_HUB_ID}`)).json();
    const ids = ready.donations.map((/** @type {{ id: string }} */ d) => d.id);
    const made = await call("POST", "ngo-handoffs", {
      role: "ngo_coordinator",
      body: handoff({ kind: "items", meals: 0, donation_ids: ids }),
    });
    assert.equal(made.status, 201);
    const h = await made.json();
    assert.equal(h.recorded_by, "ngo");
    assert.ok(h.confirmed_at);
    const again = await call("POST", "ngo-handoffs", {
      body: handoff({ kind: "items", meals: 0, donation_ids: ids }),
    });
    const problem = await again.json();
    assert.equal(problem.code, "community.donation_not_ready");
    assert.deepEqual(problem.donation_ids, ids);
  });

  test("the month's list carries the report and an NGO sees only its own", async () => {
    const res = await (
      await call("GET", "ngo-handoffs?month=2026-10", { role: "ngo_coordinator" })
    ).json();
    assert.ok(res.items.length >= 2);
    assert.ok(
      res.items.every((/** @type {{ ngo: { id: string } }} */ h) => h.ngo.id === NGO_ID.first),
    );
    assert.equal(res.report.handoffs, res.items.length);
    assert.deepEqual(Object.keys(res.report).sort(), [
      "handoffs",
      "items",
      "kg",
      "meals",
      "people_reached",
    ]);
  });
});

describe("impact", () => {
  const q = `analytics/impact?scope=hub&hub_id=${KHARGHAR_HUB_ID}&month=2026-10`;

  test("the contract's totals, a six-month trend and the societies of a hub", async () => {
    const body = await (await call("GET", q)).json();
    assert.equal(body.scope, "hub");
    assert.deepEqual(body.trend.months, [
      "2026-05",
      "2026-06",
      "2026-07",
      "2026-08",
      "2026-09",
      "2026-10",
    ]);
    assert.equal(body.trend.food_saved_kg.length, 6);
    assert.equal(body.totals.food_saved_kg, 53.5);
    assert.equal(body.totals.partners_earned_paise, 1_580_000);
    assert.equal(
      body.societies.reduce(
        (/** @type {number} */ n, /** @type {{ items_donated: number }} */ s) =>
          n + s.items_donated,
        0,
      ),
      body.totals.items_donated,
    );
    assert.ok(
      body.societies.every((/** @type {Record<string, unknown>} */ s) => !("earned_paise" in s)),
    );
  });

  test("an NGO's view never carries earnings or societies", async () => {
    const body = await (
      await call("GET", `analytics/impact?scope=ngo&ngo_id=${NGO_ID.first}&month=2026-10`, {
        role: "ngo_coordinator",
      })
    ).json();
    assert.equal(body.totals.partners_earned_paise, null);
    assert.equal(body.totals.referrals_converted, null);
    assert.equal(body.societies, null);
    assert.ok(body.ngos.length >= 1);
    const other = await call(
      "GET",
      `analytics/impact?scope=ngo&ngo_id=${NGO_ID.second}&month=2026-10`,
      { role: "ngo_coordinator" },
    );
    assert.equal(other.status, 403);
  });

  test("scope and month rules", async () => {
    assert.equal((await call("GET", "analytics/impact?scope=platform&month=2026-10")).status, 403);
    assert.equal(
      (await call("GET", "analytics/impact?scope=platform&month=2026-10", { role: "admin" }))
        .status,
      200,
    );
    const old = await call(
      "GET",
      `analytics/impact?scope=hub&hub_id=${KHARGHAR_HUB_ID}&month=2020-01`,
    );
    assert.equal(old.status, 422);
    assert.equal((await old.json()).code, "analytics.range_too_long");
    const future = await call(
      "GET",
      `analytics/impact?scope=hub&hub_id=${KHARGHAR_HUB_ID}&month=2099-01`,
    );
    assert.equal(future.status, 422);
    assert.equal((await future.json()).code, "analytics.range_invalid");
    // A scope's id left out: 403 for staff, 422 for an admin; an admin's unknown NGO is 404.
    assert.equal((await call("GET", "analytics/impact?scope=hub")).status, 403);
    assert.equal((await call("GET", "analytics/impact?scope=hub", { role: "admin" })).status, 422);
    const unknown = await call("GET", `analytics/impact?scope=ngo&ngo_id=${_id("ffff", 1)}`, {
      role: "admin",
    });
    assert.equal(unknown.status, 404);
    assert.equal((await unknown.json()).code, "community.ngo_not_found");
  });

  test("format=csv is an attachment with one row for the hub and one per NGO", async () => {
    const res = await call("GET", `${q}&format=csv`);
    assert.match(res.headers.get("content-type") ?? "", /^text\/csv/);
    assert.match(res.headers.get("content-disposition") ?? "", /^attachment/);
    const lines = (await res.text()).trim().split("\n");
    const head = lines[0].split(",");
    assert.equal(head[0], "month");
    assert.equal(head.length, 16);
    assert.ok(lines.every((l) => l.split(",").length === head.length));
    assert.ok(lines.length >= 2);
    assert.match(res.headers.get("content-disposition") ?? "", /impact-2026-10-hub-/);
  });

  test("a hand-off photo upload: handoff_photo only, JPEG/PNG/WebP, up to 5 MB", async () => {
    const ok = await call("POST", "files/presign", {
      body: { purpose: "handoff_photo", content_type: "image/jpeg", size_bytes: 1000 },
    });
    const signed = await ok.json();
    assert.match(signed.key, /^handoff_photo\/[^/]+\/[^/]+\.jpg$/);
    assert.equal(signed.method, "PUT");
    const gif = await call("POST", "files/presign", {
      body: { purpose: "handoff_photo", content_type: "image/gif", size_bytes: 10 },
    });
    assert.equal((await gif.json()).code, "platform.upload_type_not_allowed");
    const big = await call("POST", "files/presign", {
      body: { purpose: "handoff_photo", content_type: "image/png", size_bytes: 9_000_000 },
    });
    assert.equal((await big.json()).code, "platform.upload_too_large");
    const other = await call("POST", "files/presign", {
      body: { purpose: "nope", content_type: "image/png", size_bytes: 1 },
    });
    assert.equal((await other.json()).code, "platform.upload_purpose_invalid");
  });
});
