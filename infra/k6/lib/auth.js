// Sessions for the customer scripts.
//
// Local: `dev_phone` sign-in (POST /v1/auth/exchange), which the API accepts only with APP_ENV=dev
// (`just dev`), handbook 13 §13.3 item 8. It answers like the app's sign-in: a 15-minute access
// token in JSON (no X-GB-Client header, so no cookies). Keep a run under about 14 minutes.
// Remote: no sign-in at all. GB_ACCESS_TOKENS holds pre-made access tokens (comma-separated),
// taken from the environment only, never written to a file, never printed.

import http from "k6/http";
import { BASE_URL, envInt, fakeAddress, headers, IS_LOCAL } from "./config.js";

// The seed's customers with a household (backend/api/src/gharbasket/tools/seed.py).
const SEED_CUSTOMERS = [
  "+919800001001", // Priya, Sunrise Towers
  "+919800001002", // Sunita, Green Valley Heights
  "+919800001003", // Ramesh, Krishna Kunj
  "+919800001004", // Meena (captain), Sunrise Towers
  "+919800001012", // Savita (captain), Krishna Kunj
];
// `just sim` numbers its households +91 98001 00001 onwards (data/simulator, households.py).
const SIM_PREFIX = "+9198001";

const SETUP_OK = http.expectedStatuses(200);
const LOGOUT_OK = http.expectedStatuses(204, 401);

/** The phones to sign in: GB_PHONES, else the seed customers and the first GB_SIM_USERS simulated
 * households. A phone without an account becomes a new empty customer (only ever locally). */
export function customerPhones(simUsers) {
  if (__ENV.GB_PHONES) {
    return __ENV.GB_PHONES.split(",")
      .map((p) => p.trim())
      .filter(Boolean);
  }
  const phones = [...SEED_CUSTOMERS];
  for (let n = 1; n <= simUsers; n++) phones.push(`${SIM_PREFIX}${String(n).padStart(5, "0")}`);
  return phones;
}

/** Sign in up to `max` customers; [{ token, caller }]. Call it from setup(). */
export function signIn(max) {
  if (!IS_LOCAL) return remoteTokens(max);
  const users = [];
  const phones = customerPhones(envInt("GB_SIM_USERS", max)).slice(0, max);
  for (const [i, phone] of phones.entries()) {
    const caller = fakeAddress(i + 1, true);
    const res = http.post(
      `${BASE_URL}/v1/auth/exchange`,
      JSON.stringify({ dev_phone: phone, device: { platform: "android" } }),
      {
        headers: headers(null, caller, { "Content-Type": "application/json" }),
        tags: { kind: "setup", name: "POST /v1/auth/exchange" },
        responseCallback: SETUP_OK,
      },
    );
    if (res.status === 200) {
      users.push({ token: res.json("access_token"), caller });
      continue;
    }
    const code = problemCode(res);
    if (code === "auth.dev_login_disabled") {
      throw new Error("dev_phone sign-in is refused: run the API with APP_ENV=dev (`just dev`)");
    }
    if (res.status === 429) {
      console.warn(
        `sign-in rate limit after ${users.length} users (30 per 10 minutes per address, 10 per ` +
          "phone): set GB_IP_HEADER as the README says, or wait and use fewer users",
      );
      break;
    }
    console.warn(`sign-in of user ${i + 1} answered ${res.status} ${code || ""}; skipped`);
  }
  if (users.length === 0) throw new Error(`no customer could sign in at ${BASE_URL}`);
  return users;
}

function remoteTokens(max) {
  const tokens = (__ENV.GB_ACCESS_TOKENS || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  if (tokens.length === 0) {
    throw new Error(
      "a remote run takes pre-made access tokens from GB_ACCESS_TOKENS (no sign-in on staging)",
    );
  }
  return tokens.slice(0, max).map((token) => ({ token, caller: undefined }));
}

/** End the sessions setup() started (local only; a remote token belongs to its owner). */
export function signOut(users) {
  if (!IS_LOCAL) return;
  for (const user of users) {
    http.post(`${BASE_URL}/v1/auth/logout`, null, {
      headers: headers(user.token, user.caller),
      tags: { kind: "setup", name: "POST /v1/auth/logout" },
      responseCallback: LOGOUT_OK,
    });
  }
}

/** The RFC 9457 `code` of a problem answer, or null. */
export function problemCode(res) {
  try {
    return res.json("code") || null;
  } catch (_) {
    return null;
  }
}
