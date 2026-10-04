import { ApiError, request } from "@/lib/api.js";
import { CONSENT_VERSION, hasConsent, memberKeys } from "./draft.js";

/** @type {Record<import("./draft.js").MemberGroup, string>} */
const AGE_BAND = { adults: "18_59", seniors: "60_plus", children: "5_17", little: "under_5" };

/** Every consent purpose, sent explicitly granted or not (DPDP): the two the app needs are always true. */
const PURPOSES = /** @type {const} */ ([
  "service_delivery",
  "personalisation",
  "health_personalisation",
  "marketing",
  "research_anonymised",
]);

/**
 * The POST /v1/households body (HouseholdIn, M1 §3, contract #56): household, address, members and
 * consents in one atomic call, so a failure halfway can never leave a household without its
 * members or consents. Health tags go only with health_personalisation.
 * @param {import("./draft.js").OnboardingDraft} draft
 */
export function householdBody(draft) {
  const h = draft.household;
  const health = hasConsent(h, "health_personalisation");
  // Fasting and Jain rules are household-wide in the app; little children never fast.
  const rules = {
    jain: h.jain,
    fasts: h.fasting,
    no_onion_garlic_on_fast_days: h.noOnionSomeDays,
  };
  const members = memberKeys(h.members).map((key) => {
    const group = /** @type {import("./draft.js").MemberGroup} */ (key.split("-")[0]);
    return {
      age_band: AGE_BAND[group],
      fasting_rules: group === "little" ? { ...rules, fasts: [] } : rules,
      health_tags: health ? (h.health[key] ?? []) : [],
    };
  });
  return {
    society_id: draft.societyId,
    diet: h.diet,
    cuisine_tags: h.cuisines,
    weekday_cook_minutes: h.weekdayCookMinutes,
    weekend_cook_minutes: h.weekendCookMinutes,
    no_cook_days: h.noCookDays,
    budget_paise: h.budgetLockPaise,
    address: { tower_id: draft.towerId, flat: draft.flat.trim().toUpperCase(), location: null },
    members,
    consents: PURPOSES.map((purpose) => ({
      purpose,
      version: CONSENT_VERSION,
      granted:
        purpose === "service_delivery" || purpose === "personalisation" || hasConsent(h, purpose),
    })),
  };
}

/**
 * The Idempotency-Key comes from the answers themselves: tapping "Agree and continue" again with
 * the same answers replays the first result, and changed answers get a new key instead of
 * 422 request.idempotency_mismatch forever.
 * @param {unknown} body
 */
async function keyFor(body) {
  const bytes = new TextEncoder().encode(JSON.stringify(body));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `household:${Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * Creates the household from the onboarding draft in one call (M1). An existing household
 * (409 identity.household_exists, e.g. a retry after a lost response) counts as done.
 * @param {import("./draft.js").OnboardingDraft} draft
 * @returns {Promise<{ id: string }>} the household
 */
export async function submitOnboarding(draft) {
  const body = householdBody(draft);
  try {
    return /** @type {{ id: string }} */ (
      await request("/v1/households", {
        method: "POST",
        headers: { "idempotency-key": await keyFor(body) },
        body,
      })
    );
  } catch (e) {
    if (e instanceof ApiError && e.code === "identity.household_exists" && e.extra?.household_id)
      return { id: String(e.extra.household_id) };
    throw e;
  }
}
