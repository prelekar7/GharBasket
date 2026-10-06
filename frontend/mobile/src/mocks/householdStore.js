// The household a phone has set up, kept across reloads like a server's rows (M1 households,
// members, addresses and consents, M3 subscription). Shared by the onboarding mock (identity.js)
// and Profile (profile.js). Shapes are exactly the API's: HouseholdOut, SubscriptionOut, ConsentOut.

const KEY = "gb-mock-identity";

/** The current consent notice version, the same for every purpose (M1 §3.2). */
export const CONSENT_VERSION = "v1";
export const PURPOSES = [
  "service_delivery",
  "personalisation",
  "health_personalisation",
  "marketing",
  "research_anonymised",
];

/**
 * @typedef {{ jain: boolean, fasts: string[], no_onion_garlic_on_fast_days: boolean }} FastingRules
 * @typedef {{ id: string, age_band: "under_5" | "5_17" | "18_59" | "60_plus", sex: "f" | "m" | "other" | null,
 *   fasting_rules: FastingRules, health_tags: string[] }} Member
 * @typedef {{ id: string, household_id: string, society_id: string, tower_id: string, flat: string,
 *   location: { lat: number, lng: number } | null }} Address
 * @typedef {{ id: string, version: number, society_id: string, hub_id: string | null, size: number,
 *   diet: "veg" | "egg" | "nonveg", cuisine_tags: string[], weekday_cook_minutes: number,
 *   weekend_cook_minutes: number, no_cook_days: number[], budget_paise: number | null,
 *   autopilot_enabled: boolean, auto_approve: boolean, members: Member[], address: Address,
 *   created_at: string }} Household
 * @typedef {{ id: string, household_id: string, plan: "autopilot" | "tiffin" | "salad",
 *   status: "active" | "paused" | "ended", payment_mode: "wallet" | "autopay",
 *   paused_until: string | null, auto_approve: boolean, started_at: string }} Subscription
 * @typedef {{ purpose: string, version: string, granted: boolean, granted_at: string | null,
 *   revoked_at: string | null }} Consent
 * @typedef {{ household: Household, subscription: Subscription | null, consents: Consent[] }} Account
 */

/** A stable UUID-shaped id for the demo rows. @param {number} n */
export const demoId = (n) => `0190f0aa-0000-7000-8000-${String(n).padStart(12, "0")}`;

/** One entry per purpose, like GET /v1/consents. @param {string[]} granted purposes said yes to @returns {Consent[]} */
export const consentsFor = (granted) =>
  PURPOSES.map((purpose) => ({
    purpose,
    version: CONSENT_VERSION,
    granted: granted.includes(purpose),
    granted_at: granted.includes(purpose) ? "2026-09-20T10:00:00Z" : null,
    revoked_at: null,
  }));

/** @param {string[]} [fasts] @returns {FastingRules} */
const rules = (fasts = []) => ({ jain: false, fasts, no_onion_garlic_on_fast_days: false });

/** The designs' family of four in Sunrise Towers, for the demo households. @returns {Account} */
export const demoAccount = () => ({
  household: {
    id: demoId(100),
    version: 1,
    society_id: "0199a000-0000-7000-8000-00000000b001",
    hub_id: null,
    size: 4,
    diet: "veg",
    cuisine_tags: ["maharashtrian", "gujarati"],
    weekday_cook_minutes: 30,
    weekend_cook_minutes: 60,
    no_cook_days: [],
    budget_paise: 300000,
    autopilot_enabled: true,
    auto_approve: false,
    members: [
      {
        id: demoId(101),
        age_band: "18_59",
        sex: null,
        fasting_rules: rules(["shravan_mondays"]),
        health_tags: [],
      },
      {
        id: demoId(102),
        age_band: "18_59",
        sex: null,
        fasting_rules: rules(["shravan_mondays"]),
        health_tags: ["diabetes"],
      },
      { id: demoId(103), age_band: "5_17", sex: null, fasting_rules: rules(), health_tags: [] },
      { id: demoId(104), age_band: "5_17", sex: null, fasting_rules: rules(), health_tags: [] },
    ],
    address: {
      id: demoId(105),
      household_id: demoId(100),
      society_id: "0199a000-0000-7000-8000-00000000b001",
      tower_id: "b",
      flat: "B-402",
      location: null,
    },
    created_at: "2026-09-20T10:00:00Z",
  },
  subscription: {
    id: demoId(106),
    household_id: demoId(100),
    plan: "autopilot",
    status: "active",
    payment_mode: "wallet",
    paused_until: null,
    auto_approve: false,
    started_at: "2026-09-20T10:00:00Z",
  },
  consents: consentsFor(["service_delivery", "personalisation", "health_personalisation"]),
});

/** @returns {Record<string, Account>} by phone */
const all = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");

/** @param {string} phone @returns {Account | null} */
export const accountOf = (phone) => all()[phone] ?? null;

/** @param {string} phone @param {Account} account */
export function saveAccount(phone, account) {
  localStorage.setItem(KEY, JSON.stringify({ ...all(), [phone]: account }));
}

/** @param {string} phone @returns {Household | null} */
export const householdOf = (phone) => accountOf(phone)?.household ?? null;

/** @param {string} phone @param {Household} household */
export function saveHousehold(phone, household) {
  const account = accountOf(phone);
  if (account) saveAccount(phone, { ...account, household });
}
