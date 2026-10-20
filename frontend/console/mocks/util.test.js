import assert from "node:assert/strict";
import { test } from "node:test";
import { fieldMessages } from "../lib/problem-fields.js";
import { fieldError, idempotent, problem } from "./util.js";

test("fieldError builds a loc path from a name or a dotted path", () => {
  assert.deepEqual(fieldError("name", "Required", "missing"), {
    loc: ["body", "name"],
    message: "Required",
    type: "missing",
  });
  assert.deepEqual(fieldError("lines.2.price_paise", "Too low").loc, [
    "body",
    "lines",
    2,
    "price_paise",
  ]);
});

test("problem turns the old { field, message | detail } items into { loc, message, type }", async () => {
  const response = problem(422, "request.invalid", "Check", {
    errors: [
      { field: "phone", message: "Enter 10 digits" },
      { field: "count", detail: "A whole number" },
      fieldError("role", "Pick one"),
    ],
  });
  const body = await response.json();
  assert.deepEqual(body.errors, [
    { loc: ["body", "phone"], message: "Enter 10 digits", type: "value_error" },
    { loc: ["body", "count"], message: "A whole number", type: "value_error" },
    { loc: ["body", "role"], message: "Pick one", type: "value_error" },
  ]);
  assert.deepEqual(fieldMessages(body.errors), {
    phone: "Enter 10 digits",
    count: "A whole number",
    role: "Pick one",
  });
});

test("idempotent uses the real codes", async () => {
  const post = (/** @type {string | null} */ key, /** @type {unknown} */ body) =>
    new Request("http://mock.test/v1/x", {
      method: "POST",
      headers: key ? { "idempotency-key": key } : {},
      body: JSON.stringify(body),
    });
  const run = () => Response.json({ ok: true });
  const missing = await idempotent(post(null, { a: 1 }), run);
  assert.equal(missing.status, 400);
  assert.equal((await missing.json()).code, "request.idempotency_key_required");
  assert.equal((await idempotent(post("k-util", { a: 1 }), run)).status, 200);
  const other = await idempotent(post("k-util", { a: 2 }), run);
  assert.equal(other.status, 422);
  assert.equal((await other.json()).code, "request.idempotency_mismatch");
});
