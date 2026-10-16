import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { istDate } from "@/features/subscriptions/format.js";
import { ApiError, request } from "@/lib/api.js";
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
 * The captain's delivery day (GET /v1/captains/me/today, M7 §3.4; no `date` = today). Queued
 * writes are sent first, and any still waiting are laid over the answer, so a refetch never undoes
 * a scan made without signal.
 */
export function useCaptainDay() {
  return useQuery({
    queryKey: DAY,
    queryFn: async () => {
      await flush().catch(() => {});
      const day = /** @type {import("./day.js").CaptainDay} */ (
        await request("/v1/captains/me/today")
      );
      return (await waiting()).reduce(applyAction, day);
    },
  });
}

/**
 * @typedef {object} Partner GET /v1/partners/me → { items: Partner[] }, one per kind (M8 §3.3)
 * @property {string} id
 * @property {"kitchen" | "captain" | "rider" | "packer"} kind
 * @property {string} hub_id
 * @property {string} display_name "Meena tai"
 * @property {{ average: number, count: number } | null} rating Kitchens with 5 ratings or more.
 * @property {"active" | "paused" | "suspended" | "left"} status
 * @property {string} verified_at
 */

/** The captain's partner record: her display name and whether she is verified. */
export function useCaptainPartner() {
  return useQuery({
    queryKey: ["captain-partner"],
    queryFn: async () => {
      const { items } = /** @type {{ items: Partner[] }} */ (await request("/v1/partners/me"));
      return items.find((p) => p.kind === "captain") ?? null;
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
 * The handover is sent at once, because the server checks the code the captain typed and the counts
 * (422 `logistics.code_invalid`, 409 `logistics.count_mismatch`…) and she must hear it at the gate.
 * Only without signal is it kept in the outbox, to be checked when the phone is online again.
 * Throws the server's ApiError when it refuses.
 */
export function useHandover() {
  const client = useQueryClient();
  return useCallback(
    async (/** @type {string} */ stopId, /** @type {Record<string, unknown>} */ body) => {
      const action = { path: `/v1/stops/${stopId}/handover`, body };
      const show = () =>
        client.setQueryData(DAY, (/** @type {import("./day.js").CaptainDay | undefined} */ d) =>
          d ? applyAction(d, action) : d,
        );
      try {
        await request(action.path, {
          method: "POST",
          body,
          headers: { "idempotency-key": crypto.randomUUID() },
        });
        show();
      } catch (e) {
        if (e instanceof ApiError) throw e;
        show();
        await enqueue({ method: "POST", ...action });
      }
    },
    [client],
  );
}

/**
 * What the captain has noticed at a door on this phone but not sent yet: problems before "Save and
 * tell the families". Kept in the persisted query cache, so it survives a reload and is wiped on
 * sign-out with the rest. Keyed by order id.
 * @typedef {{ kind: "not_home" | "damaged", note?: string, photo_key?: string | null }} Issue
 * @typedef {{ issues: Record<string, Issue> }} Drafts
 */
const EMPTY = /** @type {Drafts} */ ({ issues: {} });

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
 * @typedef {{ date: string, bags: number, amount_paise: number }} EarningDay
 * @typedef {{ total_paise: number, bags: number, items: EarningDay[] }} Earnings This week, from EarningsWeek
 */

/**
 * This week's earnings (GET /v1/earnings, M8 §3.6; no `week` = this week): the days that paid, with
 * the bags of each day counted from the entries (`bag_delivered`, 1 unit per bag).
 */
export function useEarnings() {
  return useQuery({
    queryKey: ["captain-earnings"],
    queryFn: async () => {
      const w = /** @type {{ total_paise: number, days: { date: string, amount_paise: number }[],
       *   entries: { kind: string, units: number, at: string }[] }} */ (
        await request("/v1/earnings")
      );
      /** @type {Earnings} */
      const earnings = {
        total_paise: w.total_paise,
        bags: w.entries.filter((e) => e.kind === "captain").reduce((n, e) => n + e.units, 0),
        items: w.days
          .filter((d) => d.amount_paise > 0)
          .map((d) => ({
            ...d,
            bags: w.entries
              .filter((e) => e.kind === "captain" && istDate(new Date(e.at)) === d.date)
              .reduce((n, e) => n + e.units, 0),
          }))
          .sort((a, b) => (a.date < b.date ? 1 : -1)),
      };
      return earnings;
    },
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
