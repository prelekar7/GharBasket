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

test("the policy is sent in production only; the other headers always", () => {
  const names = (/** @type {string} */ env) => securityHeaders(env).map((h) => h.key);
  assert.ok(names("production").includes("Content-Security-Policy"));
  assert.ok(!names("development").includes("Content-Security-Policy"));
  for (const env of ["production", "development"]) {
    assert.ok(names(env).includes("X-Content-Type-Options"));
    assert.ok(names(env).includes("X-Frame-Options"));
  }
});
