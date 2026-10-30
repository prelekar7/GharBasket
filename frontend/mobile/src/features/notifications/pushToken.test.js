import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("@/lib/api.js", () => ({ request: (/** @type {any[]} */ ...a) => request(...a) }));
let online = true;
Object.defineProperty(navigator, "onLine", { configurable: true, get: () => online });

const push = await import("./pushToken.js");
const bodies = () => request.mock.calls.map((c) => c[1].body);

describe("push token", () => {
  beforeEach(async () => {
    request.mockReset();
    request.mockResolvedValue(null);
    online = true;
    push.stopPushToken();
    await push.clearPushToken();
    request.mockClear();
  });

  it("sends nothing when there is no token, at sign-in or sign-out", async () => {
    push.syncPushToken();
    await push.releasePushToken();
    expect(request).not.toHaveBeenCalled();
  });

  it("ignores empty and oversized tokens", async () => {
    push.syncPushToken();
    expect(await push.registerPushToken("")).toBe(false);
    expect(await push.registerPushToken(null)).toBe(false);
    expect(await push.registerPushToken("x".repeat(4097))).toBe(false);
    expect(request).not.toHaveBeenCalled();
  });

  it("holds a token until sign-in, then sends it with PUT once", async () => {
    await push.registerPushToken("fcm-1");
    expect(request).not.toHaveBeenCalled();
    push.syncPushToken();
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(request.mock.calls[0][0]).toBe("/v1/me/push-token");
    expect(request.mock.calls[0][1].method).toBe("PUT");
    expect(bodies()).toEqual([{ push_token: "fcm-1" }]);
    // The same token again is not sent twice; a refreshed one is.
    await push.registerPushToken("fcm-1");
    await push.registerPushToken("fcm-2");
    expect(bodies()).toEqual([{ push_token: "fcm-1" }, { push_token: "fcm-2" }]);
  });

  it("clears the token on the server at sign-out, even if the call fails", async () => {
    push.syncPushToken();
    await push.registerPushToken("fcm-1");
    request.mockRejectedValueOnce(new Error("offline"));
    await expect(push.releasePushToken()).resolves.toBeUndefined();
    expect(bodies().at(-1)).toEqual({ push_token: null });
    // Signed out: a new token is only held.
    request.mockClear();
    await push.registerPushToken("fcm-3");
    expect(request).not.toHaveBeenCalled();
  });

  it("does not call while offline and never throws on a refused call", async () => {
    push.syncPushToken();
    online = false;
    expect(await push.registerPushToken("fcm-1")).toBe(false);
    expect(request).not.toHaveBeenCalled();
    online = true;
    request.mockRejectedValueOnce(new Error("422"));
    expect(await push.registerPushToken("fcm-1")).toBe(false);
  });
});
