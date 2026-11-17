// The write path of handbook 11 §11.6, "200 concurrent box edits": each VU is one household that
// reads its box, changes one line (PATCH /v1/cycles/{id}/lines, If-Match) and approves the new
// version (POST /v1/cycles/{id}/approve, Idempotency-Key + If-Match), which makes or updates its
// M4 order. One approval in ten is sent twice with the same key and must come back as the stored
// answer (`Idempotent-Replayed: true`). Every second edit puts the line back, so the boxes end
// as they began and the test can run again until the cut-off.
//
// Needs households whose current cycle is `awaiting_approval` or `approved` with the cut-off
// ahead: README "Local data". Keep GB_VUS at or under the editable households setup() reports;
// VUs above that share a household and meet as 412 version conflicts (gb_version_conflicts), as
// two phones of one family would.
//
//   just load box-writes -e GB_PROFILE=smoke
//   just load box-writes -e GB_IP_HEADER=x-gb-loadtest-ip -e GB_USERS=205

import { sleep } from "k6";
import { signIn, signOut } from "./lib/auth.js";
import { envInt, PROFILE, think, thresholdsFor, WRITES_ALLOWED } from "./lib/config.js";
import { editAndApprove, withEditableCycles } from "./lib/flows.js";

if (!WRITES_ALLOWED) {
  throw new Error(
    "box-writes makes orders: on a shared database it needs GB_ALLOW_REMOTE_WRITES=yes",
  );
}

const USERS = envInt("GB_USERS", { smoke: 5, load: 205, staging: 2 }[PROFILE]);
const VUS = envInt("GB_VUS", { smoke: 2, load: 200, staging: 1 }[PROFILE]);

const STAGES = {
  smoke: [{ target: VUS, duration: "30s" }],
  staging: [{ target: VUS, duration: "1m" }],
  load: [
    { target: VUS, duration: "30s" },
    { target: VUS, duration: __ENV.GB_DURATION || "3m" },
    { target: 0, duration: "15s" },
  ],
};

export const options = {
  setupTimeout: "300s",
  scenarios: {
    box_edits: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: STAGES[PROFILE],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    ...thresholdsFor(["read", "write"]),
    // Business refusals (slot full, cut-off passed, …) are real failures of the test data.
    gb_refusals: ["count<1"],
  },
};

export function setup() {
  const signedIn = signIn(USERS);
  const users = withEditableCycles(signedIn);
  if (users.length === 0) {
    throw new Error('no household has an editable box before its cut-off: see README "Local data"');
  }
  if (users.length < VUS) {
    console.warn(`${users.length} editable households for ${VUS} VUs: VUs above that share one`);
  }
  return { users, signedIn };
}

export default function ({ users }) {
  // VU n is household n: no two VUs edit one box until VUs outnumber the households.
  editAndApprove(users[(__VU - 1) % users.length]);
  sleep(think(1, 3)); // the person looks at the approved box before the next change
}

export function teardown({ signedIn }) {
  signOut(signedIn);
}
