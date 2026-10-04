// MSW handlers for the auth contract (docs/modules/M1-identity.md §3.1, Part 3), matching the
// backend's /v1/auth/exchange, refresh, logout and logout-all. Shapes must match the backend
// exactly; later these are typed from the generated api-client's schema.d.ts.
import { HttpResponse, http } from "msw";

const ACCESS_TTL_S = 900;
const WEB_SESSION_KEY = "gb-mock-web-session"; // stands in for the httpOnly cookie on web

/** @param {number} status @param {string} code @param {string} title */
const problem = (status, code, title) =>
  HttpResponse.json(
    { type: `https://gharbasket.in/problems/${code}`, title, status, detail: title, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** Fictional dev numbers: …02 is a customer who is also Captain of Sunrise Towers, …03 a rider. */
function rolesFor(/** @type {string} */ phone) {
  /** @type {import("@/lib/api.js").RoleGrant[]} */
  const roles = [{ role: "customer", scope_type: null, scope_id: null, status: "verified" }];
  if (phone === "+919800000002") {
    roles.push({
      role: "captain",
      scope_type: "society",
      scope_id: "0190f0aa-5000-7000-8000-000000000001", // Sunrise Towers
      status: "verified",
    });
  }
  if (phone === "+919800000003") {
    roles.splice(0, 1, {
      role: "rider",
      scope_type: "hub",
      scope_id: "0190f0aa-6000-7000-8000-000000000001", // Kharghar hub
      status: "verified",
    });
  }
  return roles;
}

const userFor = (/** @type {string} */ phone) => ({
  id: `0190f0aa-0000-7000-8000-${phone.replace(/\D/g, "").slice(-12).padStart(12, "0")}`,
  phone_e164: phone,
  name: null,
  locale: /** @type {const} */ ("en-IN"),
  senior_mode: false,
});

/** @type {Map<string, { phone: string, exp: number }>} */ const accessTokens = new Map();
// Refresh tokens survive reloads (like a real server would keep them); access tokens do not need to.
const REFRESH_KEY = "gb-mock-refresh-tokens";
/** @type {Map<string, string>} */ const refreshTokens = new Map(
  JSON.parse(localStorage.getItem(REFRESH_KEY) ?? "[]"),
); // refresh token → phone
const saveRefresh = () => localStorage.setItem(REFRESH_KEY, JSON.stringify([...refreshTokens]));
/** Rotated-out refresh tokens → phone. Presenting one again means it was copied (M1 §3.1). */
/** @type {Map<string, string>} */ const usedRefresh = new Map();
let counter = Date.now();

function issue(/** @type {string} */ phone) {
  const access = `mock-access-${++counter}`;
  const refreshToken = `mock-refresh-${counter}`;
  accessTokens.set(access, { phone, exp: Date.now() + ACCESS_TTL_S * 1000 });
  refreshTokens.set(refreshToken, phone);
  saveRefresh();
  return { access_token: access, access_expires_in: ACCESS_TTL_S, refresh_token: refreshToken };
}

const webSession = {
  get: () => {
    try {
      return JSON.parse(localStorage.getItem(WEB_SESSION_KEY) ?? "null");
    } catch {
      return null;
    }
  },
  set: (/** @type {{ phone: string, exp: number } | null} */ v) =>
    v
      ? localStorage.setItem(WEB_SESSION_KEY, JSON.stringify(v))
      : localStorage.removeItem(WEB_SESSION_KEY),
};

/** Who is calling: Bearer token on native, the cookie stand-in on web. Expired → null. */
function caller(/** @type {Request} */ request) {
  const bearer = request.headers.get("authorization")?.replace(/^Bearer /, "");
  const session = bearer ? accessTokens.get(bearer) : webSession.get();
  return session && session.exp > Date.now() ? session.phone : null;
}

/** Phone from a Firebase ID token's `phone_number` claim (the mock does not verify signatures). */
function phoneFromIdToken(/** @type {string} */ token) {
  try {
    return (
      JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).phone_number ??
      null
    );
  } catch {
    return null;
  }
}

export const handlers = [
  http.post("*/v1/auth/exchange", async ({ request }) => {
    const body = /** @type {any} */ (await request.json());
    if (!body.device?.platform) return problem(422, "request.invalid", "The request is not valid.");
    const phone = body.firebase_id_token
      ? phoneFromIdToken(body.firebase_id_token)
      : body.dev_phone;
    if (!phone) return problem(401, "auth.invalid_token", "The sign-in token is not valid.");
    if (phone === "+919800000429")
      return problem(429, "auth.rate_limited", "Too many attempts. Try again later.");
    const pair = issue(phone);
    if (request.headers.get("x-gb-client") === "web")
      webSession.set({ phone, exp: Date.now() + ACCESS_TTL_S * 1000 });
    // +91 98000 00005 is a brand-new household, to walk through onboarding.
    return HttpResponse.json({
      ...pair,
      is_new_user: phone === "+919800000005",
      user: userFor(phone),
    });
  }),

  http.get("*/v1/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    return HttpResponse.json({ ...userFor(phone), roles: rolesFor(phone) });
  }),

  http.post("*/v1/auth/refresh", async ({ request }) => {
    if (request.headers.get("x-gb-client") === "web") {
      const session = webSession.get();
      if (!session) return problem(401, "auth.invalid_token", "Please sign in again.");
      webSession.set({ phone: session.phone, exp: Date.now() + ACCESS_TTL_S * 1000 });
      return HttpResponse.json({ access_expires_in: ACCESS_TTL_S });
    }
    const { refresh_token: token } = /** @type {any} */ (await request.json());
    const reused = usedRefresh.get(token);
    if (reused) {
      // A rotated token came back: someone copied it, so the whole session ends.
      for (const [t, owner] of refreshTokens) if (owner === reused) refreshTokens.delete(t);
      saveRefresh();
      return problem(
        401,
        "auth.refresh_reused",
        "This sign-in was used from somewhere else, so it has been ended. Sign in again.",
      );
    }
    const phone = refreshTokens.get(token);
    if (!phone) return problem(401, "auth.invalid_token", "Please sign in again.");
    refreshTokens.delete(token); // rotation: each refresh token works once
    usedRefresh.set(token, phone);
    saveRefresh();
    return HttpResponse.json(issue(phone));
  }),

  // Like the backend: only a signed-in caller can log out (an expired access token gets a 401, so
  // the app refreshes and retries), and it ends this device's session and clears the cookies.
  http.post("*/v1/auth/logout", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    const bearer = request.headers.get("authorization")?.replace(/^Bearer /, "");
    if (bearer) {
      accessTokens.delete(bearer);
      // One device = one session: its refresh token is the newest one issued for this phone.
      const mine = [...refreshTokens].filter(([, owner]) => owner === phone).at(-1);
      if (mine) refreshTokens.delete(mine[0]);
      saveRefresh();
    } else webSession.set(null);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("*/v1/auth/logout-all", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    for (const [token, owner] of refreshTokens) if (owner === phone) refreshTokens.delete(token);
    saveRefresh();
    for (const [token, s] of accessTokens) if (s.phone === phone) accessTokens.delete(token);
    webSession.set(null);
    return new HttpResponse(null, { status: 204 });
  }),
];

/** Dev helper: make the current access token expire now, to exercise refresh. */
export function expireAccessNow() {
  for (const s of accessTokens.values()) s.exp = 0;
  const web = webSession.get();
  if (web) webSession.set({ ...web, exp: 0 });
}

/** Mock-only: requests received by POST /v1/dev/echo, to check outbox replays and their keys. */
export const received = /** @type {{ key: string | null, body: unknown }[]} */ ([]);
handlers.push(
  http.post("*/v1/dev/echo", async ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Your session has expired.");
    received.push({ key: request.headers.get("idempotency-key"), body: await request.json() });
    return HttpResponse.json({ ok: true }, { status: 201 });
  }),
);
