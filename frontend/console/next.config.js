import { fileURLToPath } from "node:url";
import { securityHeaders } from "./lib/csp.js";

// The monorepo root, so file tracing and Turbopack can see frontend/packages/ui.
const root = fileURLToPath(new URL("../..", import.meta.url));

/** @type {import("next").NextConfig} */
const config = {
  cacheComponents: true,
  transpilePackages: ["@gharbasket/ui"], // ships JSX source, not a build
  outputFileTracingRoot: root,
  turbopack: { root },
  poweredByHeader: false,
  // The Content-Security-Policy and the other security headers on every response (lib/csp.js).
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders(process.env.NODE_ENV) }];
  },
};

export default config;
