import assert from "node:assert/strict";
import { test } from "node:test";
import { dayAndTime, shortDate } from "./dates.js";

test("shortDate reads the date text", () => {
  assert.equal(shortDate("2026-09-07"), "7 Sep");
  assert.equal(shortDate("2026-10-06T23:30:00Z"), "6 Oct");
});

test("dayAndTime is Indian time whatever the machine's zone", () => {
  assert.equal(dayAndTime("2026-09-06T10:12:00+05:30"), "6 Sep, 10:12 am");
  assert.equal(dayAndTime("2026-10-05T18:58:00Z"), "6 Oct, 12:28 am");
});
