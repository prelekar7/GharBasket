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

/** @typedef {"customer" | "captain" | "kitchen_partner" | "rider" | "packer"} FieldRole */
/** @typedef {{ role: string, scope_type: "society" | "hub" | "ngo" | null, scope_id: string | null, status: "applied" | "kyc_pending" | "verified" | "suspended" }} RoleGrant */
/** @typedef {{ id: string, phone_e164: string, name: string | null, locale: "en-IN" | "hi-IN" | "mr-IN", senior_mode: boolean }} UserOut */
/** @typedef {{ access_token: string | null, access_expires_in: number, refresh_token: string | null, is_new_user: boolean, user: UserOut }} ExchangeOut */
/** @typedef {{ access_token: string | null, access_expires_in: number, refresh_token: string | null }} TokensOut */
/** @typedef {{ loc: (string | number)[], message: string, type: string }} FieldProblem */

const STANDARD_MEMBERS = new Set([
  "type",
  "title",
  "status",
  "detail",
  "code",
  "instance",
  "request_id",
  "errors",
]);

/**
 * An RFC 9457 problem+json response, the same shape as ApiProblem in packages/api-client (kept as
 * `ApiError` here: nearly every consumer imports that name). Branch on `code`, never on the text.
 */
export class ApiError extends Error {
  /** @param {{ title?: string, status: number, code?: string, detail?: string, request_id?: string, errors?: FieldProblem[] } & Record<string, unknown>} problem the problem body plus the HTTP status */
  constructor(problem) {
    const code = typeof problem.code === "string" ? problem.code : `http.${problem.status}`;
    super(problem.detail ?? problem.title ?? code);
    this.name = "ApiError";
    this.status = problem.status;
    this.code = code;
    this.title = problem.title;
    this.detail = problem.detail;
    /** Extension members, e.g. `household_id` on identity.household_exists, `room_paise` on billing.wallet_limit. */
    this.extra = Object.fromEntries(
      Object.entries(problem).filter(([key]) => !STANDARD_MEMBERS.has(key)),
    );
    /** Field errors of `request.invalid` (422). */
    this.errors = Array.isArray(problem.errors) ? problem.errors : [];
    this.requestId = typeof problem.request_id === "string" ? problem.request_id : null;
  }

  /** @param {Response} res */
  static async from(res) {
    const type = res.headers.get("content-type") ?? "";
    const body = type.includes("json") ? await res.json().catch(() => ({})) : {};
    return new ApiError({ ...(body && typeof body === "object" ? body : {}), status: res.status });
  }
}

/**
 * The message of the first field error of a 422 whose `loc` ends in `name` (the body is
 * `["body", "phone"]`, so "phone" finds it), or undefined.
 * @param {unknown} problem anything: only an ApiError has field errors
 * @param {string} name
 */
export function fieldError(problem, name) {
  if (!(problem instanceof ApiError)) return undefined;
  return problem.errors.find((e) => e.loc.at(-1) === name)?.message;
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
 * @param {RequestOptions} [init]
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

/** POST /v1/auth/logout (no body): this device only. Local tokens are cleared even if the call fails. */
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
