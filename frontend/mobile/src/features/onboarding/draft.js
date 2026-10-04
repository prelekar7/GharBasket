import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Household profile for Autopilot (M1 households / household_members). Codes, not display text.
 * @typedef {{ adults: number, seniors: number, children: number, little: number }} Members
 * @typedef {keyof Members} MemberGroup
 * @typedef {object} HouseholdDraft
 * @property {Members} members
 * @property {"veg" | "egg" | "nonveg"} diet
 * @property {string[]} cuisines e.g. "maharashtrian"
 * @property {boolean} jain
 * @property {boolean} noOnionSomeDays
 * @property {string[]} fasting e.g. "shravan_mondays", "ekadashi"
 * @property {Consents} consents DPDP: what the person agreed to, as codes (health tags need health_personalisation)
 * @property {Record<string, string[]>} health tags per member key ("adults-1"; see memberKeys)
 * @property {number} weekdayCookMinutes
 * @property {number} weekendCookMinutes
 * @property {number[]} noCookDays 0 = Monday … 6 = Sunday
 * @property {number | null} budgetLockPaise monthly vegetable budget for Budget Lock (M3); null = off
 */

/** @type {MemberGroup[]} */
export const MEMBER_GROUPS = ["adults", "seniors", "children", "little"];

/** People in the household as "adults-1", "seniors-1"… from the Family step's counts. @param {Members} members */
export const memberKeys = (members) =>
  MEMBER_GROUPS.flatMap((g) => Array.from({ length: members[g] }, (_, i) => `${g}-${i + 1}`));

/**
 * Health tags only for people still in the household: removing someone in the Family step must
 * not leave their health data behind (DPDP).
 * @param {Record<string, string[]>} health @param {Members} members
 */
export const pruneHealth = (health, members) => {
  const keep = new Set(memberKeys(members));
  return Object.fromEntries(Object.entries(health).filter(([key]) => keep.has(key)));
};

/**
 * Consent purposes (M1 §3, contract #56). service_delivery and personalisation are what the app
 * needs to run; the other three are optional switches.
 * @typedef {"service_delivery" | "personalisation" | "health_personalisation" | "marketing" | "research_anonymised"} ConsentPurpose
 * @typedef {{ purpose: ConsentPurpose, version: string, granted_at: string }} ConsentGrant
 * @typedef {Partial<Record<ConsentPurpose, ConsentGrant>>} Consents only granted purposes are present
 */

/** The privacy notice version the app shows; the server answers 409 consent_version_stale if it moved on. */
export const CONSENT_VERSION = "v1";
export const REQUIRED_PURPOSES = /** @type {const} */ (["service_delivery", "personalisation"]);

/** @param {ConsentPurpose} purpose @returns {ConsentGrant} */
export const grantOf = (purpose) => ({
  purpose,
  version: CONSENT_VERSION,
  granted_at: new Date().toISOString(),
});

/** @param {HouseholdDraft} h @param {ConsentPurpose} purpose */
export const hasConsent = (h, purpose) => !!h.consents[purpose];

/** @type {HouseholdDraft} */
const EMPTY_HOUSEHOLD = {
  members: { adults: 2, seniors: 0, children: 0, little: 0 },
  diet: "veg",
  cuisines: [],
  jain: false,
  noOnionSomeDays: false,
  fasting: [],
  consents: {},
  health: {},
  weekdayCookMinutes: 30,
  weekendCookMinutes: 60,
  noCookDays: [],
  budgetLockPaise: 300000,
};

/**
 * What a new household has told us so far. Kept on the device (sessionStorage) until the last
 * onboarding step creates the household and address in one go (M1).
 * @typedef {object} OnboardingDraft
 * @property {string | null} societyId
 * @property {string | null} towerId
 * @property {string} flat
 * @property {string | null} householdId set once POST /v1/households has succeeded
 * @property {HouseholdDraft} household
 * @property {(patch: Partial<Pick<OnboardingDraft, "societyId" | "towerId" | "flat" | "householdId">>) => void} update
 * @property {(patch: Partial<HouseholdDraft>) => void} updateHousehold
 * @property {(purpose: ConsentPurpose, granted: boolean) => void} setConsent withdrawing health_personalisation also erases every health tag
 * @property {() => void} reset
 */

export const useOnboardingDraft = create(
  /** @type {import("zustand").StateCreator<OnboardingDraft, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set) => ({
        societyId: null,
        towerId: null,
        flat: "",
        householdId: null,
        household: EMPTY_HOUSEHOLD,
        update: (patch) => set(patch),
        updateHousehold: (patch) =>
          set((s) => {
            const household = { ...s.household, ...patch };
            if (patch.members) household.health = pruneHealth(household.health, patch.members);
            return { household };
          }),
        setConsent: (purpose, granted) =>
          set((s) => {
            const consents = { ...s.household.consents };
            if (granted) consents[purpose] ??= grantOf(purpose);
            else delete consents[purpose];
            const health =
              purpose === "health_personalisation" && !granted ? {} : s.household.health;
            return { household: { ...s.household, consents, health } };
          }),
        reset: () =>
          set({
            societyId: null,
            towerId: null,
            flat: "",
            householdId: null,
            household: EMPTY_HOUSEHOLD,
          }),
      }),
      {
        name: "gb-onboarding",
        storage: createJSONStorage(() => sessionStorage),
        version: 1,
        // v0 kept consents as booleans (healthConsent, whatsappConsent, insightsConsent).
        migrate: (/** @type {any} */ old) => {
          const h = old?.household;
          if (!h || h.consents) return old;
          const { healthConsent, whatsappConsent, insightsConsent, ...rest } = h;
          /** @type {Consents} */
          const consents = {};
          if (healthConsent) consents.health_personalisation = grantOf("health_personalisation");
          if (whatsappConsent) consents.marketing = grantOf("marketing");
          if (insightsConsent) consents.research_anonymised = grantOf("research_anonymised");
          return { ...old, household: { ...rest, consents } };
        },
      },
    )
  ),
);

/** Onboarding screens in order; later PRs add health, cooking, budget, consent and payment. */
export const ONBOARDING_STEPS = [
  "/onboarding/society",
  "/onboarding/flat",
  "/onboarding/family",
  "/onboarding/food",
  "/onboarding/health",
  "/onboarding/cooking",
  "/onboarding/budget",
  "/onboarding/consent",
  "/onboarding/preview",
  "/onboarding/payment",
  "/onboarding/done",
];

/** The household steps that show "Step N of 5" (designs Hf07–Hf11). */
export const HOUSEHOLD_STEPS = 5;

/**
 * The screen after `path`, or home when onboarding is done. Throws for a path missing from
 * ONBOARDING_STEPS, so a forgotten step fails loudly instead of sending people back to the start.
 * @param {string} path
 */
export function nextStep(path) {
  const i = ONBOARDING_STEPS.indexOf(path);
  if (i < 0) throw new Error(`${path} is not in ONBOARDING_STEPS`);
  return ONBOARDING_STEPS[i + 1] ?? "/";
}
