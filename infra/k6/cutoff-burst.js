// The evening before a delivery day, compressed into about seven minutes. The jobs set its shape
// (handbook 10 §10.4, M3 §6): `remind_unapproved` at T-1 19:00 tells every household whose box
// is still unapproved, `apply_auto_approve` runs at 20:55, and `cutoff_lock` at 21:00 ends all
// edits (409 subscriptions.cutoff_passed). So app sessions and box edits + approvals bump after
// the reminder, ease off, then climb to a peak in the last minutes and stop dead at 21:00. The
// docs give the shape, not the numbers: the peaks below are assumptions, set with GB_READ_PEAK
// and GB_WRITE_PEAK (iterations a minute). After a local run, `cutoff-lock.js` times the lock.
//
//   just load cutoff-burst -e GB_PROFILE=smoke
//   just load cutoff-burst -e GB_IP_HEADER=x-gb-loadtest-ip -e GB_USERS=205

import exec from "k6/execution";
import { signIn, signOut } from "./lib/auth.js";
import { envInt, PROFILE, thresholdsFor, WRITES_ALLOWED } from "./lib/config.js";
import { editAndApprove, readSession, withEditableCycles } from "./lib/flows.js";

if (!WRITES_ALLOWED) {
  throw new Error(
    "cutoff-burst makes orders: on a shared database it needs GB_ALLOW_REMOTE_WRITES=yes",
  );
}

const USERS = envInt("GB_USERS", { smoke: 5, load: 205, staging: 2 }[PROFILE]);
const READ_PEAK = envInt("GB_READ_PEAK", { smoke: 12, load: 900, staging: 6 }[PROFILE]);
const WRITE_PEAK = envInt("GB_WRITE_PEAK", { smoke: 6, load: 600, staging: 2 }[PROFILE]);

/** The evening's shape for a scenario peaking at `peak` iterations a minute. */
function evening(peak) {
  const at = (share) => Math.max(1, Math.round(peak * share));
  return [
    { target: at(0.1), duration: "30s" }, // 18:30–19:00: boxes are ready, a few look
    { target: at(0.4), duration: "30s" }, // 19:00: the cut-off reminder goes out
    { target: at(0.4), duration: "1m" },
    { target: at(0.15), duration: "1m" }, // 19:30–20:30: dinner
    { target: at(0.6), duration: "1m30s" }, // 20:30–20:50: "the box closes at 9"
    { target: peak, duration: "1m" }, // 20:50–20:59: the last minutes
    { target: peak, duration: "1m" },
    { target: 0, duration: "5s" }, // 21:00: cut-off
  ];
}

function scenario(fn, peak, vus) {
  return {
    executor: "ramping-arrival-rate",
    exec: fn,
    startRate: 0,
    timeUnit: "1m",
    stages: evening(peak),
    preAllocatedVUs: vus,
    maxVUs: vus * 4,
    gracefulStop: "5s",
  };
}

export const options = {
  setupTimeout: "300s",
  scenarios: {
    evening_reads: scenario("reads", READ_PEAK, PROFILE === "load" ? 40 : 2),
    evening_writes: scenario("writes", WRITE_PEAK, PROFILE === "load" ? 40 : 2),
  },
  thresholds: {
    ...thresholdsFor(["read", "write"]),
    gb_refusals: ["count<1"],
  },
};

export function setup() {
  const signedIn = signIn(USERS);
  const editable = withEditableCycles(signedIn, 10 * 60 * 1000); // the whole run before the cut-off
  if (editable.length === 0) {
    throw new Error('no household has an editable box before its cut-off: see README "Local data"');
  }
  return { signedIn, editable };
}

export function reads({ signedIn }) {
  readSession(signedIn[exec.scenario.iterationInTest % signedIn.length]);
}

export function writes({ editable }) {
  // A VU keeps its household, so its next edit can put the last one back. When the peak needs
  // more VUs than there are households, two VUs share a box and meet as 412s (two phones).
  editAndApprove(editable[(exec.vu.idInTest - 1) % editable.length]);
}

export function teardown({ signedIn }) {
  signOut(signedIn);
}
