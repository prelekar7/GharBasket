import { ACCESS_COOKIE, REFRESH_COOKIE, staffRoleOf } from "./session.js";

/**
 * Server-side calls to the API's auth endpoints (`/v1/auth/*`) and the relay of the cookies they set.
 * Used by the sign-in and sign-out Server Actions and by the session Route Handler
 * (app/v1/auth/session/route.js); everything else goes through lib/api.js.
 *
 * How cookies flow (real mode, `GB_API_MOCK=false`):
 *
 * The browser only ever talks to the console; the console's server talks to the API. The API
 * answers sign-in and refresh with `Set-Cookie: __Host-gb_at` (Path=/) and `gb_rt` (Path=/v1/auth),
 * both httpOnly, Secure, SameSite=Lax (M1 §3.1, ADR-0024). We copy those lines onto the console's own
 * response with `relayCookies`, attribute for attribute, so the browser stores them for the
 * console's origin, and later page requests carry `__Host-gb_at`, which lib/api.js forwards to
 * the API as a `cookie` header.
 *
 * We chose this over a `/v1/*` rewrite (what ADR-0024 describes for the apps): the console has no
 * browser code that calls the API, so a rewrite would only publish the whole API on the console's
 * origin. Server Actions and one Route Handler copy cookies instead, and nothing else is exposed.
 *
 * `gb_rt` keeps `Path=/v1/auth`, so the browser sends it only to `/v1/auth/*` on the console, and
 * the Route Handler that lives there is the one place that can refresh. That is why a refresh is a
 * redirect round trip (proxy.js and lib/api.js send the browser to `/v1/auth/session?next=…`, which
 * refreshes and sends it back) and not a transparent retry inside the failing request.
 *
 * `Secure` cookies are accepted by browsers on http://localhost (not by every browser on a LAN
 * address), which is what the API sends in dev too.
 */

/**
 * What `await cookies()` is in a Server Action or Route Handler.
 * @typedef {{ set: (name: string, value: string, options?: object) => unknown, delete: (name: string) => unknown }} CookieStore
 */

/** The API's base URL with no trailing slash. */
export function apiBase() {
  const base = process.env.GB_API_URL;
  if (!base) throw new Error("GB_API_URL is not set (needed when GB_API_MOCK=false)");
  return base.replace(/\/$/, "");
}

/**
 * One `Set-Cookie` line as the arguments of `cookies().set(name, value, options)`.
 * @param {string} line
 */
export function parseSetCookie(line) {
  const [pair = "", ...attributes] = line.split(";").map((part) => part.trim());
  const eq = pair.indexOf("=");
  /** @type {{ path?: string, maxAge?: number, expires?: Date, httpOnly?: boolean, secure?: boolean, sameSite?: "lax" | "strict" | "none" }} */
  const options = {};
  for (const attribute of attributes) {
    const [key = "", ...rest] = attribute.split("=");
    const value = rest.join("=");
    switch (key.toLowerCase()) {
      case "path":
        options.path = value;
        break;
      case "max-age":
        options.maxAge = Number(value);
        break;
      case "expires":
        options.expires = new Date(value);
        break;
      case "httponly":
        options.httpOnly = true;
        break;
      case "secure":
        options.secure = true;
        break;
      case "samesite":
        options.sameSite = /** @type {"lax" | "strict" | "none"} */ (value.toLowerCase());
        break;
    }
  }
  return { name: pair.slice(0, eq), value: pair.slice(eq + 1), options };
}

/**
 * Copies the cookies of an API response onto the console's response.
 * @param {CookieStore} store `await cookies()`
 * @param {string[]} setCookies the `Set-Cookie` lines (`response.headers.getSetCookie()`)
 */
export function relayCookies(store, setCookies) {
  for (const line of setCookies) {
    const { name, value, options } = parseSetCookie(line);
    store.set(name, value, options);
  }
}

/**
 * The value of one cookie among `Set-Cookie` lines, or null.
 * @param {string[]} setCookies
 * @param {string} name
 */
export function cookieValue(setCookies, name) {
  for (const line of setCookies) {
    const parsed = parseSetCookie(line);
    if (parsed.name === name) return parsed.value;
  }
  return null;
}

/**
 * @typedef {object} AuthResult
 * @property {boolean} ok
 * @property {number} status
 * @property {Record<string, unknown>} body the parsed JSON (a problem when not ok)
 * @property {string[]} setCookies
 */

/**
 * A call to the API as a browser would make it (`x-gb-client: web`), by a server. Sent without an
 * `Origin` header: the API's CSRF rule only refuses an Origin outside WEB_ORIGINS, and a request
 * with none passes (checked against the dev API on 8010 for GET, POST and exchange).
 * @param {"GET" | "POST"} method
 * @param {string} path
 * @param {{ body?: unknown, cookies?: Record<string, string> }} [options]
 * @returns {Promise<AuthResult>}
 */
export async function authFetch(method, path, { body, cookies = {} } = {}) {
  /** @type {Record<string, string>} */
  const headers = { accept: "application/json", "x-gb-client": "web" };
  const cookie = Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
  if (cookie) headers.cookie = cookie;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(apiBase() + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const isJson = /json/.test(response.headers.get("content-type") ?? "");
  const parsed = isJson ? await response.json().catch(() => null) : null;
  return {
    ok: response.ok,
    status: response.status,
    body: parsed && typeof parsed === "object" ? parsed : {},
    setCookies: response.headers.getSetCookie(),
  };
}

/**
 * `POST /v1/auth/exchange`: a phone proof for a session. `dev_phone` signs in without an OTP
 * (the API allows it only when APP_ENV=dev); `firebase_id_token` is the real thing and arrives with
 * Firebase phone auth in the console.
 * @param {{ dev_phone: string } | { firebase_id_token: string }} proof
 */
export function exchange(proof) {
  return authFetch("POST", "/v1/auth/exchange", {
    body: { ...proof, device: { platform: "web" } },
  });
}

/**
 * `GET /v1/me` with an access token, for the moment before the browser holds the cookie.
 * @param {string} accessToken
 */
export function fetchMe(accessToken) {
  return authFetch("GET", "/v1/me", { cookies: { [ACCESS_COOKIE]: accessToken } });
}

/**
 * `POST /v1/auth/logout` for an access token; the API revokes the device and answers with cookies
 * that clear both session cookies, to be relayed.
 * @param {string} accessToken
 */
export function logout(accessToken) {
  return authFetch("POST", "/v1/auth/logout", { cookies: { [ACCESS_COOKIE]: accessToken } });
}

/**
 * @typedef {AuthResult & { accessToken: string | null }} Refreshed
 */

/**
 * `POST /v1/auth/refresh` with the refresh cookie's value. Single-flight per token: tabs and
 * prefetches that arrive together share one call, because every refresh rotates the token and a
 * second use of the old one would otherwise look like theft (the API forgives a replay within 2
 * minutes, but sharing the answer is cheaper). The answer is kept for 2 seconds for late arrivals.
 * @param {string} refreshToken
 * @returns {Promise<Refreshed>}
 */
export function refreshSession(refreshToken) {
  const g = /** @type {{ __gbRefresh?: Map<string, Promise<Refreshed>> }} */ (globalThis);
  g.__gbRefresh ??= new Map();
  const flights = g.__gbRefresh;
  let flight = flights.get(refreshToken);
  if (!flight) {
    flight = authFetch("POST", "/v1/auth/refresh", {
      body: {},
      cookies: { [REFRESH_COOKIE]: refreshToken },
    }).then((result) => ({
      ...result,
      accessToken: cookieValue(result.setCookies, ACCESS_COOKIE),
    }));
    flights.set(refreshToken, flight);
    setTimeout(() => flights.delete(refreshToken), 2000).unref?.();
  }
  return flight;
}

/**
 * The console role for a freshly issued access token: `GET /v1/me`, then the staff role.
 * @param {string} accessToken
 * @param {string} [preferred] the role the person last worked as
 * @returns {Promise<{ role: import("./session.js").ConsoleRole | null, ok: boolean }>} `ok: false` when /v1/me failed, `role: null` when they hold no staff role
 */
export async function resolveStaffRole(accessToken, preferred) {
  const me = await fetchMe(accessToken);
  if (!me.ok) return { role: null, ok: false };
  const roles = /** @type {import("./session.js").MeRole[]} */ (
    Array.isArray(me.body.roles) ? me.body.roles : []
  );
  return { role: staffRoleOf(roles, preferred), ok: true };
}

/**
 * Clears the API's two session cookies on the browser, with the attributes they were set with (a
 * `__Host-` cookie can only be replaced by one that is Secure with Path=/).
 * @param {CookieStore} store `await cookies()`
 */
export function clearSessionCookies(store) {
  const same = { maxAge: 0, httpOnly: true, secure: true, sameSite: "lax" };
  store.set(ACCESS_COOKIE, "", { ...same, path: "/" });
  store.set(REFRESH_COOKIE, "", { ...same, path: "/v1/auth" });
}
