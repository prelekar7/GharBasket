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
 * Dev stand-in for phone OTP sign-in: the staff phones of the backend seed (+91 98000 01008 to
 * 01011) with the code 123456. M1 replaces this with the real OTP exchange.
 * @param {{ error?: string } | null} _previous
 * @param {FormData} form
 * @returns {Promise<{ error?: string, field?: "phone" | "code" }>}
 */
export async function signInWithPhone(_previous, form) {
  if (!devLoginEnabled) redirect("/login");
  const digits = String(form.get("phone") ?? "").replace(/\D/g, "");
  const phone = digits.length === 12 && digits.startsWith("91") ? digits.slice(2) : digits;
  if (phone.length !== 10) return { field: "phone", error: "Enter the 10-digit mobile number." };
  const role = Object.entries(ROLES).find(([, r]) => r.phone === phone)?.[0];
  if (!role) return { field: "phone", error: "This number is not on the staff list." };
  if (String(form.get("code") ?? "").trim() !== DEV_CODE) {
    return { field: "code", error: "That code is not right. Try again." };
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
