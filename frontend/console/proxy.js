import { NextResponse } from "next/server";
import { canAccess, itemFor } from "./lib/nav.js";
import { parseSession, ROLES, SESSION_COOKIE } from "./lib/session.js";

/**
 * Auth gate stub (TDD §9.2): no session → /login; signed in → away from /login and out of pages
 * the role does not see (lib/nav.js). Token refresh joins here with M1.
 * @param {import("next/server").NextRequest} request
 */
export function proxy(request) {
  const { pathname, search } = request.nextUrl;
  const role = parseSession(request.cookies.get(SESSION_COOKIE)?.value);

  if (!role) {
    if (pathname === "/login") return NextResponse.next();
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname + search);
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
