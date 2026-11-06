import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api.js";
import { cleanName, isNameRefused, isValidName } from "./displayName.js";

describe("partner display name (2 to 40 characters)", () => {
  it("collapses spaces the way the server reads them", () => {
    expect(cleanName("  Meena   tai ")).toBe("Meena tai");
    expect(cleanName(null)).toBe("");
  });

  it("accepts 2 to 40 characters and refuses empty, one character and too long", () => {
    expect(isValidName("Ra")).toBe(true);
    expect(isValidName("मीना ताई")).toBe(true);
    expect(isValidName("a".repeat(40))).toBe(true);
    expect(isValidName("a".repeat(41))).toBe(false);
    expect(isValidName("R")).toBe(false);
    expect(isValidName("   ")).toBe(false);
    expect(isValidName(undefined)).toBe(false);
  });

  it("knows when the API refused the name", () => {
    const named = new ApiError({ status: 422, code: "partners.display_name_invalid" });
    const field = new ApiError({
      status: 422,
      code: "request.invalid",
      errors: [{ loc: ["body", "display_name"], message: "too long", type: "string_too_long" }],
    });
    expect(isNameRefused(named)).toBe(true);
    expect(isNameRefused(field)).toBe(true);
    expect(isNameRefused(new ApiError({ status: 422, code: "partners.kind_invalid" }))).toBe(false);
    expect(isNameRefused(new ApiError({ status: 500 }))).toBe(false);
    expect(isNameRefused(new Error("offline"))).toBe(false);
  });
});
