/**
 * The console's Content-Security-Policy (handbook 13 §13.4, ASVS L1 finding F3), sent on every page
 * by next.config.js. It lists only what the console loads:
 *
 * - its own pages, scripts, styles and fonts (`'self'`). Next.js puts small inline scripts in each
 *   page to start React, and the UI kit sets inline styles, so scripts and styles also allow
 *   `'unsafe-inline'`. A nonce would need every page to render per request, which `cacheComponents`
 *   avoids; the other directives still stop loading code from anywhere else.
 * - Firebase phone sign-in on /login: reCAPTCHA scripts and frames (www.google.com, www.gstatic.com,
 *   apis.google.com) and the Identity Toolkit calls (identitytoolkit, securetoken).
 * - The maps (MapLibre): the style, tiles, fonts and sprites from tiles.openfreemap.org, and the
 *   worker from /maplibre/ (a `blob:` worker as a fallback).
 * - Images: delivery and issue photos come from short-lived storage links (R2), so any https image.
 *
 * The console's server calls the API; the browser never does (lib/auth-api.js), so `connect-src`
 * needs no API host. `frame-ancestors 'none'` stops other sites from framing the console.
 */
export const CSP_DIRECTIVES = /** @type {const} */ ([
  ["default-src", "'self'"],
  [
    "script-src",
    "'self'",
    "'unsafe-inline'",
    "https://www.google.com",
    "https://www.gstatic.com",
    "https://apis.google.com",
  ],
  ["style-src", "'self'", "'unsafe-inline'"],
  ["img-src", "'self'", "data:", "blob:", "https:"],
  ["font-src", "'self'", "data:"],
  [
    "connect-src",
    "'self'",
    "https://identitytoolkit.googleapis.com",
    "https://securetoken.googleapis.com",
    "https://www.google.com",
    "https://tiles.openfreemap.org",
  ],
  ["frame-src", "https://www.google.com", "https://www.recaptcha.net", "https://*.firebaseapp.com"],
  ["worker-src", "'self'", "blob:"],
  ["object-src", "'none'"],
  ["base-uri", "'self'"],
  ["form-action", "'self'"],
  ["frame-ancestors", "'none'"],
  ["upgrade-insecure-requests"],
]);

/** The header value: directives joined with "; ". */
export const contentSecurityPolicy = () => CSP_DIRECTIVES.map((d) => d.join(" ")).join("; ");

/**
 * The security headers for every console response. Production only: `next dev` needs `eval` for
 * React's debugging and reloads over a WebSocket, which this policy would block.
 * @param {string | undefined} nodeEnv `process.env.NODE_ENV`
 * @returns {{ key: string, value: string }[]}
 */
export function securityHeaders(nodeEnv) {
  const always = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ];
  return nodeEnv === "production"
    ? [{ key: "Content-Security-Policy", value: contentSecurityPolicy() }, ...always]
    : always;
}
