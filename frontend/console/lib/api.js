import { getResponse } from "msw";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { apiBase } from "./auth-api.js";
import { ACCESS_COOKIE, FRESH_COOKIE, isMockApi, SESSION_COOKIE } from "./session.js";

/**
 * The API layer of the console, for Server Components and Server Actions (never the browser: the
 * browser talks to the console, the console talks to the API).
 *
 * - Mock mode (default; `GB_API_MOCK` anything but "false"): requests are answered in this process
 *   from the MSW handlers in mocks/, so pages work before the backend does.
 * - Real mode (`GB_API_MOCK=false`): requests go to `GB_API_URL` (e.g. http://localhost:8010) with
 *   the session as the API wants it from a browser (M1 §3.1): the `__Host-gb_at` cookie, forwarded
 *   from the incoming request, plus `x-gb-client: web`, and `X-Active-Role: <role>[:<scope id>]`
 *   (lib/auth.js `activeRole`) so hub-scoped endpoints know which hat the person is wearing. How the
 *   cookies get there, and how an expired session is refreshed, is in lib/auth-api.js. A 401 sends
 *   the browser through `/v1/auth/session` (refresh, then back to this page).
 *
 * These read the request's cookies, so call them below a Suspense boundary (the console's
 * `loading.jsx` is one) and never inside `"use cache"`.
 */

const MOCK_ORIGIN = "http://mock.gharbasket.local";

/**
 * An RFC 9457 problem+json response. Branch on `code` ("orders.cutoff_passed"), never on `status`
 * or the text. Same shape as ApiProblem in @gharbasket/api-client; switch to importing that once
 * the console depends on the package.
 */
export class ApiProblem extends Error {
  /**
   * @param {number} status
   * @param {Record<string, unknown>} body the parsed problem (or {} when the body was not JSON)
   */
  constructor(status, body) {
    const code = typeof body.code === "string" ? body.code : `http.${status}`;
    super(typeof body.detail === "string" ? body.detail : code);
    this.name = "ApiProblem";
    this.status = status;
    this.code = code;
    /** Extension members, e.g. `household_id` on identity.household_exists. */
    this.extra = Object.fromEntries(
      Object.entries(body).filter(
        ([key]) =>
          ![
            "type",
            "title",
            "status",
            "detail",
            "code",
            "instance",
            "request_id",
            "errors",
          ].includes(key),
      ),
    );
    /** Field errors of `request.invalid` (422). */
    this.errors = Array.isArray(body.errors) ? body.errors : [];
    this.requestId = typeof body.request_id === "string" ? body.request_id : null;
  }
}

/**
 * @typedef {Record<string, string | number | boolean | null | undefined | (string | number)[]>} SearchParams
 * @typedef {object} GetOptions
 * @property {SearchParams} [searchParams] null, undefined and "" are left out; arrays repeat the key
 * @property {Record<string, string>} [headers]
 * @property {boolean} [noActiveRole] real mode: leave out X-Active-Role (only `GET /v1/me`, which says what the roles are)
 * @typedef {object} WriteOptions
 * @property {string} [idempotencyKey] one per user intent, reused when the same request is retried
 * @property {Record<string, string>} [headers]
 * @property {boolean} [noActiveRole]
 */

/** @param {string} path @param {SearchParams} [searchParams] */
function withQuery(path, searchParams = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    for (const v of Array.isArray(value) ? value : [value]) {
      if (v !== null && v !== undefined && v !== "") query.append(key, String(v));
    }
  }
  const qs = query.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

/**
 * Builds the request (mock or real) and returns the raw response, whatever its status.
 * @param {"GET" | "POST" | "PUT" | "PATCH" | "DELETE"} method
 * @param {string} path starts with /v1/
 * @param {{ searchParams?: SearchParams, body?: unknown, idempotencyKey?: string, headers?: Record<string, string>, noActiveRole?: boolean }} options
 * @returns {Promise<Response>}
 */
async function dispatch(
  method,
  path,
  { searchParams, body, idempotencyKey, headers: extra = {}, noActiveRole = false },
) {
  const mock = isMockApi();
  const incoming = await headers();
  /** @type {Record<string, string>} */
  const out = { accept: "application/json", "x-gb-client": "web", ...extra };
  if (body !== undefined) out["content-type"] = "application/json";
  if (idempotencyKey) out["idempotency-key"] = idempotencyKey;
  const language = incoming.get("accept-language");
  if (language && !out["accept-language"]) out["accept-language"] = language;

  let origin = MOCK_ORIGIN;
  if (mock) {
    // Handlers scope data by role the way the API scopes it by role and hub.
    const role = (await cookies()).get(SESSION_COOKIE)?.value;
    if (role) out["x-gb-role"] = role;
  } else {
    origin = apiBase();
    // Only the access token goes to the API; the console's own cookies (role, hub) stay here.
    const access = (await cookies()).get(ACCESS_COOKIE)?.value;
    if (access) out.cookie = `${ACCESS_COOKIE}=${access}`;
    if (!noActiveRole && !out["x-active-role"]) {
      const { activeRole } = await import("./auth.js"); // auth.js calls this file for /v1/me
      const grant = await activeRole();
      if (grant) out["x-active-role"] = grant;
    }
  }

  const request = new Request(origin + withQuery(path, searchParams), {
    method,
    headers: out,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  /** @type {Response | undefined} */
  let response;
  if (mock) {
    const { handlers } = await import("../mocks/index.js"); // kept out of the bundle's hot path
    response = await getResponse(handlers, request);
    if (!response) {
      throw new ApiProblem(501, {
        code: "mock.not_implemented",
        detail: `No mock handler for ${method} ${path}. Add one in mocks/ (see mocks/README.md).`,
      });
    }
  } else {
    response = await fetch(request);
    if (response.status === 401) await sessionExpired();
  }
  return response;
}

/**
 * The API refused the session (401): the access cookie expired, or was revoked. Send the browser
 * through `/v1/auth/session`, the one place that can read the refresh cookie (its Path is
 * /v1/auth), which refreshes and comes back to the page. A transparent retry here is not possible:
 * this request never carried `gb_rt`. If we were just sent back from a refresh (`gb_fresh`) and
 * still get 401, refreshing again would loop, so end the session instead.
 * @returns {Promise<never>}
 */
async function sessionExpired() {
  const fresh = (await cookies()).get(FRESH_COOKIE);
  if (fresh) redirect("/v1/auth/session?end=1");
  const page = (await headers()).get("x-gb-path") ?? "/";
  redirect(`/v1/auth/session?next=${encodeURIComponent(page)}`);
}

/**
 * @param {"GET" | "POST" | "PUT" | "PATCH" | "DELETE"} method
 * @param {string} path starts with /v1/
 * @param {{ searchParams?: SearchParams, body?: unknown, idempotencyKey?: string, headers?: Record<string, string> }} options
 * @returns {Promise<any>} the parsed JSON body, or null for 204
 */
async function send(method, path, options) {
  const response = await dispatch(method, path, options);
  if (response.status === 204) return null;
  const isJson = /json/.test(response.headers.get("content-type") ?? "");
  const parsed = isJson ? await response.json().catch(() => null) : null;
  if (!response.ok)
    throw new ApiProblem(response.status, parsed && typeof parsed === "object" ? parsed : {});
  return parsed;
}

/**
 * GET from the API. `const { items } = await apiGet("/v1/products", { searchParams: { kind: "veg" } })`.
 * @param {string} path
 * @param {GetOptions} [options]
 */
export function apiGet(path, options = {}) {
  return send("GET", path, options);
}

/**
 * GET a file (PDF, CSV) from the API, for Route Handlers that stream a download. Returns the bytes
 * and the response's `content-type`; errors are thrown as `ApiProblem`, like `apiGet`.
 * @param {string} path
 * @param {GetOptions & { accept: string }} options `accept` is the media type asked for ("application/pdf")
 * @returns {Promise<{ body: ArrayBuffer, contentType: string }>}
 */
export async function apiDownload(path, { accept, ...options }) {
  const response = await dispatch("GET", path, {
    ...options,
    headers: { ...options.headers, accept },
  });
  if (!response.ok) {
    const isJson = /json/.test(response.headers.get("content-type") ?? "");
    const parsed = isJson ? await response.json().catch(() => null) : null;
    throw new ApiProblem(response.status, parsed && typeof parsed === "object" ? parsed : {});
  }
  return {
    body: await response.arrayBuffer(),
    contentType: response.headers.get("content-type") ?? accept,
  };
}

/**
 * POST to the API. Every POST that creates money or orders needs an Idempotency-Key; if you pass
 * none a fresh one is made, which protects nothing on retries, so create the key once per user
 * intent (a hidden form field, or `crypto.randomUUID()` when the form renders) and pass it in.
 * @param {string} path
 * @param {unknown} [body]
 * @param {WriteOptions} [options]
 */
export function apiPost(path, body, { idempotencyKey = crypto.randomUUID(), ...options } = {}) {
  return send("POST", path, { ...options, body, idempotencyKey });
}

/**
 * @param {string} path
 * @param {unknown} body
 * @param {WriteOptions} [options]
 */
export function apiPatch(path, body, options = {}) {
  return send("PATCH", path, { ...options, body });
}

/**
 * @param {string} path
 * @param {unknown} body
 * @param {WriteOptions} [options]
 */
export function apiPut(path, body, options = {}) {
  return send("PUT", path, { ...options, body });
}

/**
 * @param {string} path
 * @param {WriteOptions} [options]
 */
export function apiDelete(path, options = {}) {
  return send("DELETE", path, options);
}
