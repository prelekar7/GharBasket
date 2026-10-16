import assert from "node:assert/strict";
import { test } from "node:test";
import { averageCurve } from "./cohorts.js";

const SIM = [
  { week: "2026-09-07", households: 200, retention_bp: [9400, 8800, 8400, 8100] },
  { week: "2026-09-14", households: 200, retention_bp: [9500, 8900, 8500, null] },
  { week: "2026-09-21", households: 200, retention_bp: [9300, 8700, null, null] },
  { week: "2026-09-28", households: 200, retention_bp: [9600, null, null, null] },
];

test("the average curve of the simulator cohorts is 94.5, 88, 84.5 and 81 percent", () => {
  assert.deepEqual(averageCurve(SIM, 4), [9450, 8800, 8450, 8100]);
});

test("a bigger cohort weighs more", () => {
  const curve = averageCurve(
    [
      { week: "a", households: 300, retention_bp: [9000] },
      { week: "b", households: 100, retention_bp: [8000] },
    ],
    1,
  );
  assert.deepEqual(curve, [8750]);
});

test("a week no cohort has reached yet is null, and no cohorts means no curve", () => {
  assert.deepEqual(averageCurve([SIM[3]], 2), [9600, null]);
  assert.deepEqual(averageCurve([], 3), [null, null, null]);
});
