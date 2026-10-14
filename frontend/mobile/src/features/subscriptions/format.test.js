import { describe, expect, it } from "vitest";
import { clockTime, istClock, shortDate, timeRange, weekdayName, weekdayOf } from "./format.js";

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
