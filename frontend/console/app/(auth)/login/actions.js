"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { canAccess, itemFor } from "@/lib/nav.js";
import { devLoginEnabled, parseSession, ROLES, SESSION_COOKIE } from "@/lib/session.js";

/** The code every dev sign-in accepts. Real OTP (POST /v1/auth/exchange, Firebase) arrives with M1. */
const DEV_CODE = "123456";

/**
 * Sets the session cookie for a role and goes where the person was headed. Dev stub: M1 replaces
 * the cookie with the refresh cookie from POST /v1/auth/exchange.
 * @param {import("@/lib/session.js").ConsoleRole} role
 * @param {string} next
 * @returns {Promise<never>}
 */
async function startSession(role, next) {
  (await cookies()).set(SESSION_COOKIE, role, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  // Back to where the person was headed, if it is a console page this role can see.
  redirect(safeNext(next, role) ?? ROLES[role].home);
}

/**
 * Dev sign-in by role (the "Continue as" buttons).
 * @param {FormData} form
 */
export async function signIn(form) {
  const role = parseSession(String(form.get("role")));
  if (!devLoginEnabled || !role) redirect("/login");
  return startSession(role, String(form.get("next") ?? ""));
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
 * sent; it only checks the number is one of the backend seed's staff phones (+91 98000 01008 to
 * 01011). Real OTP plugs in here (M1, with Firebase phone auth as in the app): this action asks
 * Firebase to SMS the code and keeps the verification id, `signInWithPhone` below exchanges the code
 * for a session with POST /v1/auth/exchange, and the 24-second resend wait comes from the provider's limit.
 * @param {unknown} _previous
 * @param {FormData} form
 * @returns {Promise<{ phone?: string, nonce?: number, error?: string }>} `nonce` is new on every send, so the page restarts its countdown
 */
export async function requestCode(_previous, form) {
  if (!devLoginEnabled) redirect("/login");
  const phone = staffDigits(form.get("phone"));
  if (!phone) return { error: "Enter the 10-digit mobile number." };
  if (!Object.values(ROLES).some((r) => r.phone === phone))
    return { error: "This number is not on the staff list." };
  return { phone, nonce: Date.now() };
}

/**
 * Step 2: dev stand-in for the OTP exchange. The staff phones of the backend seed accept the code
 * 123456. M1 replaces this with POST /v1/auth/exchange (see `requestCode`).
 * @param {{ error?: string } | null} _previous
 * @param {FormData} form
 * @returns {Promise<{ error?: string }>}
 */
export async function signInWithPhone(_previous, form) {
  if (!devLoginEnabled) redirect("/login");
  const phone = staffDigits(form.get("phone"));
  const role = Object.entries(ROLES).find(([, r]) => r.phone === phone)?.[0];
  if (!role) return { error: "Start again with a staff mobile number." };
  if (String(form.get("code") ?? "").trim() !== DEV_CODE) {
    return { error: "That code is not right. Check the SMS and try again." };
  }
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

export async function signOut() {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
