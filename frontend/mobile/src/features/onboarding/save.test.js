import { describe, expect, it } from "vitest";
import { fromMembers, toMembers } from "./members.js";
import { memberOps } from "./save.js";

const rules = (/** @type {object} */ extra = {}) => ({
  jain: false,
  fasts: /** @type {string[]} */ ([]),
  no_onion_garlic_on_fast_days: false,
  ...extra,
});
const saved = (
  /** @type {string} */ id,
  /** @type {any} */ age_band,
  /** @type {object} */ extra = {},
) => ({
  id,
  age_band,
  sex: null,
  fasting_rules: rules(),
  health_tags: /** @type {string[]} */ ([]),
  ...extra,
});
const want = (/** @type {any} */ age_band, /** @type {object} */ extra = {}) => ({
  age_band,
  fasting_rules: rules(),
  health_tags: /** @type {string[]} */ ([]),
  ...extra,
});

describe("memberOps (what Profile sends when the family changes)", () => {
  const family = [saved("a1", "18_59"), saved("a2", "18_59"), saved("c1", "5_17")];

  it("sends nothing when nothing changed", () => {
    const ops = memberOps(family, [want("18_59"), want("18_59"), want("5_17")], true);
    expect(ops).toEqual({ patches: [], posts: [], deletes: [] });
  });

  it("adds one person and removes another", () => {
    expect(
      memberOps(family, [...family.map((m) => want(m.age_band)), want("60_plus")], true).posts,
    ).toEqual([want("60_plus")]);
    expect(memberOps(family, [want("18_59"), want("5_17")], true).deletes).toEqual(["a2"]);
  });

  it("turns a removed person and an added one into one change of age band", () => {
    const ops = memberOps(family, [want("18_59"), want("18_59"), want("60_plus")], true);
    expect(ops.patches).toEqual([{ id: "c1", body: want("60_plus") }]);
    expect(ops.posts).toEqual([]);
    expect(ops.deletes).toEqual([]);
  });

  it("keeps each person's own food rules unless the rules were changed", () => {
    const mixed = [
      saved("a1", "18_59", { fasting_rules: rules({ fasts: ["ekadashi"] }) }),
      saved("a2", "18_59"),
    ];
    // The draft shows the union of fasts for everyone; untouched, nobody is changed.
    const draft = [
      want("18_59", { fasting_rules: rules({ fasts: ["ekadashi"] }) }),
      want("18_59", { fasting_rules: rules({ fasts: ["ekadashi"] }) }),
    ];
    expect(memberOps(mixed, draft, true, true).patches).toEqual([]);
    expect(memberOps(mixed, draft, true, false).patches).toHaveLength(1);
  });

  it("changes rules and tags, and ignores tags the server already erased", () => {
    const withTag = [saved("a1", "18_59", { health_tags: ["diabetes"] })];
    expect(
      memberOps(withTag, [want("18_59", { fasting_rules: rules({ jain: true }) })], true).patches,
    ).toHaveLength(1);
    expect(
      memberOps(withTag, [want("18_59", { health_tags: ["diabetes"] })], true).patches,
    ).toEqual([]);
    // Health consent revoked in this edit: the server holds no tags any more.
    expect(memberOps(withTag, [want("18_59")], false).patches).toEqual([]);
  });
});

describe("the family in the onboarding draft", () => {
  const h = /** @type {any} */ ({
    members: { adults: 2, seniors: 1, children: 1, little: 0 },
    jain: true,
    noOnionSomeDays: true,
    fasting: ["ekadashi"],
    healthConsent: true,
    health: { "adults-2": ["diabetes"], "seniors-1": ["high_bp"] },
  });

  it("goes to members and back", () => {
    const members = toMembers(h);
    expect(members.map((m) => m.age_band)).toEqual(["18_59", "18_59", "60_plus", "5_17"]);
    expect(members.map((m) => m.fasting_rules.fasts.length)).toEqual([1, 1, 1, 0]);
    expect(
      members.every((m) => m.fasting_rules.jain && m.fasting_rules.no_onion_garlic_on_fast_days),
    ).toBe(true);
    const back = fromMembers(members.map((m, i) => ({ ...m, id: `m${i}`, sex: null })));
    expect(back).toMatchObject({
      members: h.members,
      jain: true,
      noOnionSomeDays: true,
      fasting: ["ekadashi"],
      health: h.health,
    });
  });

  it("sends no health tags without the consent", () => {
    expect(toMembers({ ...h, healthConsent: false }).flatMap((m) => m.health_tags)).toEqual([]);
  });
});
