import assert from "node:assert/strict";
import { test } from "node:test";
import { fieldMessages, fieldOf, messagesOf } from "./problem-fields.js";

test("the field is the last string of loc", () => {
  assert.equal(fieldOf(["body", "items", 2, "price_paise"]), "price_paise");
  assert.equal(fieldOf(["body", "lines", 0]), "lines");
  assert.equal(fieldOf(["query", "hub_id"]), "hub_id");
  assert.equal(fieldOf([3]), null);
  assert.equal(fieldOf(undefined), null);
});

test("field messages keep the first error of a field; messagesOf keeps all", () => {
  const errors = [
    { loc: ["body", "lines", 0, "price_paise"], message: "Too low", type: "value_error" },
    { loc: ["body", "lines", 1, "price_paise"], message: "Above MRP", type: "value_error" },
    { loc: ["body", "valid_from"], message: "Pick a later day", type: "value_error" },
    { field: "legacy", message: "old shape is ignored" },
  ];
  assert.deepEqual(fieldMessages(errors), {
    price_paise: "Too low",
    valid_from: "Pick a later day",
  });
  assert.deepEqual(messagesOf(errors), [
    "Too low",
    "Above MRP",
    "Pick a later day",
    "old shape is ignored",
  ]);
  assert.deepEqual(fieldMessages(undefined), {});
});
