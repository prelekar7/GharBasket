import { getResponse } from "msw";
import { cacheLife } from "next/cache";

/**
 * Anonymous reads for the (public) pages. Unlike lib/api.js these never touch cookies or request
 * headers, so the results can sit in the Next cache and the pages stay fast. The visitor's language
 * is passed in explicitly (it becomes `Accept-Language`, which the API answers with localised text).
 * Mock mode answers from mocks/public.js; real mode calls `GB_API_URL`. There is no public list of
 * societies in v1: a visitor reaches a society page from its invite link.
 *
 * @typedef {import("../mocks/public.js").PublicSociety} PublicSociety
 * @typedef {import("../mocks/public.js").PublicCook} PublicCook
 * @typedef {"en" | "hi"} Lang
 */

const MOCK_ORIGIN = "http://mock.gharbasket.local";

/**
 * @param {string} path
 * @param {Lang} lang
 * @returns {Promise<any | null>} the parsed body, or null when the API says 404
 */
async function publicGet(path, lang) {
  const mock = process.env.GB_API_MOCK !== "false";
  const origin = mock ? MOCK_ORIGIN : (process.env.GB_API_URL ?? "").replace(/\/$/, "");
  if (!mock && !origin) throw new Error("GB_API_URL is not set (needed when GB_API_MOCK=false)");
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
  return publicGet(`/v1/public/societies/${encodeURIComponent(slug)}`, lang);
}

/**
 * The know-your-cook profile; null when unknown or when the cook has not agreed to a public one.
 * @param {string} id
 * @param {Lang} lang
 * @returns {Promise<PublicCook | null>}
 */
export async function getCook(id, lang) {
  "use cache";
  cacheLife("minutes");
  return publicGet(`/v1/public/cooks/${encodeURIComponent(id)}`, lang);
}
