// What a customer does, as the mobile app does it (frontend/mobile/src/features/*/queries.js):
// the read path behind the Week, Wallet and Statement screens, and the box edit + approve write
// path of M3 §3.1 (If-Match on every change, an Idempotency-Key on approve).

import { check, group, sleep } from "k6";
import http from "k6/http";
import { Counter, Rate } from "k6/metrics";
import { problemCode } from "./auth.js";
import { BASE_URL, headers, istMonth, think } from "./config.js";

const OK = http.expectedStatuses(200);
// No cycle yet (`subscriptions.no_cycle`) or no Budget Lock (`subscriptions.no_budget_lock`).
const OK_OR_NONE = http.expectedStatuses(200, 404);
// 412 `subscriptions.version_conflict`: another device changed the box first; the app reloads.
const OK_OR_CONFLICT = http.expectedStatuses(200, 412);

export const versionConflicts = new Rate("gb_version_conflicts");
export const idempotentReplays = new Counter("gb_idempotent_replays");
export const refusals = new Counter("gb_refusals"); // by `code`: cutoff_passed, slot_full, …

const EDITABLE = new Set(["awaiting_approval", "approved"]);

function params(user, name, kind, callback, extra) {
  return {
    headers: headers(user.token, user.caller, extra),
    tags: { kind, name },
    responseCallback: callback,
  };
}

function header(res, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(res.headers)) {
    if (key.toLowerCase() === wanted) return value;
  }
  return undefined;
}

/** The cycle's If-Match: the ETag it came with, else its version as the API quotes it. */
function etagOf(res, cycle) {
  return header(res, "etag") || `"${cycle.version}"`;
}

function body(res) {
  try {
    return res.json();
  } catch (_) {
    return null;
  }
}

function get(path, name, user, kind = "read", callback = OK) {
  return ["GET", `${BASE_URL}${path}`, null, params(user, name, kind, callback)];
}

/** One app session: open the app, the Week screen, the Wallet, this month's statement. */
export function readSession(user) {
  const month = istMonth();
  group("app start", () => {
    const [me, household] = http.batch([
      get("/v1/me", "GET /v1/me", user),
      get("/v1/households/me", "GET /v1/households/me", user),
    ]);
    check(me, { "me: 200": (r) => r.status === 200 });
    check(household, { "household: 200": (r) => r.status === 200 });
  });
  sleep(think());
  group("week", () => {
    const [cycle, unpaid, budget] = http.batch([
      get("/v1/cycles/current", "GET /v1/cycles/current", user, "read", OK_OR_NONE),
      get("/v1/orders?status=payment_failed", "GET /v1/orders?status=payment_failed", user),
      get(`/v1/budget-lock?month=${month}`, "GET /v1/budget-lock", user, "read", OK_OR_NONE),
    ]);
    check(cycle, {
      "cycle: 200 with ETag, or no cycle yet": (r) =>
        r.status === 200 ? Boolean(header(r, "etag")) : problemCode(r) === "subscriptions.no_cycle",
    });
    check(unpaid, { "unpaid orders: 200": (r) => r.status === 200 });
    check(budget, {
      "budget lock: 200 or none": (r) =>
        r.status === 200 || problemCode(r) === "subscriptions.no_budget_lock",
    });
  });
  sleep(think());
  group("wallet", () => {
    const [wallet, entries] = http.batch([
      get("/v1/wallet", "GET /v1/wallet", user),
      get("/v1/wallet/entries?limit=20", "GET /v1/wallet/entries", user),
    ]);
    check(wallet, {
      "wallet: 200, balance in integer paise": (r) =>
        r.status === 200 && Number.isInteger(r.json("balance_paise")),
    });
    check(entries, { "wallet entries: 200": (r) => r.status === 200 });
  });
  sleep(think());
  group("statement", () => {
    const res = http.request(...get(`/v1/statements?period=${month}`, "GET /v1/statements", user));
    check(res, { "statement: 200": (r) => r.status === 200 });
  });
}

/** Setup helper: the users whose current cycle can still be edited and approved. */
export function withEditableCycles(users, marginMs = 2 * 60 * 1000) {
  const ready = [];
  const seen = {};
  for (const user of users) {
    const res = http.request(
      ...get("/v1/cycles/current", "GET /v1/cycles/current", user, "setup", OK_OR_NONE),
    );
    const cycle = res.status === 200 ? body(res) : null;
    const status = cycle ? cycle.status : problemCode(res) || `http ${res.status}`;
    seen[status] = (seen[status] || 0) + 1;
    if (!cycle || !EDITABLE.has(cycle.status) || cycle.lines.length === 0) continue;
    if (Date.parse(cycle.cutoff_at) - Date.now() < marginMs) continue;
    ready.push({ ...user, cycleId: cycle.id, cutoffAt: cycle.cutoff_at });
  }
  console.log(`current cycles by status: ${JSON.stringify(seen)}; editable: ${ready.length}`);
  return ready;
}

// Per VU: the line this VU changed last, and its quantity before, so that the next edit puts it
// back. Two edits leave the box as it was, so the test can run again and again before the cut-off.
const changed = {};

function nextOp(cycle) {
  const last = changed[cycle.id];
  delete changed[cycle.id];
  if (last) {
    const line = cycle.lines.find((l) => l.product_id === last.product_id);
    if (line && line.qty !== last.qty) {
      return { op: "qty", product_id: last.product_id, qty: last.qty };
    }
  }
  // Lower a line first where one allows it (never above the box the planner made, so a Budget
  // Lock cannot refuse it), else raise a one-step line by one.
  const lowerable = cycle.lines.filter((l) => l.qty >= 2 && l.qty <= 20);
  const pool = lowerable.length > 0 ? lowerable : cycle.lines.filter((l) => l.qty === 1);
  if (pool.length === 0) return null;
  const line = pool[Math.floor(Math.random() * pool.length)];
  changed[cycle.id] = { product_id: line.product_id, qty: line.qty };
  return { op: "qty", product_id: line.product_id, qty: line.qty >= 2 ? line.qty - 1 : 2 };
}

function refused(res, what) {
  const code = problemCode(res) || `http_${res.status}`;
  refusals.add(1, { code });
  console.warn(`${what} answered ${res.status} ${code}`);
}

/** One box edit and approval: read the box, change one line (If-Match), approve the new version
 * (Idempotency-Key + If-Match), and sometimes repeat the approval to check the stored replay. */
export function editAndApprove(user, replayShare = 0.1) {
  const path = `/v1/cycles/${user.cycleId}`;
  let res = http.request(...get(path, "GET /v1/cycles/{id}", user));
  let cycle = res.status === 200 ? body(res) : null;
  if (!check(res, { "box: 200": () => cycle !== null })) return;
  if (!EDITABLE.has(cycle.status) || Date.parse(cycle.cutoff_at) <= Date.now()) {
    refusals.add(1, { code: `status_${cycle.status}` });
    return;
  }
  const op = nextOp(cycle);
  if (op === null) return;
  sleep(think(0.3, 1));

  res = http.patch(
    `${BASE_URL}${path}/lines`,
    JSON.stringify({ ops: [op] }),
    params(user, "PATCH /v1/cycles/{id}/lines", "write", OK_OR_CONFLICT, {
      "Content-Type": "application/json",
      "If-Match": etagOf(res, cycle),
    }),
  );
  versionConflicts.add(res.status === 412);
  if (res.status === 412) return; // the app reloads the box; the next iteration does
  cycle = res.status === 200 ? body(res) : null;
  if (!check(res, { "edit: 200": () => cycle !== null })) {
    refused(res, "edit");
    return;
  }
  sleep(think(0.3, 1));

  // The app's key is the intent: approving version v of this week (features/subscriptions).
  const approve = params(user, "POST /v1/cycles/{id}/approve", "write", OK_OR_CONFLICT, {
    "Idempotency-Key": `k6:cycle:${cycle.id}:approve:v${cycle.version}`,
    "If-Match": etagOf(res, cycle),
  });
  res = http.post(`${BASE_URL}${path}/approve`, null, approve);
  versionConflicts.add(res.status === 412);
  if (res.status === 412) return;
  const approved = res.status === 200 ? body(res) : null;
  const ok = check(res, {
    "approve: 200, order made": () => approved !== null && Boolean(approved.order_id),
  });
  if (!ok) {
    refused(res, "approve");
    return;
  }
  if (Math.random() < replayShare) {
    const again = http.post(`${BASE_URL}${path}/approve`, null, approve);
    const replayed = header(again, "idempotent-replayed") === "true";
    if (replayed) idempotentReplays.add(1);
    check(again, {
      "approve again, same key: the stored answer": (r) =>
        r.status === 200 && replayed && r.json("version") === approved.version,
    });
  }
}
