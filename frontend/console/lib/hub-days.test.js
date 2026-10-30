import assert from "node:assert/strict";
import { test } from "node:test";
import { hubDays, packingDays, pickDay } from "./hub-days.js";

test("hubDays counts back and ahead of today, oldest first", () => {
  const days = hubDays(3, 2);
  assert.equal(days.length, 6);
  assert.deepEqual(days, [...days].sort());
});

test("packing offers the past days and every coming delivery day, once, even four days out", () => {
  const past = ["2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"];
  const upcoming = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"];
  const days = packingDays(past, upcoming);
  assert.deepEqual(days, [
    "2026-10-17",
    "2026-10-18",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
    "2026-10-24",
  ]);
  assert.equal(pickDay("2026-10-24", days), "2026-10-24", "the day four ahead can be opened");
});

test("a delivery day the hub does not list falls back to today", () => {
  const days = packingDays(hubDays(3, 0), []);
  assert.equal(pickDay("2099-01-01", days), hubDays(0, 0)[0]);
});
