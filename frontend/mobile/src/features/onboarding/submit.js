import { flatLabel } from "@/features/identity/address.js";
import { request } from "@/lib/api.js";
import { toMembers } from "./members.js";

/** The current consent notice version, the same for every purpose (M1 §3.2). */
const CONSENT_VERSION = "v1";
const KEY = "gb-onboarding-key";

/**
 * The consents to record, as the API's purposes (M1 §3.2). The screen's "Account and delivery"
 * card is `service_delivery` and `personalisation`, both required and always on. Its three
 * optional switches are `health_personalisation` (health tags), `marketing` (WhatsApp updates:
 * box ready, payment and delivery notices) and `research_anonymised` ("improve plans for my
 * society": anonymised use of edits). Only the ones said yes to are sent: never given is the same
 * as no record.
 * @param {import("./draft.js").HouseholdDraft} h
 */
export function consentsOf(h) {
  const granted = ["service_delivery", "personalisation"];
  if (h.healthConsent) granted.push("health_personalisation");
  if (h.whatsappConsent) granted.push("marketing");
  if (h.insightsConsent) granted.push("research_anonymised");
  return granted.map((purpose) => ({ purpose, version: CONSENT_VERSION, granted: true }));
}

/**
 * The body of POST /v1/households (HouseholdIn): everything the steps collected, in one request.
 * @param {import("./draft.js").OnboardingDraft} draft
 */
export function householdBody(draft) {
  const h = draft.household;
  return {
    society_id: draft.societyId,
    address: {
      tower_id: draft.towerId,
      flat: flatLabel(draft.towerId ?? "", draft.flat),
      // No map pin in the app yet; the API accepts null.
      location: null,
    },
    diet: h.diet,
    cuisine_tags: h.cuisines,
    weekday_cook_minutes: h.weekdayCookMinutes,
    weekend_cook_minutes: h.weekendCookMinutes,
    no_cook_days: h.noCookDays,
    budget_paise: h.budgetLockPaise,
    members: toMembers(h),
    consents: consentsOf(h),
  };
}

/**
 * One Idempotency-Key per onboarding attempt, so retrying "Agree and continue" never makes two
 * households. A key must not be reused with a different body (422 request.idempotency_mismatch),
 * so editing the draft after a failed attempt gets a new one.
 * @param {string} fingerprint the body, as text
 */
function idempotencyKey(fingerprint) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (saved?.fingerprint === fingerprint) return saved.key;
  } catch {
    // an unreadable saved key is as good as none
  }
  const key = crypto.randomUUID();
  sessionStorage.setItem(KEY, JSON.stringify({ key, fingerprint }));
  return key;
}

/**
 * Creates the household from the onboarding draft: ONE call that writes the household, its
 * members, the address and the consents together (M1 §3.2).
 * @param {import("./draft.js").OnboardingDraft} draft
 * @returns {Promise<import("@/mocks/householdStore.js").Household>} the household
 */
export async function submitOnboarding(draft) {
  const body = householdBody(draft);
  const household = /** @type {import("@/mocks/householdStore.js").Household} */ (
    await request("/v1/households", {
      method: "POST",
      headers: { "idempotency-key": idempotencyKey(JSON.stringify(body)) },
      body,
    })
  );
  sessionStorage.removeItem(KEY);
  return household;
}
