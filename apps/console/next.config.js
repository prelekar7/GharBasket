import { fileURLToPath } from "node:url";

// The monorepo root, so file tracing and Turbopack can see packages/ui.
const root = fileURLToPath(new URL("../..", import.meta.url));

/** @type {import("next").NextConfig} */
const config = {
  cacheComponents: true,
  transpilePackages: ["@gharbasket/ui"], // ships JSX source, not a build
  outputFileTracingRoot: root,
  turbopack: { root },
  poweredByHeader: false,
};

export default config;
