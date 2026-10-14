import { describe, expect, it } from "vitest";
import { deliveryText, nextDeliveryDate } from "./delivery.js";

const morning = { id: "s1", label: "Morning 8-10", start: "08:00:00", end: "10:00:00" };
const evening = { id: "s2", label: "Evening 5-7", start: "17:00:00", end: "19:00:00" };

describe("society delivery days (weekday 0 = Monday)", () => {
  it("names the days and the window once for days that share a slot", () => {
    const text = deliveryText(
      [
        { weekday: 4, slot: morning },
        { weekday: 1, slot: morning },
      ],
      "en-IN",
    );
    expect(text).toMatch(/^Tue and Fri, /);
    expect(text).toContain("8");
  });

  it("separates days that have different slots", () => {
    const text = deliveryText(
      [
        { weekday: 1, slot: morning },
        { weekday: 5, slot: evening },
      ],
      "en-IN",
    );
    expect(text?.split("; ")).toHaveLength(2);
  });

  it("is null without delivery days", () => {
    expect(deliveryText([], "en-IN")).toBeNull();
    expect(nextDeliveryDate([])).toBeNull();
  });

  it("finds the next delivery day from tomorrow on", () => {
    const days = [
      { weekday: 1, slot: morning },
      { weekday: 4, slot: morning },
    ];
    expect(nextDeliveryDate(days, "2026-10-06")).toBe("2026-10-09"); // Tue → Fri
    expect(nextDeliveryDate(days, "2026-10-09")).toBe("2026-10-13"); // Fri → next Tue
    expect(nextDeliveryDate([{ weekday: 1, slot: morning }], "2026-10-06")).toBe("2026-10-13");
  });
});
