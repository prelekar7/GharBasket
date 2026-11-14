import { getResponse } from "msw";
import { cacheLife } from "next/cache";
import { publicApiOrigin } from "./public-source.js";

/**
 * Anonymous reads for the (public) pages. Unlike lib/api.js these never touch cookies or request
 * headers, so the results can sit in the Next cache and the pages stay fast. The visitor's language
 * is passed in explicitly (it becomes `Accept-Language`, which the API answers with localised text).
 * Mock mode answers from mocks/public.js; real mode calls `GB_API_URL`. The public pages have their
 * own switch, `GB_PUBLIC_API_MOCK` (defaults to `GB_API_MOCK`; see lib/public-source.js). There is no
 * public list of societies or cooks in v1: a visitor reaches a society page from its invite link, and a
 * cook's page from the link she shares.
 *
 * @typedef {import("../mocks/public.js").PublicSociety} PublicSociety
 * @typedef {import("../mocks/public.js").PublicCook} PublicCook
 * @typedef {"en" | "hi"} Lang
 */

const MOCK_ORIGIN = "http://mock.gharbasket.local";

/**
 * @param {string} path
 * @param {Lang} lang
 * @param {string | null} apiOrigin the real API, or null to answer from the mocks
 * @returns {Promise<any | null>} the parsed body, or null when the API says 404
 */
async function publicGet(path, lang, apiOrigin) {
  const mock = apiOrigin === null;
  const origin = apiOrigin ?? MOCK_ORIGIN;
  const request = new Request(origin + path, {
    headers: { accept: "application/json", "accept-language": lang, "x-gb-client": "web" },
  });
  let response;
  if (mock) {
    const { handlers } = await import("../mocks/index.js");
    response = await getResponse(handlers, request);
    if (!response) throw new Error(`No mock handler for GET ${path} (see mocks/README.md)`);
  } else {
    response = await fetch(request);
  }
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`GET ${path} failed with ${response.status}`);
  return response.json();
}

/**
 * One society and its unlock progress (M12 GET /v1/public/societies/{slug}); null when unknown.
 * @param {string} slug
 * @param {Lang} lang
 * @returns {Promise<PublicSociety | null>}
 */
export async function getSociety(slug, lang) {
  "use cache";
  cacheLife("minutes");
  return publicGet(
    `/v1/public/societies/${encodeURIComponent(slug)}`,
    lang,
    publicApiOrigin(process.env),
  );
}

/**
 * The public cook page (M8 §3.5 GET /v1/public/cooks/{handle}); null when the handle is unknown, she
 * has not agreed to a public page, or her kitchen is not active or paused (one 404 for all three).
 * Follows the same switch as the society page.
 * @param {string} handle
 * @param {Lang} lang
 * @returns {Promise<PublicCook | null>}
 */
export async function getCook(handle, lang) {
  "use cache";
  // A withdrawn page must go within about 20 minutes (M8 §3.5); the "minutes" profile can keep it for an hour.
  cacheLife({ stale: 60, revalidate: 60, expire: 300 });
  return publicGet(
    `/v1/public/cooks/${encodeURIComponent(handle)}`,
    lang,
    publicApiOrigin(process.env),
  );
}
