import { describe, expect, it } from "vitest";
import { formatPaise } from "./Money.jsx";

describe("formatPaise", () => {
  it("shows integer paise as rupees with Indian grouping", () => {
    expect(formatPaise(69000)).toBe("₹690");
    expect(formatPaise(131000)).toBe("₹1,310");
    expect(formatPaise(10000000)).toBe("₹1,00,000");
  });

  it("keeps paise only when there are some", () => {
    expect(formatPaise(12345)).toBe("₹123.45");
  });
});
