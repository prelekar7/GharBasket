import { afterEach, describe, expect, it, vi } from "vitest";

/** Loads ../../vercel.ts with GB_API_ORIGIN set (or unset), as a Vercel build would. @param {string | undefined} origin */
async function load(origin) {
  vi.resetModules();
  if (origin === undefined) vi.stubEnv("GB_API_ORIGIN", "");
  else vi.stubEnv("GB_API_ORIGIN", origin);
  return import("../../vercel.ts");
}
afterEach(() => vi.unstubAllEnvs());

describe("the PWA's Vercel routes", () => {
  it("send /v1 to production's API by default, with the origin secret set, then files, then the app", async () => {
    const { config } = await load(undefined);
    expect(config.routes).toEqual([
      {
        src: "/v1/(.*)",
        dest: "https://api.gharbasket.co.in/v1/$1",
        transforms: [
          {
            type: "request.headers",
            op: "set",
            target: { key: "x-origin-secret" },
            args: "$ORIGIN_SECRET",
            env: ["ORIGIN_SECRET"],
          },
        ],
      },
      { handle: "filesystem" },
      { src: "/(.*)", dest: "/index.html" },
    ]);
  });

  it("send /v1 to the project's own API when GB_API_ORIGIN is set (staging)", async () => {
    const { config } = await load("https://staging-api.gharbasket.co.in");
    expect(config.routes[0].dest).toBe("https://staging-api.gharbasket.co.in/v1/$1");
  });

  it("fail the build on an origin that is not plain https", async () => {
    const { apiOrigin } = await load(undefined);
    for (const bad of [
      "http://api.gharbasket.co.in",
      "https://api.gharbasket.co.in/",
      "https://api.gharbasket.co.in/v1",
      "api.gharbasket.co.in",
      "https://evil.example?x=",
    ]) {
      expect(() => apiOrigin(bad)).toThrow(/GB_API_ORIGIN/);
    }
    expect(apiOrigin(" https://staging-api.gharbasket.co.in ")).toBe(
      "https://staging-api.gharbasket.co.in",
    );
  });
});
