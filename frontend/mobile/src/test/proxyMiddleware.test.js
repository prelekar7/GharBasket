import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import middleware, { API_ORIGIN, config, SECRET_HEADER, VISITOR_HEADER } from "../../middleware.js";
import middlewareSource from "../../middleware.js?raw";
import vercelJson from "../../vercel.json";

/** The request headers the middleware hands on, as Vercel reads them from its response. */
const forwarded = (/** @type {Response} */ res) => {
  const names = (res.headers.get("x-middleware-override-headers") ?? "").split(",");
  return Object.fromEntries(
    names.filter(Boolean).map((n) => [n, res.headers.get(`x-middleware-request-${n}`)]),
  );
};

const request = (/** @type {Record<string, string>} */ headers, path = "/v1/public/societies/x") =>
  new Request(`https://app.gharbasket.co.in${path}`, { headers });

const SECRET = "s3cret-from-vercel";

describe("PWA proxy middleware", () => {
  // The deployment's variable, read through process.env the way it is on Vercel.
  beforeEach(() => vi.stubEnv("ORIGIN_SECRET", SECRET));
  afterEach(() => vi.unstubAllEnvs());

  it("runs only on the API proxy path", () => {
    expect(config.matcher).toBe("/v1/:path*");
  });

  it("runs on the Node.js runtime, where the deployment's variables are all there", () => {
    expect(config.runtime).toBe("nodejs");
  });

  it("reads the secret directly, never through a copy of process.env (the #614 bug)", () => {
    expect(middlewareSource).toContain("process.env.ORIGIN_SECRET");
    expect(middlewareSource).not.toMatch(/=\s*process\.env\s*[,;)]/);
  });

  it("sends the request to the API with its path and query", () => {
    const res = middleware(request({}, "/v1/cycles/current?lang=hi-IN"));
    expect(res.headers.get("x-middleware-rewrite")).toBe(
      `${API_ORIGIN}/v1/cycles/current?lang=hi-IN`,
    );
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

  it("drops a caller's visitor header when Vercel gives no address", () => {
    const out = forwarded(middleware(request({ [VISITOR_HEADER]: "198.51.100.1" })));
    expect(out).not.toHaveProperty(VISITOR_HEADER);
  });

  it("sets the proxy secret, replacing a forged one", () => {
    const out = forwarded(middleware(request({ [SECRET_HEADER]: "forged" })));
    expect(out[SECRET_HEADER]).toBe(SECRET);
  });

  it("drops a forged secret when the deployment has none", () => {
    vi.stubEnv("ORIGIN_SECRET", "");
    const out = forwarded(middleware(request({ [SECRET_HEADER]: "forged" })));
    expect(out).not.toHaveProperty(SECRET_HEADER);
  });

  it("keeps the other headers (the sign-in ones the API needs)", () => {
    const out = forwarded(
      middleware(request({ "x-real-ip": "203.0.113.7", authorization: "Bearer t" })),
    );
    expect(out.authorization).toBe("Bearer t");
  });
});

describe("vercel.json", () => {
  const json = /** @type {Record<string, any>} */ (vercelJson);

  it("has no `routes`, which would answer /v1 before the middleware runs", () => {
    expect(json).not.toHaveProperty("routes");
  });

  it("sends app paths to index.html but never /v1, so a missing proxy fails loudly", () => {
    const [fallback] = json.rewrites;
    expect(fallback.destination).toBe("/index.html");
    const source = new RegExp(`^${fallback.source}$`);
    expect(source.test("/week")).toBe(true);
    expect(source.test("/v1/health")).toBe(false);
  });
});
