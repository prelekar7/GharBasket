import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "../../../../mocks/hubs.js";
import { intelHandlers } from "../../../../mocks/intel.js";
import { hubWape, inSource, modelRows, percentText, predictionSeries, tiles } from "./health.js";

/** @param {string | null} role */
const get = async (role) =>
  /** @type {Response} */ (
    await getResponse(
      intelHandlers,
      new Request("http://mock.gharbasket.local/v1/analytics/ai-health", {
        headers: role ? { "x-gb-role": role } : {},
      }),
    )
  );

test("the mock is admin only and never cached", async () => {
  assert.equal((await get(null)).status, 401);
  const denied = await get("hub_manager");
  assert.equal(denied.status, 403);
  assert.equal((await denied.json()).code, "auth.forbidden");
  const ok = await get("admin");
  assert.equal(ok.headers.get("cache-control"), "no-store");
  const body = await ok.json();
  assert.deepEqual(Object.keys(body).sort(), [
    "generated_at",
    "models",
    "predictions",
    "window_days",
  ]);
  assert.deepEqual(Object.keys(body.models[0]).sort(), ["drift", "hubs", "metrics", "model_name"]);
});

test("the simulator is `synthetic`; the pilot is `pilot` and `live`, never `mixed`", () => {
  assert.equal(inSource("simulator", "synthetic"), true);
  assert.equal(inSource("simulator", "pilot"), false);
  assert.equal(inSource("pilot", "live"), true);
  assert.equal(inSource("pilot", "mixed"), false);
});

test("tiles for one hub: WAPE against its backtest, drifted days, acceptance, fallback share", async () => {
  const health = await (await get("admin")).json();
  const t = tiles(health, "simulator", KHARGHAR_HUB_ID);
  assert.equal(t.wape, 0.142);
  assert.equal(t.wape_change_pts, 1.1);
  assert.deepEqual([t.drifted_days, t.scored_days], [0, 7]);
  assert.equal(t.acceptance, 0.91);
  assert.equal(percentText(t.fallback_share ?? 0), "1.4%");
  const taloja = tiles(health, "simulator", TALOJA_HUB_ID);
  assert.equal(taloja.wape_change_pts, 14);
  assert.equal(tiles(health, "pilot", KHARGHAR_HUB_ID).wape, null);
});

test("the models table has a row per model with numbers of the source; the pilot has none", async () => {
  const health = await (await get("admin")).json();
  const rows = modelRows(health, "simulator", KHARGHAR_HUB_ID);
  assert.deepEqual(
    rows.map((r) => r.key),
    ["forecast_lightgbm", "ranker_lightgbm", "planner_ml", "planner_fallback"],
  );
  assert.equal(rows[0].metric, "wape 14.2% (live)");
  assert.equal(rows[0].state, "healthy");
  assert.equal(modelRows(health, "simulator", TALOJA_HUB_ID)[0].state, "degraded");
  assert.deepEqual(modelRows(health, "pilot", KHARGHAR_HUB_ID), []);
});

test("chart series: answers per kind and WAPE per hub", async () => {
  const health = await (await get("admin")).json();
  const answers = predictionSeries(health);
  assert.deepEqual(answers.labels[0], "Weekly plan");
  assert.deepEqual([answers.ml[0], answers.fallback[0]], [412, 9]);
  const wape = hubWape(health, "simulator", { [KHARGHAR_HUB_ID]: "Kharghar hub" });
  assert.equal(wape.labels[0], "Kharghar hub");
  assert.deepEqual(wape.realised, [14.2, 31]);
  assert.deepEqual(wape.backtest, [13.1, 17]);
});
