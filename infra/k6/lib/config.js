// Settings shared by the k6 scripts (infra/k6/README.md). Everything comes from `-e NAME=value`
// (k6's __ENV); nothing secret is ever written in these files.

// A target counts as local only when it is this machine.
const LOCAL_URL = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/i;

export const BASE_URL = (__ENV.GB_BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
export const IS_LOCAL = LOCAL_URL.test(BASE_URL);

// Any other host is a shared, free-plan deployment (README "WARNING: staging"): it needs the owner's
// go, typed out, and then always runs the small `staging` profile, whatever GB_PROFILE says.
if (!IS_LOCAL && __ENV.GB_STAGING_GO !== "yes") {
  throw new Error(
    `${BASE_URL} is not a local API. A run against staging needs the owner's go, a Cloudflare ` +
      "WAF skip rule for your IP, and GB_STAGING_GO=yes (infra/k6/README.md).",
  );
}

const PROFILES = ["smoke", "load", "staging"];
export const PROFILE = IS_LOCAL ? __ENV.GB_PROFILE || "load" : "staging";
if (!PROFILES.includes(PROFILE)) {
  throw new Error(`GB_PROFILE must be one of ${PROFILES.join(", ")}`);
}

// Writes create orders. On a shared database they also need GB_ALLOW_REMOTE_WRITES=yes.
export const WRITES_ALLOWED = IS_LOCAL || __ENV.GB_ALLOW_REMOTE_WRITES === "yes";

// Pilot SLOs, handbook 12 §12.6 (also TDD §13): API p95 < 300 ms for reads, < 800 ms for writes;
// the cut-off lock under 60 s for about 2,000 orders. The docs set no error-rate target: 1 %.
export const SLO = { readP95: 300, writeP95: 800, cutoffMs: 60000, errorRate: 0.01 };

// Staging stops itself early when its answers turn into errors (a Cloudflare challenge is a 403,
// the API's per-address limits are 429s): better a short report than a blocked IP.
const STAGING_ABORT = { threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "10s" };

/** Thresholds for the request kinds a script sends ("read", "write", "public"). */
export function thresholdsFor(kinds) {
  const out = { checks: ["rate>0.99"] };
  for (const kind of kinds) {
    const p95 = kind === "write" ? SLO.writeP95 : SLO.readP95;
    out[`http_req_duration{kind:${kind}}`] = [`p(95)<${p95}`];
    out[`http_req_failed{kind:${kind}}`] =
      PROFILE === "staging" ? [`rate<${SLO.errorRate}`, STAGING_ABORT] : [`rate<${SLO.errorRate}`];
  }
  return out;
}

// Locally, every request comes from 127.0.0.1, so the API's per-address limits (sign-in: 30 per
// 10 minutes; `/v1/public/*`: 60 a minute) would cap the test, not the server. Start the API with
// CLIENT_IP_SOURCE=header:x-gb-loadtest-ip and run k6 with GB_IP_HEADER=x-gb-loadtest-ip: each VU
// (and each signed-in user) then sends its own address from 198.18.0.0/15, the range RFC 2544
// keeps for benchmarks. Never on staging: there the address comes from Cloudflare.
export const IP_HEADER = IS_LOCAL && __ENV.GB_IP_HEADER ? __ENV.GB_IP_HEADER.toLowerCase() : null;

/** A benchmark-range address for caller `n` (VUs use 198.18.x.y, setup's sign-ins 198.19.x.y). */
export function fakeAddress(n, setup = false) {
  return `${setup ? "198.19" : "198.18"}.${(n >> 8) & 255}.${n & 255}`;
}

/** Request headers: JSON, the app's Bearer token, and (locally, if set up) the caller's address. */
export function headers(token, caller, extra = {}) {
  const out = { Accept: "application/json", "User-Agent": "gharbasket-k6", ...extra };
  if (token) out.Authorization = `Bearer ${token}`;
  if (IP_HEADER && caller !== undefined) out[IP_HEADER] = caller;
  return out;
}

/** Today's IST month, YYYY-MM (statements and the Budget Lock are per IST month). */
export function istMonth(now = Date.now()) {
  return new Date(now + 330 * 60 * 1000).toISOString().slice(0, 7);
}

/** An integer from the environment, else `fallback`. */
export function envInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === "") return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value) || value < 0) throw new Error(`${name} must be a whole number`);
  return value;
}

/** A random think time between `min` and `max` seconds. */
export function think(min = 0.5, max = 1.5) {
  return min + Math.random() * (max - min);
}
