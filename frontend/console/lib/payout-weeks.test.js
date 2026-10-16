import assert from "node:assert/strict";
import { test } from "node:test";
import { isoWeekOf, recentWeeks, runAfter, weekRange } from "./payout-weeks.js";

test("a date maps to its ISO week, Monday to Sunday", () => {
  assert.deepEqual(isoWeekOf("2026-10-14"), {
    week: "2026-W42",
    from: "2026-10-12",
    to: "2026-10-18",
  });
  assert.equal(isoWeekOf("2026-10-12").week, "2026-W42");
  assert.equal(isoWeekOf("2026-10-18").week, "2026-W42");
});

test("the first days of January can belong to the old year's last week", () => {
  assert.equal(isoWeekOf("2027-01-01").week, "2026-W53");
  assert.equal(isoWeekOf("2027-01-04").week, "2027-W01");
  assert.equal(isoWeekOf("2024-12-30").week, "2025-W01");
});

test("recent weeks start with the week that ended last Sunday", () => {
  // Wed 14 Oct 2026, 11:00 IST
  const now = Date.parse("2026-10-14T11:00:00+05:30");
  assert.deepEqual(
    recentWeeks(now, 3).map((w) => w.week),
    ["2026-W41", "2026-W40", "2026-W39"],
  );
  assert.equal(recentWeeks(now, 1)[0].from, "2026-10-05");
});

test("a week reads as a range, and its run is the next Monday at 10:00 IST", () => {
  assert.equal(weekRange({ from: "2026-10-05", to: "2026-10-11" }), "5 to 11 Oct");
  assert.equal(weekRange({ from: "2026-09-28", to: "2026-10-04" }), "28 Sep to 4 Oct");
  assert.equal(runAfter({ to: "2026-10-11" }), "2026-10-12T10:00:00+05:30");
});
