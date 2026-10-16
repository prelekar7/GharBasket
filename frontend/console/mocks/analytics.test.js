import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { analyticsHandlers } from "./analytics.js";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";

/** @param {string} path @param {string} query @param {string | null} role */
const get = async (path, query, role) =>
  /** @type {Response} */ (
    await getResponse(
      analyticsHandlers,
      new Request(`http://mock.gharbasket.local/v1/analytics/${path}?${query}`, {
        headers: role ? { "x-gb-role": role } : {},
      }),
    )
  );
const kpis = (/** @type {string} */ query, /** @type {string | null} */ role) =>
  get("kpis", `data_source=synthetic&${query}`, role);

describe("kpis", () => {
  test("each hub has its own numbers and all hubs add up", async () => {
    const [a, k, t] = await Promise.all([
      kpis("from=2026-09-07&to=2026-10-05", "admin").then((r) => r.json()),
      kpis(`hub_id=${KHARGHAR_HUB_ID}&from=2026-09-07&to=2026-10-05`, "admin").then((r) =>
        r.json(),
      ),
      kpis(`hub_id=${TALOJA_HUB_ID}&from=2026-09-07&to=2026-10-05`, "admin").then((r) => r.json()),
    ]);
    assert.equal(a.hub_id, null);
    assert.equal(a.grain, "week");
    assert.match(a.as_of, /\+05:30$/);
    assert.equal(a.periods.length, 4);
    assert.match(a.periods[0], /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(k.series.active_households.at(-1), 64);
    assert.equal(k.totals.active_households, 64);
    assert.notDeepEqual(k.series.active_households, t.series.active_households);
    assert.deepEqual(
      a.series.active_households,
      k.series.active_households.map(
        (/** @type {number} */ n, /** @type {number} */ i) => n + t.series.active_households[i],
      ),
    );
    // A weighted average sits between the hubs' own rates.
    const onTime = a.series.on_time_bp.at(-1);
    assert.ok(onTime >= t.series.on_time_bp.at(-1) && onTime <= k.series.on_time_bp.at(-1));
  });

  test("the pilot has no data: every value is null, and the source is never merged", async () => {
    const r = await get("kpis", `data_source=real&hub_id=${KHARGHAR_HUB_ID}`, "franchise_owner");
    const body = await r.json();
    assert.equal(body.data_source, "real");
    assert.ok(body.series.active_households.every((/** @type {unknown} */ v) => v === null));
    assert.equal(body.totals.on_time_bp, null);
  });

  test("data_source is required, ranges are checked", async () => {
    const none = await get("kpis", `hub_id=${KHARGHAR_HUB_ID}`, "admin");
    assert.equal(none.status, 422);
    assert.equal((await none.json()).code, "analytics.data_source_required");
    const backwards = await kpis("from=2026-10-05&to=2026-09-07", "admin");
    assert.equal((await backwards.json()).code, "analytics.range_invalid");
    const long = await kpis("from=2020-01-06&to=2026-10-05", "admin");
    assert.equal((await long.json()).code, "analytics.range_too_long");
  });

  test("only admins may ask for all hubs or for a hub that is not theirs", async () => {
    assert.equal((await kpis("", "hub_manager")).status, 403);
    assert.equal((await kpis("", "franchise_owner")).status, 403);
    assert.equal((await kpis(`hub_id=${TALOJA_HUB_ID}`, "franchise_owner")).status, 403);
    assert.equal((await kpis(`hub_id=${KHARGHAR_HUB_ID}`, "franchise_owner")).status, 200);
    assert.equal((await kpis(`hub_id=${KHARGHAR_HUB_ID}`, "ngo_coordinator")).status, 403);
    assert.equal((await kpis("hub_id=nowhere", "admin")).status, 404);
    assert.equal((await kpis(`hub_id=${KHARGHAR_HUB_ID}`, null)).status, 401);
    const denied = await kpis("", "hub_manager");
    assert.equal((await denied.json()).code, "auth.forbidden");
  });
});

describe("societies", () => {
  test("items carry uuids, the unlock progress and the rates, and nothing personal", async () => {
    const r = await get(
      "societies",
      `hub_id=${KHARGHAR_HUB_ID}&data_source=synthetic`,
      "franchise_owner",
    );
    const body = await r.json();
    assert.equal(body.hub_id, KHARGHAR_HUB_ID);
    const sunrise = body.items[0];
    assert.match(sunrise.society_id, /^[0-9a-f-]{36}$/);
    assert.deepEqual(Object.keys(sunrise).sort(), [
      "active_households",
      "approved_on_time_bp",
      "average_box_paise",
      "joined",
      "name",
      "paused_households",
      "society_id",
      "status",
      "threshold",
    ]);
    const unlocking = body.items.find(
      (/** @type {{ status: string }} */ s) => s.status === "unlocking",
    );
    assert.equal(unlocking.approved_on_time_bp, null);
    assert.ok(unlocking.joined < unlocking.threshold);
  });

  test("hub staff may read, an admin must choose a hub, the pilot is empty", async () => {
    assert.equal((await get("societies", "data_source=synthetic", "admin")).status, 403);
    const real = await get(
      "societies",
      `hub_id=${KHARGHAR_HUB_ID}&data_source=real`,
      "hub_manager",
    );
    assert.deepEqual((await real.json()).items, []);
  });
});

describe("cohorts", () => {
  test("admin only; retention is null for weeks not reached yet", async () => {
    assert.equal((await get("cohorts", "data_source=synthetic", "hub_manager")).status, 403);
    const body = await (await get("cohorts", "data_source=synthetic&weeks=8", "admin")).json();
    assert.equal(body.weeks, 8);
    assert.equal(body.cohorts.length, 8);
    const [oldest, newest] = [body.cohorts[0], body.cohorts.at(-1)];
    assert.equal(oldest.retention_bp.length, 8);
    assert.equal(oldest.retention_bp.filter((/** @type {unknown} */ v) => v !== null).length, 7);
    assert.equal(newest.retention_bp.filter((/** @type {unknown} */ v) => v !== null).length, 0);
    assert.equal(oldest.small, false);
  });

  test("weeks 4 to 26, and the pilot is empty", async () => {
    const bad = await get("cohorts", "data_source=synthetic&weeks=3", "admin");
    assert.equal(bad.status, 422);
    const real = await get("cohorts", "data_source=real", "admin");
    assert.deepEqual((await real.json()).cohorts, []);
  });
});
