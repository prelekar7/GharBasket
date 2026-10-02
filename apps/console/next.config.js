import { fileURLToPath } from "node:url";

// The monorepo root, so standalone output and Turbopack can see packages/ui.
const root = fileURLToPath(new URL("../..", import.meta.url));

/** @type {import("next").NextConfig} */
const config = {
  output: "standalone", // Docker image on EC2 (TDD §3.3)
  cacheComponents: true,
  transpilePackages: ["@gharbasket/ui"], // ships JSX source, not a build
  outputFileTracingRoot: root,
  turbopack: { root },
  poweredByHeader: false,
};

export default config;
