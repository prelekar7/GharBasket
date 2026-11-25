import { describe, expect, it } from "vitest";
import { isValidName, NAME_MAX, tidyName } from "./name.js";

describe("the name rule (same as the API's platform/names.py)", () => {
  it("trims and collapses inner whitespace", () => {
    expect(tidyName("  Priya \t  Sharma\n")).toBe("Priya Sharma");
    expect(tidyName("   ")).toBe("");
  });

  it("takes 2 to 40 characters once tidied", () => {
    expect(isValidName("P")).toBe(false);
    expect(isValidName(" P ")).toBe(false);
    expect(isValidName("   ")).toBe(false);
    expect(isValidName("Pr")).toBe(true);
    expect(isValidName("a".repeat(NAME_MAX))).toBe(true);
    expect(isValidName(`  ${"a".repeat(NAME_MAX)}  `)).toBe(true);
    expect(isValidName("a".repeat(NAME_MAX + 1))).toBe(false);
  });

  it("counts code points, as Python's len does", () => {
    expect(isValidName("सु")).toBe(true); // two code points
    // 20 emoji are 40 UTF-16 units but 20 code points: valid, as on the server.
    expect(isValidName("🙂".repeat(21))).toBe(true);
    expect(isValidName("🙂".repeat(41))).toBe(false);
  });
});
