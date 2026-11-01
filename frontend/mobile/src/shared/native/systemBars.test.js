import { afterEach, describe, expect, it, vi } from "vitest";

const setStyle = vi.fn(async (/** @type {object} */ _o) => {});
vi.mock("@capacitor/core", () => ({
  SystemBars: { setStyle },
  SystemBarsStyle: { Dark: "DARK", Light: "LIGHT" },
}));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./systemBars.js");
}

/** A matchMedia stand-in the test can flip. @param {boolean} dark */
function fakeScheme(dark) {
  const query = { matches: dark, addEventListener: vi.fn(), removeEventListener: vi.fn() };
  vi.stubGlobal("matchMedia", () => query);
  return query;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  setStyle.mockClear();
  vi.doUnmock("@/lib/env.js");
  vi.unstubAllGlobals();
});

describe("startSystemBars", () => {
  it("sets light icons on the dark theme and dark icons on the light theme, following changes", async () => {
    const { startSystemBars } = await load(true);
    const query = fakeScheme(true);
    const stop = startSystemBars();
    await settle();
    expect(setStyle).toHaveBeenLastCalledWith({ style: "DARK" });
    query.matches = false;
    query.addEventListener.mock.calls[0][1]();
    await settle();
    expect(setStyle).toHaveBeenLastCalledWith({ style: "LIGHT" });
    stop();
    expect(query.removeEventListener).toHaveBeenCalled();
  });

  it("does nothing on the web", async () => {
    const { startSystemBars } = await load(false);
    const query = fakeScheme(true);
    startSystemBars();
    await settle();
    expect(setStyle).not.toHaveBeenCalled();
    expect(query.addEventListener).not.toHaveBeenCalled();
  });
});
