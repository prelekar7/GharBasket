import { describe, expect, it } from "vitest";
import { bagQr, parseBagQr } from "./scan.js";

describe("bagQr", () => {
  it("is GB1: plus the order id without dashes, the label M6 prints", () => {
    const id = "0199A000-0000-7000-8000-0000000000AB";
    expect(bagQr(id)).toBe("GB1:0199a0000000700080000000000000ab");
    expect(parseBagQr(bagQr(id))).toBe(bagQr(id));
  });

  it("reads a typed or scanned bag code loosely in case and spacing, and nothing else", () => {
    expect(parseBagQr("  GB1:0199A0000000700080000000000000AB ")).toBe(
      "GB1:0199a0000000700080000000000000ab",
    );
    expect(parseBagQr("B-214")).toBeNull();
    expect(parseBagQr("GB1A:0199a0000000700080000000000000ab")).toBeNull();
    expect(parseBagQr("GB1:abc")).toBeNull();
  });
});
