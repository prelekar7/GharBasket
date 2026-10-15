"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cookieValue, exchange, logout, relayCookies, resolveStaffRole } from "@/lib/auth-api.js";
import { canAccess, itemFor } from "@/lib/nav.js";
import {
  ACCESS_COOKIE,
  devLoginEnabled,
  FRESH_COOKIE,
  HUB_COOKIE,
  isMockApi,
  MOCK_ROLES,
  parseSession,
  ROLES,
  SESSION_COOKIE,
} from "@/lib/session.js";

/** The code every dev sign-in accepts, until Firebase phone auth is in the console. */
const DEV_CODE = "123456";

/**
 * Remembers the role the person works as and goes where they were headed.
 * @param {import("@/lib/session.js").ConsoleRole} role
 * @param {string} next
 * @returns {Promise<never>}
 */
async function startSession(role, next) {
  const jar = await cookies();
  jar.set(SESSION_COOKIE, role, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  jar.delete(HUB_COOKIE); // a hub chosen by the last person is theirs, not this one's
  // Back to where the person was headed, if it is a console page this role can see.
  redirect(safeNext(next, role) ?? ROLES[role].home);
}

/**
 * Real sign-in (`GB_API_MOCK=false`): `POST /v1/auth/exchange`, relay the API's cookies to the
 * browser (how they flow: lib/auth-api.js), then check `GET /v1/me` for a staff role. Someone with
 * none is signed out again and shown "This console is for GharBasket staff".
 * @param {{ dev_phone: string } | { firebase_id_token: string }} proof
 * @param {string} next
 * @returns {Promise<{ error: string }>} only when sign-in failed; success redirects
 */
async function startRealSession(proof, next) {
  const result = await exchange(proof);
  if (!result.ok) {
    const code = String(result.body.code ?? "");
    if (code === "auth.rate_limited") return { error: "Too many attempts. Wait a few minutes." };
    if (code === "auth.account_disabled") return { error: "This account is switched off." };
    if (code === "auth.dev_login_disabled")
      return { error: "Dev sign-in is off on this API. Use the real code." };
    return { error: "We could not sign you in. Check the number and try again." };
  }
  const jar = await cookies();
  relayCookies(jar, result.setCookies);
  const access = cookieValue(result.setCookies, ACCESS_COOKIE);
  const { role, ok } = await resolveStaffRole(access ?? "");
  if (!ok) return { error: "We could not load your account. Try again." };
  if (!role) {
    // Not staff: end the session we just made, and say so.
    const out = await logout(access ?? "");
    relayCookies(jar, out.setCookies);
    redirect("/not-staff");
  }
  return startSession(role, next);
}

/**
 * Dev sign-in by role (the "Continue as" buttons). Mock mode sets the role cookie. Real mode signs
 * that seed person in with `dev_phone`, which the API allows only when APP_ENV=dev.
 * @param {FormData} form
 */
export async function signIn(form) {
  const role = parseSession(String(form.get("role")));
  if (!devLoginEnabled || !role) redirect("/login");
  const next = String(form.get("next") ?? "");
  if (isMockApi() && ROLES[role].realOnly) redirect("/login");
  if (isMockApi()) return startSession(role, next);
  await startRealSession({ dev_phone: `+91${ROLES[role].phone}` }, next);
  redirect("/login");
}

/**
 * The 10 digits of a staff phone as typed ("98000 01011", "+91 98000 01011"), or null.
 * @param {FormDataEntryValue | null} value
 */
function staffDigits(value) {
  const digits = String(value ?? "").replace(/\D/g, "");
  const phone = digits.length === 12 && digits.startsWith("91") ? digits.slice(2) : digits;
  return phone.length === 10 ? phone : null;
}

/**
 * Step 1 of the sign-in, and the Resend button: "send" a code to a staff phone. Dev stand-in: nothing is
 * sent. In mock mode it checks the number is one of the backend seed's staff phones (+91 98000 01008
 * to 01011); in real mode any 10-digit number goes on, and the API decides after the exchange who
 * is staff. Firebase phone auth plugs in here (as in the app): this action asks Firebase to SMS the
 * code and keeps the verification id, `signInWithPhone` below exchanges the Firebase ID token with
 * `{ firebase_id_token }` instead of `{ dev_phone }`, and the 24-second resend wait comes from the
 * provider's limit.
 * @param {unknown} _previous
 * @param {FormData} form
 * @returns {Promise<{ phone?: string, nonce?: number, error?: string }>} `nonce` is new on every send, so the page restarts its countdown
 */
export async function requestCode(_previous, form) {
  if (!devLoginEnabled) redirect("/login");
  const phone = staffDigits(form.get("phone"));
  if (!phone) return { error: "Enter the 10-digit mobile number." };
  if (isMockApi() && !MOCK_ROLES.some(([, r]) => r.phone === phone))
    return { error: "This number is not on the staff list." };
  return { phone, nonce: Date.now() };
}

/**
 * Step 2: the code. The dev code 123456 is accepted for any number (mock mode: the seed staff
 * phones). Real mode then calls `POST /v1/auth/exchange` with `{ dev_phone }` (see `requestCode`
 * for the Firebase version).
 * @param {{ error?: string } | null} _previous
 * @param {FormData} form
 * @returns {Promise<{ error?: string }>}
 */
export async function signInWithPhone(_previous, form) {
  if (!devLoginEnabled) redirect("/login");
  const phone = staffDigits(form.get("phone"));
  const role = MOCK_ROLES.find(([, r]) => r.phone === phone)?.[0];
  if (!phone || (isMockApi() && !role)) return { error: "Start again with a staff mobile number." };
  if (String(form.get("code") ?? "").trim() !== DEV_CODE) {
    return { error: "That code is not right. Check the SMS and try again." };
  }
  if (!isMockApi())
    return startRealSession({ dev_phone: `+91${phone}` }, String(form.get("next") ?? ""));
  return startSession(
    /** @type {import("@/lib/session.js").ConsoleRole} */ (role),
    String(form.get("next") ?? ""),
  );
}

/**
 * The `next` path, normalised, if it stays on this site and the role can see it. Parsing first
 * stops tricks like "/\evil.com", which browsers read as another host.
 * @param {string} next
 * @param {import("@/lib/session.js").ConsoleRole} role
 */
function safeNext(next, role) {
  const base = "http://console";
  const url = URL.parse(next, base);
  if (!url || url.origin !== base) return null;
  const item = itemFor(url.pathname);
  return item && canAccess(role, item) ? url.pathname + url.search : null;
}

/**
 * Sign out. Real mode first calls `POST /v1/auth/logout`, which revokes this device's session at
 * once, and relays the cookies that clear the API's two.
 */
export async function signOut() {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  if (!isMockApi() && access) {
    const out = await logout(access).catch(() => null);
    if (out) relayCookies(jar, out.setCookies);
  }
  jar.delete(SESSION_COOKIE);
  jar.delete(HUB_COOKIE);
  jar.delete(FRESH_COOKIE);
  redirect("/login");
}
