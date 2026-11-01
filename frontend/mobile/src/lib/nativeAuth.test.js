import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The Android app's token handling, against a fake secure storage and a fake API.

/** @type {Map<string, string>} */
const store = new Map();
const storage = {
  setKeyPrefix: vi.fn(async () => {}),
  get: vi.fn(async (/** @type {string} */ k) => store.get(k) ?? null),
  set: vi.fn(async (/** @type {string} */ k, /** @type {string} */ v) => {
    store.set(k, v);
  }),
  remove: vi.fn(async (/** @type {string} */ k) => store.delete(k)),
};
vi.mock("@aparajita/capacitor-secure-storage", () => ({ SecureStorage: storage }));
vi.mock("@/features/notifications/pushToken.js", () => ({
  releasePushToken: vi.fn(async () => {}),
  stopPushToken: vi.fn(),
  syncPushToken: vi.fn(),
}));
vi.mock("@/lib/localData.js", () => ({ clearLocalData: vi.fn(async () => {}) }));
vi.mock("@/lib/queryClient.js", () => ({ queryClient: { cancelQueries: vi.fn(async () => {}) } }));

const PROFILE = { id: "u1", phone_e164: "+919800000002", roles: [] };

/** @param {number} status @param {unknown} [body] */
const reply = (status, body = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
  });

/** @returns {{ promise: Promise<void>, resolve: () => void }} */
function deferred() {
  /** @type {() => void} */
  let resolve = () => {};
  const promise = new Promise((r) => {
    resolve = () => r(undefined);
  });
  return { promise, resolve };
}

/** @type {{ url: string, auth: string | null, body: any }[]} */
let calls;
/** Answers per path; a function can wait (a slow server). @type {Record<string, (req: Request, body: any) => Response | Promise<Response>>} */
let routes;

async function load() {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: true, MOCK: false, API_BASE: "https://api.test" }));
  vi.stubEnv("MODE", "native");
  const api = await import("@/lib/api.js");
  const { tokens } = await import("@/lib/tokens.js");
  const { useSession } = await import("@/lib/session.js");
  return { api, tokens, useSession };
}

beforeEach(() => {
  store.clear();
  calls = [];
  routes = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (/** @type {Request} */ req) => {
      const path = new URL(req.url).pathname;
      const text = await req.clone().text();
      calls.push({
        url: path,
        auth: req.headers.get("authorization"),
        body: text ? JSON.parse(text) : undefined,
      });
      const route = routes[path];
      if (!route) throw new Error(`unexpected ${path}`);
      return route(req, text ? JSON.parse(text) : undefined);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.doUnmock("@/lib/env.js");
});

describe("restore on a cold start", () => {
  it("answers at once, without any request, when no one ever signed in", async () => {
    const { useSession } = await load();
    expect(await useSession.getState().restore()).toBe(false);
    expect(calls).toEqual([]);
  });

  it("refreshes first and then asks /v1/me, never sending /v1/me to a certain 401", async () => {
    store.set("refresh_token", "r1");
    routes["/v1/auth/refresh"] = () => reply(200, { access_token: "a2", refresh_token: "r2" });
    routes["/v1/me"] = () => reply(200, PROFILE);
    const { useSession, tokens } = await load();
    expect(await useSession.getState().restore()).toBe(true);
    expect(calls.map((c) => [c.url, c.auth])).toEqual([
      ["/v1/auth/refresh", null],
      ["/v1/me", "Bearer a2"],
    ]);
    expect(await tokens.refreshToken()).toBe("r2");
  });

  it("is false when the stored refresh token is dead, and forgets it", async () => {
    store.set("refresh_token", "dead");
    routes["/v1/auth/refresh"] = () => reply(401, { code: "auth.refresh_reused" });
    const { useSession } = await load();
    expect(await useSession.getState().restore()).toBe(false);
    expect(store.has("refresh_token")).toBe(false);
    expect(calls.map((c) => c.url)).toEqual(["/v1/auth/refresh"]);
  });
});

describe("a refresh that was already running when the person signed in", () => {
  it("does not wipe the new session when its old refresh token turns out dead", async () => {
    // The emulator bug: the app started with a stale refresh token, GET /v1/me got a 401 and
    // POST /v1/auth/refresh went to a sleeping server. Meanwhile the person signed in. 16 s later the
    // 401 came back, removed the NEW refresh token and sent the app back to login.
    store.set("refresh_token", "stale");
    const slow = deferred();
    routes["/v1/me"] = (req) =>
      req.headers.get("authorization") === "Bearer a-new"
        ? reply(200, PROFILE)
        : reply(401, { code: "auth.unauthenticated" });
    routes["/v1/auth/refresh"] = async () => {
      await slow.promise;
      return reply(401, { code: "auth.refresh_reused" });
    };
    routes["/v1/auth/exchange"] = () =>
      reply(200, { access_token: "a-new", refresh_token: "r-new", user: PROFILE });
    const { api, tokens } = await load();
    const expired = vi.fn();
    api.onSessionExpired(expired);

    const early = api.me(); // 401, then refresh() with "stale", which waits for the server
    await vi.waitFor(() => expect(calls.map((c) => c.url)).toContain("/v1/auth/refresh"));
    await api.exchange({ firebaseIdToken: "fb" }); // the person signs in
    slow.resolve(); // the old refresh is answered 401 now

    expect(await early).toEqual(PROFILE); // retried with the new access token
    expect(await tokens.refreshToken()).toBe("r-new");
    expect(tokens.access).toBe("a-new");
    expect(expired).not.toHaveBeenCalled();
  });

  it("does not show login for an old request answered 401 after the new sign-in", async () => {
    const slow = deferred();
    routes["/v1/me"] = async (req) => {
      if (req.headers.get("authorization") === "Bearer a-new") return reply(200, PROFILE);
      await slow.promise;
      return reply(401);
    };
    routes["/v1/auth/exchange"] = () =>
      reply(200, { access_token: "a-new", refresh_token: "r-new", user: PROFILE });
    const { api, tokens } = await load();
    const expired = vi.fn();
    api.onSessionExpired(expired);
    const old = api.me();
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    await api.exchange({ firebaseIdToken: "fb" });
    slow.resolve();
    expect(await old).toEqual(PROFILE);
    expect(expired).not.toHaveBeenCalled();
    expect(await tokens.refreshToken()).toBe("r-new");
  });
});

describe("exchange", () => {
  it("is single-flight: a double submit makes one session", async () => {
    const gate = deferred();
    routes["/v1/auth/exchange"] = async () => {
      await gate.promise;
      return reply(200, { access_token: "a1", refresh_token: "r1", user: PROFILE });
    };
    const { api } = await load();
    const first = api.exchange({ firebaseIdToken: "fb" });
    const second = api.exchange({ firebaseIdToken: "fb" });
    gate.resolve();
    expect(await first).toBe(await second);
    expect(calls.filter((c) => c.url === "/v1/auth/exchange")).toHaveLength(1);
    expect(calls[0].body.device).toEqual({ platform: "android", push_token: null });
    expect(calls[0].body.keep_account).toBeUndefined();
  });

  // M1 §3.5: a sign-in during the deletion grace period asks "Keep my account?" first.
  const pending = () => {
    routes["/v1/auth/exchange"] = (_req, body) =>
      body.keep_account
        ? reply(200, {
            access_token: "a1",
            refresh_token: "r1",
            deletion_cancelled: true,
            user: PROFILE,
          })
        : reply(409, { code: "identity.deletion_pending", purge_on: "2026-11-30" });
  };

  it("does not keep the account without consent: the 409 reaches the caller and saves nothing", async () => {
    pending();
    const { api, tokens } = await load();
    const error = await api.exchange({ firebaseIdToken: "fb" }).catch((e) => e);
    expect(error.code).toBe("identity.deletion_pending");
    expect(error.extra.purge_on).toBe("2026-11-30");
    // Cancel is "do nothing more": one request, no keep_account, no tokens.
    expect(calls).toHaveLength(1);
    expect(calls[0].body.keep_account).toBeUndefined();
    expect(store.size).toBe(0);
    expect(tokens.access).toBeNull();
  });

  it("repeats the exchange with the same ID token and keep_account on consent", async () => {
    pending();
    const { api, tokens } = await load();
    await api.exchange({ firebaseIdToken: "fb" }).catch(() => {});
    const out = await api.exchange({ firebaseIdToken: "fb", keepAccount: true });
    expect(out.deletion_cancelled).toBe(true);
    expect(calls.map((c) => [c.body.firebase_id_token, c.body.keep_account])).toEqual([
      ["fb", undefined],
      ["fb", true],
    ]);
    expect(await tokens.refreshToken()).toBe("r1");
  });
});

describe("sign-out on the phone", () => {
  it("removes the refresh token from secure storage and the access token from memory", async () => {
    routes["/v1/auth/logout"] = () => reply(204);
    const { api, tokens, useSession } = await load();
    await tokens.save({ access_token: "a1", refresh_token: "r1" });
    expect(store.get("refresh_token")).toBe("r1");
    await useSession.getState().signOut();
    expect(store.has("refresh_token")).toBe(false);
    expect(tokens.access).toBeNull();
    expect(await tokens.refreshToken()).toBeNull();
    expect(calls[0].auth).toBe("Bearer a1");
    expect(api).toBeDefined();
  });

  it("sends no more calls for the old session, and a 401 already in flight stays silent", async () => {
    // On the vivo phone, four screens were still refetching when the person signed out; their
    // 401s started refreshes and reported an expired session.
    const slow = deferred();
    const gate = deferred();
    routes["/v1/wallet"] = async () => {
      await slow.promise;
      return reply(401);
    };
    routes["/v1/notifications"] = () => reply(200, []);
    routes["/v1/auth/refresh"] = () => reply(200, { access_token: "a2", refresh_token: "r2" });
    routes["/v1/auth/logout"] = async () => {
      await gate.promise;
      return reply(204);
    };
    const { api, tokens, useSession } = await load();
    const expired = vi.fn();
    api.onSessionExpired(expired);
    await tokens.save({ access_token: "a1", refresh_token: "r1" });

    const inFlight = api.request("/v1/wallet").catch((e) => e);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    const signingOut = useSession.getState().signOut();
    await vi.waitFor(() => expect(calls.map((c) => c.url)).toContain("/v1/auth/logout"));

    const refetch = await api.request("/v1/notifications").catch((e) => e);
    expect(refetch.code).toBe("auth.signing_out");
    slow.resolve();
    gate.resolve();
    expect((await inFlight).status).toBe(401);
    await signingOut;

    expect(calls.map((c) => c.url)).toEqual(["/v1/wallet", "/v1/auth/logout"]);
    expect(expired).not.toHaveBeenCalled();
    expect(store.has("refresh_token")).toBe(false);
    // Signed out: a screen that refetches after the sign-out (the cache was cleared under it) still
    // sends nothing, until the next sign-in.
    expect((await api.request("/v1/notifications").catch((e) => e)).code).toBe("auth.signing_out");
    routes["/v1/auth/exchange"] = () => reply(200, { access_token: "a3", refresh_token: "r3" });
    await api.exchange({ devPhone: "+919800001001" });
    expect(await api.request("/v1/notifications")).toEqual([]);
  });

  it("clears the tokens even when the server cannot be reached", async () => {
    routes["/v1/auth/logout-all"] = () => {
      throw new TypeError("Failed to fetch");
    };
    const { tokens, useSession } = await load();
    await tokens.save({ access_token: "a1", refresh_token: "r1" });
    await useSession.getState().signOut({ everywhere: true });
    expect(store.has("refresh_token")).toBe(false);
    expect(tokens.access).toBeNull();
  });
});
