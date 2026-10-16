import { ApiError, request } from "@/lib/api.js";

/** @typedef {import("@/lib/payments.js").TopUpOrder} TopUpOrder */

/** @param {string} id */
const fetchTopUp = (id) => /** @type {Promise<TopUpOrder>} */ (request(`/v1/wallet/topups/${id}`));

/**
 * After Checkout succeeds the webhook credits the wallet, never the app: ask for the top-up every
 * 2 seconds, up to 30 (M5 §3.1), until it is no longer "created". Resolves with its last status,
 * or "timeout" when it is still unpaid. A 404 means it is not ours, so it is thrown; a failed
 * request is tried again until the time is up.
 * @param {string} id
 * @param {{ intervalMs?: number, timeoutMs?: number, signal?: AbortSignal, fetchStatus?: (id: string) => Promise<{ status: string }> }} [options]
 * @returns {Promise<TopUpOrder["status"] | "timeout">}
 */
export async function pollTopUp(
  id,
  { intervalMs = 2000, timeoutMs = 30_000, signal, fetchStatus = fetchTopUp } = {},
) {
  const until = Date.now() + timeoutMs;
  for (;;) {
    try {
      const { status } = await fetchStatus(id);
      if (status !== "created") return /** @type {TopUpOrder["status"]} */ (status);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) throw e;
    }
    if (signal?.aborted || Date.now() + intervalMs > until) return "timeout";
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
