import { afterEach, describe, expect, it, vi } from "vitest";

const hide = vi.fn(async () => {});
vi.mock("@capacitor/splash-screen", () => ({ SplashScreen: { hide } }));

/** Loads platform.js with env.js reporting a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./platform.js");
}

afterEach(() => {
  hide.mockClear();
  vi.doUnmock("@/lib/env.js");
});

describe("withPlugin", () => {
  it("uses the plugin's module in the Android app", async () => {
    const { withPlugin } = await load(true);
    const fallback = vi.fn(() => "web");
    const got = await withPlugin(
      async () => ({ Plugin: { answer: () => "native" } }),
      ({ Plugin }) => Plugin.answer(),
      fallback,
    );
    expect(got).toBe("native");
    expect(fallback).not.toHaveBeenCalled();
  });

  it("never loads the plugin on the web, and answers with the fallback", async () => {
    const { withPlugin } = await load(false);
    const loader = vi.fn(async () => ({}));
    expect(
      await withPlugin(
        loader,
        () => "native",
        () => "web",
      ),
    ).toBe("web");
    expect(await withPlugin(loader, () => "native")).toBeUndefined();
    expect(loader).not.toHaveBeenCalled();
  });
});

/** A router stand-in whose first screen renders when the test says so. */
function fakeRouter() {
  /** @type {(() => void) | null} */
  let listener = null;
  const unsubscribe = vi.fn(() => {
    listener = null;
  });
  return {
    subscribe: vi.fn((/** @type {string} */ _event, /** @type {() => void} */ fn) => {
      listener = fn;
      return unsubscribe;
    }),
    render: () => listener?.(),
    unsubscribe,
  };
}

describe("startNative", () => {
  afterEach(() => vi.useRealTimers());

  it("keeps the splash until the first screen has rendered, then hides it once", async () => {
    const { startNative } = await load(true);
    const router = fakeRouter();
    startNative(router);
    expect(router.subscribe).toHaveBeenCalledWith("onRendered", expect.any(Function));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(hide).not.toHaveBeenCalled();
    router.render();
    router.render();
    await vi.waitFor(() => expect(hide).toHaveBeenCalledOnce());
    expect(router.unsubscribe).toHaveBeenCalled();
  });

  it("hides the splash after SPLASH_MAX_MS when the first screen is slow (a waking API)", async () => {
    const { startNative, SPLASH_MAX_MS } = await load(true);
    vi.useFakeTimers();
    const router = fakeRouter();
    startNative(router);
    await vi.advanceTimersByTimeAsync(SPLASH_MAX_MS);
    vi.useRealTimers();
    await vi.waitFor(() => expect(hide).toHaveBeenCalledOnce());
    router.render();
    expect(hide).toHaveBeenCalledOnce();
  });

  it("does nothing on the web", async () => {
    const { startNative } = await load(false);
    const router = fakeRouter();
    startNative(router);
    router.render();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(router.subscribe).not.toHaveBeenCalled();
    expect(hide).not.toHaveBeenCalled();
  });
});
