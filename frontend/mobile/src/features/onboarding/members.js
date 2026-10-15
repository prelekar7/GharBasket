// The family the screens collect (counts per age group, one set of food rules, health tags per
// person) as the API's members (M1 MemberIn / MemberOut), and back for editing.

/** @typedef {import("./draft.js").HouseholdDraft} HouseholdDraft */
/** @typedef {import("@/mocks/householdStore.js").Member} Member */
/** @typedef {"adults" | "seniors" | "children" | "little"} Group */

/** @type {Record<Group, Member["age_band"]>} */
export const AGE_BAND = {
  adults: "18_59",
  seniors: "60_plus",
  children: "5_17",
  little: "under_5",
};
/** @type {Group[]} */
export const GROUPS = ["adults", "seniors", "children", "little"];
/** At most this many people in one household (M1). */
export const MAX_MEMBERS = 12;

/** The key of the n-th person of a group in the draft's `health`: "adults-1". @param {Group} group @param {number} n 1-based */
export const personKey = (group, n) => `${group}-${n}`;

/**
 * The draft's family as MemberIn. The household's food rules are the same for everyone: Jain and
 * "no onion-garlic on fast days" go on every member, the fasting days only on adults and seniors
 * (children don't fast). Health tags go only with consent (DPDP).
 * @param {HouseholdDraft} h
 */
export function toMembers(h) {
  return GROUPS.flatMap((group) =>
    Array.from({ length: h.members[group] }, (_, i) => ({
      age_band: AGE_BAND[group],
      fasting_rules: {
        jain: h.jain,
        fasts: group === "adults" || group === "seniors" ? h.fasting : [],
        no_onion_garlic_on_fast_days: h.noOnionSomeDays,
      },
      health_tags: h.healthConsent ? (h.health[personKey(group, i + 1)] ?? []) : [],
    })),
  );
}

/**
 * The draft's family fields from a saved household's members: counts from `age_band`, the rules
 * from `fasting_rules` (any member Jain makes the household Jain), health tags per person in
 * member order.
 * @param {Member[]} members @returns {Pick<HouseholdDraft, "members" | "jain" | "noOnionSomeDays" | "fasting" | "health">}
 */
export function fromMembers(members) {
  const counts = { adults: 0, seniors: 0, children: 0, little: 0 };
  /** @type {Record<string, string[]>} */
  const health = {};
  for (const m of members) {
    const group = GROUPS.find((g) => AGE_BAND[g] === m.age_band) ?? "adults";
    counts[group] += 1;
    if (m.health_tags.length) health[personKey(group, counts[group])] = m.health_tags;
  }
  return {
    members: counts,
    jain: members.some((m) => m.fasting_rules.jain),
    noOnionSomeDays: members.some((m) => m.fasting_rules.no_onion_garlic_on_fast_days),
    fasting: [...new Set(members.flatMap((m) => m.fasting_rules.fasts))],
    health,
  };
}
