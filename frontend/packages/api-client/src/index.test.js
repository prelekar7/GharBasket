import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiProblem, createApi, newIdempotencyKey } from "./index.js";

/**
 * A fetch that records the request and answers with `status` and `body`.
 * @param {number} status
 * @param {unknown} body
 */
function fakeFetch(status, body) {
  /** @type {Request[]} */
  const seen = [];
  /** @type {typeof globalThis.fetch} */
  const fetch = async (input) => {
    const request = /** @type {Request} */ (input);
    seen.push(request);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
    });
  };
  return { fetch, seen };
}

test("apps send the Bearer token and the active role, and no cookies", async () => {
  const { fetch, seen } = fakeFetch(200, { id: "u1" });
  const api = createApi({
    baseUrl: "https://api.test",
    fetch,
    getAccessToken: () => "tok",
    getActiveRole: () => "captain:s1",
  });

  const me = await api.unwrap(api.client.GET("/v1/me"));

  assert.deepEqual(me, { id: "u1" });
  assert.equal(seen[0].url, "https://api.test/v1/me");
  assert.equal(seen[0].headers.get("authorization"), "Bearer tok");
  assert.equal(seen[0].headers.get("x-active-role"), "captain:s1");
  assert.equal(seen[0].headers.get("x-gb-client"), null);
  assert.equal(seen[0].credentials, "omit");
});

test("browsers send X-GB-Client: web with cookies and never a Bearer token", async () => {
  const { fetch, seen } = fakeFetch(200, {});
  const api = createApi({
    baseUrl: "https://app.test",
    web: true,
    fetch,
    getAccessToken: () => "x",
  });

  await api.unwrap(api.client.GET("/v1/me"));

  assert.equal(seen[0].headers.get("x-gb-client"), "web");
  assert.equal(seen[0].headers.get("authorization"), null);
  assert.equal(seen[0].credentials, "include");
});

test("a problem response becomes an ApiProblem with its code and extension members", async () => {
  const problem = {
    type: "urn:gharbasket:problem:identity.household_exists",
    title: "Conflict",
    status: 409,
    code: "identity.household_exists",
    detail: "You already have a household.",
    instance: "/v1/households",
    request_id: "r1",
    household_id: "h1",
  };
  const api = createApi({ baseUrl: "https://api.test", fetch: fakeFetch(409, problem).fetch });

  const error = await api
    .unwrap(api.client.GET("/v1/households/me"))
    .catch((/** @type {unknown} */ e) => e);

  assert.ok(error instanceof ApiProblem);
  assert.equal(error.status, 409);
  assert.equal(error.code, "identity.household_exists");
  assert.equal(error.message, "You already have a household.");
  assert.deepEqual(error.extra, { household_id: "h1" });
  assert.equal(error.requestId, "r1");
});

test("idempotency keys are fresh UUIDs", () => {
  const [a, b] = [newIdempotencyKey(), newIdempotencyKey()];
  assert.match(a, /^[0-9a-f-]{36}$/);
  assert.notEqual(a, b);
});
