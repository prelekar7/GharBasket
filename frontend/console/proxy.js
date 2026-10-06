import { NextResponse } from "next/server";
import { canAccess, itemFor } from "./lib/nav.js";
import { ACCESS_COOKIE, isMockApi, parseSession, ROLES, SESSION_COOKIE } from "./lib/session.js";

/** Pages of the (public) route group and their crawler files: open to signed-out visitors. */
const PUBLIC_EXACT = new Set(["/", "/privacy", "/robots.txt", "/sitemap.xml", "/not-staff"]);
const PUBLIC_PREFIXES = ["/s/", "/cook/"];

/** @param {string} pathname */
function isPublic(pathname) {
  return PUBLIC_EXACT.has(pathname) || PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

/**
 * Auth gate (TDD §9.2): no session → /login; signed in → away from /login and out of pages the role
 * does not see (lib/nav.js). The (public) pages (landing at "/", privacy, society invite, know your
 * cook) skip the gate for signed-out visitors; a signed-in staff member who opens "/" still lands on
 * their console home.
 *
 * "Signed in" is the `gb_session` role cookie in mock mode. In real mode it is that cookie plus the
 * API's access cookie `__Host-gb_at`, which expires after 15 minutes. When a page navigation finds it
 * gone, the browser is sent to `/v1/auth/session?next=…` (app/v1/auth/session/route.js), the one
 * place that is sent the refresh cookie (its Path is /v1/auth), which refreshes and comes back. The
 * API itself is never called from here.
 *
 * It also tells the pages which path was asked for (`x-gb-path`, a request header that replaces
 * whatever the client sent), which lib/api.js needs to come back to the page after a refresh.
 * @param {import("next/server").NextRequest} request
 */
export function proxy(request) {
  const { pathname, search } = request.nextUrl;
  const remembered = parseSession(request.cookies.get(SESSION_COOKIE)?.value);
  const live = isMockApi() || request.cookies.has(ACCESS_COOKIE);
  const role = live ? remembered : null;

  const forwarded = new Headers(request.headers);
  forwarded.set("x-gb-path", pathname + search);
  const pass = () => NextResponse.next({ request: { headers: forwarded } });

  if (pathname.startsWith("/v1/auth/")) return pass(); // the session Route Handler, signed in or not

  if (!role) {
    if (pathname === "/login" || isPublic(pathname)) return pass();
    // Real mode, session cookie but no access cookie (or the other way round): try to refresh.
    const refreshable = !isMockApi() && request.method === "GET";
    const target = new URL(refreshable ? "/v1/auth/session" : "/login", request.url);
    target.searchParams.set("next", pathname + search);
    return NextResponse.redirect(target);
  }

  const item = itemFor(pathname);
  const allowed = item !== null && canAccess(role, item);
  if (pathname === "/login" || pathname === "/" || (item !== null && !allowed)) {
    return NextResponse.redirect(new URL(ROLES[role].home, request.url));
  }
  return pass();
}

export const config = {
  // Everything except Next internals and static files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
