/**
 * A neighbour's invite opens `/join?s=<society slug>` (the console's society page, M12 §3.7; the
 * API's `join_url`). The slug is kept in the onboarding draft until the society step, because the
 * person usually signs in first and the role step does not keep `?next`.
 *
 * The society step looks the slug up with GET /v1/societies?slug= (M9 §3.1) and keeps only a result
 * with exactly that slug. Referral codes (`?ref=`, M9 §3.3) are not read yet.
 */

/** A society slug as M9 makes them: lowercase words joined by single hyphens. */
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * `?s=` of /join: a valid slug (trimmed, lowercased, at most 80 characters) or nothing; anything
 * else is dropped, never trusted. Plain code, not zod: a route's search check is part of the entry
 * chunk (see lib/search.js).
 * @param {Record<string, unknown>} search
 * @returns {{ s?: string }}
 */
export function joinSearch(search) {
  const s = typeof search.s === "string" ? search.s.trim().toLowerCase() : "";
  return s.length <= 80 && SLUG.test(s) ? { s } : {};
}

/**
 * The invited society among the results: only an exact slug match counts, so a near miss never
 * signs someone up to the wrong society.
 * @template {{ slug: string }} T
 * @param {T[]} items @param {string | null} slug
 * @returns {T | null}
 */
export const invitedSociety = (items, slug) =>
  slug ? (items.find((s) => s.slug === slug) ?? null) : null;
