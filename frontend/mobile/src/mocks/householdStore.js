// The household a phone has set up, kept across reloads like a server's row (M1 households and
// members, M3 subscription pause). Shared by the onboarding mock (identity.js) and Profile (profile.js).

const KEY = "gb-mock-households-full";

/**
 * @typedef {{ id: string, version: number, society_id: string, flat: string | null, address_id: string | null, size: number,
 *   members: { adults: number, seniors: number, children: number, little: number }, diet: string,
 *   cuisine_tags: string[], rules: { jain: boolean, no_onion_some_days: boolean }, fasting: string[],
 *   health_consent: boolean, health: Record<string, string[]>, weekday_cook_minutes: number,
 *   weekend_cook_minutes: number, no_cook_days: number[], budget_paise: number | null,
 *   subscription: { id: string, status: "active" | "paused", paused_until: string | null } }} Household
 */

/** The designs' family of four in Sunrise Towers, for the demo households. @returns {Household} */
export const demoHousehold = () => ({
  id: "hh-1",
  version: 1,
  society_id: "sunrise-towers",
  flat: "B-402",
  address_id: "addr-1",
  size: 4,
  members: { adults: 2, seniors: 0, children: 2, little: 0 },
  diet: "veg",
  cuisine_tags: ["maharashtrian", "gujarati"],
  rules: { jain: false, no_onion_some_days: false },
  fasting: ["shravan_mondays"],
  health_consent: true,
  health: { "adults-2": ["diabetes"] },
  weekday_cook_minutes: 30,
  weekend_cook_minutes: 60,
  no_cook_days: [],
  budget_paise: 300000,
  subscription: { id: "sub-1", status: "active", paused_until: null },
});

/** @returns {Record<string, Household>} by phone */
const all = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");

/** @param {string} phone @returns {Household | null} */
export const householdOf = (phone) => all()[phone] ?? null;

/** @param {string} phone @param {Household} h */
export function saveHousehold(phone, h) {
  localStorage.setItem(KEY, JSON.stringify({ ...all(), [phone]: h }));
}
