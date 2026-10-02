// API client for the frozen auth contract (docs/modules/M1-identity.md §3.1 and Part 3).
// Web (PWA): same-origin relative /v1, httpOnly cookies, `X-GB-Client: web`; JS never sees tokens.
// Native (Capacitor, `--mode native`): absolute VITE_API_BASE_URL, `Authorization: Bearer`.
// Until the backend is live, VITE_API_MOCK (on unless "false") answers from MSW handlers.
// Replace with the generated frontend/packages/api-client after `just gen-api`; never hand-edit it.
import i18n from "@/i18n.js";
import { tokens } from "@/lib/tokens.js";

const NATIVE = import.meta.env.MODE === "native";
const BASE = NATIVE ? (import.meta.env.VITE_API_BASE_URL ?? "") : "";
const MOCK = import.meta.env.VITE_API_MOCK !== "false";

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
 * @param {{ method?: string, body?: unknown }} [init]
 */
async function send(path, { method = "GET", body } = {}) {
  /** @type {Record<string, string>} */
  const headers = { accept: "application/json", "accept-language": i18n.language };
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
  if (MOCK) {
    const { mockFetch } = await import("@/mocks/index.js");
    return mockFetch(request);
  }
  return fetch(request);
}

/** @type {Promise<boolean> | null} */
let refreshing = null;

/**
 * POST /v1/auth/refresh, single-flight: one refresh at a time in this tab, and on the web one at a
 * time across tabs too (navigator.locks), so a rotating refresh token is never used twice.
 * @returns {Promise<boolean>} whether the session is valid again
 */
export function refresh() {
  refreshing ??= (async () => {
    const run = async () => {
      const refreshToken = await tokens.refreshToken();
      if (NATIVE && !refreshToken) return false;
      const res = await send("/v1/auth/refresh", {
        method: "POST",
        body: NATIVE ? { refresh_token: refreshToken } : {},
      });
      if (!res.ok) {
        await tokens.clear();
        return false;
      }
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

/**
 * Any API call. A 401 outside /v1/auth/* triggers one refresh and one retry; if that fails the
 * session has expired.
 * @param {string} path
 * @param {{ method?: string, body?: unknown }} [init]
 */
export async function request(path, init) {
  let res = await send(path, init);
  const isAuthCall = path.startsWith("/v1/auth/");
  if (res.status === 401 && !isAuthCall) {
    if (await refresh()) res = await send(path, init);
    if (res.status === 401) onExpired();
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

/** POST /v1/auth/logout: this device only. Local tokens are cleared even if the call fails. */
export async function logout() {
  try {
    await request("/v1/auth/logout", {
      method: "POST",
      body: NATIVE ? { refresh_token: await tokens.refreshToken() } : {},
    });
  } finally {
    await tokens.clear();
  }
}

/** POST /v1/auth/logout-all: every device of this user. */
export async function logoutAll() {
  try {
    await request("/v1/auth/logout-all", { method: "POST", body: {} });
  } finally {
    await tokens.clear();
  }
}
