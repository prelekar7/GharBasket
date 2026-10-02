"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { sectionOf } from "../../../lib/nav.js";
import { devLoginEnabled, parseSession, ROLES, SESSION_COOKIE } from "../../../lib/session.js";

/**
 * Dev sign-in stub: sets the session cookie for the chosen role. Replaced by POST /v1/auth/exchange
 * and the refresh cookie in M1.
 * @param {FormData} form
 */
export async function signIn(form) {
  const role = parseSession(String(form.get("role")));
  if (!devLoginEnabled || !role) redirect("/login");
  (await cookies()).set(SESSION_COOKIE, role, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  // Go back where the person was headed, if it is a console page this role can see.
  redirect(safeNext(String(form.get("next") ?? ""), role) ?? ROLES[role].home);
}

/**
 * The `next` path, normalised, if it stays on this site and the role can see it. Parsing first
 * stops tricks like "/\evil.com", which browsers read as another host.
 * @param {string} next
 * @param {import("../../../lib/session.js").ConsoleRole} role
 */
function safeNext(next, role) {
  const base = "http://console";
  const url = URL.parse(next, base);
  if (!url || url.origin !== base) return null;
  const section = sectionOf(url.pathname);
  return section && ROLES[role].sections.includes(section) ? url.pathname + url.search : null;
}

export async function signOut() {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
