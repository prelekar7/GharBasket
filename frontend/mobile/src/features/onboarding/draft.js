import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Household profile for Autopilot (M1 households / household_members). Codes, not display text.
 * @typedef {{ adults: number, seniors: number, children: number, little: number }} Members
 * @typedef {object} HouseholdDraft
 * @property {Members} members
 * @property {"veg" | "egg" | "nonveg"} diet
 * @property {string[]} cuisines e.g. "maharashtrian"
 * @property {boolean} jain
 * @property {boolean} noOnionSomeDays
 * @property {string[]} fasting e.g. "shravan_mondays", "ekadashi"
 * @property {boolean} healthConsent DPDP: health tags are used only when this is true
 * @property {Record<string, string[]>} health tags per member key ("adult-1")
 * @property {number} weekdayCookMinutes
 * @property {number} weekendCookMinutes
 * @property {number[]} noCookDays 0 = Monday … 6 = Sunday
 * @property {number | null} budgetLockPaise monthly vegetable budget for Budget Lock (M3); null = off
 * @property {boolean} whatsappConsent optional DPDP consent for WhatsApp updates
 * @property {boolean} insightsConsent optional DPDP consent to improve plans for the society (anonymised)
 */

/** @type {HouseholdDraft} */
const EMPTY_HOUSEHOLD = {
  members: { adults: 2, seniors: 0, children: 0, little: 0 },
  diet: "veg",
  cuisines: [],
  jain: false,
  noOnionSomeDays: false,
  fasting: [],
  healthConsent: false,
  health: {},
  weekdayCookMinutes: 30,
  weekendCookMinutes: 60,
  noCookDays: [],
  budgetLockPaise: 300000,
  whatsappConsent: false,
  insightsConsent: false,
};

/**
 * What a new household has told us so far. Kept on the device (sessionStorage) until the last
 * onboarding step creates the household and address in one go (M1).
 * @typedef {object} OnboardingDraft
 * @property {string | null} societyId
 * @property {string | null} towerId
 * @property {string} flat
 * @property {HouseholdDraft} household
 * @property {(patch: Partial<Pick<OnboardingDraft, "societyId" | "towerId" | "flat">>) => void} update
 * @property {(patch: Partial<HouseholdDraft>) => void} updateHousehold
 * @property {() => void} reset
 */

export const useOnboardingDraft = create(
  /** @type {import("zustand").StateCreator<OnboardingDraft, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set) => ({
        societyId: null,
        towerId: null,
        flat: "",
        household: EMPTY_HOUSEHOLD,
        update: (patch) => set(patch),
        updateHousehold: (patch) => set((s) => ({ household: { ...s.household, ...patch } })),
        reset: () => set({ societyId: null, towerId: null, flat: "", household: EMPTY_HOUSEHOLD }),
      }),
      { name: "gb-onboarding", storage: createJSONStorage(() => sessionStorage) },
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
