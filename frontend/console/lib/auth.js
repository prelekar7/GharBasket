import { cookies } from "next/headers";
import { parseSession, ROLES, SESSION_COOKIE } from "./session.js";

/**
 * The signed-in role from the session cookie, or null. Server only; reading cookies makes the
 * caller dynamic, so call it below a Suspense boundary (cacheComponents).
 */
export async function getRole() {
  return parseSession((await cookies()).get(SESSION_COOKIE)?.value);
}

/**
 * Who is signed in, for the top bar. From the dev session for now; GET /v1/me replaces this with M1.
 */
export async function getPerson() {
  const role = await getRole();
  return role ? { role, name: ROLES[role].person, roleLabel: ROLES[role].label } : null;
}
