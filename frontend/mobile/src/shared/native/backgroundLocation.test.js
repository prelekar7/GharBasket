import { afterEach, describe, expect, it, vi } from "vitest";

/** @type {((position?: object, error?: { code?: string }) => void) | null} */
let callback = null;
const addWatcher = vi.fn(async (/** @type {object} */ _options, cb) => {
  callback = cb;
  return "watcher-1";
});
const removeWatcher = vi.fn(async () => {});
const openSettings = vi.fn(async () => {});
const registerPlugin = vi.fn(() => ({ addWatcher, removeWatcher, openSettings }));
vi.mock("@capacitor/core", () => ({ registerPlugin }));

/** Loads the wrapper with env.js reporting a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./backgroundLocation.js");
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  callback = null;
  addWatcher.mockClear();
  removeWatcher.mockClear();
  openSettings.mockClear();
  registerPlugin.mockClear();
  vi.doUnmock("@/lib/env.js");
});

describe("startTracking in the Android app", () => {
  it("adds a background watcher with a notification and maps the location to a ping", async () => {
    const { startTracking } = await load(true);
    await import("@/i18n.js");
    await flush(); // i18next finishes its start-up
    const onPing = vi.fn();
    startTracking({ onPing });
    await flush();
    expect(registerPlugin).toHaveBeenCalledWith("BackgroundGeolocation");
    expect(addWatcher).toHaveBeenCalledOnce();
    expect(addWatcher.mock.calls[0][0]).toEqual({
      backgroundTitle: "Delivering for GharBasket",
      backgroundMessage: "Sharing your location while the route is in progress",
      requestPermissions: true,
      stale: false,
      distanceFilter: 10,
    });
    callback?.({
      latitude: 19.03,
      longitude: 73.07,
      accuracy: 12.6,
      speed: 4.2,
      bearing: 90.4,
      time: Date.parse("2026-11-01T06:00:00Z"),
    });
    callback?.({
      latitude: 19.04,
      longitude: 73.08,
      accuracy: 8,
      speed: null,
      bearing: null,
      time: null,
    });
    expect(onPing).toHaveBeenNthCalledWith(1, {
      at: "2026-11-01T06:00:00.000Z",
      lat: 19.03,
      lng: 73.07,
      accuracy_m: 13,
      speed_mps: 4.2,
      heading: 90,
    });
    expect(onPing.mock.calls[1][0]).toMatchObject({
      accuracy_m: 8,
      speed_mps: null,
      heading: null,
    });
  });

  it("removes the watcher when stopped, and stops listening", async () => {
    const { startTracking } = await load(true);
    const onPing = vi.fn();
    const stop = startTracking({ onPing });
    await flush();
    stop();
    await flush();
    expect(removeWatcher).toHaveBeenCalledWith({ id: "watcher-1" });
    callback?.({ latitude: 19, longitude: 73, accuracy: 5, speed: null, bearing: null, time: 1 });
    expect(onPing).not.toHaveBeenCalled();
  });

  it("removes the watcher even when stopped before the plugin has started", async () => {
    const { startTracking } = await load(true);
    startTracking({ onPing: vi.fn() })();
    await flush();
    expect(removeWatcher).toHaveBeenCalledWith({ id: "watcher-1" });
  });

  it("reports a refused permission as denied, any other error as failed", async () => {
    const { startTracking } = await load(true);
    const onError = vi.fn();
    startTracking({ onPing: vi.fn(), onError });
    await flush();
    callback?.(undefined, { code: "NOT_AUTHORIZED" });
    callback?.(undefined, { code: "OTHER" });
    expect(onError.mock.calls).toEqual([[{ kind: "denied" }], [{ kind: "failed" }]]);
  });

  it("reports a plugin that cannot start as failed", async () => {
    addWatcher.mockRejectedValueOnce(new Error("no service"));
    const { startTracking } = await load(true);
    const onError = vi.fn();
    startTracking({ onPing: vi.fn(), onError });
    await flush();
    expect(onError).toHaveBeenCalledWith({ kind: "failed" });
  });

  it("opens the app settings", async () => {
    const { openLocationSettings } = await load(true);
    await openLocationSettings();
    expect(openSettings).toHaveBeenCalledOnce();
  });
});

describe("startTracking on the web", () => {
  it("uses navigator.geolocation and never touches the plugin", async () => {
    /** @type {{ fix: ((p: object) => void) | null }} */
    const watcher = { fix: null };
    const watchPosition = vi.fn((cb) => {
      watcher.fix = cb;
      return 7;
    });
    const clearWatch = vi.fn();
    vi.stubGlobal("navigator", { geolocation: { watchPosition, clearWatch } });
    try {
      const { startTracking, openLocationSettings } = await load(false);
      const onPing = vi.fn();
      const stop = startTracking({ onPing });
      watcher.fix?.({
        timestamp: Date.parse("2026-11-01T06:00:00Z"),
        coords: { latitude: 19, longitude: 73, accuracy: 9.4, speed: null, heading: Number.NaN },
      });
      expect(onPing).toHaveBeenCalledWith({
        at: "2026-11-01T06:00:00.000Z",
        lat: 19,
        lng: 73,
        accuracy_m: 9,
        speed_mps: null,
        heading: null,
      });
      stop();
      expect(clearWatch).toHaveBeenCalledWith(7);
      await openLocationSettings();
      expect(registerPlugin).not.toHaveBeenCalled();
      expect(openSettings).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
