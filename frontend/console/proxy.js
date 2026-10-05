import { NextResponse } from "next/server";
import { canAccess, itemFor } from "./lib/nav.js";
import { parseSession, ROLES, SESSION_COOKIE } from "./lib/session.js";

/** Pages of the (public) route group and their crawler files: open to signed-out visitors. */
const PUBLIC_EXACT = new Set(["/", "/privacy", "/robots.txt", "/sitemap.xml"]);
const PUBLIC_PREFIXES = ["/s/", "/cook/"];

/** @param {string} pathname */
function isPublic(pathname) {
  return PUBLIC_EXACT.has(pathname) || PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

/**
 * Auth gate stub (TDD §9.2): no session → /login; signed in → away from /login and out of pages
 * the role does not see (lib/nav.js). Token refresh joins here with M1. The (public) pages (landing at
 * "/", privacy, society invite, know your cook) skip the gate for signed-out visitors; a signed-in
 * staff member who opens "/" still lands on their console home.
 * @param {import("next/server").NextRequest} request
 */
export function proxy(request) {
  const { pathname, search } = request.nextUrl;
  const role = parseSession(request.cookies.get(SESSION_COOKIE)?.value);

  if (!role) {
    if (pathname === "/login" || isPublic(pathname)) return NextResponse.next();
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }

  const item = itemFor(pathname);
  const allowed = item !== null && canAccess(role, item);
  if (pathname === "/login" || pathname === "/" || (item !== null && !allowed)) {
    return NextResponse.redirect(new URL(ROLES[role].home, request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Everything except Next internals and static files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
