// Vercel Routing Middleware: the PWA's `/v1/*` proxy to the API (handbook 12 "PWA").
//
// Browsers call the API same-origin through this proxy, so its cookies work (ADR-0024). Two request
// headers tell the API about the caller (handbook 13 §13.3):
// - `x-origin-secret`: the project's ORIGIN_SECRET, so the API knows the request came through its
//   own proxy and not from a direct caller;
// - `x-gb-visitor-ip`: the visitor's address from Vercel's own value at the edge (`ipAddress()` reads
//   the `x-real-ip` Vercel sets). The API reads it only with the secret
//   (PROXY_CLIENT_IP_SOURCE=header:x-gb-visitor-ip), and counts its per-address limits by it.
// Both are set, never appended, so anything the caller sent is replaced, or removed when there is
// no value.
//
// The whole proxy lives here, not in vercel.json: a vercel.json `routes` entry that sends `/v1/*`
// to another host is answered before Routing Middleware runs, so the visitor header never arrived
// (27 Nov 2026: the caller's own header reached the API untouched).
import { ipAddress, rewrite } from "@vercel/functions";

export const API_ORIGIN = "https://api.gharbasket.co.in";
export const VISITOR_HEADER = "x-gb-visitor-ip";
export const SECRET_HEADER = "x-origin-secret";

export const config = { matcher: "/v1/:path*" };

/**
 * @param {Request} request
 * @param {Record<string, string | undefined>} [env] the deployment's variables (tests pass their own)
 */
export default function middleware(request, env = process.env) {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  const ip = ipAddress(request);
  if (ip) headers.set(VISITOR_HEADER, ip);
  else headers.delete(VISITOR_HEADER);
  const secret = env.ORIGIN_SECRET;
  if (secret) headers.set(SECRET_HEADER, secret);
  else headers.delete(SECRET_HEADER);
  return rewrite(new URL(`${url.pathname}${url.search}`, API_ORIGIN), { request: { headers } });
}
