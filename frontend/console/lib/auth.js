import { cookies } from "next/headers";
import { cache } from "react";
import { apiGet } from "./api.js";
import {
  ACCESS_COOKIE,
  HUB_COOKIE,
  isMockApi,
  parseSession,
  ROLES,
  SCOPE_TYPE,
  SESSION_COOKIE,
  staffRoleOf,
} from "./session.js";

/**
 * @typedef {object} Me `GET /v1/me` (M1 §3.1)
 * @property {string} id
 * @property {string} phone_e164
 * @property {string | null} name
 * @property {"en-IN" | "hi-IN" | "mr-IN"} locale
 * @property {boolean} senior_mode
 * @property {import("./session.js").MeRole[]} roles
 */

/**
 * Who the API says is signed in. Real mode only (null in mock mode). One call per request however
 * many components ask. It is the one API call that leaves out X-Active-Role, because it is what
 * tells us which role to name.
 * @returns {Promise<Me | null>}
 */
export const getMe = cache(async () => {
  if (isMockApi()) return null;
  if (!(await cookies()).get(ACCESS_COOKIE)) return null;
  return /** @type {Me} */ (await apiGet("/v1/me", { noActiveRole: true }));
});

/**
 * The console role the person works as, or null (signed out, or a user with no staff role).
 * Mock mode: the role in the session cookie. Real mode: their staff role from `GET /v1/me`, the one
 * they last used (`gb_session`) if they still hold it, else the best they hold. Server only;
 * reading cookies makes the caller dynamic, so call it below a Suspense boundary (cacheComponents).
 * @returns {Promise<import("./session.js").ConsoleRole | null>}
 */
export async function getRole() {
  const remembered = (await cookies()).get(SESSION_COOKIE)?.value;
  if (isMockApi()) return parseSession(remembered);
  const me = await getMe();
  return me ? staffRoleOf(me.roles, remembered) : null;
}

/**
 * Who is signed in, for the top bar. Real mode: the name from `GET /v1/me` (or the phone number when
 * they have none).
 */
export async function getPerson() {
  const role = await getRole();
  if (!role) return null;
  const me = await getMe();
  return {
    role,
    name: me ? (me.name ?? me.phone_e164) : ROLES[role].person,
    roleLabel: ROLES[role].label,
  };
}

/**
 * The `X-Active-Role` value for API calls (real mode; null in mock mode or signed out): the role
 * alone when it is not scoped (`admin`), else `role:scope_id` (M1 §3.1, the same form as the access
 * token's roles and the app's role switcher). The scope is the hub selected in the top bar for hub
 * roles (`hub_manager:<hub uuid>`), or the NGO for `ngo_coordinator`. The API accepts only a grant
 * the caller holds (else 403 `auth.role_not_granted`), so the hub is taken from `/v1/me`, never
 * from the cookie alone, and an unknown cookie falls back to their first hub.
 * @returns {Promise<string | null>}
 */
export async function activeRole() {
  if (isMockApi()) return null;
  const role = await getRole();
  if (!role) return null;
  const scopeType = SCOPE_TYPE[role];
  if (!scopeType) return role;
  const me = await getMe();
  const scopes = (me?.roles ?? [])
    .filter((g) => g.role === role && g.status === "verified" && g.scope_id)
    .map((g) => /** @type {string} */ (g.scope_id));
  const chosen = (await cookies()).get(HUB_COOKIE)?.value;
  const scope = scopeType === "hub" && chosen && scopes.includes(chosen) ? chosen : scopes[0];
  return scope ? `${role}:${scope}` : role;
}
