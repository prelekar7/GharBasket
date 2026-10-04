// MSW handlers for the frozen auth contract (docs/modules/M1-identity.md §3.1, Part 3). Shapes
// must match the backend exactly; later these come from openapi.json. /v1/auth/refresh, logout
// and logout-all are not in §3.1 yet, so their shapes here are our proposal (see the PR).
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
      scope_id: "sunrise-towers",
      status: "verified",
    });
  }
  if (phone === "+919800000003") {
    roles.splice(0, 1, {
      role: "rider",
      scope_type: "hub",
      scope_id: "hub-thane-1",
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
    const phone = refreshTokens.get(token);
    if (!phone) return problem(401, "auth.invalid_token", "Please sign in again.");
    refreshTokens.delete(token); // rotation: each refresh token works once
    saveRefresh();
    return HttpResponse.json(issue(phone));
  }),

  http.post("*/v1/auth/logout", async ({ request }) => {
    const body = /** @type {any} */ (await request.json().catch(() => ({})));
    if (body.refresh_token) refreshTokens.delete(body.refresh_token);
    saveRefresh();
    webSession.set(null);
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
