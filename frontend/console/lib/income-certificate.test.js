import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { PARTNER_ID } from "../mocks/partner-ids.js";
import { partnersHandlers } from "../mocks/partners.js";
import { certificateRedirect } from "./income-certificate.js";

/** An `ApiProblem` as lib/api.js builds it (recognised by its name, since lib/api.js needs Next.js). */
const problem = (/** @type {number} */ status, /** @type {string} */ code) =>
  Object.assign(new Error(code), { name: "ApiProblem", status, code });

/** The API of the mocks, as `apiGet` would reach it (role: a hub manager). */
const mockApi = async (
  /** @type {string} */ path,
  /** @type {Record<string, string>} */ searchParams,
) => {
  const url = new URL(`http://mock.gharbasket.local${path}`);
  for (const [name, value] of Object.entries(searchParams)) url.searchParams.set(name, value);
  const response = await getResponse(
    partnersHandlers,
    new Request(url, { headers: { "x-gb-role": "hub_manager" } }),
  );
  assert.ok(response);
  const body = await response.json();
  if (!response.ok) throw problem(response.status, body.code);
  return body;
};

test("the route asks the API for the partner's certificate and redirects to the link it gets", async () => {
  /** @type {{ path: string, searchParams: Record<string, string> }[]} */
  const asked = [];
  const response = await certificateRedirect(
    "a/b",
    new URLSearchParams("from=2026-05&to=2026-10&other=1"),
    async (path, searchParams) => {
      asked.push({ path, searchParams });
      return { url: "https://files.example/certificate.pdf?sig=abc" };
    },
  );
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), "https://files.example/certificate.pdf?sig=abc");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(asked, [
    {
      path: "/v1/partners/a%2Fb/earnings/certificate",
      searchParams: { from: "2026-05", to: "2026-10" },
    },
  ]);
});

test("without months the route asks for none, and the mock API's link is a PDF of this site", async () => {
  const response = await certificateRedirect(PARTNER_ID.lata, new URLSearchParams(), mockApi);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), "/mock-income-certificate.pdf");
});

test("a refusal is a short sentence, not the raw problem", async () => {
  const cases = /** @type {[string, number, RegExp][]} */ ([
    ["partners.partner_not_found", 404, /not found/],
    ["auth.forbidden", 403, /own hub/],
    ["partners.certificate_period_invalid", 422, /12 months/],
    ["partners.certificate_rate_limited", 429, /Too many/],
    ["platform.storage_unavailable", 503, /Try again later/],
    ["something.new", 500, /Could not prepare/],
  ]);
  for (const [code, status, text] of cases) {
    const response = await certificateRedirect("x", new URLSearchParams(), async () => {
      throw problem(status === 500 ? 502 : status, code);
    });
    assert.equal(response.status, status, code);
    assert.match(await response.text(), text, code);
    assert.equal(response.headers.get("location"), null);
  }
});

test("a link that is not a web address is not followed", async () => {
  for (const url of [
    "javascript:alert(1)",
    "data:application/pdf;base64,AAAA",
    "//evil.example/x",
    undefined,
  ]) {
    const response = await certificateRedirect("x", new URLSearchParams(), async () => ({ url }));
    assert.equal(response.status, 500, String(url));
    assert.equal(response.headers.get("location"), null);
  }
});

test("a thrown Next.js redirect (sign in again) is passed on, other failures are a 500", async () => {
  const redirect = Object.assign(new Error("NEXT_REDIRECT"), {
    digest: "NEXT_REDIRECT;replace;/v1/auth/session",
  });
  await assert.rejects(
    certificateRedirect("x", new URLSearchParams(), async () => {
      throw redirect;
    }),
    redirect,
  );
  const down = await certificateRedirect("x", new URLSearchParams(), async () => {
    throw new TypeError("fetch failed");
  });
  assert.equal(down.status, 500);
});
