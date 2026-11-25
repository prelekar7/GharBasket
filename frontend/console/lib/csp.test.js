import assert from "node:assert/strict";
import { test } from "node:test";
import { contentSecurityPolicy, securityHeaders } from "./csp.js";

/** @param {string} name */
const directive = (name) =>
  contentSecurityPolicy()
    .split("; ")
    .find((d) => d.split(" ")[0] === name)
    ?.split(" ")
    .slice(1) ?? [];

test("nothing loads from anywhere by default, and nobody may frame the console", () => {
  assert.deepEqual(directive("default-src"), ["'self'"]);
  assert.deepEqual(directive("object-src"), ["'none'"]);
  assert.deepEqual(directive("frame-ancestors"), ["'none'"]);
  assert.deepEqual(directive("form-action"), ["'self'"]);
});

test("Firebase phone sign-in and the maps are allowed, and only them", () => {
  assert.ok(directive("script-src").includes("https://www.google.com")); // reCAPTCHA
  assert.ok(directive("connect-src").includes("https://identitytoolkit.googleapis.com"));
  assert.ok(directive("connect-src").includes("https://tiles.openfreemap.org"));
  assert.ok(directive("worker-src").includes("'self'")); // /maplibre/ worker
  assert.ok(!directive("script-src").includes("'unsafe-eval'"));
  assert.ok(!directive("script-src").includes("https:"));
  assert.ok(!directive("connect-src").includes("https:"));
});

/**
 * Whether a `connect-src` source allows a request to `url` (CSP3 host-source matching, for the
 * forms this policy uses: an exact https origin or `https://*.<host>`).
 * @param {string} url
 */
const connectAllowed = (url) => {
  const { protocol, host } = new URL(url);
  return directive("connect-src").some((source) => {
    if (!source.startsWith("https://") || protocol !== "https:") return false;
    const allowed = source.slice("https://".length);
    return allowed.startsWith("*.") ? host.endsWith(allowed.slice(1)) : host === allowed;
  });
};

test("the browser may PUT a hand-off photo to its signed R2 link, and to nowhere else", () => {
  // The shapes boto3 signs for R2: path style, and the bucket as a subdomain.
  assert.ok(
    connectAllowed(
      "https://0123abcd.r2.cloudflarestorage.com/gb-staging-media/h/1.jpg?X-Amz-Signature=x",
    ),
  );
  assert.ok(connectAllowed("https://gb-staging-media.0123abcd.r2.cloudflarestorage.com/h/1.jpg"));
  assert.ok(!connectAllowed("https://r2.cloudflarestorage.com.evil.example/h/1.jpg"));
  assert.ok(!connectAllowed("https://pub-0123.r2.dev/h/1.jpg")); // public buckets stay out
  assert.ok(!connectAllowed("http://0123abcd.r2.cloudflarestorage.com/h/1.jpg"));
});

test("the policy is sent in production only; the other headers always", () => {
  const names = (/** @type {string} */ env) => securityHeaders(env).map((h) => h.key);
  assert.ok(names("production").includes("Content-Security-Policy"));
  assert.ok(!names("development").includes("Content-Security-Policy"));
  for (const env of ["production", "development"]) {
    assert.ok(names(env).includes("X-Content-Type-Options"));
    assert.ok(names(env).includes("X-Frame-Options"));
  }
});
