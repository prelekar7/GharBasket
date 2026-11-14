import { describe, expect, it } from "vitest";
import { buildId } from "./buildId.js";

const SHA = "3b328570a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6";

describe("the build id that discards an older build's saved cache", () => {
  it("is the commit Vercel builds, so a redeploy of the same commit keeps the cache", () => {
    expect(buildId({ VERCEL_GIT_COMMIT_SHA: SHA })).toBe("3b328570a1c2");
    expect(buildId({ VERCEL_GIT_COMMIT_SHA: SHA })).toBe(buildId({ VERCEL_GIT_COMMIT_SHA: SHA }));
  });

  it("changes with the commit", () => {
    expect(buildId({ VERCEL_GIT_COMMIT_SHA: SHA })).not.toBe(
      buildId({ VERCEL_GIT_COMMIT_SHA: `f${SHA.slice(1)}` }),
    );
  });

  it("uses CI's commit, then a set VITE_BUILD_ID before either", () => {
    expect(buildId({ GITHUB_SHA: SHA })).toBe("3b328570a1c2");
    expect(buildId({ VITE_BUILD_ID: "demo-1", VERCEL_GIT_COMMIT_SHA: SHA })).toBe("demo-1");
  });

  it("is the build time for a local or Android build, so each build is new", () => {
    expect(buildId({}, () => 1)).toBe("t1");
    expect(buildId({ VERCEL_GIT_COMMIT_SHA: "" }, () => 2)).toBe("t2");
    expect(buildId({}, () => 1)).not.toBe(buildId({}, () => 2));
  });
});
