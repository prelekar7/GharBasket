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
