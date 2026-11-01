import { beforeEach, describe, expect, it, vi } from "vitest";

/** @type {string[]} Calls to the phone and the server, in order. */
let calls = [];
/** @type {Record<string, (event: any) => void>} */
let handlers = {};
let permission = "prompt";
let asked = "granted";

// restoreMocks (vitest.config.js) empties vi.fn implementations before each test: set in beforeEach.
const messaging = {
  createChannel: vi.fn(),
  checkPermissions: vi.fn(),
  requestPermissions: vi.fn(),
  getToken: vi.fn(),
  deleteToken: vi.fn(),
  addListener: vi.fn(),
};
vi.mock("@capacitor-firebase/messaging", () => ({
  FirebaseMessaging: messaging,
  Importance: { High: 4 },
}));

const registerPushToken = vi.fn();
const clearPushToken = vi.fn();
vi.mock("@/features/notifications/pushToken.js", () => ({
  registerPushToken: (/** @type {string} */ t) => registerPushToken(t),
  clearPushToken: () => clearPushToken(),
}));

const toast = vi.fn();
vi.mock("@gharbasket/ui", () => ({ toast: (/** @type {any[]} */ ...a) => toast(...a) }));
vi.mock("@/i18n.js", () => ({ default: { t: (/** @type {string} */ k) => k } }));

/** A stand-in for the session store: just `signedIn`, `getState` and `subscribe`. */
function fakeSession() {
  let state = { signedIn: false };
  /** @type {Array<(s: typeof state, before: typeof state) => void>} */
  const listeners = [];
  return {
    getState: () => state,
    subscribe: (/** @type {any} */ fn) => void listeners.push(fn),
    set(/** @type {boolean} */ signedIn) {
      const before = state;
      state = { signedIn };
      for (const fn of listeners) fn(state, before);
    },
  };
}

/** A router stand-in whose first screen renders when the test says so. */
function fakeRouter() {
  /** @type {(() => void) | null} */
  let onRendered = null;
  return {
    navigate: vi.fn(),
    subscribe: vi.fn((/** @type {string} */ _e, /** @type {() => void} */ fn) => {
      onRendered = fn;
      return () => {};
    }),
    render: () => onRendered?.(),
  };
}

/** Loads push.js against a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  const session = fakeSession();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  vi.doMock("@/lib/session.js", () => ({ useSession: session }));
  const push = await import("./push.js");
  const router = fakeRouter();
  const client = { invalidateQueries: vi.fn() };
  push.startPush(router, /** @type {any} */ (client));
  // Wait for the listeners: the app registers them at start-up, long before anyone signs in (and
  // two imports of the plugin at the same moment are not what a test is about).
  if (native) await vi.waitFor(() => expect(handlers.notificationReceived).toBeTypeOf("function"));
  return { push, session, router, client };
}

const settle = () => vi.waitFor(() => expect(messaging.getToken).toHaveBeenCalled());
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

beforeEach(() => {
  calls = [];
  handlers = {};
  permission = "prompt";
  asked = "granted";
  localStorage.clear();
  messaging.createChannel.mockImplementation(async (/** @type {any} */ o) => {
    calls.push(`channel:${o.id}`);
  });
  messaging.checkPermissions.mockImplementation(async () => ({ receive: permission }));
  messaging.requestPermissions.mockImplementation(async () => {
    calls.push("ask");
    return { receive: asked };
  });
  messaging.getToken.mockImplementation(async () => ({ token: "fcm-1" }));
  messaging.deleteToken.mockImplementation(async () => {
    calls.push("deleteToken");
  });
  messaging.addListener.mockImplementation(
    async (/** @type {string} */ name, /** @type {any} */ fn) => {
      handlers[name] = fn;
      return { remove: async () => {} };
    },
  );
  registerPushToken.mockImplementation(async (/** @type {string} */ t) => {
    calls.push(`register:${t}`);
  });
  clearPushToken.mockImplementation(async () => {
    calls.push("clearPushToken");
  });
});

describe("sign-in", () => {
  it("creates the channel, asks once, then registers the token", async () => {
    const { session } = await load(true);
    session.set(true);
    await settle();
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalledWith("fcm-1"));
    expect(calls).toEqual(["channel:updates", "ask", "register:fcm-1"]);
    expect(messaging.createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ id: "updates", name: "notif.channelName", importance: 4 }),
    );
  });

  it("does not ask when the permission is already granted", async () => {
    permission = "granted";
    const { session } = await load(true);
    session.set(true);
    await settle();
    expect(messaging.requestPermissions).not.toHaveBeenCalled();
  });

  it("starts at once when the app opens already signed in", async () => {
    vi.resetModules();
    permission = "granted";
    const session = fakeSession();
    session.set(true);
    vi.doMock("@/lib/env.js", () => ({ NATIVE: true, MOCK: false, API_BASE: "" }));
    vi.doMock("@/lib/session.js", () => ({ useSession: session }));
    const push = await import("./push.js");
    push.startPush(fakeRouter(), /** @type {any} */ ({ invalidateQueries: vi.fn() }));
    await vi.waitFor(() => expect(handlers.notificationReceived).toBeTypeOf("function"));
    await settle();
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalledWith("fcm-1"));
  });

  it("asks and fetches the token once when the start-up and a sign-in both ask for it", async () => {
    vi.resetModules();
    const session = fakeSession();
    session.set(true);
    vi.doMock("@/lib/env.js", () => ({ NATIVE: true, MOCK: false, API_BASE: "" }));
    vi.doMock("@/lib/session.js", () => ({ useSession: session }));
    const push = await import("./push.js");
    push.startPush(fakeRouter(), /** @type {any} */ ({ invalidateQueries: vi.fn() }));
    // The session restores while the start-up is still registering listeners.
    session.set(false);
    session.set(true);
    await settle();
    await tick();
    expect(messaging.createChannel).toHaveBeenCalledOnce();
    expect(messaging.checkPermissions).toHaveBeenCalledOnce();
    expect(messaging.requestPermissions).toHaveBeenCalledOnce();
    expect(messaging.getToken).toHaveBeenCalledOnce();
    expect(registerPushToken).toHaveBeenCalledOnce();
  });

  it("does it again for the next session after a sign-out", async () => {
    permission = "granted";
    const { session } = await load(true);
    session.set(true);
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalledTimes(1));
    await tick();
    session.set(false);
    await vi.waitFor(() => expect(messaging.deleteToken).toHaveBeenCalled());
    session.set(true);
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalledTimes(2));
    expect(messaging.getToken).toHaveBeenCalledTimes(2);
  });

  it("registers no token and remembers it when the person says no", async () => {
    asked = "denied";
    const { session } = await load(true);
    session.set(true);
    await vi.waitFor(() => expect(messaging.requestPermissions).toHaveBeenCalledOnce());
    await tick();
    expect(messaging.getToken).not.toHaveBeenCalled();
    expect(registerPushToken).not.toHaveBeenCalled();
    expect(localStorage.getItem("gb-push-denied")).toBe("1");
  });

  it("does not ask again after a no, and ignores token refreshes then", async () => {
    localStorage.setItem("gb-push-denied", "1");
    permission = "denied";
    const { session } = await load(true);
    session.set(true);
    await tick();
    expect(messaging.requestPermissions).not.toHaveBeenCalled();
    expect(messaging.getToken).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(handlers.tokenReceived).toBeTypeOf("function"));
    handlers.tokenReceived({ token: "fcm-2" });
    expect(registerPushToken).not.toHaveBeenCalled();
  });

  it("forgets an old no once the phone's settings grant the permission", async () => {
    localStorage.setItem("gb-push-denied", "1");
    permission = "granted";
    const { session } = await load(true);
    session.set(true);
    await settle();
    expect(localStorage.getItem("gb-push-denied")).toBeNull();
  });

  it("still gets a token when the channel cannot be created", async () => {
    messaging.createChannel.mockRejectedValueOnce(new Error("no"));
    permission = "granted";
    const { session } = await load(true);
    session.set(true);
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalledWith("fcm-1"));
  });
});

describe("token refresh", () => {
  it("registers the new token", async () => {
    const { session } = await load(true);
    session.set(true);
    await vi.waitFor(() => expect(handlers.tokenReceived).toBeTypeOf("function"));
    handlers.tokenReceived({ token: "fcm-2" });
    expect(registerPushToken).toHaveBeenCalledWith("fcm-2");
  });
});

describe("sign-out", () => {
  it("clears the server's token first, then deletes the phone's", async () => {
    permission = "granted";
    const { session } = await load(true);
    session.set(true);
    await vi.waitFor(() => expect(registerPushToken).toHaveBeenCalled());
    calls = [];
    session.set(false);
    await vi.waitFor(() => expect(messaging.deleteToken).toHaveBeenCalled());
    expect(calls).toEqual(["clearPushToken", "deleteToken"]);
  });

  it("does not register a token when the person signed out while we waited", async () => {
    permission = "granted";
    let release = () => {};
    messaging.getToken.mockImplementationOnce(
      () => new Promise((resolve) => (release = () => resolve({ token: "late" }))),
    );
    const { session } = await load(true);
    session.set(true);
    await settle();
    session.set(false);
    release();
    await tick();
    expect(registerPushToken).not.toHaveBeenCalledWith("late");
  });
});

describe("taps", () => {
  /** @param {unknown} link */
  const tap = (link) =>
    handlers.notificationActionPerformed({ actionId: "tap", notification: { data: { link } } });

  it("opens a known route once the first screen has rendered", async () => {
    const { router } = await load(true);
    await vi.waitFor(() => expect(handlers.notificationActionPerformed).toBeTypeOf("function"));
    tap("/orders"); // the tap that started the app
    expect(router.navigate).not.toHaveBeenCalled();
    router.render();
    expect(router.navigate).toHaveBeenCalledWith({ to: "/orders" });
    tap("/wallet");
    expect(router.navigate).toHaveBeenLastCalledWith({ to: "/wallet" });
  });

  it("opens every route in the allow-list", async () => {
    const { router, push } = await load(true);
    await vi.waitFor(() => expect(handlers.notificationActionPerformed).toBeTypeOf("function"));
    router.render();
    for (const link of push.LINKS) tap(link);
    expect(router.navigate.mock.calls.map((c) => c[0].to)).toEqual(push.LINKS);
  });

  it.each([
    ["an unknown route", "/admin"],
    ["another site", "https://evil.example/orders"],
    ["a protocol-relative link", "//evil.example"],
    ["a route with a query", "/orders?x=1"],
    ["a script", "javascript:alert(1)"],
    ["not a string", 42],
    ["nothing", undefined],
  ])("ignores %s", async (_name, link) => {
    const { router } = await load(true);
    await vi.waitFor(() => expect(handlers.notificationActionPerformed).toBeTypeOf("function"));
    router.render();
    tap(link);
    handlers.notificationActionPerformed({ actionId: "tap", notification: {} });
    expect(router.navigate).not.toHaveBeenCalled();
  });
});

describe("foreground messages", () => {
  it("refreshes the inbox and shows the title as a toast", async () => {
    const { client } = await load(true);
    await vi.waitFor(() => expect(handlers.notificationReceived).toBeTypeOf("function"));
    handlers.notificationReceived({ notification: { title: "Box on its way", body: "10 min" } });
    expect(client.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["notifications"] });
    expect(toast).toHaveBeenCalledWith("Box on its way", { description: "10 min" });
  });

  it("refreshes the inbox but shows no toast for a message with no title", async () => {
    const { client } = await load(true);
    await vi.waitFor(() => expect(handlers.notificationReceived).toBeTypeOf("function"));
    handlers.notificationReceived({ notification: { data: {} } });
    expect(client.invalidateQueries).toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("the web", () => {
  it("does nothing: no plugin, no listeners, no router subscription", async () => {
    const { session, router } = await load(false);
    session.set(true);
    session.set(false);
    await tick();
    expect(router.subscribe).not.toHaveBeenCalled();
    expect(Object.values(messaging).some((fn) => fn.mock.calls.length > 0)).toBe(false);
    expect(registerPushToken).not.toHaveBeenCalled();
    expect(clearPushToken).not.toHaveBeenCalled();
  });
});
