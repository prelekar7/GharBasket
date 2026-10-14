import assert from "node:assert/strict";
import { test } from "node:test";
import { isOffered, monthLabel, monthOptions } from "./months.js";

test("the month list is 24 months, newest first, in Indian time", () => {
  // 31 Oct 2026, 20:00 UTC is already 1 Nov in India.
  const options = monthOptions(Date.UTC(2026, 9, 31, 20, 0));
  assert.equal(options.length, 24);
  assert.deepEqual(options[0], { value: "2026-11", label: "November 2026" });
  assert.equal(options[1].value, "2026-10");
  assert.equal(options[23].value, "2024-12");
  assert.equal(options[12].value, "2025-11");
});

test("a month label and the check for one on offer", () => {
  assert.equal(monthLabel("2026-09"), "September 2026");
  assert.equal(isOffered("2026-09", monthOptions(Date.UTC(2026, 9, 14))), true);
  assert.equal(isOffered("2020-01", monthOptions(Date.UTC(2026, 9, 14))), false);
  assert.equal(isOffered(undefined, monthOptions(Date.UTC(2026, 9, 14))), false);
});
