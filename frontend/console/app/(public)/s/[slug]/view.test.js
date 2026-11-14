import assert from "node:assert/strict";
import { test } from "node:test";
import { societyView } from "./view.js";

/** @param {Partial<import("../../../../mocks/public.js").PublicSociety>} over */
const society = (over) => ({
  slug: "green-valley-heights-kharghar",
  name: "Green Valley Heights",
  area: "Sector 35, Kharghar",
  status: /** @type {"unlocking" | "live"} */ ("unlocking"),
  homes_joined: 18,
  unlock_target: 25,
  captain_name: null,
  ...over,
});

test("an unlocking society with a count says how many homes are left", () => {
  const v = societyView(society({}));
  assert.equal(v.away, 7);
  assert.equal(v.titleKey, "unlockingTitle");
  assert.equal(societyView(society({ homes_joined: 24 })).titleKey, "unlockingTitleOne");
});

test("under 5 households the API hides the count, so the page names no number", () => {
  const v = societyView(society({ homes_joined: null }));
  assert.equal(v.joined, null);
  assert.equal(v.away, null);
  assert.equal(v.titleKey, "unlockingTitleEarly");
});

test("a live society is unlocked even below its target (staging: 6 of 20)", () => {
  const v = societyView(society({ status: "live", homes_joined: 6, unlock_target: 20 }));
  assert.equal(v.live, true);
  assert.equal(v.titleKey, "liveTitle");
});

test("a missing area is left out instead of printing null", () => {
  const v = societyView(society({ area: null }));
  assert.equal(v.place, "Green Valley Heights");
  assert.equal(v.named, "Green Valley Heights");
  assert.equal(societyView(society({})).place, "Green Valley Heights, Sector 35, Kharghar");
  assert.equal(societyView(society({})).named, "Green Valley Heights (Sector 35, Kharghar)");
});
