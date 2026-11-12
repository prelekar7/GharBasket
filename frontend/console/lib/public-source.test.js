import assert from "node:assert/strict";
import { test } from "node:test";
import { publicApiOrigin } from "./public-source.js";

const API = "https://api.gharbasket.co.in";

test("the public pages use the mocks unless a switch says false", () => {
  assert.equal(publicApiOrigin({}), null);
  assert.equal(publicApiOrigin({ GB_API_URL: API }), null);
  assert.equal(publicApiOrigin({ GB_API_MOCK: "true", GB_API_URL: API }), null);
  assert.equal(
    publicApiOrigin({ GB_PUBLIC_API_MOCK: "true", GB_API_MOCK: "false", GB_API_URL: API }),
    null,
  );
});

test("they follow GB_API_MOCK when GB_PUBLIC_API_MOCK is unset", () => {
  assert.equal(publicApiOrigin({ GB_API_MOCK: "false", GB_API_URL: API }), API);
});

test("GB_PUBLIC_API_MOCK=false reads the real API while the staff screens stay on mocks", () => {
  assert.equal(publicApiOrigin({ GB_PUBLIC_API_MOCK: "false", GB_API_URL: `${API}/` }), API);
});

test("real mode without GB_API_URL is a configuration error", () => {
  assert.throws(() => publicApiOrigin({ GB_PUBLIC_API_MOCK: "false" }), /GB_API_URL is not set/);
  assert.throws(
    () => publicApiOrigin({ GB_API_MOCK: "false", GB_API_URL: "" }),
    /GB_API_URL is not set/,
  );
});
