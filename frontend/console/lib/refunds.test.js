import assert from "node:assert/strict";
import { test } from "node:test";
import { kindText, needsAttention, reasonText, statusText } from "./refunds.js";

test("every reason and status of the contract has plain words", () => {
  for (const r of ["shortage", "issue", "undelivered", "no_kitchen", "carry_over", "other"])
    assert.notEqual(reasonText(r), r);
  for (const s of ["credited", "pending", "processing", "processed", "partly_failed", "failed"])
    assert.notEqual(statusText(s), s);
  assert.equal(kindText("addon"), "Add-on");
  assert.equal(reasonText("something_new"), "Other");
});

test("only a refund that did not fully reach the bank needs attention", () => {
  assert.deepEqual(
    ["credited", "pending", "processed", "failed", "partly_failed"].map(needsAttention),
    [false, false, false, true, true],
  );
});
