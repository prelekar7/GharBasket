import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";
import { AUTOPAY } from "@/lib/features.js";

/**
 * One line of the wallet ledger, signed (+ into the wallet). `kind` may be one the app doesn't know
 * yet: show a generic label for it. `ref` says what caused the entry.
 * @typedef {{ id: string, kind: "topup" | "topup_bonus" | "box_debit" | "refund" | "adjustment" | "refund_to_source" | "refund_to_source_failed",
 *   amount_paise: number, at: string,
 *   ref: { type: "topup", id: string } | { type: "refund", id: string } | { type: "order", id: string, delivery_date: string | null } | null }} Entry
 * `cap_paise` is the most the wallet can hold; `topup_room_paise` what can be added right now;
 * `refundable_paise` what can go back to the paying UPI or card (M5 §3.3).
 * @typedef {{ balance_paise: number, cap_paise: number, topup_room_paise: number, refundable_paise?: number, entries: Entry[] }} Wallet
 */

/** Wallet balance, the room left to top up, and the latest 20 entries (GET /v1/wallet, M5). */
export function useWallet() {
  return useQuery({
    queryKey: ["wallet"],
    queryFn: () => /** @type {Promise<Wallet>} */ (request("/v1/wallet")),
  });
}

/** @typedef {{ points: number, goal: { points: number, code: string } | null, expiring: { points: number, on: string }[] }} KindnessBalance */

/** Kindness points, kept apart from money (GET /v1/kindness/balance, M5). `goal` is null when unset. */
export function useKindness() {
  return useQuery({
    queryKey: ["kindness"],
    queryFn: () => /** @type {Promise<KindnessBalance>} */ (request("/v1/kindness/balance")),
  });
}

/**
 * Turn points into donated meals, the only way to use them (POST /v1/kindness/donations, M5 §3.6).
 * One key per number of meals until it is answered, so a dropped connection never donates twice;
 * 409 billing.not_enough_points means fewer points than meals × goal.
 */
export function useDonateMeal() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<number, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {number} */ meals) => {
      keys.current[meals] ??= crypto.randomUUID();
      return /** @type {Promise<{ id: string, meals: number, points: number, code: string, at: string }>} */ (
        request("/v1/kindness/donations", {
          method: "POST",
          body: { meals },
          headers: { "idempotency-key": keys.current[meals] },
        })
      );
    },
    onSettled: (_d, e, meals) => {
      if (!e || (e instanceof ApiError && e.status < 500)) delete keys.current[meals];
      client.invalidateQueries({ queryKey: ["kindness"] });
    },
  });
}

/**
 * Refund to source (M5 §3.3): unused wallet money back to the paying UPI or card. `parts` are the
 * top-ups the money is taken back from; the refund is processed by a job some minutes later.
 * @typedef {{ id: string, amount_paise: number, reason: "customer" | "account_deleted",
 *   status: "pending" | "processing" | "processed" | "partly_failed" | "failed",
 *   parts: { topup_id: string, razorpay_payment_id: string, amount_paise: number, status: string, razorpay_refund_id: string | null }[],
 *   created_at: string, settled_at: string | null }} Refund
 */

/** Refunds to source, newest first (GET /v1/wallet/refunds, M5 §3.3). */
export function useRefunds() {
  return useQuery({
    queryKey: ["wallet-refunds"],
    queryFn: async () =>
      /** @type {{ items: Refund[], next_cursor: string | null }} */ (
        await request("/v1/wallet/refunds")
      ).items,
  });
}

/** Send unused wallet money back to its source; `null` = everything refundable (POST /v1/wallet/refund-to-source). */
export function useRefundToSource() {
  const client = useQueryClient();
  const key = useRef(/** @type {string | null} */ (null));
  return useMutation({
    mutationFn: (/** @type {number | null} */ amountPaise) => {
      key.current ??= crypto.randomUUID();
      return /** @type {Promise<Refund>} */ (
        request("/v1/wallet/refund-to-source", {
          method: "POST",
          body: { amount_paise: amountPaise },
          headers: { "idempotency-key": key.current },
        })
      );
    },
    onSuccess: () => {
      key.current = null;
      client.invalidateQueries({ queryKey: ["wallet"] });
      client.invalidateQueries({ queryKey: ["wallet-refunds"] });
    },
  });
}

/**
 * One month of the ledger with its opening and closing balance; each entry carries the running balance (M5 §3.2).
 * @typedef {{ period: string, currency: "INR", opening_paise: number, credits_paise: number, debits_paise: number, closing_paise: number,
 *   entries: (Entry & { balance_after_paise: number })[], generated_at: string }} Statement
 */

/** One month of the wallet ledger (GET /v1/statements?period=YYYY-MM, M5). @param {string} period */
export function useStatement(period) {
  return useQuery({
    queryKey: ["statement", period],
    queryFn: () =>
      /** @type {Promise<Statement>} */ (
        request(`/v1/statements?${new URLSearchParams({ period })}`)
      ),
  });
}

/** @typedef {{ id: string, status: "active", max_paise: number, notice_hours: number }} Mandate */

/**
 * Set up UPI Autopay: each box is charged to UPI at the cut-off, up to `maxPaise` a month (POST
 * /v1/mandates, M5). Shared by onboarding (Hf14) and the UPI Autopay screen (HcAutopay).
 * @param {{ key: string, maxPaise?: number }} v
 */
export const createMandate = ({ key, maxPaise }) => {
  if (!AUTOPAY) return Promise.reject(new Error("UPI Autopay is off in this build"));
  return /** @type {Promise<Mandate>} */ (
    request("/v1/mandates", {
      method: "POST",
      headers: { "idempotency-key": key },
      body: maxPaise ? { max_paise: maxPaise } : {},
    })
  );
};

/** The live mandate, or null: when there is none (404) and always when Autopay is off in this build. */
export async function fetchMandate() {
  if (!AUTOPAY) return null;
  try {
    return /** @type {Mandate} */ (await request("/v1/mandates/current"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** @param {string} id */
export const cancelMandate = (id) => {
  if (!AUTOPAY) return Promise.reject(new Error("UPI Autopay is off in this build"));
  return request(`/v1/mandates/${id}`, { method: "DELETE" });
};

/** Your UPI Autopay mandate, or null when boxes are paid from the wallet (GET /v1/mandates/current). */
export function useMandate() {
  return useQuery({
    queryKey: ["mandate"],
    queryFn: fetchMandate,
  });
}

/** Turn UPI Autopay on with a limit; a retry of the same attempt reuses its key. */
export function useCreateMandate() {
  const client = useQueryClient();
  const key = useRef(/** @type {string | null} */ (null));
  return useMutation({
    mutationFn: (/** @type {number} */ maxPaise) => {
      key.current ??= crypto.randomUUID();
      return createMandate({ key: key.current, maxPaise });
    },
    onSuccess: (m) => {
      key.current = null;
      client.setQueryData(["mandate"], m);
    },
  });
}

/** Cancel UPI Autopay; boxes are paid from the wallet again (DELETE /v1/mandates/{id}, M5). */
export function useCancelMandate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: cancelMandate,
    onSuccess: () => client.setQueryData(["mandate"], null),
  });
}
