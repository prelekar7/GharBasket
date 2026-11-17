// The pages anyone may open without signing in: GET /v1/public/societies/{slug} (M9/M12, the
// society landing page) and GET /v1/public/cooks/{handle} (M8, "Know your cook"). A 200 carries
// `Cache-Control: public, max-age=300, stale-while-revalidate=600`; a 404 or 429 `no-store`.
// k6 caches nothing, so every request reaches the API: this measures the origin, the worst case.
//
// The API allows 60 of these a minute per address (platform/rate_limit.py). Without GB_IP_HEADER
// (README) a local run stays under that, so it measures latency, not capacity.
//
//   just load public                        # local, `load` profile
//   just load public -e GB_PROFILE=smoke

import { check } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { Rate } from "k6/metrics";
import {
  BASE_URL,
  envInt,
  fakeAddress,
  headers,
  IP_HEADER,
  PROFILE,
  thresholdsFor,
} from "./lib/config.js";

// The seed's societies (tools/seed.py); cooks have a public page only with their consent, so
// none by default: pass handles in GB_COOK_HANDLES.
const SLUGS = (
  __ENV.GB_SOCIETY_SLUGS ||
  "sunrise-towers-kharghar,green-valley-heights-kharghar,shree-ganesh-chs-kharghar," +
    "panchavati-residency-kamothe,krishna-kunj-new-panvel,sai-darshan-apartments-panvel"
)
  .split(",")
  .filter(Boolean);
const COOKS = (__ENV.GB_COOK_HANDLES || "").split(",").filter(Boolean);
const MISSING = "k6-no-such-society"; // a 404 is not cached; one request in ten asks for it

// Requests a minute: under the per-address limit unless the API reads GB_IP_HEADER's address.
const PER_MINUTE = { smoke: 20, load: IP_HEADER ? 3000 : 50, staging: 30 };
const DURATION = { smoke: "30s", load: "3m", staging: "2m" };

export const options = {
  scenarios: {
    public_pages: {
      executor: "constant-arrival-rate",
      rate: envInt("GB_RATE_PER_MIN", PER_MINUTE[PROFILE]),
      timeUnit: "1m",
      duration: __ENV.GB_DURATION || DURATION[PROFILE],
      preAllocatedVUs: PROFILE === "load" ? 20 : 2,
      maxVUs: PROFILE === "load" ? 200 : 5,
    },
  },
  thresholds: { ...thresholdsFor(["public"]), gb_rate_limited: ["rate<0.01"] },
};

const rateLimited = new Rate("gb_rate_limited");
const FOUND = http.expectedStatuses(200);
const NOT_FOUND = http.expectedStatuses(404);

export function setup() {
  if (!IP_HEADER && PROFILE === "load") {
    console.warn("GB_IP_HEADER is not set: one address, so the rate stays under 60 a minute");
  }
}

export default function () {
  const n = exec.scenario.iterationInTest;
  const roll = Math.random();
  let path;
  let name;
  let missing = false;
  if (roll < 0.1) {
    [path, name, missing] = [
      `/v1/public/societies/${MISSING}`,
      "GET /v1/public/societies/{slug}",
      true,
    ];
  } else if (COOKS.length > 0 && roll < 0.4) {
    path = `/v1/public/cooks/${COOKS[n % COOKS.length]}`;
    name = "GET /v1/public/cooks/{handle}";
  } else {
    path = `/v1/public/societies/${SLUGS[n % SLUGS.length]}`;
    name = "GET /v1/public/societies/{slug}";
  }
  const res = http.get(`${BASE_URL}${path}`, {
    headers: headers(null, fakeAddress((exec.vu.idInTest % 65535) + 1)),
    tags: { kind: "public", name },
    responseCallback: missing ? NOT_FOUND : FOUND,
  });
  rateLimited.add(res.status === 429);
  const cache = res.headers["Cache-Control"] || "";
  check(res, {
    "public page: 200, cacheable for 5 minutes": (r) =>
      missing || (r.status === 200 && cache.includes("public") && cache.includes("max-age=300")),
    "missing page: 404, never cached": (r) =>
      !missing || (r.status === 404 && cache.includes("no-store")),
  });
}
