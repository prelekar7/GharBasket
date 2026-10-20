// Proof that a test ran on the real backend and a record of what it found missing.
//  - Every /v1/* answer the browser gets from an app's proxy (mobile) or from the API itself must carry the
//    backend's X-Request-ID (platform/request_context.py). A mock (MSW) would not; the test fails.
//  - A 404 http.not_found or 405 http.method_not_allowed (a route the backend does not have yet) is recorded in
//    test-results/unshipped.json, so a run lists the calls the apps make that are not shipped.
// The console's own /v1/auth/session route is the console's answer, not the backend's, and is skipped; its other
// calls are server-side and not seen by the browser.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { ORIGINS } from "./stack.js";

const FILE = `${import.meta.dirname}/../test-results/unshipped.json`;
const NOT_SHIPPED = new Set(["http.not_found", "http.method_not_allowed"]);

function backendAnswer(url) {
  const { origin, pathname } = new URL(url);
  if (!pathname.startsWith("/v1/")) return false;
  if (origin === ORIGINS.console) return false;
  return true;
}

function record(entries) {
  if (!entries.length) return;
  mkdirSync(`${import.meta.dirname}/../test-results`, { recursive: true });
  let known = [];
  try {
    known = JSON.parse(readFileSync(FILE, "utf8"));
  } catch {}
  const seen = new Set(known.map((e) => `${e.method} ${e.path}`));
  for (const e of entries) {
    if (seen.has(`${e.method} ${e.path}`)) continue;
    seen.add(`${e.method} ${e.path}`);
    known.push(e);
  }
  writeFileSync(FILE, `${JSON.stringify(known, null, 2)}\n`);
}

/**
 * Start watching a page. Returns `finish()`: waits for the open checks, records the unshipped calls, and returns
 * the answers that lacked the backend's request id.
 * @param {import("@playwright/test").Page} page @param {string} testTitle
 */
export function watch(page, testTitle) {
  const checks = [];
  const mocked = [];
  const unshipped = [];
  page.on("response", (response) => {
    const url = response.url();
    if (!backendAnswer(url)) return;
    const request = response.request();
    // Ids out, so a route is listed once: /v1/cycles/{}/meals.
    const path = new URL(url).pathname.replace(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g,
      "{}",
    );
    checks.push(
      (async () => {
        if (!(await response.headerValue("x-request-id"))) {
          mocked.push(`${request.method()} ${path} -> ${response.status()}`);
          return;
        }
        if (response.status() === 404 || response.status() === 405) {
          const body = await response.json().catch(() => null);
          if (NOT_SHIPPED.has(body?.code))
            unshipped.push({ method: request.method(), path, code: body.code, test: testTitle });
        }
      })().catch(() => {}),
    );
  });
  return async function finish() {
    await Promise.all(checks);
    record(unshipped);
    return mocked;
  };
}
