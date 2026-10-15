"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.task_not_found": "That bag is no longer on the list. Reload the board.",
  "hub_ops.task_packed": "That bag is already packed, so nobody holds it.",
  "hub_ops.task_not_started": "Nobody holds that bag yet.",
  "hub_ops.date_out_of_range": "Bags can only be built for today or the next two days.",
};

/**
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<{ ok: true, data: T } | { ok: false, message: string }>}
 */
async function attempt(run) {
  try {
    return { ok: true, data: await run() };
  } catch (e) {
    if (e instanceof ApiProblem)
      return { ok: false, message: MESSAGES[e.code] ?? "That did not work. Please try again." };
    throw e;
  }
}

/**
 * Frees a bag a packer started, so any packer of the hub can take it (M6 §3.2). The order stays in picking.
 * @param {string} taskId @param {string} idempotencyKey one per release
 */
export async function releaseTask(taskId, idempotencyKey) {
  return attempt(() =>
    apiPost(`/v1/packing-tasks/${taskId}/release`, undefined, { idempotencyKey }),
  );
}

/**
 * Builds the day's packing tasks now (the 05:00 job, on demand): one task per locked box or add-on order
 * that has none yet. Running it again adds nothing.
 * @param {string} date @param {string} idempotencyKey
 * @returns {Promise<{ ok: true, data: { created: number, existing: number } } | { ok: false, message: string }>}
 */
export async function buildTasks(date, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/packing-tasks/build`, { date }, { idempotencyKey }),
  );
}
