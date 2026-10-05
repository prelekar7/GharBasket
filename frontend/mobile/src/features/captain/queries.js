import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { request } from "@/lib/api.js";
import { db, enqueue, flush } from "@/lib/outbox.js";
import { applyAction } from "./day.js";

const DAY = ["captain-day"];

/** Writes still waiting in the outbox (offline), oldest first. @returns {Promise<import("./day.js").Action[]>} */
async function waiting() {
  try {
    return await db.table("outbox").where("status").equals("waiting").sortBy("createdAt");
  } catch {
    return [];
  }
}

/**
 * The captain's delivery day (GET /v1/logistics/captain/today, M7, proposal). Queued writes are
 * sent first, and any still waiting are laid over the answer, so a refetch never undoes a scan
 * made without signal.
 */
export function useCaptainDay() {
  return useQuery({
    queryKey: DAY,
    queryFn: async () => {
      await flush().catch(() => {});
      const day = /** @type {import("./day.js").CaptainDay} */ (
        await request("/v1/logistics/captain/today")
      );
      return (await waiting()).reduce(applyAction, day);
    },
  });
}

/** A captain write: shown at once, sent through the offline outbox with its own Idempotency-Key. */
export function useCaptainWrite() {
  const client = useQueryClient();
  return useCallback(
    (/** @type {import("./day.js").Action} */ action) => {
      client.setQueryData(DAY, (/** @type {import("./day.js").CaptainDay | undefined} */ d) =>
        d ? applyAction(d, action) : d,
      );
      return enqueue({ method: "POST", ...action });
    },
    [client],
  );
}

/**
 * What the captain has done on this phone but not sent yet: crates scanned before "All received",
 * and problems at the door before "Save and tell the families". Kept in the persisted query cache,
 * so it survives a reload and is wiped on sign-out with the rest.
 * @typedef {{ kind: "not_home" | "damaged", note?: string, photo_key?: string | null, outcome?: "lobby" | "return" }} Issue
 * @typedef {{ crates: string[], issues: Record<string, Issue> }} Drafts
 */
const EMPTY = /** @type {Drafts} */ ({ crates: [], issues: {} });

/** @param {string | undefined} date */
export function useDrafts(date) {
  const client = useQueryClient();
  const key = ["captain-drafts", date];
  const { data = EMPTY } = useQuery({
    queryKey: key,
    queryFn: () => EMPTY,
    staleTime: Number.POSITIVE_INFINITY,
    enabled: !!date,
  });
  const update = (/** @type {(d: Drafts) => Drafts} */ fn) =>
    client.setQueryData(key, (/** @type {Drafts | undefined} */ d) => fn(d ?? EMPTY));
  return /** @type {const} */ ([data, update]);
}

/**
 * @typedef {{ date: string, boxes: number, tiffins: number, amount_paise: number }} EarningDay
 * @typedef {{ display_name: string, scope_name: string, verified: boolean, total_paise: number, items: EarningDay[] }} Earnings
 */

/** The last delivery days' earnings (GET /v1/earnings, M8 US-7; no `week` = the latest days). */
export function useEarnings() {
  return useQuery({
    queryKey: ["captain-earnings"],
    queryFn: () => /** @type {Promise<Earnings>} */ (request("/v1/earnings")),
  });
}

/** Downloads the income certificate PDF (GET /v1/earnings/certificate → a short-lived link). */
export async function downloadCertificate() {
  const { url, filename } = /** @type {{ url: string, filename: string }} */ (
    await request("/v1/earnings/certificate")
  );
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
}
