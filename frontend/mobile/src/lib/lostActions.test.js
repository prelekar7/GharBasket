import { describe, expect, it } from "vitest";
import { useLostActions } from "./lostActions.js";

describe("lost actions notice", () => {
  it("keeps the notice in storage until it is dismissed, names and counts only", () => {
    useLostActions.getState().show([{ action: "handover", count: 2 }]);
    const saved = JSON.parse(localStorage.getItem("gb-lost-actions") ?? "{}");
    expect(saved.state).toEqual({ items: [{ action: "handover", count: 2 }] });

    useLostActions.getState().dismiss();
    expect(useLostActions.getState().items).toEqual([]);
    expect(JSON.parse(localStorage.getItem("gb-lost-actions") ?? "{}").state.items).toEqual([]);
  });
});
