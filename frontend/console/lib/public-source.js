import { isMockApiIn } from "./sign-in.js";

/**
 * Where the (public) pages read from, kept apart from lib/public-api.js so it can be tested without
 * Next. `GB_PUBLIC_API_MOCK` switches the public pages on their own and defaults to `GB_API_MOCK`, so
 * a deploy can show real societies behind invite links while the staff screens stay on demo data.
 * With neither set, Vercel's production deployment reads the real API (lib/sign-in.js).
 *
 * @param {Record<string, string | undefined>} env usually `process.env`
 * @returns {string | null} the API origin to call, or null to answer from the mocks
 */
export function publicApiOrigin(env) {
  if (isPublicMock(env)) return null;
  const origin = (env.GB_API_URL ?? "").replace(/\/$/, "");
  if (!origin)
    throw new Error("GB_API_URL is not set (needed when the public pages use the real API)");
  return origin;
}

/**
 * Whether the public pages answer from the mocks. The landing page uses it to link to the mock
 * cook only in mock mode: a real cook page exists only once a kitchen turns hers on (M8 §3.5).
 * @param {Record<string, string | undefined>} env usually `process.env`
 */
export function isPublicMock(env) {
  if (env.GB_PUBLIC_API_MOCK !== undefined) return env.GB_PUBLIC_API_MOCK !== "false";
  return isMockApiIn(env);
}
