import { beforeEach, describe, expect, it, vi } from "vitest";
import hi from "@/locales/hi-IN.json";

// Hindi and Marathi are separate chunks: loaded for a saved choice before the first render, and by
// setLocale() on a change; en-IN is always there.
describe("lazy locales", () => {
  beforeEach(() => vi.resetModules());

  it("has en-IN at once and no Hindi strings until asked", async () => {
    const { default: i18n } = await import("@/i18n.js");
    expect(i18n.t("welcome.title")).not.toBe("welcome.title");
    expect(i18n.hasResourceBundle("hi-IN", "translation")).toBe(false);
  });

  it("loads the saved language before i18nReady resolves", async () => {
    localStorage.setItem("gb-locale", "hi-IN");
    const { default: i18n, i18nReady } = await import("@/i18n.js");
    await i18nReady;
    expect(i18n.language).toBe("hi-IN");
    expect(i18n.t("welcome.title")).toBe(hi.welcome.title);
  });

  it("setLocale fetches the strings, then switches", async () => {
    const { default: i18n, setLocale } = await import("@/i18n.js");
    await setLocale("hi-IN");
    expect(i18n.t("welcome.title")).toBe(hi.welcome.title);
    expect(localStorage.getItem("gb-locale")).toBe("hi-IN");
  });
});

describe("a language chunk that cannot load", () => {
  it("still lets the app render, in English", async () => {
    vi.resetModules();
    localStorage.setItem("gb-locale", "mr-IN");
    vi.doMock("@/locales/mr-IN.json", () => {
      throw new Error("chunk failed to load");
    });
    const { default: i18n, i18nReady } = await import("@/i18n.js");
    await expect(i18nReady).resolves.toBeUndefined();
    expect(i18n.t("welcome.title")).not.toBe("welcome.title");
    vi.doUnmock("@/locales/mr-IN.json");
    localStorage.removeItem("gb-locale");
  });
});
