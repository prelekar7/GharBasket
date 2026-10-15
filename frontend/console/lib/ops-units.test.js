import assert from "node:assert/strict";
import { test } from "node:test";
import { isWholeUnit, opsUnit, parseQty, withUnit } from "./ops-units.js";

test("a catalog step rolls up to its operations unit", () => {
  assert.equal(opsUnit({ unit: "g" }), "kg");
  assert.equal(opsUnit({ unit: "kg" }), "kg");
  assert.equal(opsUnit({ unit: "ml" }), "l");
  assert.equal(opsUnit({ unit: "bunch" }), "bunch");
  assert.equal(opsUnit({ unit: "pack" }), "pack");
  assert.equal(opsUnit({ unit: "piece" }), "piece");
  assert.equal(opsUnit({ unit: "dozen" }), "piece");
});

test("quantities are read the way the API checks them", () => {
  assert.equal(parseQty("2.5", "kg"), 2.5);
  assert.equal(parseQty(" 12 ", "bunch"), 12);
  assert.equal(parseQty("1.2345", "kg"), null);
  assert.equal(parseQty("2.5", "bunch"), null);
  assert.equal(parseQty("-1", "kg"), null);
  assert.equal(parseQty("0", "kg", 0.001), null);
  assert.equal(parseQty("abc", "kg"), null);
  assert.equal(isWholeUnit("pack"), true);
  assert.equal(isWholeUnit("l"), false);
});

test("units are written in the plural when needed", () => {
  assert.equal(withUnit(1, "bunch"), "1 bunch");
  assert.equal(withUnit(6, "bunch"), "6 bunches");
  assert.equal(withUnit(2.5, "kg"), "2.5 kg");
});
