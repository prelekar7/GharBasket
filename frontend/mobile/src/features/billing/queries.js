import { useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

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
