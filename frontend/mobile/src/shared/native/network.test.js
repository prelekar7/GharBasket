import { afterEach, describe, expect, it, vi } from "vitest";

const remove = vi.fn();
/** @type {((s: { connected: boolean }) => void) | undefined} */
let emit;
const getStatus = vi.fn(async () => ({ connected: false, connectionType: "none" }));
const addListener = vi.fn(async (/** @type {string} */ _e, /** @type {typeof emit} */ fn) => {
  emit = fn;
  return { remove };
});
vi.mock("@capacitor/network", () => ({ Network: { getStatus, addListener } }));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./network.js");
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  emit = undefined;
  vi.doUnmock("@/lib/env.js");
});

describe("isOnline", () => {
  it("asks the Network plugin in the app", async () => {
    const { isOnline } = await load(true);
    expect(await isOnline()).toBe(false);
  });

  it("reads navigator.onLine on the web", async () => {
    const { isOnline } = await load(false);
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    expect(await isOnline()).toBe(true);
    expect(getStatus).not.toHaveBeenCalled();
  });
});

describe("onNetworkChange", () => {
  it("follows networkStatusChange in the app and stops cleanly", async () => {
    const { onNetworkChange } = await load(true);
    const fn = vi.fn();
    const stop = onNetworkChange(fn);
    await settle();
    emit?.({ connected: true });
    expect(fn).toHaveBeenCalledWith(true);
    stop();
    expect(remove).toHaveBeenCalledTimes(1);
  });

  it("follows the browser's online and offline events on the web", async () => {
    const { onNetworkChange } = await load(false);
    const fn = vi.fn();
    const stop = onNetworkChange(fn);
    window.dispatchEvent(new Event("offline"));
    window.dispatchEvent(new Event("online"));
    expect(fn.mock.calls).toEqual([[false], [true]]);
    stop();
    window.dispatchEvent(new Event("offline"));
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
