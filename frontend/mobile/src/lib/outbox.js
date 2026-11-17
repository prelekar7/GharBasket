// Offline outbox for field-role writes (Part 4): each action is saved in IndexedDB (Dexie) with
// its own Idempotency-Key and sent in order when the device is online again, so a retry never
// repeats an action on the server. There is no pre-emptive token refresh: request() refreshes and
// retries by itself when a send gets a 401, so N queued actions cost N requests, not N more
// refreshes (each of which also rotates the refresh token).
import { App } from "@capacitor/app";
import Dexie, { liveQuery } from "dexie";
import { useEffect, useState } from "react";
import { ApiError, request } from "@/lib/api.js";

/**
 * @typedef {object} OutboxItem
 * @property {number} [id]
 * @property {string} key Idempotency-Key, fixed when the action is first saved
 * @property {string} method
 * @property {string} path
 * @property {unknown} body
 * @property {number} createdAt
 * @property {"waiting" | "failed"} status "failed" = the server refused it for good (4xx)
 * @property {number} attempts
 * @property {string} [lastError] problem+json code of the last failure
 * @property {string} [target] what the action is about ("deliveries/<id>"), see targetOf()
 */

export const db = new Dexie("gharbasket");
db.version(1).stores({ outbox: "++id, createdAt, status" });
const outbox = /** @type {import("dexie").Table<OutboxItem, number>} */ (db.table("outbox"));

/**
 * What an action is about: "/v1/deliveries/<id>/proof" -> "deliveries/<id>". Actions on the same
 * target are the same door, stop, route or bag, whatever they did to it.
 * @param {string} path
 */
export const targetOf = (path) =>
  path
    .match(/^\/v1\/([a-z-]+)\/([^/?]+)/)
    ?.slice(1, 3)
    .join("/");

/**
 * Saves a write and tries to send it straight away. `key` is the Idempotency-Key a direct send
 * already used: if that send reached the server but its answer was lost, the replay is then the
 * same request, never a second one.
 * @returns {Promise<string>} its Idempotency-Key
 */
export async function enqueue(
  /** @type {{ method?: string, path: string, body?: unknown, key?: string }} */ action,
) {
  const key = action.key ?? crypto.randomUUID();
  await outbox.add({
    key,
    method: action.method ?? "POST",
    path: action.path,
    body: action.body ?? {},
    target: targetOf(action.path),
    createdAt: Date.now(),
    status: "waiting",
    attempts: 0,
  });
  flush();
  return key;
}

/**
 * Errors worth retrying later: offline, server trouble, expired session, rate limits, and the same
 * request still running on the server (409 `request.idempotency_in_progress`).
 */
const retryable = (/** @type {unknown} */ e) =>
  !(e instanceof ApiError) ||
  e.status >= 500 ||
  [401, 408, 429].includes(e.status) ||
  e.code === "request.idempotency_in_progress";

/**
 * Refusals that mean "this is already done (or no longer applies)": the action's goal is met, so
 * the item is dropped like a success instead of staying as a failure nobody can fix. Not every
 * `*_already_*` code belongs here: `logistics.photo_already_used` and
 * `logistics.pickup_already_reported` say the request itself was wrong, so those stay failed.
 * Only codes of actions the field apps queue are listed.
 */
const ALREADY_DONE = new Set([
  "logistics.routes_started", // the route is already running (a replayed Start)
  "logistics.already_handed_over", // the stop was already handed over
  "logistics.delivery_closed", // the order is already delivered, refunded or otherwise closed
  "hub_ops.task_packed", // the bag is already packed
]);

/**
 * @param {string} code @param {string} path
 * `logistics.delivery_closed` on `/undelivered` can also mean a not-home bag the captain cannot
 * hold yet (logistics deliveries.py): dropping it would leave the bag never held and the family
 * never told, so there it stays a failure she sees.
 */
const alreadyDone = (code, path) =>
  ALREADY_DONE.has(code) && !(code === "logistics.delivery_closed" && /\/undelivered$/.test(path));

/** @type {Promise<void> | null} */
let flushing = null;
/** Set when flush() is called during a run: something may have been saved after that run read its list. */
let again = false;
/** @type {ReturnType<typeof setTimeout> | null} */
let retryTimer = null;

/** Next try after a retryable failure: 15 s, 30 s, 1 min … up to 10 min, with some jitter. */
const backoffMs = (/** @type {number} */ attempts) =>
  Math.min(15_000 * 2 ** Math.max(attempts - 1, 0), 600_000) * (0.8 + Math.random() * 0.4);

function scheduleRetry(/** @type {number} */ attempts) {
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = setTimeout(() => {
    retryTimer = null;
    flush();
  }, backoffMs(attempts));
}

/**
 * What, inside its path, an action is about: packing's pick and shortage name the line's product in
 * the body, so two of them on one bag are about different lines. Other actions are about the path.
 * @param {unknown} body
 */
const subjectOf = (body) =>
  String(/** @type {{ product_id?: unknown } | null} */ (body)?.product_id ?? "");

/**
 * A later action succeeded for a target: failed items of the same action on the same target, and
 * about the same thing, are out of date (a wrong delivery code, once the door was delivered by
 * photo), so they go. A failed shortage for one product stays when a pick of another product on
 * that bag succeeds: it drives a refund. Items saved after it stay. Call it after a direct (not
 * queued) send succeeds too.
 * @param {{ path: string, body?: unknown, createdAt?: number }} done
 */
export async function dropStaleFailures(done) {
  const target = targetOf(done.path);
  if (!target) return;
  const before = done.createdAt ?? Date.now();
  const subject = subjectOf(done.body);
  await outbox
    .where("status")
    .equals("failed")
    .filter(
      (f) =>
        f.target === target &&
        f.path === done.path &&
        subjectOf(f.body) === subject &&
        f.createdAt <= before,
    )
    .delete();
}

/** One pass over the waiting actions. @returns {Promise<boolean>} true = stop, retry later */
async function pass() {
  const waiting = await outbox.where("status").equals("waiting").sortBy("createdAt");
  for (const item of waiting) {
    try {
      await request(item.path, {
        method: item.method,
        body: item.body,
        headers: { "idempotency-key": item.key },
      });
      await outbox.delete(/** @type {number} */ (item.id));
      await dropStaleFailures(item);
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "network";
      if (e instanceof ApiError && alreadyDone(code, item.path)) {
        // Same as a success for ordering: the next item goes on.
        await outbox.delete(/** @type {number} */ (item.id));
        await dropStaleFailures(item);
        continue;
      }
      const status = retryable(e) ? "waiting" : "failed";
      await outbox.update(/** @type {number} */ (item.id), {
        status,
        attempts: item.attempts + 1,
        lastError: code,
      });
      if (status === "waiting") {
        scheduleRetry(item.attempts + 1);
        return true;
      }
    }
  }
  return false;
}

/**
 * Sends waiting actions in order; stops at the first one that should be retried later. One run at
 * a time: a call made during a run does not start another, it makes that run read the list again,
 * so an action saved mid-flush goes out in the same burst.
 */
export function flush() {
  if (!navigator.onLine) return Promise.resolve();
  if (flushing) {
    again = true;
    return flushing;
  }
  flushing = (async () => {
    try {
      do {
        again = false;
        if (await pass()) break;
      } while (again);
    } finally {
      flushing = null;
      again = false;
    }
  })();
  return flushing;
}

/**
 * Failed items are never re-sent: the server refused them for good, so the same request would be
 * refused again (a Start re-sent 18 times, wrong codes re-sent after the door was delivered). The
 * person sees why and dismisses them, and "Try now" only flushes the waiting ones. A failed item
 * goes only by this, by a later success for the same target (dropStaleFailures) or at sign-out.
 */
export const dismissFailed = (/** @type {number} */ id) => outbox.delete(id);

/** Dismisses every failed item. */
export const dismissAllFailed = () => outbox.where("status").equals("failed").delete();

/** Flush on start, when the connection comes back and when the app returns to the foreground. */
export function startOutbox() {
  window.addEventListener("online", () => flush());
  App.addListener("appStateChange", ({ isActive }) => {
    if (isActive) flush();
  });
  flush();
}

/** Live count of actions in one state: "waiting" to be sent, or "failed" (refused by the server). */
export function useOutboxCount(/** @type {"waiting" | "failed"} */ status = "waiting") {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const sub = liveQuery(() => outbox.where("status").equals(status).count()).subscribe({
      next: setCount,
      error: () => setCount(0),
    });
    return () => sub.unsubscribe();
  }, [status]);
  return count;
}

/** Live list of the failed items, oldest first. */
export function useFailedItems() {
  const [items, setItems] = useState(/** @type {OutboxItem[]} */ ([]));
  useEffect(() => {
    const sub = liveQuery(() =>
      outbox.where("status").equals("failed").sortBy("createdAt"),
    ).subscribe({
      next: setItems,
      error: () => setItems([]),
    });
    return () => sub.unsubscribe();
  }, []);
  return items;
}

/** Empties the outbox (sign-out). */
export const clearOutbox = () => outbox.clear();

/** An action's name from its path: "/v1/stops/<id>/handover?x=1" -> "handover". @param {string} path */
export const actionOf = (path) => path.split("?")[0].split("/").pop() || "other";

/**
 * What is in the outbox, waiting or failed, as action names and how many of each, in the order
 * they were first saved. Read just before a session that ended is wiped from the device, so the
 * person is told what was not sent. Names only: no ids, nothing about a family or an order.
 * @returns {Promise<{ action: string, count: number }[]>}
 */
export async function summarizeOutbox() {
  const counts = new Map();
  for (const { path } of await outbox.orderBy("createdAt").toArray()) {
    const action = actionOf(path);
    counts.set(action, (counts.get(action) ?? 0) + 1);
  }
  return [...counts].map(([action, count]) => ({ action, count }));
}
