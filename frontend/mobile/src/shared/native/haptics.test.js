import { afterEach, describe, expect, it, vi } from "vitest";

const notification = vi.fn(async (/** @type {object} */ _o) => {});
vi.mock("@capacitor/haptics", () => ({
  Haptics: { notification },
  NotificationType: { Success: "SUCCESS", Warning: "WARNING", Error: "ERROR" },
}));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./haptics.js");
}

afterEach(() => {
  notification.mockClear();
  vi.doUnmock("@/lib/env.js");
  Object.assign(navigator, { vibrate: undefined });
});

describe("haptics", () => {
  it("plays the success and warning notifications in the app", async () => {
    const { tapSuccess, tapWarning } = await load(true);
    await tapSuccess();
    await tapWarning();
    expect(notification.mock.calls).toEqual([[{ type: "SUCCESS" }], [{ type: "WARNING" }]]);
  });

  it("vibrates on the web where the phone can", async () => {
    const { tapSuccess, tapWarning } = await load(false);
    const vibrate = vi.fn(() => true);
    Object.assign(navigator, { vibrate });
    await tapSuccess();
    await tapWarning();
    expect(vibrate.mock.calls).toEqual([[40], [[60, 40, 60]]]);
    expect(notification).not.toHaveBeenCalled();
  });

  it("does nothing, quietly, where there is no vibration", async () => {
    const { tapSuccess } = await load(false);
    await expect(tapSuccess()).resolves.toBeUndefined();
  });

  it("never throws when the plugin does", async () => {
    const { tapWarning } = await load(true);
    notification.mockRejectedValueOnce(new Error("no vibrator"));
    await expect(tapWarning()).resolves.toBeUndefined();
  });
});
