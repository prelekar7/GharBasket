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

/** Sends waiting actions in order; stops at the first one that should be retried later. */
export function flush() {
  if (!navigator.onLine) return Promise.resolve();
  flushing ??= (async () => {
    try {
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
          if (status === "waiting") return;
        }
      }
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}

/** Flush on start, when the connection comes back and when the app returns to the foreground. */
export function startOutbox() {
  window.addEventListener("online", () => flush());
  App.addListener("appStateChange", ({ isActive }) => {
    if (isActive) flush();
  });
  flush();
}

/** Live count of actions still waiting to be sent. */
export function useWaitingCount() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const sub = liveQuery(() => outbox.where("status").equals("waiting").count()).subscribe({
      next: setCount,
      error: () => setCount(0),
    });
    return () => sub.unsubscribe();
  }, []);
  return count;
}

/** Empties the outbox (sign-out). */
export const clearOutbox = () => outbox.clear();
