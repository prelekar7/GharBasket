import { ApiError, request } from "@/lib/api.js";
import { onAppResume } from "@/lib/payments.js";

/** @typedef {import("@/lib/payments.js").TopUpOrder} TopUpOrder */

/** @param {string} id */
export const fetchTopUp = (id) =>
  /** @type {Promise<TopUpOrder>} */ (request(`/v1/wallet/topups/${id}`));

/**
 * After Checkout succeeds the webhook credits the wallet, never the app: ask for the top-up every
 * 2 seconds, up to 30 (M5 §3.1), until it is no longer "created". Resolves with its last status,
 * or "timeout" when it is still unpaid. A 404 means it is not ours, so it is thrown; a failed
 * request is tried again until the time is up.
 *
 * When the app comes back to the foreground (the person returns from a UPI app, or from the home
 * screen) it asks at once and the 30 seconds start again: timers do not run while the app is away,
 * so the time spent in GPay must not count against the wait.
 * @param {string} id
 * @param {{ intervalMs?: number, timeoutMs?: number, signal?: AbortSignal, fetchStatus?: (id: string) => Promise<{ status: string }>, subscribeResume?: (callback: () => void) => Promise<() => void> }} [options]
 * @returns {Promise<TopUpOrder["status"] | "timeout">}
 */
export async function pollTopUp(
  id,
  {
    intervalMs = 2000,
    timeoutMs = 30_000,
    signal,
    fetchStatus = fetchTopUp,
    subscribeResume = onAppResume,
  } = {},
) {
  let until = Date.now() + timeoutMs;
  /** @type {(() => void) | null} */
  let wake = null;
  const stopResume = await subscribeResume(() => {
    until = Date.now() + timeoutMs;
    wake?.();
  });
  try {
    for (;;) {
      try {
        const { status } = await fetchStatus(id);
        if (status !== "created") return /** @type {TopUpOrder["status"]} */ (status);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) throw e;
      }
      if (signal?.aborted || Date.now() + intervalMs > until) return "timeout";
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, intervalMs);
        wake = () => {
          clearTimeout(timer);
          resolve(undefined);
        };
      });
      wake = null;
    }
  } finally {
    stopResume();
  }
}
