// Vercel Routing Middleware for the PWA's `/v1/*` proxy (vercel.json routes it to the API).
//
// The API limits some calls per visitor address (handbook 13 §13.3). Rather than count
// X-Forwarded-For hops between Vercel and the API, which depend on the path a request takes, the
// visitor's address comes from Vercel's own value here at the edge (`ipAddress()` reads the
// `x-real-ip` Vercel sets) and goes to the API in `x-gb-visitor-ip`. The header is set, never
// appended, so a value the caller sent is replaced or removed. The API reads it only with the
// proxy's `x-origin-secret` (PROXY_CLIENT_IP_SOURCE=header:x-gb-visitor-ip).
import { ipAddress, next } from "@vercel/functions";

export const VISITOR_HEADER = "x-gb-visitor-ip";

export const config = { matcher: "/v1/:path*" };

/** @param {Request} request */
export default function middleware(request) {
  const headers = new Headers(request.headers);
  const ip = ipAddress(request);
  if (ip) headers.set(VISITOR_HEADER, ip);
  else headers.delete(VISITOR_HEADER);
  return next({ request: { headers } });
}
