import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import {
  clearSessionCookies,
  logout,
  refreshSession,
  relayCookies,
  resolveStaffRole,
} from "@/lib/auth-api.js";
import {
  ACCESS_COOKIE,
  FRESH_COOKIE,
  HUB_COOKIE,
  isMockApi,
  REFRESH_COOKIE,
  SESSION_COOKIE,
} from "@/lib/session.js";

/**
 * Refreshes the session. It lives at `/v1/auth/session` on purpose: the API's refresh cookie
 * `gb_rt` has `Path=/v1/auth`, so the browser sends it to this URL and to no page (see
 * lib/auth-api.js). Real mode only; in mock mode there is no session to refresh.
 *
 * - `GET ?next=/orders`: a navigation. proxy.js sends the browser here when the access cookie is gone
 *   (it lasts 15 minutes), and lib/api.js when the API answers 401. It refreshes (single-flight),
 *   relays the new cookies, re-reads `GET /v1/me` for the staff role, and redirects to `next`. With
 *   no refresh cookie, or a refresh the API refuses (expired, signed out, reused), it clears the
 *   session and goes to /login, remembering `next`. Someone with no staff role goes to /not-staff.
 * - `GET ?end=1`: end the session (after a 401 right after a refresh, which would otherwise loop).
 * - `POST`: the same refresh with no redirect, for `SessionKeeper`, which refreshes before the
 *   access cookie runs out so a Server Action is never the first request to find it gone. 204, or
 *   401 when the session is over.
 */

/** @param {string | null} next */
function safeNext(next) {
  const base = "http://console";
  const url = next ? URL.parse(next, base) : null;
  return url && url.origin === base ? url.pathname + url.search : "/";
}

/** @param {import("@/lib/auth-api.js").CookieStore} jar */
function endSession(jar) {
  clearSessionCookies(jar);
  jar.delete(SESSION_COOKIE);
  jar.delete(HUB_COOKIE);
  jar.delete(FRESH_COOKIE);
}

/** @param {import("next/server").NextRequest} request */
export async function GET(request) {
  if (isMockApi()) redirect("/login");
  const params = request.nextUrl.searchParams;
  const next = safeNext(params.get("next"));
  const toLogin = `/login${next === "/" ? "" : `?next=${encodeURIComponent(next)}`}`;
  const jar = await cookies();

  if (params.has("end")) {
    const access = jar.get(ACCESS_COOKIE)?.value;
    if (access) await logout(access).catch(() => null);
    endSession(jar);
    redirect("/login");
  }

  const refreshToken = jar.get(REFRESH_COOKIE)?.value;
  const refreshed = refreshToken ? await refreshSession(refreshToken) : null;
  if (!refreshed?.ok || !refreshed.accessToken) {
    endSession(jar);
    redirect(toLogin);
  }
  relayCookies(jar, refreshed.setCookies);
  const { role, ok } = await resolveStaffRole(
    refreshed.accessToken,
    jar.get(SESSION_COOKIE)?.value,
  );
  if (!ok) {
    endSession(jar);
    redirect(toLogin);
  }
  if (!role) {
    await logout(refreshed.accessToken).catch(() => null);
    endSession(jar);
    redirect("/not-staff");
  }
  jar.set(SESSION_COOKIE, role, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  jar.set(FRESH_COOKIE, "1", { httpOnly: true, sameSite: "lax", path: "/", maxAge: 10 });
  redirect(next);
}

/** @param {Request} request */
export async function POST(request) {
  // A custom header cannot be sent cross-site without a CORS preflight, which we do not answer.
  if (isMockApi() || request.headers.get("x-gb-client") !== "web")
    return new Response(null, { status: 403 });
  const jar = await cookies();
  const refreshToken = jar.get(REFRESH_COOKIE)?.value;
  const refreshed = refreshToken ? await refreshSession(refreshToken) : null;
  if (!refreshed?.ok) return new Response(null, { status: 401 });
  relayCookies(jar, refreshed.setCookies);
  return new Response(null, { status: 204 });
}
