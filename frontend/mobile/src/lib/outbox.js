// Offline outbox for field-role writes (Part 4): each action is saved in IndexedDB (Dexie) with
// its own Idempotency-Key and sent in order when the device is online again, so a retry never
// repeats an action on the server. The token is refreshed before a replay.
import { App } from "@capacitor/app";
import Dexie, { liveQuery } from "dexie";
import { useEffect, useState } from "react";
import { ApiError, refresh, request } from "@/lib/api.js";

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
 */

export const db = new Dexie("gharbasket");
db.version(1).stores({ outbox: "++id, createdAt, status" });
const outbox = /** @type {import("dexie").Table<OutboxItem, number>} */ (db.table("outbox"));

/** Saves a write and tries to send it straight away. @returns {Promise<string>} its Idempotency-Key */
export async function enqueue(
  /** @type {{ method?: string, path: string, body?: unknown }} */ action,
) {
  const key = crypto.randomUUID();
  await outbox.add({
    key,
    method: action.method ?? "POST",
    path: action.path,
    body: action.body ?? {},
    createdAt: Date.now(),
    status: "waiting",
    attempts: 0,
  });
  flush();
  return key;
}

/** Errors worth retrying later: offline, server trouble, expired session, rate limits. */
const retryable = (/** @type {unknown} */ e) =>
  !(e instanceof ApiError) || e.status >= 500 || [401, 408, 429].includes(e.status);

/** @type {Promise<void> | null} */
let flushing = null;
/** An action was saved while a flush was running: run once more when it ends. */
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
 * Sends waiting actions in order; stops at the first one that should be retried later and
 * schedules that retry. Actions saved during a flush are picked up before it ends.
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
        const waiting = await outbox.where("status").equals("waiting").sortBy("createdAt");
        if (waiting.length === 0 || !(await refresh())) return;
        for (const item of waiting) {
          try {
            await request(item.path, {
              method: item.method,
              body: item.body,
              headers: { "idempotency-key": item.key },
            });
            await outbox.delete(/** @type {number} */ (item.id));
          } catch (e) {
            const code = e instanceof ApiError ? e.code : "network";
            const status = retryable(e) ? "waiting" : "failed";
            await outbox.update(/** @type {number} */ (item.id), {
              status,
              attempts: item.attempts + 1,
              lastError: code,
            });
            if (status === "waiting") {
              scheduleRetry(item.attempts + 1);
              return;
            }
          }
        }
      } while (again);
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}

/**
 * Sends the actions the server refused again (after the person has fixed the cause, or to try
 * once more). They are never deleted silently: they stay until sent or until sign-out.
 */
export async function retryFailed() {
  await outbox.where("status").equals("failed").modify({ status: "waiting" });
  return flush();
}

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

/** Empties the outbox (sign-out). */
export const clearOutbox = () => outbox.clear();
