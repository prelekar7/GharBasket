import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { analyticsHandlers } from "./analytics.js";

/** @param {string} query @param {string | null} role */
const kpis = (query, role) =>
  getResponse(
    analyticsHandlers,
    new Request(`http://mock.gharbasket.local/v1/analytics/kpis?${query}`, {
      headers: role ? { "x-gb-role": role } : {},
    }),
  );

test("each hub has its own numbers and all hubs add up", async () => {
  const [a, k, t] = await Promise.all([
    kpis("from=2026-09-07&to=2026-10-05", "admin").then((r) => r?.json()),
    kpis("hub_id=kharghar&from=2026-09-07&to=2026-10-05", "admin").then((r) => r?.json()),
    kpis("hub_id=taloja&from=2026-09-07&to=2026-10-05", "admin").then((r) => r?.json()),
  ]);
  assert.equal(a.hub_id, null);
  assert.equal(k.active_households.at(-1), 64);
  assert.notDeepEqual(k.active_households, t.active_households);
  assert.deepEqual(
    a.active_households,
    k.active_households.map(
      (/** @type {number} */ n, /** @type {number} */ i) => n + t.active_households[i],
    ),
  );
  assert.deepEqual(
    a.partner_earnings_paise,
    k.partner_earnings_paise.map(
      (/** @type {number} */ n, /** @type {number} */ i) => n + t.partner_earnings_paise[i],
    ),
  );
  // A weighted average sits between the hubs' own rates.
  const onTime = a.on_time_bp.at(-1);
  assert.ok(onTime >= t.on_time_bp.at(-1) && onTime <= k.on_time_bp.at(-1));
});

test("only admins may ask for all hubs or for a hub that is not theirs", async () => {
  assert.equal((await kpis("", "hub_manager"))?.status, 403);
  assert.equal((await kpis("", "franchise_owner"))?.status, 403);
  assert.equal((await kpis("hub_id=taloja", "franchise_owner"))?.status, 403);
  assert.equal((await kpis("hub_id=kharghar", "franchise_owner"))?.status, 200);
  assert.equal((await kpis("hub_id=nowhere", "admin"))?.status, 404);
  assert.equal((await kpis("hub_id=kharghar", null))?.status, 401);
});
