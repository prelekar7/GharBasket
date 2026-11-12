import { z } from "zod";

/**
 * A neighbour's invite opens `/join?s=<society slug>` (the console's society page, M12 §3.7; the
 * API's `join_url`). The slug is kept in the onboarding draft until the society step, because the
 * person usually signs in first and the role step does not keep `?next`.
 *
 * The signed-in API has no lookup by slug, so the society step searches with the slug's words and
 * keeps only a result with exactly that slug. Referral codes (`?ref=`, M9 §3.3) are not read yet.
 */

/** A society slug as M9 makes them: lowercase words joined by single hyphens. */
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** `?s=` of /join: a valid slug or nothing (anything else is dropped, never trusted). */
export const joinSearch = z.object({
  s: z.string().trim().toLowerCase().max(80).regex(SLUG).optional().catch(undefined),
});

/** "sunrise-towers-kharghar" → "sunrise towers kharghar": the words a society search finds it by. @param {string} slug */
export const slugWords = (slug) => slug.split("-").join(" ");

/**
 * The invited society among search results: only an exact slug match counts, so a near miss never
 * signs someone up to the wrong society.
 * @template {{ slug: string }} T
 * @param {T[]} items @param {string | null} slug
 * @returns {T | null}
 */
export const invitedSociety = (items, slug) =>
  slug ? (items.find((s) => s.slug === slug) ?? null) : null;
