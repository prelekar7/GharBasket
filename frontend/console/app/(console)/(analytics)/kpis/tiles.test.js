import assert from "node:assert/strict";
import { test } from "node:test";
import { KHARGHAR_HUB_ID } from "../../../../mocks/hubs.js";
import { buildTiles } from "./tiles.js";

/** The simulator seed's last four weeks, as in the KPI design. */
const KPIS = {
  hub_id: KHARGHAR_HUB_ID,
  data_source: /** @type {const} */ ("simulator"),
  weeks: ["7 Sep", "14 Sep", "21 Sep", "28 Sep"],
  active_households: [52, 55, 58, 64],
  weeks_accepted_bp: [8700, 8800, 8900, 9100],
  waste_g_per_100kg: [2800, 2600, 2300, 1900],
  on_time_bp: [9600, 9700, 9700, 9700],
  partner_earnings_paise: [620_000, 690_000, 733_000, 845_000],
};

test("the five tiles match the simulator fixture", () => {
  const t = buildTiles(KPIS);
  assert.deepEqual(
    t.map((x) => [x.label, x.value, x.delta.value, x.delta.direction]),
    [
      ["Active households", "64", "+6", "up"],
      ["Weeks accepted", "91%", "+2 pts", "up"],
      ["Waste per 100 kg sold", "1.9 kg", "−0.4 kg", "down"],
      ["Delivered on time", "97%", "no change", "flat"],
      ["Partner earnings", "₹8,450", "+₹1,120", "up"],
    ],
  );
});

test("less waste is good and more waste is not", () => {
  const [, , waste] = buildTiles(KPIS);
  assert.equal(waste.delta.good, true);
  const worse = buildTiles({ ...KPIS, waste_g_per_100kg: [1900, 2300] })[2];
  assert.equal(worse.delta.good, false);
});

test("a tile's trend is the whole window, and the earnings hint names the latest week", () => {
  const t = buildTiles(KPIS);
  assert.deepEqual(t[0].spark, [52, 55, 58, 64]);
  assert.equal(t[4].hint, "Week of 28 Sep");
});
