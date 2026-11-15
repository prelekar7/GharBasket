// Times M4's cut-off lock: "under 60 s for about 2,000 orders" (M4 US-2 and §9, handbook 12
// §12.6). It triggers `cutoff_lock` once through POST /v1/internal/jobs/cutoff_lock with the
// cycles' own cut-off as `scheduled_for`, as the VM scheduler does at 21:00 IST, and reads the
// run's start and finish. The local seed has at most a few hundred households, so besides the
// time it reports the time scaled to 2,000 orders (linear, an estimate) and holds both to 60 s.
//
// LOCAL ONLY, and one way: every approved box of that delivery date is debited and locked (or
// left payment_failed), so those boxes can no longer be edited. Approve boxes first (box-writes
// or cutoff-burst), then:
//
//   just load cutoff-lock -e GB_CONFIRM_CUTOFF=yes
//   just load cutoff-lock -e GB_CONFIRM_CUTOFF=yes -e GB_CUTOFF_AT=2026-11-16T21:00:00+05:30

import { check } from "k6";
import http from "k6/http";
import { Counter, Trend } from "k6/metrics";
import { signIn, signOut } from "./lib/auth.js";
import { BASE_URL, headers, IS_LOCAL, SLO } from "./lib/config.js";
import { withEditableCycles } from "./lib/flows.js";

if (!IS_LOCAL) {
  throw new Error("cutoff-lock runs only against a local API: on staging the lock is the VM's job");
}
if (__ENV.GB_CONFIRM_CUTOFF !== "yes") {
  throw new Error(
    "cutoff-lock debits and locks every approved box of that delivery date in the local " +
      "database; set GB_CONFIRM_CUTOFF=yes to go ahead",
  );
}

export const options = {
  setupTimeout: "120s",
  scenarios: {
    cutoff_lock: { executor: "per-vu-iterations", vus: 1, iterations: 1, maxDuration: "6m" },
  },
  thresholds: {
    checks: ["rate==1"],
    gb_cutoff_ms: [`max<${SLO.cutoffMs}`],
    gb_cutoff_ms_per_2000: [`max<${SLO.cutoffMs}`],
  },
};

const lockMs = new Trend("gb_cutoff_ms", true);
const per2000 = new Trend("gb_cutoff_ms_per_2000", true);
const orders = new Counter("gb_cutoff_orders");

export function setup() {
  if (__ENV.GB_CUTOFF_AT) return { scheduledFor: __ENV.GB_CUTOFF_AT };
  // The cut-off of a household's current box (21:00 IST the day before its delivery).
  const users = signIn(5);
  const [first] = withEditableCycles(users, 0);
  signOut(users);
  if (!first) {
    throw new Error("no open box to take the cut-off from: set GB_CUTOFF_AT (an ISO time)");
  }
  return { scheduledFor: first.cutoffAt };
}

export default function ({ scheduledFor }) {
  // Only when the local API has JOBS_TOKEN set (it has none by default): from the environment.
  const res = http.post(
    `${BASE_URL}/v1/internal/jobs/cutoff_lock`,
    JSON.stringify({ scheduled_for: scheduledFor }),
    {
      headers: headers(__ENV.GB_JOBS_TOKEN, undefined, { "Content-Type": "application/json" }),
      tags: { kind: "job", name: "POST /v1/internal/jobs/cutoff_lock" },
      timeout: "300s",
    },
  );
  let run = null;
  try {
    run = res.status === 200 ? res.json() : null;
  } catch (_) {
    run = null;
  }
  if (!check(res, { "cutoff_lock: 200 and the run is ok": () => run?.status === "ok" })) {
    console.error(`cutoff_lock answered ${res.status}: ${String(res.body).slice(0, 300)}`);
    return;
  }
  const ms = Date.parse(run.finished_at) - Date.parse(run.started_at);
  const summary = run.summary || {};
  const handled = (summary.locked || 0) + (summary.payment_failed || 0);
  lockMs.add(ms);
  orders.add(handled);
  console.log(
    `cutoff_lock for ${summary.delivery_date}: ${handled} orders in ${ms} ms ` +
      `(${JSON.stringify(summary)}); request ${Math.round(res.timings.duration)} ms`,
  );
  if (handled === 0) {
    console.warn("no order was locked: approve boxes first, or this date was locked already");
    return;
  }
  per2000.add((ms / handled) * 2000);
}
