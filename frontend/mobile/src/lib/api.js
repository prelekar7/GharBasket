// API client for the frozen auth contract (docs/modules/M1-identity.md §3.1 and Part 3).
// Web (PWA): same-origin relative /v1, httpOnly cookies, `X-GB-Client: web`; JS never sees tokens.
// Native (Capacitor, `--mode native`): absolute VITE_API_BASE_URL, `Authorization: Bearer`.
// On the dev server only, VITE_API_MOCK=true answers from MSW handlers (see lib/env.js).
// Replace with the generated frontend/packages/api-client after `just gen-api`; never hand-edit it.
import i18n from "@/i18n.js";
import { API_BASE as BASE, MOCK, NATIVE } from "@/lib/env.js";
import { tokens } from "@/lib/tokens.js";

/** @typedef {"customer" | "captain" | "kitchen" | "rider" | "packer"} FieldRole */
/** @typedef {{ role: string, scope_type: string | null, scope_id: string | null, status: "verified" | "pending" }} RoleGrant */
/** @typedef {{ id: string, phone_e164: string, name: string | null, locale: "en-IN" | "hi-IN" | "mr-IN", senior_mode: boolean }} UserOut */
/** @typedef {{ access_token: string, access_expires_in: number, refresh_token: string, is_new_user: boolean, user: UserOut }} ExchangeOut */

export class ApiError extends Error {
  /** @param {{ title?: string, status: number, code?: string, detail?: string }} problem RFC 9457 problem+json */
  constructor(problem) {
    super(problem.title ?? `HTTP ${problem.status}`);
    this.status = problem.status;
    this.code = problem.code ?? "http.error";
    this.detail = problem.detail;
  }

  /** @param {Response} res */
  static async from(res) {
    const type = res.headers.get("content-type") ?? "";
    const body = type.includes("json") ? await res.json().catch(() => ({})) : {};
    return new ApiError({ ...body, status: res.status });
  }
}

/** @typedef {{ method?: string, body?: unknown, headers?: Record<string, string> }} RequestOptions */

/** @type {string | null} */
let activeRole = null;
/** `X-Active-Role: <role>:<scope_id>` (or just `<role>` when unscoped), set by the session store. */
export function setActiveRoleHeader(/** @type {string | null} */ value) {
  activeRole = value;
}

let onExpired = () => {};
/** Called once a refresh has failed, so the app can show login. */
export function onSessionExpired(/** @type {() => void} */ fn) {
  onExpired = fn;
}

/**
 * @param {string} path
 * @param {RequestOptions} [init]
 */
async function send(path, { method = "GET", body, headers: extra } = {}) {
  /** @type {Record<string, string>} */
  const headers = { accept: "application/json", "accept-language": i18n.language, ...extra };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (activeRole) headers["x-active-role"] = activeRole;
  if (NATIVE) {
    if (tokens.access) headers.authorization = `Bearer ${tokens.access}`;
  } else {
    headers["x-gb-client"] = "web";
  }
  const request = new Request(new URL(BASE + path, location.href), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: NATIVE ? "omit" : "same-origin",
  });
  // `DEV` inline (as well as inside MOCK) lets the bundler drop the mocks from every build.
  if (import.meta.env.DEV && MOCK) {
    const { mockFetch } = await import("@/mocks/index.js");
    return mockFetch(request);
  }
  return fetch(request);
}

/** @type {Promise<boolean> | null} */
let refreshing = null;
/** Whether the last refresh failed because the session is over (401), not because the server was busy. */
let ended = false;

/**
 * POST /v1/auth/refresh, single-flight: one refresh at a time in this tab, and on the web one at a
 * time across tabs too (navigator.locks), so a rotating refresh token is never used twice.
 * @returns {Promise<boolean>} whether the session is valid again
 */
export function refresh() {
  refreshing ??= (async () => {
    const run = async () => {
      const refreshToken = await tokens.refreshToken();
      ended = NATIVE && !refreshToken;
      if (ended) return false;
      const res = await send("/v1/auth/refresh", {
        method: "POST",
        body: NATIVE ? { refresh_token: refreshToken } : {},
      });
      // Only a 401 means the refresh token is dead. A 429 or a cold-start 503 keeps the
      // session, so a busy server never signs anyone out.
      ended = res.status === 401;
      if (ended) await tokens.clear();
      if (!res.ok) return false;
      await tokens.save(await res.json());
      return true;
    };
    try {
      return !NATIVE && navigator.locks
        ? await navigator.locks.request("gb-refresh", run)
        : await run();
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** Calls that must never trigger a refresh: they are how a session starts or renews. */
const NO_RETRY = new Set(["/v1/auth/exchange", "/v1/auth/refresh"]);

/**
 * Any API call. A 401 triggers one refresh and one retry; if that fails the session has expired.
 * Logout retries too: otherwise, after 15 idle minutes, it would get a 401, the server would never
 * clear the cookies, and the next person on this device would be signed back in.
 * @param {string} path
 * @param {RequestOptions} [init]
 */
export async function request(path, init) {
  let res = await send(path, init);
  if (res.status === 401 && !NO_RETRY.has(path)) {
    if (await refresh()) {
      res = await send(path, init);
      if (res.status === 401) onExpired();
    } else if (ended) onExpired();
  }
  if (!res.ok) throw await ApiError.from(res);
  return res.status === 204 ? null : res.json();
}

/**
 * POST /v1/auth/exchange with a Firebase ID token, or `dev_phone` against a local dev backend
 * only (never staging).
 * @param {{ firebaseIdToken?: string, devPhone?: string }} input
 * @returns {Promise<ExchangeOut>}
 */
export async function exchange({ firebaseIdToken, devPhone }) {
  const device = { platform: NATIVE ? "android" : "web", push_token: null };
  const body = firebaseIdToken
    ? { firebase_id_token: firebaseIdToken, device }
    : { dev_phone: devPhone, device };
  const out = await request("/v1/auth/exchange", { method: "POST", body });
  await tokens.save(out);
  return out;
}

/** GET /v1/me @returns {Promise<UserOut & { roles: RoleGrant[] }>} */
export const me = () => request("/v1/me");

/**
 * POST /v1/auth/logout: this device only. The server ends the signed-in session and clears the
 * cookies; local tokens are cleared even if the call fails.
 */
export async function logout() {
  try {
    await request("/v1/auth/logout", { method: "POST" });
  } finally {
    await tokens.clear();
  }
}

/** POST /v1/auth/logout-all: every device of this user. */
export async function logoutAll() {
  try {
    await request("/v1/auth/logout-all", { method: "POST" });
  } finally {
    await tokens.clear();
  }
}
