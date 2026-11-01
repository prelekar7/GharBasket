import { afterEach, describe, expect, it, vi } from "vitest";

/** @type {(() => void) | undefined} */
let pressBack;
const remove = vi.fn();
const minimizeApp = vi.fn(async () => {});
const addListener = vi.fn(async (/** @type {string} */ _event, /** @type {() => void} */ fn) => {
  pressBack = fn;
  return { remove };
});
vi.mock("@capacitor/app", () => ({ App: { addListener, minimizeApp } }));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./backButton.js");
}

/** A router stand-in. @param {string} pathname @param {boolean} canGoBack */
const fakeRouter = (pathname, canGoBack) => ({
  history: { back: vi.fn(), canGoBack: () => canGoBack },
  state: { location: { pathname } },
});

/** Lets the async listener finish. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  document.body.innerHTML = "";
  pressBack = undefined;
  vi.doUnmock("@/lib/env.js");
});

describe("handleBack", () => {
  it("goes back on a screen inside a flow", async () => {
    const { handleBack } = await load(true);
    const router = fakeRouter("/orders/o1/track", true);
    await handleBack(router, { minimizeApp });
    expect(router.history.back).toHaveBeenCalled();
    expect(minimizeApp).not.toHaveBeenCalled();
  });

  it.each(["/", "/welcome", "/login", "/week", "/box", "/orders", "/kindness", "/profile"])(
    "minimises on the root screen %s even with history behind it",
    async (path) => {
      const { handleBack } = await load(true);
      const router = fakeRouter(path, true);
      await handleBack(router, { minimizeApp });
      expect(minimizeApp).toHaveBeenCalled();
      expect(router.history.back).not.toHaveBeenCalled();
    },
  );

  it.each(["/captain", "/captain/", "/kitchen", "/rider/", "/packer"])(
    "minimises on the role home %s",
    async (path) => {
      const { handleBack } = await load(true);
      await handleBack(fakeRouter(path, true), { minimizeApp });
      expect(minimizeApp).toHaveBeenCalledTimes(1);
    },
  );

  it("minimises when a deep screen has no history (opened from a link)", async () => {
    const { handleBack } = await load(true);
    const router = fakeRouter("/orders/o1/track", false);
    await handleBack(router, { minimizeApp });
    expect(minimizeApp).toHaveBeenCalled();
    expect(router.history.back).not.toHaveBeenCalled();
  });

  it("closes an open sheet with Escape instead of going back", async () => {
    const { handleBack } = await load(true);
    document.body.innerHTML = '<div role="dialog" data-state="open"></div>';
    const keys = vi.fn((/** @type {Event} */ e) => /** @type {KeyboardEvent} */ (e).key);
    document.addEventListener("keydown", keys, { once: true });
    const router = fakeRouter("/orders/o1/track", true);
    await handleBack(router, { minimizeApp });
    expect(keys).toHaveReturnedWith("Escape");
    expect(router.history.back).not.toHaveBeenCalled();
    expect(minimizeApp).not.toHaveBeenCalled();
  });

  it("ignores a sheet that is already closing, and closes the topmost of two", async () => {
    const { closeOverlay } = await load(true);
    document.body.innerHTML =
      '<div id="a" role="dialog"></div><div id="b" role="alertdialog"></div><div id="c" role="dialog" data-state="closed"></div>';
    /** @type {string[]} */
    const hit = [];
    for (const id of ["a", "b", "c"])
      document.getElementById(id)?.addEventListener("keydown", () => hit.push(id));
    expect(closeOverlay()).toBe(true);
    expect(hit).toEqual(["b"]);
    document.body.innerHTML = '<div role="dialog" data-state="closed"></div>';
    expect(closeOverlay()).toBe(false);
  });
});

describe("startBackButton", () => {
  it("listens for the hardware back button and acts on it in the app", async () => {
    const { startBackButton } = await load(true);
    const router = fakeRouter("/week", true);
    startBackButton(router);
    await settle();
    expect(addListener).toHaveBeenCalledWith("backButton", expect.any(Function));
    pressBack?.();
    await settle();
    expect(minimizeApp).toHaveBeenCalled();
  });

  it("removes the listener when stopped, even if it was stopped before it was added", async () => {
    const { startBackButton } = await load(true);
    const stop = startBackButton(fakeRouter("/week", true));
    stop();
    await settle();
    expect(remove).toHaveBeenCalledTimes(1);
  });

  it("does nothing on the web", async () => {
    const { startBackButton } = await load(false);
    startBackButton(fakeRouter("/week", true));
    await settle();
    expect(addListener).not.toHaveBeenCalled();
  });
});
