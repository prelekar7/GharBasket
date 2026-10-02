// Auth calls, exactly as frozen in docs/modules/M1-identity.md §3.1 (walking-skeleton contract).
// Until the backend lands (feat/M0-api-platform), VITE_API_MOCK (default on) answers in-app.
// Replace with the generated packages/api-client once `just gen-api` exists; never hand-edit that client.

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const MOCK = import.meta.env.VITE_API_MOCK !== "false";

/** @typedef {"customer" | "captain" | "kitchen" | "rider" | "packer"} FieldRole */
/** @typedef {{ role: string, scope_type: string | null, scope_id: string | null, status: "verified" | "pending" }} RoleGrant */
/** @typedef {{ id: string, phone_e164: string, name: string | null, locale: "en-IN" | "hi-IN" | "mr-IN", senior_mode: boolean }} UserOut */
/** @typedef {{ access_token: string, access_expires_in: number, refresh_token: string, is_new_user: boolean, user: UserOut }} ExchangeOut */

export class ApiError extends Error {
  /** @param {{ title: string, status: number, code: string }} problem RFC 9457 problem+json */
  constructor(problem) {
    super(problem.title);
    this.status = problem.status;
    this.code = problem.code;
  }
}

/** @param {string} path @param {RequestInit} [init] */
async function request(path, init) {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) throw new ApiError(await res.json());
  return res.json();
}

/**
 * POST /v1/auth/exchange. In dev the contract allows `dev_phone` instead of a Firebase ID token;
 * Firebase OTP is wired after spike S-2.
 * @param {{ devPhone: string, platform: "android" | "web" }} input
 * @returns {Promise<ExchangeOut>}
 */
export async function exchange({ devPhone, platform }) {
  const body = { dev_phone: devPhone, device: { platform, push_token: null } };
  if (!MOCK) return request("/v1/auth/exchange", { method: "POST", body: JSON.stringify(body) });
  return {
    access_token: `mock.${devPhone}`,
    access_expires_in: 900,
    refresh_token: "mock-refresh",
    is_new_user: false,
    user: {
      id: "0190f0aa-0000-7000-8000-000000000001",
      phone_e164: devPhone,
      name: null,
      locale: "en-IN",
      senior_mode: false,
    },
  };
}

/**
 * GET /v1/me. Mock: +919800000002 is a customer who is also Captain of Sunrise Towers.
 * @param {string} accessToken
 * @returns {Promise<UserOut & { roles: RoleGrant[] }>}
 */
export async function me(accessToken) {
  if (!MOCK) return request("/v1/me", { headers: { authorization: `Bearer ${accessToken}` } });
  const phone = accessToken.replace("mock.", "");
  /** @type {RoleGrant[]} */
  const roles = [{ role: "customer", scope_type: null, scope_id: null, status: "verified" }];
  if (phone === "+919800000002") {
    roles.push({
      role: "captain",
      scope_type: "society",
      scope_id: "sunrise-towers",
      status: "verified",
    });
  }
  return {
    id: "0190f0aa-0000-7000-8000-000000000001",
    phone_e164: phone,
    name: null,
    locale: "en-IN",
    senior_mode: false,
    roles,
  };
}
