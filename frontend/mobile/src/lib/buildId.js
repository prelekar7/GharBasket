/**
 * The id of this build, baked into the bundle as `import.meta.env.VITE_BUILD_ID` (vite.config.js).
 * The saved query cache carries it as its `buster` (lib/queryClient.js), so the first start of a new
 * deploy drops data cached by the old one instead of handing it, in an old shape, to new code.
 *
 * Runs in vite.config.js, at build time, so it reads the build's environment:
 * - `VITE_BUILD_ID` when someone sets it on purpose;
 * - else the commit being built: `VERCEL_GIT_COMMIT_SHA` on Vercel, `GITHUB_SHA` in CI. A redeploy
 *   of the same commit keeps the cache, as it should;
 * - else (a local or Android build) the build's start time, so every build is new.
 * @param {Record<string, string | undefined>} env @param {() => number} [now]
 * @returns {string}
 */
export function buildId(env, now = Date.now) {
  return (
    env.VITE_BUILD_ID ||
    env.VERCEL_GIT_COMMIT_SHA?.slice(0, 12) ||
    env.GITHUB_SHA?.slice(0, 12) ||
    `t${now().toString(36)}`
  );
}
