// The customer's core read path: open the app (me, household), the Week screen (this week's
// cycle, unpaid orders, Budget Lock), the Wallet (balance, entries) and this month's statement,
// with a short pause between screens. About nine requests per session.
//
//   just load customer-reads                         # local, `load` profile
//   just load customer-reads -e GB_PROFILE=smoke
//   just load customer-reads -e GB_SESSIONS_PER_MIN=1200 -e GB_USERS=60 -e GB_IP_HEADER=x-gb-loadtest-ip

import exec from "k6/execution";
import { signIn, signOut } from "./lib/auth.js";
import { envInt, PROFILE, thresholdsFor } from "./lib/config.js";
import { readSession } from "./lib/flows.js";

// Users signed in by setup(); sessions spread over them (one household, several phones is fine).
const USERS = envInt("GB_USERS", { smoke: 5, load: 30, staging: 5 }[PROFILE]);
const PER_MINUTE = envInt("GB_SESSIONS_PER_MIN", { smoke: 6, load: 600, staging: 6 }[PROFILE]);

const SCENARIOS = {
  smoke: { executor: "constant-arrival-rate", rate: PER_MINUTE, duration: "1m" },
  staging: { executor: "constant-arrival-rate", rate: PER_MINUTE, duration: "2m" },
  load: {
    executor: "ramping-arrival-rate",
    startRate: 0,
    stages: [
      { target: PER_MINUTE, duration: "1m" },
      { target: PER_MINUTE, duration: __ENV.GB_DURATION || "4m" },
      { target: 0, duration: "30s" },
    ],
  },
};

export const options = {
  setupTimeout: "180s",
  scenarios: {
    customer_reads: {
      ...SCENARIOS[PROFILE],
      timeUnit: "1m",
      preAllocatedVUs: PROFILE === "load" ? 30 : 2,
      maxVUs: PROFILE === "load" ? 300 : 5,
      gracefulStop: "10s",
    },
  },
  thresholds: thresholdsFor(["read"]),
};

export function setup() {
  return { users: signIn(USERS) };
}

export default function ({ users }) {
  readSession(users[exec.scenario.iterationInTest % users.length]);
}

export function teardown({ users }) {
  signOut(users);
}
