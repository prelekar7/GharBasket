import { NextResponse } from "next/server";
import { sectionOf } from "./lib/nav.js";
import { parseSession, ROLES, SESSION_COOKIE } from "./lib/session.js";

/**
 * Auth gate stub (TDD §9.2): no session → /login; signed in → away from /login and out of sections
 * the role does not see. Token refresh joins here with M1.
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

  const section = sectionOf(pathname);
  const allowed = section !== null && ROLES[role].sections.includes(section);
  if (pathname === "/login" || pathname === "/" || (section !== null && !allowed)) {
    return NextResponse.redirect(new URL(ROLES[role].home, request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Everything except Next internals and static files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
