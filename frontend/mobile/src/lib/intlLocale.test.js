import { describe, expect, it } from "vitest";
import { clock, dayLabel } from "@/features/kitchen/format.js";
import { shortDate, timeRange, weekRange } from "@/features/subscriptions/format.js";
import i18n from "@/i18n.js";
import { intlLocale } from "./intlLocale.js";

const DEVANAGARI_DIGIT = /[०-९]/;

describe("intlLocale", () => {
  it("forces Latin digits for Marathi only", () => {
    expect(intlLocale("mr-IN")).toBe("mr-IN-u-nu-latn");
    expect(intlLocale("mr-IN-u-nu-latn")).toBe("mr-IN-u-nu-latn");
    expect(intlLocale("hi-IN")).toBe("hi-IN");
    expect(intlLocale("en-IN")).toBe("en-IN");
  });
});

describe("Marathi digits", () => {
  it("renders ICU `#` and {n, number} in plurals with Latin digits", async () => {
    await i18n.changeLanguage("mr-IN");
    const waiting = i18n.t("outbox.waiting", { count: 18 });
    expect(waiting).toContain("18");
    expect(waiting).not.toMatch(DEVANAGARI_DIGIT);
    const family = i18n.t("onboarding.family.total", { count: 12 });
    expect(family).toMatch(/^12 /);
    expect(family).not.toMatch(DEVANAGARI_DIGIT);
  });

  it("renders dates and times with Latin digits", () => {
    expect(shortDate("2026-10-18", "mr-IN")).toMatch(/^18 /);
    expect(weekRange("2026-10-12", "mr-IN")).not.toMatch(DEVANAGARI_DIGIT);
    expect(timeRange({ from: "08:00", to: "10:00" }, "mr-IN")).toMatch(/8 ते 10$/);
    expect(clock("21:30", "mr-IN")).toContain("9:30");
    expect(dayLabel("2026-10-18", "mr-IN")).not.toMatch(DEVANAGARI_DIGIT);
  });
});
