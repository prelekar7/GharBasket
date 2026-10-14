import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";

/** @typedef {{ balance_paise: number, entries: { id: string, kind: string, amount_paise: number, at: string }[] }} Wallet */

/** Wallet balance and recent entries (GET /v1/wallet, M5). */
export function useWallet() {
  return useQuery({
    queryKey: ["wallet"],
    queryFn: () => /** @type {Promise<Wallet>} */ (request("/v1/wallet")),
  });
}

/** @typedef {{ points: number, goal: { points: number, code: string }, expiring: { points: number, on: string }[] }} KindnessBalance */

/** Kindness points, kept apart from money (GET /v1/kindness/balance, M5). */
export function useKindness() {
  return useQuery({
    queryKey: ["kindness"],
    queryFn: () => /** @type {Promise<KindnessBalance>} */ (request("/v1/kindness/balance")),
  });
}

/** @typedef {{ period: string, opening_paise: number, credits_paise: number, debits_paise: number, closing_paise: number, entries: (Wallet["entries"][number] & { ref?: string })[] }} Statement */

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
export const createMandate = ({ key, maxPaise }) =>
  /** @type {Promise<Mandate>} */ (
    request("/v1/mandates", {
      method: "POST",
      headers: { "idempotency-key": key },
      body: maxPaise ? { max_paise: maxPaise } : {},
    })
  );

/** Your UPI Autopay mandate, or null when boxes are paid from the wallet (GET /v1/mandates/current). */
export function useMandate() {
  return useQuery({
    queryKey: ["mandate"],
    queryFn: async () => {
      try {
        return /** @type {Mandate} */ (await request("/v1/mandates/current"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
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
    mutationFn: (/** @type {string} */ id) => request(`/v1/mandates/${id}`, { method: "DELETE" }),
    onSuccess: () => client.setQueryData(["mandate"], null),
  });
}
