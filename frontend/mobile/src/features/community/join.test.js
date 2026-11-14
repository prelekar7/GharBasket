import { describe, expect, it } from "vitest";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { invitedSociety, joinSearch } from "./join.js";

describe("/join?s= (a neighbour's invite)", () => {
  it("keeps a valid slug, lowercased and trimmed", () => {
    expect(joinSearch.parse({ s: "sunrise-towers-kharghar" })).toEqual({
      s: "sunrise-towers-kharghar",
    });
    expect(joinSearch.parse({ s: "  Sunrise-Towers-Kharghar " }).s).toBe("sunrise-towers-kharghar");
  });

  it("drops anything that is not a slug", () => {
    for (const s of [
      "",
      "a b",
      "../login",
      "https://evil.example",
      "-x",
      "x--y",
      "a".repeat(81),
      42,
    ])
      expect(joinSearch.parse({ s }).s).toBeUndefined();
    expect(joinSearch.parse({}).s).toBeUndefined();
  });

  it("accepts only an exact slug", () => {
    const items = [
      { id: "1", slug: "sunrise-towers-kharghar-2" },
      { id: "2", slug: "sunrise-towers-kharghar" },
    ];
    expect(invitedSociety(items, "sunrise-towers-kharghar")?.id).toBe("2");
    expect(invitedSociety(items, "sunrise-towers")).toBeNull();
    expect(invitedSociety(items, null)).toBeNull();
  });
});

describe("the invite in the onboarding draft", () => {
  it("is kept on the device and cleared by reset (sign-out)", () => {
    useOnboardingDraft.getState().update({ inviteSlug: "green-valley-heights-kharghar" });
    expect(JSON.parse(localStorage.getItem("gb-onboarding") ?? "{}").state.inviteSlug).toBe(
      "green-valley-heights-kharghar",
    );
    useOnboardingDraft.getState().reset();
    expect(useOnboardingDraft.getState().inviteSlug).toBeNull();
  });
});
