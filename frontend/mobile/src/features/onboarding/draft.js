import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * What a new household has told us so far. Kept on the device (sessionStorage) until the last
 * onboarding step creates the household and address in one go (M1).
 * @typedef {object} OnboardingDraft
 * @property {string | null} societyId
 * @property {string | null} towerId
 * @property {string} flat
 * @property {(patch: Partial<Pick<OnboardingDraft, "societyId" | "towerId" | "flat">>) => void} update
 * @property {() => void} reset
 */

export const useOnboardingDraft = create(
  /** @type {import("zustand").StateCreator<OnboardingDraft, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set) => ({
        societyId: null,
        towerId: null,
        flat: "",
        update: (patch) => set(patch),
        reset: () => set({ societyId: null, towerId: null, flat: "" }),
      }),
      { name: "gb-onboarding", storage: createJSONStorage(() => sessionStorage) },
    )
  ),
);

/** Onboarding screens in order; later PRs add family, food, budget, consent and payment. */
export const ONBOARDING_STEPS = ["/onboarding/society", "/onboarding/flat"];

/** The screen after `path`, or home when onboarding is done. @param {string} path */
export const nextStep = (path) => ONBOARDING_STEPS[ONBOARDING_STEPS.indexOf(path) + 1] ?? "/";
