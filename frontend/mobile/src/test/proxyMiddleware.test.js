import { describe, expect, it } from "vitest";
import middleware, { config, VISITOR_HEADER } from "../../middleware.js";

/** The request headers the middleware hands on, as Vercel reads them from its response. */
const forwarded = (/** @type {Response} */ res) => {
  const names = (res.headers.get("x-middleware-override-headers") ?? "").split(",");
  return Object.fromEntries(
    names.filter(Boolean).map((n) => [n, res.headers.get(`x-middleware-request-${n}`)]),
  );
};

const request = (/** @type {Record<string, string>} */ headers) =>
  new Request("https://app.gharbasket.co.in/v1/public/societies/x", { headers });

describe("PWA proxy middleware", () => {
  it("runs only on the API proxy path", () => {
    expect(config.matcher).toBe("/v1/:path*");
  });

  it("names the visitor by Vercel's own address, replacing anything the caller sent", () => {
    const out = forwarded(
      middleware(
        request({
          "x-real-ip": "203.0.113.7",
          [VISITOR_HEADER]: "198.51.100.1",
          "x-forwarded-for": "198.51.100.2",
        }),
      ),
    );
    expect(out[VISITOR_HEADER]).toBe("203.0.113.7");
  });

  it("drops a caller's header when Vercel gives no address", () => {
    const out = forwarded(middleware(request({ [VISITOR_HEADER]: "198.51.100.1" })));
    expect(out).not.toHaveProperty(VISITOR_HEADER);
  });

  it("keeps the other headers (the sign-in ones the API needs)", () => {
    const out = forwarded(
      middleware(request({ "x-real-ip": "203.0.113.7", authorization: "Bearer t" })),
    );
    expect(out.authorization).toBe("Bearer t");
  });
});
