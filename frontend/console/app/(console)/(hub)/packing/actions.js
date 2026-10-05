"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * What the person is told for each failure; the `code` decides, never the server's text.
 * @type {Record<string, string>}
 */
const MESSAGES = {
  "packing.not_packed_yet": "Pack and label the bag first, then it can go in a crate.",
  "packing.crate_full": "Every crate for that society is full. Start a new crate at the van.",
  "packing.packers_busy": "Every packer already has a bag open. Try again when one is packed.",
  "packing.nothing_to_pack": "No bags are waiting to be packed.",
  "packing.task_not_found": "That bag is no longer on today’s list. Reload the board.",
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
 * Moves a bag to another column of the packing board.
 * @param {string} taskId @param {string} to a packing status @param {string} idempotencyKey one per move
 */
export async function moveTask(taskId, to, idempotencyKey) {
  return attempt(() => apiPost(`/v1/packing-tasks/${taskId}/move`, { to }, { idempotencyKey }));
}

/** Gives the next bag in route order to a free packer. @param {string} idempotencyKey */
export async function assignNext(idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/packing-tasks/assign-next`, {}, { idempotencyKey }),
  );
}

/** Sends the bag-label sheets to the hub printer. @param {string} idempotencyKey */
export async function printLabels(idempotencyKey) {
  const hub = await currentHub();
  return attempt(() => apiPost(`/v1/hubs/${hub.id}/packing-labels/print`, {}, { idempotencyKey }));
}
