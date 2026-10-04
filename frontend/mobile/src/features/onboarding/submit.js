import { request } from "@/lib/api.js";

const AGE_BAND = { adults: "18_59", seniors: "60_plus", children: "5_17", little: "under_5" };
const CONSENT_VERSION = "v1";

/** One Idempotency-Key per onboarding, so retrying "Agree and continue" never makes two households. */
function idempotencyKey() {
  const k = "gb-onboarding-key";
  let key = sessionStorage.getItem(k);
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem(k, key);
  }
  return key;
}

/**
 * Creates the household from the onboarding draft (M1): household → address → consents →
 * members. Consents go before members because the API refuses health tags without consent.
 * Health tags are sent only when health personalisation is consented to.
 * @param {import("./draft.js").OnboardingDraft} draft
 * @returns {Promise<{ id: string }>} the household
 */
export async function submitOnboarding(draft) {
  const h = draft.household;
  const members = /** @type {(keyof typeof AGE_BAND)[]} */ (Object.keys(AGE_BAND)).flatMap(
    (group) =>
      Array.from({ length: h.members[group] }, (_, i) => ({ key: `${group}-${i + 1}`, group })),
  );

  const household = /** @type {{ id: string }} */ (
    await request("/v1/households", {
      method: "POST",
      headers: { "idempotency-key": idempotencyKey() },
      body: {
        society_id: draft.societyId,
        size: members.length,
        diet: h.diet,
        cuisine_tags: h.cuisines,
        rules: { jain: h.jain, no_onion_some_days: h.noOnionSomeDays },
        weekday_cook_minutes: h.weekdayCookMinutes,
        weekend_cook_minutes: h.weekendCookMinutes,
        no_cook_days: h.noCookDays,
        budget_paise: h.budgetLockPaise,
      },
    })
  );

  await request("/v1/addresses", {
    method: "POST",
    body: {
      household_id: household.id,
      society_id: draft.societyId,
      tower: draft.towerId,
      flat: draft.flat.trim(),
    },
  });

  const consents = {
    account: true,
    health_personalisation: h.healthConsent,
    whatsapp_updates: h.whatsappConsent,
    society_insights: h.insightsConsent,
  };
  for (const [purpose, granted] of Object.entries(consents)) {
    await request("/v1/consents", {
      method: "POST",
      body: { purpose, version: CONSENT_VERSION, granted },
    });
  }

  for (const m of members) {
    await request(`/v1/households/${household.id}/members`, {
      method: "POST",
      body: {
        age_band: AGE_BAND[m.group],
        health_tags: h.healthConsent ? (h.health[m.key] ?? []) : [],
        fasting_rules:
          m.group === "adults" || m.group === "seniors" ? { days: h.fasting } : { days: [] },
      },
    });
  }

  sessionStorage.removeItem("gb-onboarding-key");
  return household;
}
