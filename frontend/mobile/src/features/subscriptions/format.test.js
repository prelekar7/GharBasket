import { describe, expect, it } from "vitest";
import {
  clockTime,
  humanize,
  istClock,
  istDate,
  perUnit,
  qtyLabel,
  reasonText,
  shortDate,
  timeRange,
  weekdayName,
  weekdayOf,
} from "./format.js";

describe("timeRange", () => {
  it("writes a window as the designs do, sharing the day period", () => {
    expect(timeRange({ from: "08:00", to: "10:00" }, "en-IN")).toBe("8 to 10 AM");
    expect(timeRange({ from: "08:00", to: "10:00" }, "hi-IN")).toBe("सुबह 8 से 10");
    expect(timeRange({ from: "08:00", to: "10:00" }, "mr-IN")).toMatch(/^\S+ 8 ते 10$/);
  });

  it("names both periods when the window crosses noon, and keeps minutes", () => {
    expect(timeRange({ from: "11:30", to: "13:00" }, "en-IN")).toBe("11:30 AM to 1 PM");
    expect(timeRange({ from: "11:30", to: "13:00" }, "hi-IN")).toBe("सुबह 11:30 से दोपहर 1");
  });
});

describe("dates", () => {
  it("knows the weekday of an ISO date, Monday first", () => {
    expect(weekdayOf("2026-10-05")).toBe(0);
    expect(weekdayOf("2026-10-06")).toBe(1);
    expect(weekdayOf("2026-10-11")).toBe(6);
  });

  it("names weekdays and short dates in the person's language", () => {
    expect(weekdayName("2026-10-06", "en-IN")).toBe("Tue");
    expect(weekdayName("2026-10-06", "hi-IN")).toBe("मंगल");
    expect(shortDate("2026-10-06", "en-IN")).toBe("6 Oct");
  });

  it("reads India time from any timestamp", () => {
    expect(istClock("2026-10-06T02:50:00Z")).toBe("08:20");
    expect(istClock("2026-10-06T08:20:00+05:30")).toBe("08:20");
    expect(clockTime("08:20", "en-IN")).toBe("8:20 am");
    expect(clockTime("19:00", "hi-IN")).toBe("शाम 7");
  });
});

describe("slots and moments from the API", () => {
  it("writes a slot with seconds, and a window the old way, and nothing for no slot", () => {
    expect(timeRange({ start: "07:00:00", end: "09:00:00" }, "en-IN")).toBe("7 to 9 AM");
    expect(timeRange({ from: "08:00", to: "10:00" }, "en-IN")).toBe("8 to 10 AM");
    expect(timeRange(undefined, "en-IN")).toBe("");
  });

  it("reads the India date and weekday of a UTC timestamp", () => {
    expect(istDate("2026-10-05T15:30:00Z")).toBe("2026-10-05");
    expect(istDate("2026-10-05T19:00:00Z")).toBe("2026-10-06");
    expect(weekdayName("2026-10-05T19:00:00Z", "en-IN")).toBe("Tue");
  });
});

describe("box line words", () => {
  it("says one step in the API's label and more as a count of steps", () => {
    expect(qtyLabel({ qty: 1, step: { label: "250 g" } })).toBe("250 g");
    expect(qtyLabel({ qty: 3, step: { label: "1 kg" } })).toBe("3 × 1 kg");
  });

  it("prices per kilo, litre, bunch or each from one step", () => {
    expect(perUnit({ qty: 250, unit: "g" }, 2000)).toEqual({ price_paise: 8000, unit: "kg" });
    expect(perUnit({ qty: 500, unit: "ml" }, 3400)).toEqual({ price_paise: 6800, unit: "l" });
    expect(perUnit({ qty: 1, unit: "kg" }, 3500)).toEqual({ price_paise: 3500, unit: "kg" });
    expect(perUnit({ qty: 1, unit: "bunch" }, 1000)).toEqual({ price_paise: 1000, unit: "bunch" });
    expect(perUnit({ qty: 6, unit: "piece" }, 4000)).toEqual({ price_paise: 667, unit: "each" });
  });

  it("turns planner slugs into names", () => {
    expect(humanize("batata-bhaji")).toBe("Batata bhaji");
  });
});

describe("reasonText", () => {
  /** A stand-in for i18next that shows the key and the params it got. @type {any} */
  const t = (/** @type {string} */ key, /** @type {object} */ p = {}) =>
    `${key}${Object.keys(p).length ? JSON.stringify(p) : ""}`;

  it("names the recipes of a planned line, a few at a time", () => {
    expect(
      reasonText(t, { code: "meal_plan", params: { recipes: ["batata-bhaji", "kanda-poha"] } }),
    ).toBe('reasons.meal_plan{"recipes":"Batata bhaji and Kanda poha"}');
    expect(
      reasonText(t, { code: "meal_plan", params: { recipes: ["a-b", "c-d", "e-f", "g-h"] } }),
    ).toContain("+2");
  });

  it("falls back to a plain sentence when the params a sentence needs are missing", () => {
    expect(reasonText(t, { code: "meal_plan", params: { recipes: [] } })).toBe(
      "reasons.meal_plan_plain",
    );
    expect(reasonText(t, { code: "in_season", params: {} })).toBe("reasons.in_season_plain");
    expect(reasonText(t, { code: "price_swap", params: null })).toBe("reasons.price_swap_plain");
    expect(reasonText(t, { code: "cheaper", params: {} })).toBe("reasons.cheaper_plain");
    expect(reasonText(t, { code: "daily_use", params: null })).toBe("reasons.daily_use");
  });

  it("formats what a cheaper substitute saves, and fasting weekdays", () => {
    expect(reasonText(t, { code: "cheaper", params: { saves_paise: 500 } })).toContain("₹5");
    expect(reasonText(t, { code: "festival", params: { days: [0, 1] } })).toContain(
      "days.long.d0 and days.long.d1",
    );
  });
});
