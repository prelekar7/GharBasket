/**
 * Where the (public) pages read from, kept apart from lib/public-api.js so it can be tested without
 * Next. `GB_PUBLIC_API_MOCK` switches the public pages on their own and defaults to `GB_API_MOCK`, so
 * a deploy can show real societies behind invite links while the staff screens stay on demo data
 * (the console's staff sign-in is not built yet, handbook 12 §12.2).
 *
 * @param {Record<string, string | undefined>} env usually `process.env`
 * @returns {string | null} the API origin to call, or null to answer from the mocks
 */
export function publicApiOrigin(env) {
  const flag = env.GB_PUBLIC_API_MOCK ?? env.GB_API_MOCK;
  if (flag !== "false") return null;
  const origin = (env.GB_API_URL ?? "").replace(/\/$/, "");
  if (!origin)
    throw new Error("GB_API_URL is not set (needed when the public pages use the real API)");
  return origin;
}
