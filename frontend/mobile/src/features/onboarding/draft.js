import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { fromMembers } from "./members.js";

/**
 * Household profile for Autopilot (M1 households / household_members). Codes, not display text.
 * @typedef {{ adults: number, seniors: number, children: number, little: number }} Members
 * @typedef {object} HouseholdDraft
 * @property {Members} members
 * @property {"veg" | "egg" | "nonveg"} diet
 * @property {string[]} cuisines e.g. "maharashtrian"
 * @property {boolean} jain household rule; on every member's `fasting_rules.jain`, needs diet "veg"
 * @property {boolean} noOnionSomeDays on every member's `fasting_rules.no_onion_garlic_on_fast_days`
 * @property {string[]} fasting e.g. "shravan_mondays", "ekadashi"; on each adult's and senior's `fasting_rules.fasts`
 * @property {boolean} healthConsent DPDP: health tags are used only when this is true
 * @property {Record<string, string[]>} health tags per member key ("adults-1"; see memberKeys)
 * @property {number} weekdayCookMinutes
 * @property {number} weekendCookMinutes
 * @property {number[]} noCookDays 0 = Monday … 6 = Sunday
 * @property {number | null} budgetLockPaise monthly vegetable budget for Budget Lock (M3); null = off
 * @property {boolean} whatsappConsent optional DPDP consent for WhatsApp updates: purpose `marketing`
 * @property {boolean} insightsConsent optional DPDP consent to improve plans for the society (anonymised): purpose `research_anonymised`
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

/** @type {(keyof Members)[]} */
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

/** Health tags and consents: never kept past the tab (DPDP), so a resumed draft asks for them again. */
const SENSITIVE = ["health", "healthConsent", "whatsappConsent", "insightsConsent"];
const STORAGE_KEY = "gb-onboarding";

/** @param {Storage} store @param {string} name */
const read = (store, name) => {
  try {
    return JSON.parse(store.getItem(name) ?? "null");
  } catch {
    return null;
  }
};
/** @param {Record<string, unknown>} obj @param {boolean} sensitive */
const pick = (obj, sensitive) =>
  Object.fromEntries(Object.entries(obj).filter(([k]) => SENSITIVE.includes(k) === sensitive));

/**
 * The draft is split in two. What is not sensitive (society, flat, family, food, cooking, budget,
 * and how far the person got) lives in localStorage, so it survives Android killing the app or the
 * tab closing. The sensitive part lives in sessionStorage. Both are removed on sign-out
 * (lib/localData.js).
 * @type {import("zustand/middleware").StateStorage}
 */
const splitStorage = {
  getItem: (name) => {
    const durable = read(localStorage, name);
    if (!durable) return null;
    const session = read(sessionStorage, name);
    return JSON.stringify({
      ...durable,
      state: {
        ...durable.state,
        household: { ...durable.state.household, ...session?.state?.household },
      },
    });
  },
  setItem: (name, value) => {
    const { state, version } = JSON.parse(value);
    try {
      localStorage.setItem(
        name,
        JSON.stringify({ state: { ...state, household: pick(state.household, false) }, version }),
      );
      sessionStorage.setItem(
        name,
        JSON.stringify({ state: { household: pick(state.household, true) }, version }),
      );
    } catch {
      // Storage blocked (private mode): the draft just isn't kept.
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
      sessionStorage.removeItem(name);
    } catch {
      // Nothing was kept.
    }
  },
};

/**
 * What a new household has told us so far. Kept on the device until the last onboarding step
 * creates the household, with its members, address and consents, in one call (M1); see `splitStorage` for what is kept where.
 * @typedef {object} OnboardingDraft
 * @property {string | null} societyId
 * @property {string | null} towerId
 * @property {string} flat
 * @property {number} reached index in ONBOARDING_STEPS of the furthest screen opened
 * @property {HouseholdDraft} household
 * @property {(path: string) => void} markReached
 * @property {(patch: Partial<Pick<OnboardingDraft, "societyId" | "towerId" | "flat">>) => void} update
 * @property {(patch: Partial<HouseholdDraft>) => void} updateHousehold
 * @property {() => void} reset
 * @property {{ id: string, version: number } | null} editing set while Profile edits a saved household
 * @property {(household: import("@/features/identity/profile.js").Household, healthConsent: boolean) => void} startEdit
 * @property {() => void} stopEdit
 */

export const useOnboardingDraft = create(
  /** @type {import("zustand").StateCreator<OnboardingDraft, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set) => ({
        societyId: null,
        towerId: null,
        flat: "",
        reached: 0,
        household: EMPTY_HOUSEHOLD,
        markReached: (path) => {
          const i = ONBOARDING_STEPS.indexOf(path);
          if (i > useOnboardingDraft.getState().reached) set({ reached: i });
        },
        update: (patch) => set(patch),
        updateHousehold: (patch) =>
          set((s) => {
            const household = { ...s.household, ...patch };
            if (patch.members) household.health = pruneHealth(household.health, patch.members);
            return { household };
          }),
        reset: () =>
          set({
            societyId: null,
            towerId: null,
            flat: "",
            reached: 0,
            household: EMPTY_HOUSEHOLD,
            editing: null,
          }),
        editing: null,
        // Profile reuses the onboarding steps to edit: load the saved household into the draft.
        // Health consent is its own record (GET /v1/consents), so the caller passes it.
        startEdit: (h, healthConsent) =>
          set({
            editing: { id: h.id, version: h.version },
            household: {
              ...EMPTY_HOUSEHOLD,
              ...fromMembers(h.members),
              diet: h.diet,
              cuisines: h.cuisine_tags,
              healthConsent,
              weekdayCookMinutes: h.weekday_cook_minutes,
              weekendCookMinutes: h.weekend_cook_minutes,
              noCookDays: h.no_cook_days,
              budgetLockPaise: h.budget_paise,
            },
          }),
        stopEdit: () => set({ editing: null, household: EMPTY_HOUSEHOLD }),
      }),
      {
        name: STORAGE_KEY,
        storage: createJSONStorage(() => splitStorage),
        // A draft saved before a field existed (or without its sensitive part) still gets every field.
        merge: (persisted, current) => {
          const saved = /** @type {Partial<OnboardingDraft> | undefined} */ (persisted);
          return { ...current, ...saved, household: { ...EMPTY_HOUSEHOLD, ...saved?.household } };
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

/**
 * Where to send someone who has not finished onboarding: the furthest screen they opened, not
 * step 1. Without society and tower the draft cannot be trusted, so it starts at the society.
 * Health tags and consents are not kept past the tab, so if they are gone (the app was killed, or
 * the tab closed) the person re-enters at the health step, which asks for consent again.
 */
export function resumeStep() {
  const { societyId, towerId, reached } = useOnboardingDraft.getState();
  if (!societyId) return "/onboarding/society";
  if (!towerId) return "/onboarding/flat";
  const last = ONBOARDING_STEPS.indexOf("/onboarding/done") - 1;
  const target = Math.min(Math.max(reached, ONBOARDING_STEPS.indexOf("/onboarding/flat")), last);
  const sensitiveKept = read(sessionStorage, STORAGE_KEY) !== null;
  const health = ONBOARDING_STEPS.indexOf("/onboarding/health");
  return ONBOARDING_STEPS[!sensitiveKept && target > health ? health : target];
}
